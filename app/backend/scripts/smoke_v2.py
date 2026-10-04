"""End-to-end smoke test with the fake LLM (never calls OpenAI, never touches the
real runs.db). Run from app/backend:  uv run python scripts/smoke_v2.py

Posts samples A-H to v2 and A-E to v1, waits for each, and checks the run, the
four job rows, the SVG renders, feedback and the evaluation summary.
"""
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

# Before any app import: app.config reads these at import time.
_TMP = tempfile.TemporaryDirectory()
os.environ["PLAYGROUND_FAKE_LLM"] = "1"
os.environ["PLAYGROUND_DB"] = str(Path(_TMP.name) / "smoke.db")
os.environ["OPENAI_AGENTS_DISABLE_TRACING"] = "1"
os.environ.pop("OPENAI_API_KEY", None)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import app  # noqa: E402

TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def wait(client: TestClient, path: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = client.get(path).json()
        if run["status"] in TERMINAL:
            return run
        time.sleep(0.05)
    raise AssertionError(f"{path} did not finish")


def get_svg(client: TestClient, url: str) -> None:
    r = client.get(url)
    check(r.status_code == 200, f"{url}: {r.status_code} {r.text[:200]}")
    check("image/svg+xml" in r.headers["content-type"], f"{url}: not SVG")
    check("<svg" in r.text, f"{url}: no <svg> in body")


def smoke_v2(client: TestClient) -> list[str]:
    samples = client.get("/api/v2/samples").json()
    check([s["key"] for s in samples] == list("ABCDEFGH"), "v2 samples are A-H")
    ids = []
    for s in samples:
        body = {"items": s["items"], "boxes": s["boxes"],
                "instructions": s["instructions"]}
        r = client.post("/api/v2/runs", json=body)
        check(r.status_code == 202, f"v2 {s['key']}: POST {r.status_code} {r.text}")
        run_id = r.json()["run_id"]
        run = wait(client, f"/api/v2/runs/{run_id}")
        check(run["status"] == "succeeded", f"v2 {s['key']}: {run['status']} {run['error']}")
        check(run["fake"] is True, f"v2 {s['key']}: run.fake")
        check(len(run["jobs"]) == 4, f"v2 {s['key']}: {len(run['jobs'])} job rows")
        check([j["job"] for j in run["jobs"]] ==
              ["extract", "strategy", "check", "notes"], f"v2 {s['key']}: job order")
        final = run["final"]
        if final is not None:
            for n in range(1, len(final["plan"]["boxes"]) + 1):
                get_svg(client, f"/api/v2/runs/{run_id}/render?plan=final&box={n}")
            get_svg(client, f"/api/v2/runs/{run_id}/svg?plan=final")
        ids.append(run_id)
        print(f"v2 {s['key']}: {run['status']} source={run['source']} "
              f"boxes={len(final['plan']['boxes']) if final else 0} "
              f"jobs={[j['status'] for j in run['jobs']]}")

    # four job rows per run, all marked fake, in storage
    with sqlite3.connect(config.DB_PATH) as conn:
        for run_id in ids:
            rows = conn.execute(
                "SELECT fake FROM job_calls WHERE run_id=?", (run_id,)).fetchall()
            check(len(rows) == 4 and all(r[0] == 1 for r in rows),
                  f"{run_id}: job_calls rows {rows}")

    # feedback PUT, then it shows in GET /runs/{id} jobs[].feedback
    run_id = ids[samples.index(next(s for s in samples if s["key"] == "G"))]
    r = client.put(f"/api/v2/runs/{run_id}/feedback/strategy",
                   json={"rating": "up", "comment": "smoke"})
    check(r.status_code == 200, f"feedback PUT: {r.status_code} {r.text}")
    run = client.get(f"/api/v2/runs/{run_id}").json()
    fb = next(j for j in run["jobs"] if j["job"] == "strategy")["feedback"]
    check(fb is not None and fb["rating"] == "up" and fb["comment"] == "smoke",
          f"feedback not persisted: {fb}")
    print("v2 feedback: PUT 200 and visible in jobs[].feedback")

    # evaluation: fake runs are hidden unless include_fake
    hidden = client.get("/api/v2/eval/summary").json()["groups"]
    shown = client.get("/api/v2/eval/summary?include_fake=true").json()["groups"]
    check(hidden == [], f"eval without include_fake: {len(hidden)} groups")
    check(len({g["job"] for g in shown}) == 4,
          f"eval with include_fake: jobs {sorted({g['job'] for g in shown})}")
    print(f"v2 eval: {len(hidden)} groups hidden, {len(shown)} groups "
          f"over {len({g['job'] for g in shown})} jobs with include_fake=true")
    return ids


def smoke_v1(client: TestClient) -> None:
    samples = client.get("/api/samples").json()
    check([s["key"] for s in samples] == list("ABCDE"), "v1 samples are A-E")
    for s in samples:
        body = {"items": s["items"], "boxes": s["boxes"],
                "instructions": s["instructions"],
                "extraction": {"model": "gpt-4.1-nano"},
                "planning": {"model": "gpt-5.4-nano", "reasoning_effort": "low"}}
        r = client.post("/api/runs", json=body)
        check(r.status_code == 202, f"v1 {s['key']}: POST {r.status_code} {r.text}")
        run_id = r.json()["run_id"]
        run = wait(client, f"/api/runs/{run_id}")
        check(run["status"] == "succeeded", f"v1 {s['key']}: {run['status']} {run['error']}")
        rendered = 0
        for plan in ("model", "packer"):
            side = run.get(plan)
            if side and side.get("plan"):
                for n in range(1, len(side["plan"]["boxes"]) + 1):
                    get_svg(client, f"/api/runs/{run_id}/render?plan={plan}&box={n}")
                    rendered += 1
        print(f"v1 {s['key']}: {run['status']} rendered_boxes={rendered}")


def main() -> int:
    with TestClient(app, base_url="http://localhost") as client:
        smoke_v2(client)
        smoke_v1(client)
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        _TMP.cleanup()
