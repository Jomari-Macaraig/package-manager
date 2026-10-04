"""RunManager: one pipeline at a time in a daemon worker thread.

The active run keeps an in-memory event list (SSE polls it). Events are also
persisted as they happen. At the end the final state is written to SQLite in
one transaction, then the `end` event is appended in memory, and only then is
the slot released.
"""

import asyncio
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

from . import config, store
from .fake_llm import FakeClient
from .pipeline import (PipelineError, RunCancelled, new_partial,
                       partial_result, run_pipeline)
from .schemas import RunRequest

log = logging.getLogger(__name__)


class RunBusy(Exception):
    """A run is already active (HTTP 409)."""


class NotActive(Exception):
    """The run is not the active one (HTTP 409)."""


@dataclass
class ActiveRun:
    run_id: uuid.UUID
    events: list[dict[str, Any]] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    t0: float = field(default_factory=time.monotonic)


class RunManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.active: ActiveRun | None = None
        # tests inject a FakeClient here
        self.client_factory: Callable[[], Any] | None = None

    # --- public ---------------------------------------------------------

    def start(self, req: RunRequest, meta: dict[str, str]) -> uuid.UUID:
        """Sync, no await: check-and-set of the slot is atomic."""
        run_id = uuid.uuid4()
        with self.lock:
            if self.active is not None:
                raise RunBusy()
            active = ActiveRun(run_id=run_id)
            self.active = active
        created = False
        try:
            store.create(
                run_id=str(run_id), trace_id=f"trace_{run_id.hex}",
                request_id=meta["request_id"], client_ip=meta["client_ip"],
                user_agent=meta["user_agent"],
                title=", ".join(i.name for i in req.items[:3]),
                extraction_model=req.extraction.model,
                planning_model=req.planning.model,
                request_json=req.model_dump_json())
            created = True
            active.thread = threading.Thread(
                target=self._work, args=(active, req, meta), daemon=True,
                name=f"run-{run_id.hex[:8]}")
            active.thread.start()
        except BaseException:
            with self.lock:
                self.active = None
            if created:
                try:
                    store.set_status(str(run_id), "failed",
                                     "Could not start the run")
                except Exception:
                    log.exception("could not mark run %s failed", run_id)
            raise
        return run_id

    def cancel(self, run_id: uuid.UUID) -> None:
        with self.lock:
            if self.active is None or self.active.run_id != run_id:
                raise NotActive()
            self.active.cancel.set()

    def active_id(self) -> uuid.UUID | None:
        with self.lock:
            return self.active.run_id if self.active else None

    def get_active(self, run_id: uuid.UUID) -> ActiveRun | None:
        with self.lock:
            if self.active is not None and self.active.run_id == run_id:
                return self.active
            return None

    def events_from(self, active: ActiveRun, start: int) -> list[dict]:
        """A copy of events[start:], taken under the lock."""
        with self.lock:
            return list(active.events[start:])

    async def shutdown(self) -> None:
        with self.lock:
            active = self.active
        if active is not None:
            active.cancel.set()
            if active.thread is not None:
                await asyncio.to_thread(active.thread.join, 5)
        await asyncio.to_thread(store.mark_interrupted)

    # --- worker ---------------------------------------------------------

    def _emit(self, active: ActiveRun, event: dict[str, Any]) -> None:
        with self.lock:
            entry = {"id": len(active.events),
                     "t": round(time.monotonic() - active.t0, 3), **event}
            active.events.append(entry)
            snapshot = list(active.events)
        try:  # outside the lock
            store.append_event(str(active.run_id), snapshot)
        except Exception:
            log.exception("could not persist event for %s", active.run_id)

    def _client(self):
        if self.client_factory is not None:
            return self.client_factory()
        if config.FAKE_LLM:
            return FakeClient()
        if not os.getenv("OPENAI_API_KEY"):
            raise PipelineError("OPENAI_API_KEY is not set")
        return OpenAI(timeout=config.OPENAI_TIMEOUT_S,
                      max_retries=config.OPENAI_MAX_RETRIES)

    def _work(self, active: ActiveRun, req: RunRequest,
              meta: dict[str, str]) -> None:
        run_id = active.run_id
        partial = new_partial()
        status: str = "failed"
        error: str | None = "Internal error"
        result: dict[str, Any] | None = None
        try:
            self._emit(active, {"type": "started"})
            client = self._client()
            result = run_pipeline(
                req, client, run_id=run_id, meta=meta,
                emit=lambda ev: self._emit(active, ev),
                should_stop=active.cancel.is_set, partial=partial)
            if result["final"] is not None:
                status, error = "succeeded", None
            else:
                status = "failed"
                error = "No valid plan from the model or the packer"
        except RunCancelled:
            status, error = "cancelled", None
            result = partial_result(partial)
        except PipelineError as exc:
            status, error = "failed", str(exc)
            result = partial_result(partial)
        except BaseException:
            log.exception("run %s failed", run_id)
            status, error = "failed", "Internal error"
            result = partial_result(partial)
        finally:
            self._finish(active, status, error, result)

    def _finish(self, active: ActiveRun, status: str, error: str | None,
                result: dict[str, Any] | None) -> None:
        run_id = str(active.run_id)
        try:
            # (1) the end event, built once
            with self.lock:
                end = {"id": len(active.events),
                       "t": round(time.monotonic() - active.t0, 3),
                       "type": "end", "status": status, "error": error}
                stored_events = [*active.events, end]
            svgs = history = None
            if result is not None:
                result = dict(result)
                svgs = result.pop("svgs", None)
                history = result.pop("history", None)
            # (2) one guarded transaction incl. `end`
            wrote: bool | None = None
            for attempt in (1, 2):
                try:
                    wrote = store.finish(run_id, status, error, result, svgs,
                                         history, stored_events)
                    break
                except Exception:
                    log.exception("store.finish failed for %s (attempt %d)",
                                  run_id, attempt)
            if wrote is None:
                try:
                    store.set_status(run_id, status, error)
                except Exception:
                    log.exception("store.set_status failed for %s", run_id)
                wrote = True
            # (3) `end` in memory (skipped if the row was already
            # marked interrupted)
            if wrote:
                with self.lock:
                    active.events.append(end)
        finally:
            # (4) release the slot on every path
            with self.lock:
                if self.active is active:
                    self.active = None
