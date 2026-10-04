"""SQLite storage for v2 runs, per-job call rows (the evaluation data) and
feedback. Same file as v1 (config.DB_PATH, read at call time); stdlib
sqlite3, a connection per call.

Runs that never get job rows (documented): interrupted runs, and runs whose
`finish` failed twice and fell back to `set_status`. They are invisible to
evaluation.
"""

import builtins
import json
import math
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from . import config
from .store import now_iso

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs_v2 (
  run_id TEXT PRIMARY KEY, trace_id TEXT, request_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('running','succeeded','failed','cancelled','interrupted')),
  error TEXT, created_at TEXT NOT NULL, finished_at TEXT,
  client_ip TEXT, user_agent TEXT, title TEXT,
  fake INTEGER NOT NULL DEFAULT 0,
  source TEXT, reason TEXT,
  total_cost REAL NOT NULL DEFAULT 0, total_s REAL NOT NULL DEFAULT 0,
  over_budget INTEGER NOT NULL DEFAULT 0,
  request_json TEXT NOT NULL, result_json TEXT, svgs_json TEXT,
  events_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS runs_v2_created ON runs_v2 (created_at);
CREATE TABLE IF NOT EXISTS job_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  job TEXT NOT NULL CHECK (job IN ('extract','strategy','check','notes')),
  status TEXT NOT NULL CHECK (status IN ('ok','fallback','skipped')),
  fake INTEGER NOT NULL DEFAULT 0,
  model TEXT, reasoning_effort TEXT, prompt_version TEXT NOT NULL,
  calls INTEGER NOT NULL DEFAULT 0,
  checks_json TEXT NOT NULL DEFAULT '[]',
  checks_passed INTEGER NOT NULL DEFAULT 0, checks_total INTEGER NOT NULL DEFAULT 0,
  prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
  reasoning_tokens INTEGER NOT NULL DEFAULT 0,
  cost REAL NOT NULL DEFAULT 0, latency_s REAL NOT NULL DEFAULT 0,
  error TEXT, answer_json TEXT, output_json TEXT, messages_json TEXT,
  created_at TEXT NOT NULL   -- the run's finished_at, so `since` filters match runs
);
CREATE UNIQUE INDEX IF NOT EXISTS job_calls_run_job ON job_calls (run_id, job);
CREATE INDEX IF NOT EXISTS job_calls_group ON job_calls (job, prompt_version, model, created_at);
CREATE TABLE IF NOT EXISTS feedback (
  run_id TEXT NOT NULL,
  job TEXT NOT NULL CHECK (job IN ('extract','strategy','check','notes')),
  rating TEXT CHECK (rating IN ('up','down')),
  comment TEXT NOT NULL DEFAULT '',
  wrong_verdicts_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, job)
);
"""

JOB_ORDER = ("extract", "strategy", "check", "notes")

SUMMARY_COLUMNS = ("run_id, created_at, finished_at, status, title, source, "
                   "reason, total_cost, total_s, over_budget, fake")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        with conn:
            conn.executescript(SCHEMA)


def _dumps(value: Any) -> str | None:
    return json.dumps(value) if value is not None else None


def create_run(run_id: str, request_json: str, *, trace_id: str,
               request_id: str, client_ip: str, user_agent: str, title: str,
               fake: bool, created_at: str | None = None) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO runs_v2 (run_id, trace_id, request_id, status, "
            "created_at, client_ip, user_agent, title, fake, request_json, "
            "events_json) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, '[]')",
            (run_id, trace_id, request_id, created_at or now_iso(),
             client_ip, user_agent, title, int(fake), request_json))


def set_fake(run_id: str, fake: bool) -> None:
    """The client is only known once the worker starts."""
    with closing(_connect()) as conn, conn:
        conn.execute("UPDATE runs_v2 SET fake = ? WHERE run_id = ? "
                     "AND status = 'running'", (int(fake), run_id))


def append_event(run_id: str, events: list[dict[str, Any]]) -> None:
    """Persist the full event list (only while the row is running)."""
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE runs_v2 SET events_json = ? "
            "WHERE run_id = ? AND status = 'running'",
            (json.dumps(events), run_id))


def finish(run_id: str, status: str, error: str | None,
           result: dict[str, Any] | None, svgs: dict[str, Any] | None,
           events: list[dict[str, Any]],
           jobs: list[dict[str, Any]] | None) -> bool:
    """One guarded transaction: the run row, then (only if that row was
    still running) the job rows, idempotently. False if the row was no
    longer running."""
    ts = now_iso()
    result = result or {}
    totals = result.get("totals") or {}
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE runs_v2 SET status = ?, error = ?, finished_at = ?, "
            "source = ?, reason = ?, total_cost = ?, total_s = ?, "
            "over_budget = ?, result_json = ?, svgs_json = ?, "
            "events_json = ? WHERE run_id = ? AND status = 'running'",
            (status, error, ts, result.get("source"), result.get("reason"),
             totals.get("cost", 0.0), totals.get("seconds", 0.0),
             int(bool(totals.get("over_budget"))),
             _dumps(result) if result else None, _dumps(svgs),
             json.dumps(events), run_id))
        wrote = cur.rowcount == 1
        if wrote and jobs:
            fake = conn.execute("SELECT fake FROM runs_v2 WHERE run_id = ?",
                                (run_id,)).fetchone()["fake"]
            for job in jobs:
                checks = job["checks"]
                conn.execute(
                    "INSERT INTO job_calls (run_id, job, status, fake, "
                    "model, reasoning_effort, prompt_version, calls, "
                    "checks_json, checks_passed, checks_total, "
                    "prompt_tokens, completion_tokens, reasoning_tokens, "
                    "cost, latency_s, error, answer_json, output_json, "
                    "messages_json, created_at) VALUES (?, ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(run_id, job) DO NOTHING",
                    (run_id, job["job"], job["status"], fake, job["model"],
                     job["reasoning_effort"], job["prompt_version"],
                     job["calls"], json.dumps(checks),
                     sum(1 for c in checks if c["passed"]), len(checks),
                     job["prompt_tokens"], job["completion_tokens"],
                     job["reasoning_tokens"], job["cost"], job["latency_s"],
                     job["error"], _dumps(job["answer"]),
                     _dumps(job["output"]), _dumps(job["messages"]), ts))
    return wrote


def set_status(run_id: str, status: str, error: str | None) -> bool:
    """Minimal fallback when finish() cannot write (same guard)."""
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE runs_v2 SET status = ?, error = ?, finished_at = ? "
            "WHERE run_id = ? AND status = 'running'",
            (status, error, now_iso(), run_id))
        return cur.rowcount == 1


def get(run_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM runs_v2 WHERE run_id = ?",
                           (run_id,)).fetchone()
    return dict(row) if row else None


def list(limit: int = 100) -> builtins.list[dict[str, Any]]:  # noqa: A001
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM runs_v2 "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def events(run_id: str) -> tuple[str, str | None, builtins.list[dict[str, Any]]] | None:
    """(status, error, stored events) in one read, or None if unknown."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT status, error, events_json FROM runs_v2 WHERE run_id = ?",
            (run_id,)).fetchone()
    if row is None:
        return None
    return row["status"], row["error"], json.loads(row["events_json"] or "[]")


