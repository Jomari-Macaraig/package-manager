"""v2 API: routes, shared slot, storage, feedback, evaluation maths,
instruction-sample gates, contract fixture."""
import copy
import json
import os
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

import openai
import pytest
from fastapi.testclient import TestClient

import contract
from app import config, store, store_v2
from app.fake_llm import FakeClient
from app.main import create_app
from app.pipeline_v2 import V2_ADAPTER
from app.runs import NotActive
from app.samples import SAMPLES, SAMPLES_V2
from app.schemas import OrderInput, to_pipeline_inputs
from app.schemas_v2 import RunV2
from conftest import sample_body
from packing.jobs.strategy import strategy_answer_from_plan
from packing.packer import pack, pack_guided, preference_report
from packing.planner import _coverage_errors
from packing.settings import PackingSettings, prepare_boxes, prepare_items
from packing.validation import plan_score, validate_plan
from test_v2_core import http_error

TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")
FIXTURE = Path(__file__).parent / "fixtures" / "run_v2.json"
RID = "33333333-3333-4333-8333-333333333333"
ALL = {s["key"]: s for s in SAMPLES + SAMPLES_V2}


def body_v2(key="A", **overrides):
    sample = ALL[key]
    body = {"items": copy.deepcopy(sample["items"]),
            "boxes": copy.deepcopy(sample["boxes"]),
            "instructions": sample["instructions"]}
    body.update(overrides)
    return body


