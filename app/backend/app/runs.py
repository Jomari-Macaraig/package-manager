"""RunManager: one pipeline at a time in a daemon worker thread.

The active run keeps an in-memory event list (SSE polls it). Events are also
persisted as they happen. At the end the final state is written to SQLite in
one transaction, then the `end` event is appended in memory, and only then is
the slot released.

The manager is generic over an adapter (v1 by default, pipeline_v2.V2_ADAPTER
for v2). The slot is shared (`Slot`), so only one run of either kind is
active; each manager only sees the runs of its own `kind`.
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
    """A run is already active (HTTP 409); .kind is the active run's kind."""

    def __init__(self, kind: str = "v1") -> None:
        super().__init__("A run is already in progress")
        self.kind = kind


class NotActive(Exception):
    """The run is not the active one (HTTP 409)."""


@dataclass
class ActiveRun:
    run_id: uuid.UUID
    events: list[dict[str, Any]] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    t0: float = field(default_factory=time.monotonic)
    kind: str = "v1"


@dataclass
class Slot:
    """The one run slot, shared by the v1 and v2 managers."""
    lock: threading.Lock = field(default_factory=threading.Lock)
    active: ActiveRun | None = None


class V1Adapter:
    """What RunManager needs from a pipeline; this one is today's v1."""

    pipeline = staticmethod(run_pipeline)
    new_partial = staticmethod(new_partial)
    partial_result = staticmethod(partial_result)

    # looked up at call time, so tests can patch the store functions
    @staticmethod
    def set_status(run_id: str, status: str, error: str | None) -> bool:
        return store.set_status(run_id, status, error)

    @staticmethod
    def append_event(run_id: str, events: list[dict]) -> None:
        store.append_event(run_id, events)

    @staticmethod
    def mark_interrupted() -> int:
        return store.mark_interrupted()

    @staticmethod
    def title(req: RunRequest) -> str:
        return ", ".join(i.name for i in req.items[:3])

    def create_run(self, run_id: str, req: RunRequest,
                   meta: dict[str, str]) -> None:
        store.create(
            run_id=run_id, trace_id=f"trace_{uuid.UUID(run_id).hex}",
            request_id=meta["request_id"], client_ip=meta["client_ip"],
            user_agent=meta["user_agent"], title=self.title(req),
            extraction_model=req.extraction.model,
            planning_model=req.planning.model,
            request_json=req.model_dump_json())

    @staticmethod
    def finish(run_id: str, status: str, error: str | None,
               result: dict[str, Any] | None, events: list[dict]) -> bool:
        svgs = history = None
        if result is not None:
            result = dict(result)
            svgs = result.pop("svgs", None)
            history = result.pop("history", None)
        return store.finish(run_id, status, error, result, svgs, history,
                            events)

    @staticmethod
    def cancelled_result(exc: Exception, partial: dict[str, Any]):
        return partial_result(partial)

    @staticmethod
    def error_result(exc: Exception, partial: dict[str, Any]):
        return partial_result(partial)

    @staticmethod
    def outcome(result: dict[str, Any]) -> tuple[str, str | None]:
        if result["final"] is not None:
            return "succeeded", None
        return "failed", "No valid plan from the model or the packer"


V1_ADAPTER = V1Adapter()


