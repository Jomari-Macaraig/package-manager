"""SQLite storage for runs (stdlib sqlite3, a connection per call)."""

import builtins
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, trace_id TEXT, request_id TEXT,
  status TEXT NOT NULL, error TEXT,
  created_at TEXT NOT NULL, finished_at TEXT,
  client_ip TEXT, user_agent TEXT,
  title TEXT, extraction_model TEXT, planning_model TEXT,
  source TEXT, total_cost REAL,
  request_json TEXT NOT NULL, result_json TEXT,
  svgs_json TEXT, history_json TEXT, events_json TEXT
);
"""

SUMMARY_COLUMNS = ("run_id, created_at, finished_at, status, title, "
                   "extraction_model, planning_model, source, total_cost, "
                   "client_ip")


def now_iso() -> str:
    return (datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect()) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        with conn:
            conn.execute(SCHEMA)


def create(*, run_id: str, trace_id: str, request_id: str, client_ip: str,
           user_agent: str, title: str, extraction_model: str,
           planning_model: str, request_json: str,
           created_at: str | None = None) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO runs (run_id, trace_id, request_id, status, "
            "created_at, client_ip, user_agent, title, extraction_model, "
            "planning_model, request_json, events_json) "
            "VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, '[]')",
            (run_id, trace_id, request_id, created_at or now_iso(),
             client_ip, user_agent, title, extraction_model, planning_model,
             request_json))


def append_event(run_id: str, events: list[dict[str, Any]]) -> None:
    """Persist the full event list (only while the row is running)."""
    with closing(_connect()) as conn, conn:
        conn.execute(
            "UPDATE runs SET events_json = ? "
            "WHERE run_id = ? AND status = 'running'",
            (json.dumps(events), run_id))


def finish(run_id: str, status: str, error: str | None,
           result: dict[str, Any] | None, svgs: dict[str, Any] | None,
           history: dict[str, Any] | None,
           events: list[dict[str, Any]]) -> bool:
    """One guarded transaction. False if the row was no longer running."""
    final = (result or {}).get("final")
    stats = (result or {}).get("stats")
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE runs SET status = ?, error = ?, finished_at = ?, "
            "source = ?, total_cost = ?, result_json = ?, svgs_json = ?, "
            "history_json = ?, events_json = ? "
            "WHERE run_id = ? AND status = 'running'",
            (status, error, now_iso(),
             final["source"] if final else None,
             stats["total_cost"] if stats else None,
             json.dumps(result) if result is not None else None,
             json.dumps(svgs) if svgs is not None else None,
             json.dumps(history) if history is not None else None,
             json.dumps(events), run_id))
        return cur.rowcount == 1


def set_status(run_id: str, status: str, error: str | None) -> bool:
    """Minimal fallback when finish() cannot write (same guard)."""
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "UPDATE runs SET status = ?, error = ?, finished_at = ? "
            "WHERE run_id = ? AND status = 'running'",
            (status, error, now_iso(), run_id))
        return cur.rowcount == 1


def get(run_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?",
                           (run_id,)).fetchone()
    return dict(row) if row else None


def list(limit: int = 100) -> builtins.list[dict[str, Any]]:  # noqa: A001
    with closing(_connect()) as conn:
        rows = conn.execute(
            f"SELECT {SUMMARY_COLUMNS} FROM runs "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def events(run_id: str) -> tuple[str, str | None, builtins.list[dict[str, Any]]] | None:
    """(status, error, stored events) in one read, or None if unknown."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT status, error, events_json FROM runs WHERE run_id = ?",
            (run_id,)).fetchone()
    if row is None:
        return None
    return row["status"], row["error"], json.loads(row["events_json"] or "[]")


def history(run_id: str) -> dict[str, Any] | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT history_json FROM runs WHERE run_id = ?",
                           (run_id,)).fetchone()
    if row is None:
        return None
    return json.loads(row["history_json"]) if row["history_json"] else {
        "extraction": [], "planning": []}


def svg(run_id: str, which: str) -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT svgs_json FROM runs WHERE run_id = ?",
                           (run_id,)).fetchone()
    if row is None or not row["svgs_json"]:
        return None
    return json.loads(row["svgs_json"]).get(which)


def mark_interrupted() -> int:
    """Running rows -> interrupted, with an `end` event appended."""
    count = 0
    with closing(_connect()) as conn, conn:
        rows = conn.execute(
            "SELECT run_id, created_at, events_json FROM runs "
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
                "UPDATE runs SET status = 'interrupted', error = ?, "
                "finished_at = ?, events_json = ? "
                "WHERE run_id = ? AND status = 'running'",
                (message, now_iso(), json.dumps(events), row["run_id"]))
            count += cur.rowcount
    return count