def svg(run_id: str, which: str) -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT svgs_json FROM runs_v2 WHERE run_id = ?",
                           (run_id,)).fetchone()
    if row is None or not row["svgs_json"]:
        return None
    return json.loads(row["svgs_json"]).get(which)


def mark_interrupted() -> int:
    """Running rows -> interrupted, with an `end` event appended. No job
    rows are written for them."""
    count = 0
    with closing(_connect()) as conn, conn:
        rows = conn.execute(
            "SELECT run_id, created_at, events_json FROM runs_v2 "
            "WHERE status = 'running'").fetchall()
        for row in rows:
            events = json.loads(row["events_json"] or "[]")
            try:
                started = datetime.fromisoformat(row["created_at"])
                t = round((datetime.now(timezone.utc) - started)
                          .total_seconds(), 3)
            except ValueError:
                t = 0.0
            message = "Interrupted by a server restart"
            events.append({"id": len(events), "type": "end", "t": t,
                           "status": "interrupted", "error": message})
            cur = conn.execute(
                "UPDATE runs_v2 SET status = 'interrupted', error = ?, "
                "finished_at = ?, events_json = ? "
                "WHERE run_id = ? AND status = 'running'",
                (message, now_iso(), json.dumps(events), row["run_id"]))
            count += cur.rowcount
    return count