def start_v2(client, key="A", **overrides):
    r = client.post("/api/v2/runs", json=body_v2(key, **overrides))
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def wait_v2(client, run_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(f"/api/v2/runs/{run_id}").json()
        if run["status"] in TERMINAL:
            return run
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def sql(query, *params):
    with closing(sqlite3.connect(config.DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(query, params).fetchall()]


@pytest.fixture
def gated(app):
    gate = threading.Event()
    app.state.runs.client_factory = lambda: FakeClient(gate=gate)
    app.state.runs_v2.client_factory = lambda: FakeClient(gate=gate)
    yield gate
    gate.set()


# --- config, samples, request validation --------------------------------------

def test_config_and_samples(client):
    cfg = client.get("/api/v2/config").json()
    assert set(cfg["jobs"]) == {"extract", "strategy", "check", "notes"}
    assert cfg["jobs"]["strategy"]["default"] == {
        "model": "gpt-5.4-nano", "reasoning_effort": "low"}
    assert cfg["jobs"]["extract"]["default"]["reasoning_effort"] is None
    strategy = {m["id"]: m for m in cfg["jobs"]["strategy"]["models"]}
    assert strategy["gpt-5.4-mini"]["efforts"] == ["low", "medium", "high"]
    assert all(m["efforts"] is None for j in ("extract", "check", "notes")
               for m in cfg["jobs"][j]["models"])
    assert cfg["budget"] == {"usd": 0.02, "seconds": 30}
    assert cfg["volume_margin"] == 1.15 and cfg["caps"]["max_units"] == 150
    samples = client.get("/api/v2/samples").json()
    assert [s["key"] for s in samples] == list("ABCDEFGH")
    for s in samples:
        contract.check(s, contract.SAMPLE_V2)
    assert [s["key"] for s in client.get("/api/samples").json()] == list("ABCDE")


@pytest.mark.parametrize("jobs", [
    {"bogus": {"model": "gpt-4.1-nano"}},
    {"extract": {"model": "gpt-4.1-nano", "extra": 1}},
    {"extract": {"model": "nope"}},
    {"extract": {"model": "gpt-4.1-nano", "reasoning_effort": "low"}},
    {"strategy": {"model": "gpt-5.4-nano", "reasoning_effort": None}},
    {"strategy": {"model": "gpt-5.4-nano", "reasoning_effort": "extreme"}},
    {"strategy": {"model": "gpt-4.1-nano", "reasoning_effort": "low"}},
])
def test_bad_job_choices_are_422(client, jobs):
    r = client.post("/api/v2/runs", json=body_v2("A", jobs=jobs))
    assert r.status_code == 422


def test_unknown_top_level_key_is_422_in_v2_but_accepted_in_v1(client):
    assert client.post("/api/v2/runs", json=body_v2("A", extra=1)
                       ).status_code == 422
    r = client.post("/api/runs", json=sample_body("A", extra=1))
    assert r.status_code == 202


# --- runs ---------------------------------------------------------------------

@pytest.mark.parametrize("key", list("ABCDEFGH"))
def test_fake_run_on_every_sample(client, key):
    run = wait_v2(client, start_v2(client, key))
    assert run["status"] == "succeeded" and run["error"] is None
    RunV2.model_validate(run)
    assert run["fake"] is True
    assert [j["job"] for j in run["jobs"]] == [
        "extract", "strategy", "check", "notes"]
    rows = sql("SELECT job, fake FROM job_calls WHERE run_id = ?",
               run["run_id"])
    assert len(rows) == 4 and all(r["fake"] == 1 for r in rows)
    assert sql("SELECT fake FROM runs_v2")[0]["fake"] == 1
    assert run["events"][0]["type"] == "started"
    assert run["events"][-1]["type"] == "end"
    assert run["source"] in ("model", "packer", "none")
    assert run["totals"]["cost"] > 0 and run["totals"]["budget"] == {
        "usd": 0.02, "seconds": 30}
    if run["final"]:
        assert run["final"]["source"] == run["source"]
        assert all(n.startswith(("Cut box", "Box")) for n in
                   run["final"]["cut_notes"])
        assert run["final"]["plan"]["notes"] == run["final"]["cut_notes"]
        for box in range(1, len(run["final"]["plan"]["boxes"]) + 1):
            r = client.get(f"/api/v2/runs/{run['run_id']}/render",
                           params={"plan": "final", "box": box})
            assert r.status_code == 200 and r.text.startswith("<svg")


def test_run_list_summary_and_history(client):
    run_id = start_v2(client, "G")
    run = wait_v2(client, run_id)
    [summary] = client.get("/api/v2/runs").json()
    assert summary["run_id"] == run_id and summary["fake"] is True
    assert summary["title"] == "Kettle, Shoe Box, Jar"
    assert summary["source"] == run["source"] and summary["total_cost"] > 0
    assert summary["over_budget"] is False
    assert client.get("/api/runs").json() == []  # v1 history is separate


def test_jobs_override_is_used(client, app):
    clients = []
    app.state.runs_v2.client_factory = lambda: clients.append(
        FakeClient()) or clients[-1]
    jobs = {"strategy": {"model": "gpt-5.4-mini", "reasoning_effort": "medium"},
            "notes": {"model": "gpt-5.4-nano"}}
    run = wait_v2(client, start_v2(client, "G", jobs=jobs))
    by = {j["job"]: j for j in run["jobs"]}
    assert (by["strategy"]["model"], by["strategy"]["reasoning_effort"]) == (
        "gpt-5.4-mini", "medium")
    assert by["notes"]["model"] == "gpt-5.4-nano"
    assert by["extract"]["model"] == "gpt-4.1-nano"
    strategy_call = next(c for c in clients[0].parse_calls
                         if c["model"] == "gpt-5.4-mini")
    assert strategy_call["max_completion_tokens"] == 32000
    notes_call = next(c for c in clients[0].parse_calls
                      if c["model"] == "gpt-5.4-nano"
                      and c["max_completion_tokens"] == 1500)
    assert notes_call["reasoning_effort"] == "none"  # fixed kwargs


def test_skipped_jobs_record_the_configured_model(client):
    run = wait_v2(client, start_v2(client, "A"))
    by = {j["job"]: j for j in run["jobs"]}
    assert by["extract"]["status"] == "skipped" and by["extract"]["calls"] == 0
    assert by["extract"]["model"] == "gpt-4.1-nano"
    assert by["check"]["status"] == "skipped"
    assert by["check"]["model"] == "gpt-4.1-mini" and by["check"]["cost"] == 0
    assert run["verdicts"] == []
    assert run["reason"] in ("better metrics", "same as packer")


def test_job_messages_route(client):
    run_id = start_v2(client, "G")
    wait_v2(client, run_id)
    r = client.get(f"/api/v2/runs/{run_id}/jobs/strategy/messages")
    messages = r.json()["messages"]
    assert [m["role"] for m in messages][:2] == ["system", "user"]
    assert client.get(f"/api/v2/runs/{RID}/jobs/strategy/messages"
                      ).status_code == 404
    assert client.get(f"/api/v2/runs/{run_id}/jobs/nope/messages"
                      ).status_code == 422


def test_routes_404_and_render_rules(client, gated):
    assert client.get(f"/api/v2/runs/{RID}").status_code == 404
    assert client.get("/api/v2/runs/not-a-uuid").status_code == 404
    assert client.get(f"/api/v2/runs/{RID}/events").status_code == 404
    run_id = start_v2(client, "A")
    assert client.get(f"/api/v2/runs/{run_id}/render",
                      params={"plan": "final", "box": 1}).status_code == 409
    running = client.get(f"/api/v2/runs/{run_id}").json()
    assert running["status"] == "running" and running["jobs"] == []
    assert running["final"] is None and running["source"] is None
    RunV2.model_validate(running)
    gated.set()
    wait_v2(client, run_id)
    assert client.get(f"/api/v2/runs/{run_id}/render",
                      params={"plan": "final", "box": 9}).status_code == 404
    assert client.get(f"/api/v2/runs/{run_id}/svg",
                      params={"plan": "packer"}).headers[
        "content-disposition"].endswith('-packer.svg"')
    assert client.get(f"/api/v2/runs/{run_id}/svg",
                      params={"plan": "bogus"}).status_code == 422


def test_sse_replays_a_finished_v2_run(client):
    run_id = start_v2(client, "G")
    run = wait_v2(client, run_id)
    text = client.get(f"/api/v2/runs/{run_id}/events").text
    frames = [json.loads(line[6:]) for line in text.split("\n")
              if line.startswith("data: ")]
    assert [f["id"] for f in frames] == list(range(len(frames)))
    assert frames == run["events"]
    assert [f["type"] for f in frames] == [
        "started", "job", "packer", "job", "job", "chosen", "job", "end"]


# --- shared slot ----------------------------------------------------------------

def test_v1_run_blocks_v2_and_hides_from_v2_active(client, gated):
    v1 = client.post("/api/runs", json=sample_body("A")).json()["run_id"]
    r = client.post("/api/v2/runs", json=body_v2("A"))
    assert r.status_code == 409
    assert r.json() == {"detail": "A run is already in progress",
                        "kind": "v1"}
    assert client.get("/api/runs/active").json() == {"run_id": v1}
    assert client.get("/api/v2/runs/active").json() == {"run_id": None}
    gated.set()
    wait_for(lambda: client.get("/api/runs/active").json()["run_id"] is None)


def test_v2_run_blocks_v1_and_hides_from_v1_active(client, gated):
    v2 = start_v2(client, "A")
    r = client.post("/api/runs", json=sample_body("A"))
    assert r.status_code == 409
    assert r.json() == {"detail": "A run is already in progress",
                        "kind": "v2"}
    assert client.post("/api/v2/runs", json=body_v2("A")).json()[
        "kind"] == "v2"
    assert client.get("/api/runs/active").json() == {"run_id": None}
    assert client.get("/api/v2/runs/active").json() == {"run_id": v2}
    gated.set()
    wait_v2(client, v2)
    assert client.get("/api/v2/runs/active").json() == {"run_id": None}
    # the slot is free again, for either kind
    assert client.post("/api/runs", json=sample_body("A")).status_code == 202


def test_cancel_only_sees_runs_of_its_own_kind(client, app, gated):
    v2 = start_v2(client, "A")
    with pytest.raises(NotActive):
        app.state.runs.cancel(uuid.UUID(v2))
    # the v1 route does not know the run at all
    assert client.post(f"/api/runs/{v2}/cancel", json={}).status_code == 404
    gated.set()
    wait_v2(client, v2)
    v1 = client.post("/api/runs", json=sample_body("A")).json()["run_id"]
    with pytest.raises(NotActive):
        app.state.runs_v2.cancel(uuid.UUID(v1))
    assert client.post(f"/api/v2/runs/{v1}/cancel", json={}).status_code == 404


def test_cancel_v2_run_gives_four_jobs(client, gated):
    run_id = start_v2(client, "G")
    wait_for(lambda: len(store_v2.events(run_id)[2]) >= 3)  # packer done
    assert client.post(f"/api/v2/runs/{run_id}/cancel", json={}
                       ).status_code == 202
    gated.set()
    run = wait_v2(client, run_id)
    assert run["status"] == "cancelled"
    assert run["events"][-1]["status"] == "cancelled"
    jobs = {j["job"]: j for j in run["jobs"]}
    assert list(jobs) == ["extract", "strategy", "check", "notes"]
    assert jobs["extract"]["status"] == "ok"
    assert [jobs[n]["status"] for n in ("check", "notes")] == [
        "skipped", "skipped"]
    assert jobs["notes"]["error"] == "cancelled"
    assert len(sql("SELECT 1 FROM job_calls")) == 4
    assert client.post(f"/api/v2/runs/{run_id}/cancel", json={}
                       ).status_code == 409
    assert client.post(f"/api/v2/runs/{RID}/cancel", json={}
                       ).status_code == 404


def test_cors_allows_put(client):
    r = client.options(
        f"/api/v2/runs/{RID}/feedback/strategy",
        headers={"Origin": "http://localhost:5173",
                 "Access-Control-Request-Method": "PUT"})
    assert r.status_code == 200
    assert "PUT" in r.headers["access-control-allow-methods"]


# --- failures -----------------------------------------------------------------

def test_config_error_fails_the_run_and_keeps_job_rows(client, app):
    app.state.runs_v2.client_factory = lambda: FakeClient(
        script=[http_error(openai.AuthenticationError, 401)])
    run = wait_v2(client, start_v2(client, "G"))
    assert run["status"] == "failed"
    assert "OPENAI_API_KEY" in run["error"]
    assert run["events"][-1]["status"] == "failed"
    statuses = [(j["job"], j["status"], j["error"]) for j in run["jobs"]]
    assert statuses[1:] == [("strategy", "skipped", "not run"),
                            ("check", "skipped", "not run"),
                            ("notes", "skipped", "not run")]
    assert run["jobs"][0]["status"] == "ok"
    assert len(sql("SELECT 1 FROM job_calls")) == 4
    assert run["totals"]["cost"] > 0  # the extract call is counted
    RunV2.model_validate(run)


def test_missing_key_fails_a_v2_run_without_job_rows(client, monkeypatch):
    monkeypatch.setattr(config, "FAKE_LLM", False)
    run = wait_v2(client, start_v2(client, "A"))
    assert run["status"] == "failed" and "OPENAI_API_KEY" in run["error"]
    assert run["jobs"] == []
    assert sql("SELECT 1 FROM job_calls") == []


def test_no_valid_plan_still_succeeds_with_final_null(client, monkeypatch):
    import packing.v2 as v2
    real = v2.pack

    def broken(items, boxes, settings=None, estimates=None):
        flat, _ = real(items, boxes, settings, estimates)
        return flat, ["forced error"]
    monkeypatch.setattr(v2, "pack", broken)
    run = wait_v2(client, start_v2(client, "G"))
    assert run["status"] == "succeeded" and run["error"] is None
    assert run["final"] is None and run["source"] == "none"
    assert run["reason"] == "no valid plan"
    assert run["packer"]["ok"] is False
    assert run["packer"]["errors"] == ["forced error"]
    assert [j["status"] for j in run["jobs"]][1:] == [
        "fallback", "skipped", "skipped"]
    assert client.get(f"/api/v2/runs/{run['run_id']}/render",
                      params={"plan": "final", "box": 1}).status_code == 404
    RunV2.model_validate(run)
    assert V2_ADAPTER.outcome({"final": None}) == ("succeeded", None)


# --- storage ---------------------------------------------------------------------

def make_job(name, status="ok", model="m", version="v1", checks=(),
             latency=1.0, cost=0.01, output=None, effort=None):
    return {"job": name, "status": status, "model": model,
            "reasoning_effort": effort, "prompt_version": version,
            "calls": 0 if status == "skipped" else 1,
            "checks": [{"name": n, "passed": p, "detail": ""}
                       for n, p in checks],
            "prompt_tokens": 1, "completion_tokens": 1,
            "reasoning_tokens": 0, "cost": cost, "latency_s": latency,
            "error": None, "answer": None, "output": output, "messages": []}


def all_jobs(**overrides):
    jobs = {n: make_job(n) for n in ("extract", "strategy", "check", "notes")}
    jobs.update(overrides)
    return list(jobs.values())


def make_run(rid=None, *, fake=False, status="succeeded", source="model",
             jobs=None, created_at=None):
    rid = rid or str(uuid.uuid4())
    store_v2.create_run(rid, "{}", trace_id="t", request_id="r",
                        client_ip="ip", user_agent="ua", title="x",
                        fake=fake)
    wrote = store_v2.finish(
        rid, status, None, {"source": source, "reason": "r", "totals": {
            "cost": 0.0, "seconds": 0.0, "over_budget": False}},
        {}, [], jobs if jobs is not None else all_jobs())
    assert wrote
    if created_at:
        with closing(sqlite3.connect(config.DB_PATH)) as conn, conn:
            conn.execute("UPDATE job_calls SET created_at = ? "
                         "WHERE run_id = ?", (created_at, rid))
    return rid


@pytest.fixture
def db(app):
    store.init()
    store_v2.init()


def test_finish_is_idempotent_and_guarded(db):
    rid = make_run()
    assert len(sql("SELECT 1 FROM job_calls")) == 4
    again = store_v2.finish(rid, "succeeded", None, {}, {}, [], all_jobs())
    assert again is False  # the row is no longer running: no new rows
    assert len(sql("SELECT 1 FROM job_calls")) == 4
    # a direct duplicate insert is ignored (ON CONFLICT DO NOTHING)
    store_v2.create_run(RID, "{}", trace_id="t", request_id="r",
                        client_ip="ip", user_agent="ua", title="x",
                        fake=False)
    jobs = all_jobs() + all_jobs()
    assert store_v2.finish(RID, "succeeded", None, {}, {}, [], jobs)
    assert len(sql("SELECT 1 FROM job_calls WHERE run_id = ?", RID)) == 4


def test_interrupted_on_restart_has_no_job_rows(app):
    store.init()
    store_v2.init()
    store_v2.create_run(RID, json.dumps(body_v2("A")), trace_id="t", request_id="r",
                        client_ip="ip", user_agent="ua", title="x",
                        fake=False)
    store_v2.append_event(RID, [{"id": 0, "type": "started", "t": 0.0}])
    with TestClient(app, base_url="http://localhost") as c:
        row = store_v2.get(RID)
        assert row["status"] == "interrupted"
        assert json.loads(row["events_json"])[-1]["status"] == "interrupted"
        run = c.get(f"/api/v2/runs/{RID}").json()
        assert run["jobs"] == [] and run["status"] == "interrupted"
        text = c.get(f"/api/v2/runs/{RID}/events").text
        assert "event: end" in text
    assert sql("SELECT 1 FROM job_calls") == []
    assert store_v2.finish(RID, "succeeded", None, {}, {}, [], all_jobs()
                           ) is False
    assert store_v2.set_status(RID, "failed", "x") is False


# --- feedback ----------------------------------------------------------------------

def test_feedback_rules(client):
    run_id = start_v2(client, "A")  # extract and check are skipped
    run = wait_v2(client, run_id)
    url = f"/api/v2/runs/{run_id}/feedback"
    r = client.put(f"{url}/strategy", json={"rating": "up", "comment": "ok"})
    assert r.status_code == 200
    fb = r.json()
    assert (fb["rating"], fb["comment"], fb["wrong_verdicts"]) == (
        "up", "ok", [])
    # persisted and shown in the run
    jobs = {j["job"]: j for j in client.get(f"/api/v2/runs/{run_id}"
                                            ).json()["jobs"]}
    assert jobs["strategy"]["feedback"]["rating"] == "up"
    assert jobs["notes"]["feedback"] is None
    # update keeps one row; clearing the rating keeps the comment
    first = sql("SELECT created_at FROM feedback")[0]["created_at"]
    r = client.put(f"{url}/strategy", json={"comment": "only a comment"})
    assert r.json()["rating"] is None
    assert len(sql("SELECT 1 FROM feedback")) == 1
    assert sql("SELECT created_at FROM feedback")[0]["created_at"] == first
    # rejected
    assert client.put(f"{url}/extract", json={"rating": "up"}
                      ).status_code == 422  # skipped
    assert client.put(f"{url}/bogus", json={"rating": "up"}
                      ).status_code == 422
    assert client.put(f"{url}/notes", json={"wrong_verdicts": [0]}
                      ).status_code == 422
    assert client.put(f"{url}/notes", json={"rating": "meh"}
                      ).status_code == 422
    assert client.put(f"{url}/notes", json={"comment": "x" * 2001}
                      ).status_code == 422
    assert client.put(f"{url}/notes", json={"nope": 1}).status_code == 422
    assert client.put(f"/api/v2/runs/{RID}/feedback/notes",
                      json={"rating": "up"}).status_code == 404
    assert run["jobs"][3]["status"] == "ok"


def test_feedback_wrong_verdicts_for_check_and_run_not_terminal(client, gated):
    run_id = start_v2(client, "G")
    url = f"/api/v2/runs/{run_id}/feedback/check"
    assert client.put(url, json={"rating": "up"}).status_code == 409
    gated.set()
    wait_v2(client, run_id)
    assert client.put(url, json={"wrong_verdicts": [1]}).status_code == 422
    assert client.put(url, json={"wrong_verdicts": [-1]}).status_code == 422
    r = client.put(url, json={"wrong_verdicts": [0, 0]})
    assert r.status_code == 200 and r.json()["wrong_verdicts"] == [0]


def test_feedback_for_a_run_without_job_rows_is_404(client, app):
    store.init()
    store_v2.init()
    store_v2.create_run(RID, "{}", trace_id="t", request_id="r",
                        client_ip="ip", user_agent="ua", title="x",
                        fake=False)
    store_v2.set_status(RID, "failed", "x")
    r = client.put(f"/api/v2/runs/{RID}/feedback/notes",
                   json={"rating": "up"})
    assert r.status_code == 404


# --- evaluation --------------------------------------------------------------------

def group(client, **params):
    r = client.get("/api/v2/eval/summary", params=params)
    assert r.status_code == 200, r.text
    return r.json()["groups"]


def test_eval_empty_and_bad_since(client, db):
    assert group(client) == []
    r = client.get("/api/v2/eval/summary", params={"since": "yesterday"})
    assert r.status_code == 422
    assert client.get("/api/v2/eval/summary", params={"job": "x"}
                      ).status_code == 422


def test_eval_rates_percentiles_and_per_check(client, db):
    for n, (status, latency, passed) in enumerate([
            ("ok", 1.0, True), ("ok", 2.0, True), ("ok", 3.0, False),
            ("fallback", 4.0, False)]):
        make_run(jobs=all_jobs(strategy=make_job(
            "strategy", status, latency=latency, cost=0.02,
            checks=[("parsed", True), ("plan_valid", passed)])))
    make_run(jobs=all_jobs(strategy=make_job("strategy", "skipped")))
    [g] = group(client, job="strategy")
    assert (g["calls"], g["skipped"]) == (4, 1)
    assert g["ok_rate"] == 0.75 and g["fallback_rate"] == 0.25
    assert g["all_checks_rate"] == 0.5
    assert g["per_check"] == {"parsed": 1.0, "plan_valid": 0.5}
    assert (g["p50_latency_s"], g["p95_latency_s"]) == (2.0, 4.0)
    assert g["avg_cost"] == pytest.approx(0.02)
    assert g["wrong_verdict_rate"] is None
    assert (g["thumbs_up"], g["thumbs_down"]) == (0, 0)
    assert g["first_seen"] <= g["last_seen"]
    assert g["prompt_version"] == "v1" and g["model"] == "m"


def test_eval_null_rates_for_skipped_only_groups(client, db):
    make_run(jobs=all_jobs(check=make_job("check", "skipped")))
    [g] = group(client, job="check")
    assert g["calls"] == 0 and g["skipped"] == 1
    for key in ("ok_rate", "fallback_rate", "all_checks_rate", "avg_cost",
                "p50_latency_s", "p95_latency_s", "wrong_verdict_rate"):
        assert g[key] is None, key
    assert g["per_check"] == {}


def test_eval_groups_by_version_model_and_effort(client, db):
    make_run(jobs=all_jobs(strategy=make_job("strategy", effort="low")))
    make_run(jobs=all_jobs(strategy=make_job("strategy", effort="high")))
    make_run(jobs=all_jobs(strategy=make_job("strategy", model="m2")))
    make_run(jobs=all_jobs(strategy=make_job("strategy", version="v2")))
    groups = group(client, job="strategy")
    assert len(groups) == 4
    assert {(g["prompt_version"], g["model"], g["reasoning_effort"])
            for g in groups} == {("v1", "m", "low"), ("v1", "m", "high"),
                                 ("v1", "m2", None), ("v2", "m", None)}


def test_eval_thumbs_wrong_verdicts_and_model_won(client, db):
    verdicts = [{"index": n, "verdict": "honoured", "reason": ""}
                for n in range(2)]
    ids = [make_run(source=src, jobs=all_jobs(
        check=make_job("check", output=verdicts)))
        for src in ("model", "packer", "none")]
    failed = make_run(status="failed", source="model")  # not decided
    store_v2.upsert_feedback(ids[0], "check", "down", "", [1])
    store_v2.upsert_feedback(ids[1], "check", None, "meh", [])
    store_v2.upsert_feedback(ids[2], "check", None, "", [])  # not reviewed
    store_v2.upsert_feedback(ids[0], "strategy", "up", "", [])
    store_v2.upsert_feedback(ids[1], "strategy", "down", "", [])
    store_v2.upsert_feedback(failed, "strategy", "up", "", [])
    [check] = group(client, job="check")
    assert check["wrong_verdict_rate"] == 1 / 4  # 1 wrong of 2 reviewed runs' 4
    assert (check["thumbs_up"], check["thumbs_down"]) == (0, 1)
    [strategy] = group(client, job="strategy")
    assert strategy["model_won_rate"] == 1 / 3  # succeeded runs only
    assert (strategy["thumbs_up"], strategy["thumbs_down"]) == (2, 1)
    [notes] = group(client, job="notes")
    assert notes["model_won_rate"] is None


def test_eval_excludes_fake_runs_by_default(client, db):
    make_run(fake=True)
    assert group(client) == []
    assert len(group(client, include_fake="true")) == 4
    make_run(fake=False)
    assert len(group(client)) == 4
    assert {g["calls"] for g in group(client, include_fake="true")} == {2}


def test_eval_since_is_normalised_to_milliseconds(client, db):
    make_run(created_at="2026-03-01T10:00:00.250Z")
    make_run(created_at="2026-03-01T10:00:00.000Z")
    make_run(created_at="2026-03-01T09:59:59.999Z")
    [g] = group(client, job="notes", since="2026-03-01T10:00:00Z")
    assert g["calls"] == 2  # the first second is not dropped
    [g] = group(client, job="notes", since="2026-03-01T10:00:00.100Z")
    assert g["calls"] == 1
    assert store_v2.normalise_since("2026-03-01T10:00:00Z") == (
        "2026-03-01T10:00:00.000Z")
    assert store_v2.normalise_since("2026-03-01T10:00:00.5Z") == (
        "2026-03-01T10:00:00.500Z")


def test_nearest_rank_percentiles():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert store_v2._percentile(values, 50) == 5.0
    assert store_v2._percentile(values, 95) == 10.0
    assert store_v2._percentile([7.0], 95) == 7.0
    assert store_v2._percentile([], 50) is None


# --- instruction samples F-H ----------------------------------------------------------

# the guided choices that honour each sample's instruction
GATES = {
    "F": {"boxes": ["Medium"], "order": ["Cast Iron Pan", "Glass Bowl"],
          "prefs": {"Cast Iron Pan": ("back", "any"),
                    "Glass Bowl": ("front", "any")}},
    "G": {"boxes": ["Wide", "Medium"], "order": ["Shoe Box", "Kettle", "Jar"],
          "prefs": {"Kettle": ("right", "any"), "Jar": ("left", "any")}},
    "H": {"boxes": ["Small"], "order": ["Tray", "Vase"],
          "prefs": {"Vase": ("any", "top")}},
}


@pytest.mark.parametrize("key", ["F", "G", "H"])
def test_instruction_sample_gates(key):
    sample = ALL[key]
    req = OrderInput.model_validate({k: sample[k] for k in (
        "items", "boxes", "instructions")})
    items, boxes = to_pipeline_inputs(req)
    settings = PackingSettings()
    items_p = prepare_items(items, boxes, settings)
    boxes_p = prepare_boxes(boxes, settings)
    baseline, errors = pack(items_p, boxes_p, settings)
    assert errors == []
    gate = GATES[key]
    ids = {i["name"]: i["id"] for i in items_p}
    box_ids = {b["name"]: b["id"] for b in boxes_p}
    prefs = {ids[n]: {"side": s, "level": lv}
             for n, (s, lv) in gate["prefs"].items()}
    # the packer ignores the instruction
    report = preference_report(baseline, items_p, boxes_p, prefs)
    assert any(r["honoured"] < r["total"] for r in report), report
    # the matching guided placement honours it at no extra cost
    answer = strategy_answer_from_plan(baseline)
    flat, errors = pack_guided(
        items_p, boxes_p, settings, [box_ids[n] for n in gate["boxes"]],
        [ids[n] for n in gate["order"]],
        {r.id: r.dims for r in answer.rotations}, prefs)
    assert errors == [] and validate_plan(flat, items_p, boxes_p,
                                          settings) == []
    report = preference_report(flat, items_p, boxes_p, prefs)
    assert report and all(r["honoured"] == r["total"] for r in report)
    assert len(flat.boxes) <= len(baseline.boxes)
    assert _coverage_errors(flat, baseline, boxes_p) == []
    assert not flat.leftovers
    assert plan_score(flat, boxes_p)[1] <= 1.15 * plan_score(
        baseline, boxes_p)[1]


def test_sample_g_matches_the_plan_prototype():
    sample = ALL["G"]
    req = OrderInput.model_validate({k: sample[k] for k in (
        "items", "boxes", "instructions")})
    items, boxes = to_pipeline_inputs(req)
    items_p = prepare_items(items, boxes, PackingSettings())
    boxes_p = prepare_boxes(boxes, PackingSettings())
    baseline, _ = pack(items_p, boxes_p, PackingSettings())
    prefs = {1: {"side": "right", "level": "any"},
             3: {"side": "left", "level": "any"}}
    rows = {r["name"]: r for r in preference_report(
        baseline, items_p, boxes_p, prefs)}
    assert (rows["Kettle"]["honoured"], rows["Kettle"]["total"]) == (4, 5)
    assert (rows["Jar"]["honoured"], rows["Jar"]["total"]) == (2, 3)


# --- contract + shared fixture ------------------------------------------------------

def normalise(run: dict) -> dict:
    """Fixed run id, timestamps, event t and latencies: deterministic."""
    run = copy.deepcopy(run)
    run["run_id"] = "00000000-0000-4000-8000-000000000001"
    run["created_at"] = run["finished_at"] = "2026-01-01T00:00:00.000Z"
    for event in run["events"]:
        event["t"] = float(event["id"])
    for job in run["jobs"]:
        job["latency_s"] = 1.0
    run["totals"]["seconds"] = 4.0
    return run


def test_contract_and_shared_fixture(client):
    run = wait_v2(client, start_v2(client, "G"))
    assert run["status"] == "succeeded"
    model = RunV2.model_validate(run)
    assert model.model_dump(mode="json") == run
    fixture = normalise(run)
    RunV2.model_validate(fixture)
    if os.environ.get("UPDATE_FIXTURES") == "1":
        FIXTURE.parent.mkdir(exist_ok=True)
        FIXTURE.write_text(json.dumps(fixture, indent=2) + "\n")
    committed = json.loads(FIXTURE.read_text())
    RunV2.model_validate(committed)
    assert committed["run_id"] == "00000000-0000-4000-8000-000000000001"
    assert committed["fake"] is True and committed["status"] == "succeeded"
    assert len(committed["jobs"]) == 4 and committed["final"]["notes"]


FRONTEND_FIXTURE = (Path(__file__).resolve().parents[2]
                    / "frontend" / "src" / "test" / "run_v2.json")


def test_frontend_fixture_copy_is_in_sync():
    """The frontend's copy must equal the committed backend fixture (JSON compare).
    If this fails: cp tests/fixtures/run_v2.json ../frontend/src/test/run_v2.json"""
    assert json.loads(FRONTEND_FIXTURE.read_text()) == json.loads(FIXTURE.read_text())
