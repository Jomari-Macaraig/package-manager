"""Server-sent events for a run.

`event_stream` is an async generator so tests can drive it directly; the
route only wraps it in a StreamingResponse. Rules (see the plan's SSE
section): terminal runs always end with an `end` frame; live runs read the
in-memory list of the active run; events carry ids so clients can resume.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from .runs import RunManager

log = logging.getLogger(__name__)

ROW_CHECK_S = 1.0  # how often a live stream re-reads the row status


def frame(event: dict[str, Any]) -> str:
    return (f"id: {event['id']}\nevent: {event['type']}\n"
            f"data: {json.dumps(event)}\n\n")


def _synthetic_end(events: list[dict], status: str, error: str | None,
                   ) -> dict[str, Any]:
    return {"id": len(events), "type": "end",
            "t": events[-1]["t"] if events else 0.0,
            "status": status, "error": error}


def _stored_remainder(events: list[dict], start: int, status: str,
                      error: str | None) -> list[dict]:
    """Stored events from `start`; the stored `end` is always included, even
    when `start` is past it."""
    out = [e for e in events if e["id"] >= start]
    if not any(e["type"] == "end" for e in out):
        end = next((e for e in events if e["type"] == "end"), None)
        out.append(end or _synthetic_end(events, status, error))
    return out


async def event_stream(
    manager: RunManager, store: Any, run_id: uuid.UUID | str, start: int,
    is_disconnected: Callable[[], Awaitable[bool]],
    poll_s: float = 0.25, heartbeat_s: float = 15,
) -> AsyncIterator[str]:
    rid = str(run_id)
    uid = uuid.UUID(rid)
    index = max(start, 0)
    try:
        row = await asyncio.to_thread(store.events, rid)
        if row is None:
            return
        status, error, events = row
        active = manager.get_active(uid) if status == "running" else None

        if status == "running" and active is None:
            # the slot is gone: the row should become terminal very soon
            deadline = time.monotonic() + 2
            while status == "running" and time.monotonic() < deadline:
                await asyncio.sleep(min(poll_s, 0.1))
                row = await asyncio.to_thread(store.events, rid)
                if row is None:
                    return
                status, error, events = row
                active = manager.get_active(uid)
                if active is not None:
                    break
            if status == "running" and active is None:
                yield frame({"id": len(events), "type": "end",
                             "t": events[-1]["t"] if events else 0.0,
                             "status": "failed",
                             "error": "Run state could not be saved"})
                return

        if active is None:  # terminal: replay from the store
            for event in _stored_remainder(events, index, status, error):
                yield frame(event)
            return

        last_sent = time.monotonic()
        last_row_check = last_sent
        released_at: float | None = None
        while True:
            for event in manager.events_from(active, index):
                yield frame(event)
                last_sent = time.monotonic()
                index = event["id"] + 1
                if event["type"] == "end":
                    return
            if await is_disconnected():
                return

            released = manager.get_active(uid) is not active
            now = time.monotonic()
            if released or now - last_row_check >= ROW_CHECK_S:
                last_row_check = now
                row = await asyncio.to_thread(store.events, rid)
                if row is not None and row[0] != "running":
                    status, error, events = row
                    # drain the in-memory tail first (it may hold `end`)
                    tail = manager.events_from(active, index)
                    if tail and tail[-1]["type"] == "end":
                        continue
                    for event in _stored_remainder(events, index, status,
                                                   error):
                        yield frame(event)
                    return
            if released:
                released_at = released_at or now
                if now - released_at > 2:  # row never became terminal
                    yield frame({"id": index, "type": "end",
                                 "t": 0.0, "status": "failed",
                                 "error": "Run state could not be saved"})
                    return
            if now - last_sent >= heartbeat_s:
                yield ": ping\n\n"
                last_sent = now
            await asyncio.sleep(poll_s)
    finally:
        log.debug("event stream for %s closed", rid)