# --- jobs and feedback -------------------------------------------------------

def _feedback(row: sqlite3.Row) -> dict[str, Any] | None:
    if row["fb_updated_at"] is None:
        return None
    return {"rating": row["fb_rating"], "comment": row["fb_comment"],
            "wrong_verdicts": json.loads(row["fb_wrong"]),
            "updated_at": row["fb_updated_at"]}


def job_views(run_id: str) -> builtins.list[dict[str, Any]]:
    """The JobView dicts of a run in pipeline order ([] when it has no job
    rows), each with its feedback."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT j.*, f.rating AS fb_rating, f.comment AS fb_comment, "
            "f.wrong_verdicts_json AS fb_wrong, f.updated_at AS fb_updated_at "
            "FROM job_calls j LEFT JOIN feedback f "
            "ON f.run_id = j.run_id AND f.job = j.job "
            "WHERE j.run_id = ?", (run_id,)).fetchall()
    views = [{
        "job": r["job"], "status": r["status"], "model": r["model"],
        "reasoning_effort": r["reasoning_effort"],
        "prompt_version": r["prompt_version"], "calls": r["calls"],
        "checks": json.loads(r["checks_json"]),
        "output": json.loads(r["output_json"]) if r["output_json"] else None,
        "answer": json.loads(r["answer_json"]) if r["answer_json"] else None,
        "error": r["error"], "cost": r["cost"], "latency_s": r["latency_s"],
        "tokens": {"prompt": r["prompt_tokens"],
                   "completion": r["completion_tokens"],
                   "reasoning": r["reasoning_tokens"]},
        "feedback": _feedback(r)} for r in rows]
    return sorted(views, key=lambda v: JOB_ORDER.index(v["job"]))


def job_messages(run_id: str, job: str) -> builtins.list[dict[str, Any]] | None:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT messages_json FROM job_calls WHERE run_id = ? AND job = ?",
            (run_id, job)).fetchone()
    if row is None:
        return None
    return json.loads(row["messages_json"]) if row["messages_json"] else []


def job_row(run_id: str, job: str) -> dict[str, Any] | None:
    """status and output of one job row (feedback rules need them)."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT status, output_json FROM job_calls "
            "WHERE run_id = ? AND job = ?", (run_id, job)).fetchone()
    if row is None:
        return None
    return {"status": row["status"], "output": json.loads(row["output_json"])
            if row["output_json"] else None}


def upsert_feedback(run_id: str, job: str, rating: str | None, comment: str,
                    wrong_verdicts: builtins.list[int]) -> dict[str, Any] | None:
    """Insert or update the feedback of a job row. None when the job row
    does not exist. created_at is kept on update."""
    ts = now_iso()
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO feedback (run_id, job, rating, comment, "
            "wrong_verdicts_json, created_at, updated_at) "
            "SELECT ?, ?, ?, ?, ?, ?, ? WHERE EXISTS "
            "(SELECT 1 FROM job_calls WHERE run_id = ? AND job = ?) "
            "ON CONFLICT(run_id, job) DO UPDATE SET rating = excluded.rating, "
            "comment = excluded.comment, "
            "wrong_verdicts_json = excluded.wrong_verdicts_json, "
            "updated_at = excluded.updated_at",
            (run_id, job, rating, comment, json.dumps(wrong_verdicts), ts, ts,
             run_id, job))
        if cur.rowcount != 1:
            return None
    return {"rating": rating, "comment": comment,
            "wrong_verdicts": wrong_verdicts, "updated_at": ts}


# --- evaluation --------------------------------------------------------------

def normalise_since(since: str) -> str:
    """ISO Z to millisecond precision ("…:00Z" -> "…:00.000Z") so rows in
    the first second of `since` are not dropped by the lexical compare."""
    if "." not in since:
        return since[:-1] + ".000Z"
    head, frac = since[:-1].split(".")
    return f"{head}.{frac.ljust(3, '0')[:3]}Z"


