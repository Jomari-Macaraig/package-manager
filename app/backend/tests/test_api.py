import json
import threading
import time

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

import contract
from app import config, store
from app.fake_llm import FakeClient
from app.main import create_app
from conftest import sample_body, wait_terminal

JSON = {"content-type": "application/json"}
RID = "22222222-2222-4222-8222-222222222222"


def start_run(client, body=None, headers=None):
    r = client.post("/api/runs", json=body or sample_body("A"),
                    headers=headers)
    assert r.status_code == 202, r.text
    return r.json()["run_id"]


def parse_sse(text: str) -> list[dict]:
    frames = []
    for block in text.strip().split("\n\n"):
        if not block.strip() or block.startswith(":"):
            continue
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        frames.append({"id": int(fields["id"]), "event": fields["event"],
                       "data": json.loads(fields["data"])})
    return frames


@pytest.fixture
def gated(app):
    gate = threading.Event()
    app.state.runs.client_factory = lambda: FakeClient(gate=gate)
    yield gate
    gate.set()


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("condition not reached")


def test_health_config_samples(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    cfg = client.get("/api/config")
    contract.check(cfg.json(), contract.CONFIG)
    assert cfg.json()["defaults"]["extraction_model"] == "gpt-4.1-nano"
    samples = client.get("/api/samples").json()
    assert [s["key"] for s in samples] == ["A", "B", "C"]
    assert [(len(s["items"]), len(s["boxes"])) for s in samples] == [
        (5, 5), (6, 5), (4, 4)]
    for s in samples:
        contract.check(s, contract.SAMPLE)
        assert all("id" not in i for i in s["items"] + s["boxes"])
        assert all(b["stock"] is None for b in s["boxes"])


def test_run_sample_a_end_to_end_and_contract(client):
    run_id = start_run(client)
    run = wait_terminal(client, run_id)
    assert run["status"] == "succeeded" and run["error"] is None
    contract.check(run, contract.RUN)
    assert run["trace_id"] == f"trace_{run_id.replace('-', '')}"
    assert run["final"]["source"] in ("model", "packer")
    assert run["model"]["ok"] and run["packer"]["ok"]
    assert run["events"][0]["type"] == "started"
    assert run["events"][-1]["type"] == "end"
    assert run["events"][-1]["status"] == "succeeded"
    # replay over SSE: ids 0..n, ends with end
    r = client.get(f"/api/runs/{run_id}/events")
    assert r.headers["content-type"] == "text/event-stream"
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["x-accel-buffering"] == "no"
    frames = parse_sse(r.text)
    assert [f["id"] for f in frames] == list(range(len(frames)))
    assert frames[-1]["event"] == "end"
    assert frames[-1]["data"]["status"] == "succeeded"
    assert [f["data"] for f in frames] == run["events"]
    contract.check(client.get("/api/runs").json()[0], contract.RUN_SUMMARY)


@pytest.mark.parametrize("key", ["B", "C"])
def test_other_samples_validate_against_contract(client, key):
    run = wait_terminal(client, start_run(client, sample_body(key)))
    assert run["status"] == "succeeded"
    contract.check(run, contract.RUN)
    if key == "C":
        assert run["final"]["plan"]["boxes"] == []


def test_sse_resume_with_last_event_id_and_from(client):
    run_id = start_run(client)
    run = wait_terminal(client, run_id)
    r = client.get(f"/api/runs/{run_id}/events",
                   headers={"Last-Event-ID": "2"})
    frames = parse_sse(r.text)
    assert frames[0]["id"] == 3
    assert frames[-1]["event"] == "end"
    r = client.get(f"/api/runs/{run_id}/events?from=4")
    assert parse_sse(r.text)[0]["id"] == 4
    # header wins over ?from=
    r = client.get(f"/api/runs/{run_id}/events?from=4",
                   headers={"Last-Event-ID": "0"})
    assert parse_sse(r.text)[0]["id"] == 1
    # past the end: still the stored end
    r = client.get(f"/api/runs/{run_id}/events?from=999")
    frames = parse_sse(r.text)
    assert [f["event"] for f in frames] == ["end"]
    assert frames[0]["id"] == run["events"][-1]["id"]


def test_events_persisted_while_running(app, client, gated):
    run_id = start_run(client)

    def stored():
        return store.events(run_id)[2]

    events = wait_for(lambda: (e := stored()) and len(e) >= 4 and e)
    assert [e["type"] for e in events][:4] == [
        "started", "extracted", "prepared", "packer"]
    assert events[-1]["type"] != "end"
    row = store.get(run_id)
    assert row["status"] == "running"
    running = client.get(f"/api/runs/{run_id}").json()
    assert running["status"] == "running"
    assert running["final"] is None and running["stats"] is None
    contract.check(running, contract.RUN)
    assert client.get("/api/runs/active").json() == {"run_id": run_id}
    gated.set()
    done = wait_terminal(client, run_id)
    assert done["events"][-1]["type"] == "end"
    assert client.get("/api/runs/active").json() == {"run_id": None}


def test_second_run_conflicts_then_cancel_and_next_run(client, gated):
    run_id = start_run(client)
    r = client.post("/api/runs", json=sample_body("A"))
    assert r.status_code == 409 and "detail" in r.json()
    wait_for(lambda: len(store.events(run_id)[2]) >= 4)
    assert client.post(f"/api/runs/{run_id}/cancel", json={}).status_code \
        == 202
    gated.set()
    run = wait_terminal(client, run_id)
    assert run["status"] == "cancelled"
    assert run["stats"]["planning"]["prompt_tokens"] == 1000
    assert run["packer"]["ok"] is True and run["final"] is None
    assert run["events"][-1]["status"] == "cancelled"
    contract.check(run, contract.RUN)
    # slot released: the next run is accepted
    assert start_run(client)
    # cancelling a finished run: 409; unknown run: 404
    assert client.post(f"/api/runs/{run_id}/cancel", json={}).status_code \
        == 409
    assert client.post(f"/api/runs/{RID}/cancel", json={}).status_code == 404


def test_cancel_requires_json_content_type(client):
    r = client.post(f"/api/runs/{RID}/cancel", content="x",
                    headers={"content-type": "text/plain"})
    assert r.status_code == 415


def test_finish_after_mark_interrupted_does_not_overwrite(client):
    store.create(run_id=RID, trace_id="t", request_id="r", client_ip="ip",
                 user_agent="ua", title="x", extraction_model="m",
                 planning_model="p", request_json="{}")
    assert store.mark_interrupted() == 1
    ok = store.finish(RID, "succeeded", None, {}, {}, {}, [])
    assert ok is False
    row = store.get(RID)
    assert row["status"] == "interrupted"
    assert json.loads(row["events_json"])[-1]["status"] == "interrupted"
    assert store.set_status(RID, "failed", "x") is False


def test_startup_marks_running_rows_interrupted(app):
    store.init()
    store.create(run_id=RID, trace_id="t", request_id="r", client_ip="ip",
                 user_agent="ua", title="x", extraction_model="m",
                 planning_model="p", request_json="{}")
    store.append_event(RID, [{"id": 0, "type": "started", "t": 0.0},
                             {"id": 1, "type": "extracted", "t": 0.1,
                              "tokens": 0, "skipped": True}])
    with TestClient(app, base_url="http://localhost") as c:
        events = c.get(f"/api/runs/{RID}/events")
        row = store.get(RID)
    assert row["status"] == "interrupted" and row["finished_at"]
    stored = json.loads(row["events_json"])
    assert stored[-1] == {**stored[-1], "id": 2, "type": "end",
                          "status": "interrupted"}
    assert parse_sse(events.text)[-1]["data"]["status"] == "interrupted"


@pytest.mark.parametrize("mutate,needle", [
    (lambda b: b.update(items=b["items"] * 5), "List should have at most"),
    (lambda b: b["items"][0].update(quantity=151), "150"),
    (lambda b: b.update(instructions="x" * 2001), "2000"),
    (lambda b: b["items"][1].update(name=b["items"][0]["name"].upper()),
     "Value error, duplicate item name"),
    (lambda b: b["items"][0].update(name="bad\x00name"),
     "Value error, control characters"),
    (lambda b: b["items"][0].update(color="c" * 33), "32"),
    (lambda b: b["extraction"].update(model="gpt-9"),
     "Value error, unknown extraction model"),
    (lambda b: b["planning"].update(model="gpt-9"),
     "Value error, unknown planning model"),
    (lambda b: b["items"][0].update(x=0), "greater than"),
    (lambda b: b["items"][0].update(weight=-1), "greater than or equal"),
    (lambda b: b["boxes"][0].update(stock=1001), "less than or equal"),
    (lambda b: b.update(boxes=[]), "at least 1"),
    (lambda b: b.update(instructions="a\x07b"),
     "Value error, control characters"),
])
def test_validation_limits(client, mutate, needle):
    body = sample_body("A")
    body = json.loads(json.dumps(body))
    mutate(body)
    r = client.post("/api/runs", json=body)
    assert r.status_code == 422, r.text
    assert needle in json.dumps(r.json()["detail"])
    assert all({"loc", "msg", "type"} <= set(e) for e in r.json()["detail"])


def test_total_units_cap(client):
    body = json.loads(json.dumps(sample_body("A")))
    body["items"] = [{**body["items"][0], "name": "a", "quantity": 100},
                     {**body["items"][0], "name": "b", "quantity": 51}]
    r = client.post("/api/runs", json=body)
    assert r.status_code == 422
    assert "exceeds the cap" in r.text


def test_instructions_allow_newline_and_tab(client):
    body = sample_body("A", instructions="line one\n\tline two")
    assert client.post("/api/runs", json=body).status_code == 202


def test_non_json_content_type_415_and_charset_accepted(client):
    r = client.post("/api/runs", content=json.dumps(sample_body("A")),
                    headers={"content-type": "text/plain"})
    assert r.status_code == 415 and "detail" in r.json()
    r = client.post("/api/runs", content=json.dumps(sample_body("A")),
                    headers={"content-type":
                             "application/json; charset=utf-8"})
    assert r.status_code == 202


def test_body_too_large_413(client):
    r = client.post("/api/runs", content=b"x" * (300 * 1024), headers=JSON)
    assert r.status_code == 413 and "detail" in r.json()
    assert "x-request-id" in r.headers


def test_foreign_origin_403_allowed_origin_ok(client):
    r = client.post("/api/runs", json=sample_body("A"),
                    headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and "detail" in r.json()
    r = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_bad_host_400_with_request_id(client):
    r = client.get("/api/health", headers={"Host": "evil.example"})
    assert r.status_code == 400
    assert "x-request-id" in r.headers


def test_invalid_run_id_404_and_plan_literal_422(client):
    assert client.get("/api/runs/not-a-uuid").status_code == 404
    assert client.get("/api/runs/not-a-uuid/history").status_code == 404
    assert client.get(f"/api/runs/{RID}").status_code == 404
    assert client.get(f"/api/runs/{RID}/events").status_code == 404
    assert client.get(f"/api/runs/{RID}/history").status_code == 404
    run_id = start_run(client)
    wait_terminal(client, run_id)
    assert client.get(f"/api/runs/{run_id}/render?plan=foo&box=1"
                      ).status_code == 422
    assert client.get(f"/api/runs/{run_id}/svg?plan=foo").status_code == 422
    assert client.get(f"/api/runs/{run_id}/render?plan=model&box=1&upto=0"
                      ).status_code == 422


def test_infinity_and_nan_rejected(client):
    text = json.dumps(sample_body("A"))
    r = client.post("/api/runs", headers=JSON,
                    content=text.replace('"weight": 0.9', '"weight": Infinity'))
    assert r.status_code == 422
    r = client.post("/api/runs", headers=JSON,
                    content=text.replace('"weight": 0.9', '"weight": NaN'))
    assert r.status_code == 422


def test_render_and_svg_endpoints(client):
    run_id = start_run(client, sample_body("B"))
    wait_terminal(client, run_id)
    r = client.get(f"/api/runs/{run_id}/render?plan=model&box=1&upto=2")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/svg+xml"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == (
        "default-src 'none'; style-src 'unsafe-inline'")
    assert r.headers["cache-control"] == "max-age=3600"
    assert r.text.startswith("<svg")
    full = client.get(f"/api/runs/{run_id}/render?plan=packer&box=2&upto=999")
    assert full.status_code == 200  # upto is clamped
    assert client.get(f"/api/runs/{run_id}/render?plan=model&box=99"
                      ).status_code == 404
    assert client.get(f"/api/runs/{run_id}/render?plan=model&box=0"
                      ).status_code == 404
    assert client.get(f"/api/runs/{run_id}/render?plan=model").status_code \
        == 422
    d = client.get(f"/api/runs/{run_id}/svg?plan=packer")
    assert d.status_code == 200
    assert d.headers["content-disposition"] == (
        f'attachment; filename="run-{run_id}-packer.svg"')
    assert d.headers["x-content-type-options"] == "nosniff"
    assert d.text.startswith("<svg")


def test_render_while_running_409_and_missing_side_404(client, gated):
    run_id = start_run(client)
    wait_for(lambda: len(store.events(run_id)[2]) >= 4)
    assert client.get(f"/api/runs/{run_id}/render?plan=model&box=1"
                      ).status_code == 409
    assert client.get(f"/api/runs/{run_id}/svg?plan=model").status_code == 404
    gated.set()
    wait_terminal(client, run_id)


def test_svg_escapes_item_names(client):
    body = json.loads(json.dumps(sample_body("A")))
    body["items"][0]["name"] = "<script>\"'&"
    run_id = start_run(client, body)
    run = wait_terminal(client, run_id)
    assert run["status"] == "succeeded"
    for url in (f"/api/runs/{run_id}/svg?plan=model",
                f"/api/runs/{run_id}/render?plan=model&box=1"):
        text = client.get(url).text
        assert "<script>" not in text
        assert "&lt;script&gt;" in text


def test_request_id_ip_and_user_agent(client):
    r = client.get("/api/health")
    assert r.headers["x-request-id"]
    assert client.get("/api/health", headers={"X-Request-ID": RID}
                      ).headers["x-request-id"] == RID
    assert client.get("/api/health", headers={"X-Request-ID": "junk"}
                      ).headers["x-request-id"] != "junk"
    run_id = start_run(client, headers={"X-Request-ID": RID,
                                        "User-Agent": "u" * 300})
    run = wait_terminal(client, run_id)
    assert run["request_id"] == RID
    assert run["client_ip"] == "testclient"
    assert run["user_agent"] == "u" * 200
    row = store.get(run_id)
    assert (row["request_id"], row["client_ip"]) == (RID, "testclient")


def test_missing_api_key_fails_run(app, client, monkeypatch):
    monkeypatch.setattr(config, "FAKE_LLM", False)
    run = wait_terminal(client, start_run(client))
    assert run["status"] == "failed"
    assert run["error"] == "OPENAI_API_KEY is not set"
    assert run["events"][-1]["status"] == "failed"
    assert run["final"] is None


def test_openai_auth_error_fails_run(app, client):
    request = httpx.Request("POST", "http://x")
    err = openai.AuthenticationError(
        "sk-secret-key rejected",
        response=httpx.Response(401, request=request), body=None)
    app.state.runs.client_factory = lambda: FakeClient(script=[err])
    run = wait_terminal(client, start_run(client))
    assert run["status"] == "failed"
    assert run["error"] == ("OpenAI authentication failed — "
                            "check OPENAI_API_KEY")
    assert "sk-secret" not in json.dumps(run)
    assert run["packer"]["ok"] is True  # partial result kept
    contract.check(run, contract.RUN)


def test_internal_error_is_generic(app, client):
    class Boom(FakeClient):
        def _parse(self, **kwargs):
            raise RuntimeError("secret internals")

        def __init__(self):
            super().__init__()
            self.chat.completions.parse = self._parse

    app.state.runs.client_factory = Boom
    run = wait_terminal(client, start_run(client))
    assert run["status"] == "failed" and run["error"] == "Internal error"
    assert "secret" not in json.dumps(run)


def test_history_endpoint(client):
    run_id = start_run(client, sample_body("A", instructions="pad 0.1"))
    wait_terminal(client, run_id)
    h = client.get(f"/api/runs/{run_id}/history").json()
    assert set(h) == {"extraction", "planning"}
    assert h["extraction"][0]["role"] == "system"
    assert h["planning"][0]["role"] == "system"
    assert h["planning"][-1]["role"] == "assistant"


def test_run_list_newest_first_and_summary(client):
    first = start_run(client)
    wait_terminal(client, first)
    second = start_run(client, sample_body("C"))
    wait_terminal(client, second)
    rows = client.get("/api/runs").json()
    assert [r["run_id"] for r in rows][:2] == [second, first]
    assert rows[1]["title"] == "Mug, Dinner Plate, Candle Jar"
    assert rows[0]["source"] in ("model", "packer")
    assert rows[0]["total_cost"] > 0