class RunManager:
    def __init__(self, adapter: Any = V1_ADAPTER, slot: Slot | None = None,
                 kind: str = "v1") -> None:
        self.adapter = adapter
        self.slot = slot or Slot()
        self.kind = kind
        # tests inject a FakeClient here
        self.client_factory: Callable[[], Any] | None = None

    @property
    def lock(self) -> threading.Lock:
        return self.slot.lock

    @property
    def active(self) -> ActiveRun | None:
        """The active run of this manager's kind (None for the other)."""
        active = self.slot.active
        if active is not None and active.kind == self.kind:
            return active
        return None

    # --- public ---------------------------------------------------------

    def start(self, req: Any, meta: dict[str, str]) -> uuid.UUID:
        """Sync, no await: check-and-set of the slot is atomic."""
        run_id = uuid.uuid4()
        with self.lock:
            if self.slot.active is not None:
                raise RunBusy(self.slot.active.kind)
            active = ActiveRun(run_id=run_id, kind=self.kind)
            self.slot.active = active
        created = False
        try:
            self.adapter.create_run(str(run_id), req, meta)
            created = True
            active.thread = threading.Thread(
                target=self._work, args=(active, req, meta), daemon=True,
                name=f"run-{run_id.hex[:8]}")
            active.thread.start()
        except BaseException:
            with self.lock:
                self.slot.active = None
            if created:
                try:
                    self.adapter.set_status(str(run_id), "failed",
                                            "Could not start the run")
                except Exception:
                    log.exception("could not mark run %s failed", run_id)
            raise
        return run_id

    def cancel(self, run_id: uuid.UUID) -> None:
        with self.lock:
            active = self.active
            if active is None or active.run_id != run_id:
                raise NotActive()
            active.cancel.set()

    def active_id(self) -> uuid.UUID | None:
        with self.lock:
            active = self.active
            return active.run_id if active else None

    def get_active(self, run_id: uuid.UUID) -> ActiveRun | None:
        with self.lock:
            active = self.active
            if active is not None and active.run_id == run_id:
                return active
            return None

    def events_from(self, active: ActiveRun, start: int) -> list[dict]:
        """A copy of events[start:], taken under the lock."""
        with self.lock:
            return list(active.events[start:])

    async def shutdown(self) -> None:
        """Cancel and join whatever run is in the slot, of either kind. The
        lifespan then marks the leftover running rows interrupted."""
        with self.lock:
            active = self.slot.active
        if active is not None:
            active.cancel.set()
            if active.thread is not None:
                await asyncio.to_thread(active.thread.join, 5)

    # --- worker ---------------------------------------------------------

    def _emit(self, active: ActiveRun, event: dict[str, Any]) -> None:
        with self.lock:
            entry = {"id": len(active.events),
                     "t": round(time.monotonic() - active.t0, 3), **event}
            active.events.append(entry)
            snapshot = list(active.events)
        try:  # outside the lock
            self.adapter.append_event(str(active.run_id), snapshot)
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

    def _work(self, active: ActiveRun, req: Any,
              meta: dict[str, str]) -> None:
        run_id = active.run_id
        adapter = self.adapter
        partial = adapter.new_partial()
        status: str = "failed"
        error: str | None = "Internal error"
        result: dict[str, Any] | None = None
        try:
            self._emit(active, {"type": "started"})
            client = self._client()
            result = adapter.pipeline(
                req, client, run_id=run_id, meta=meta,
                emit=lambda ev: self._emit(active, ev),
                should_stop=active.cancel.is_set, partial=partial)
            status, error = adapter.outcome(result)
        except RunCancelled as exc:
            status, error = "cancelled", None
            result = adapter.cancelled_result(exc, partial)
        except PipelineError as exc:
            status, error = "failed", str(exc)
            result = adapter.error_result(exc, partial)
        except BaseException:
            log.exception("run %s failed", run_id)
            status, error = "failed", "Internal error"
            result = adapter.partial_result(partial)
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
            # (2) one guarded transaction incl. `end`
            wrote: bool | None = None
            for attempt in (1, 2):
                try:
                    wrote = self.adapter.finish(run_id, status, error,
                                                result, stored_events)
                    break
                except Exception:
                    log.exception("finish failed for %s (attempt %d)",
                                  run_id, attempt)
            if wrote is None:
                try:
                    self.adapter.set_status(run_id, status, error)
                except Exception:
                    log.exception("set_status failed for %s", run_id)
                wrote = True
            # (3) `end` in memory (skipped if the row was already
            # marked interrupted)
            if wrote:
                with self.lock:
                    active.events.append(end)
        finally:
            # (4) release the slot on every path
            with self.lock:
                if self.slot.active is active:
                    self.slot.active = None