def _percentile(values: builtins.list[float], p: float) -> float | None:
    """Nearest-rank."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(math.ceil(p / 100 * len(ordered)), 1) - 1]


def eval_rows(job: str | None = None, since: str | None = None,
              include_fake: bool = False) -> builtins.list[dict[str, Any]]:
    """EvalGroup dicts: one per (job, prompt_version, model, effort)."""
    where, params = [], []
    if job:
        where.append("j.job = ?")
        params.append(job)
    if since:
        where.append("j.created_at >= ?")
        params.append(normalise_since(since))
    if not include_fake:
        where.append("j.fake = 0")
    sql = (
        "SELECT j.job, j.status, j.model, j.reasoning_effort, "
        "j.prompt_version, j.checks_json, j.cost, j.latency_s, "
        "j.output_json, j.created_at, f.run_id AS fb_run, "
        "f.rating AS fb_rating, f.comment AS fb_comment, "
        "f.wrong_verdicts_json AS fb_wrong, r.status AS run_status, "
        "r.source AS run_source FROM job_calls j "
        "LEFT JOIN feedback f ON f.run_id = j.run_id AND f.job = j.job "
        "LEFT JOIN runs_v2 r ON r.run_id = j.run_id"
        + (" WHERE " + " AND ".join(where) if where else ""))
    with closing(_connect()) as conn:
        rows = conn.execute(sql, params).fetchall()

    groups: dict[tuple, builtins.list[sqlite3.Row]] = {}
    for row in rows:
        key = (row["job"], row["prompt_version"], row["model"],
               row["reasoning_effort"])
        groups.setdefault(key, []).append(row)

    def rate(count: int, total: int) -> float | None:
        return count / total if total else None

    out = []
    for (name, version, model, effort), members in groups.items():
        ran = [r for r in members if r["status"] != "skipped"]
        checks = [json.loads(r["checks_json"]) for r in ran]
        per_check: dict[str, builtins.list[bool]] = {}
        for call_checks in checks:
            for c in call_checks:
                per_check.setdefault(c["name"], []).append(c["passed"])
        rated = [r for r in ran if r["fb_rating"] is not None]
        wrong = verdicts = 0
        if name == "check":
            for r in ran:
                reviewed = r["fb_run"] is not None and (
                    r["fb_rating"] is not None or r["fb_comment"]
                    or json.loads(r["fb_wrong"]))
                if r["status"] == "ok" and reviewed:
                    wrong += len(json.loads(r["fb_wrong"]))
                    verdicts += len(json.loads(r["output_json"] or "[]"))
        won = decided = 0
        if name == "strategy":
            for r in ran:
                if r["run_status"] == "succeeded":
                    decided += 1
                    won += r["run_source"] == "model"
        latencies = [r["latency_s"] for r in ran]
        out.append({
            "job": name, "prompt_version": version, "model": model,
            "reasoning_effort": effort, "calls": len(ran),
            "skipped": len(members) - len(ran),
            "ok_rate": rate(sum(r["status"] == "ok" for r in ran), len(ran)),
            "fallback_rate": rate(
                sum(r["status"] == "fallback" for r in ran), len(ran)),
            "all_checks_rate": rate(
                sum(all(c["passed"] for c in cs) for cs in checks), len(ran)),
            "per_check": {n: sum(v) / len(v) for n, v in per_check.items()},
            "thumbs_up": sum(r["fb_rating"] == "up" for r in rated),
            "thumbs_down": sum(r["fb_rating"] == "down" for r in rated),
            "wrong_verdict_rate": rate(wrong, verdicts)
            if name == "check" else None,
            "model_won_rate": rate(won, decided)
            if name == "strategy" else None,
            "avg_cost": (sum(r["cost"] for r in ran) / len(ran)
                         if ran else None),
            "p50_latency_s": _percentile(latencies, 50),
            "p95_latency_s": _percentile(latencies, 95),
            "first_seen": min(r["created_at"] for r in members),
            "last_seen": max(r["created_at"] for r in members)})
    return sorted(out, key=lambda g: (
        JOB_ORDER.index(g["job"]), g["prompt_version"], g["model"] or "",
        g["reasoning_effort"] or ""))
