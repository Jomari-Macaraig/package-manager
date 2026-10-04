"""Pipeline v2 adapter: runs packing.v2.run_v2 and turns its result into the
stored run (sides, final plan, SVGs, costs, totals) and the job rows.

Config errors (auth, permission, not found, bad request) fail the run via
v1's `_openai_error`; the job rows survive in `partial["jobs"]`.
"""

import time
import uuid
from collections.abc import Callable
from typing import Any

from agents import trace  # type: ignore[attr-defined]

from packing.jobs.base import JobConfig, JobResult
from packing.render_svg import render_svg
from packing.settings import final_plan
from packing.v2 import (JOB_ORDER, ConfigErrorV2, RunCancelledV2, V2Result,
                        run_v2)

from . import config, store_v2
from .fake_llm import FakeClient
from .pipeline import _cost, _openai_error, _side
from .schemas import to_pipeline_inputs
from .schemas_v2 import RunRequestV2

Emit = Callable[[dict[str, Any]], None]


# --- configuration -------------------------------------------------------------

def job_configs(req: RunRequestV2) -> dict[str, JobConfig]:
    """A JobConfig per job: the request's choice or the default; fixed
    model kwargs (EXTRACTION_MODELS) and the output limit from config."""
    out = {}
    for name in JOB_ORDER:
        spec = config.JOBS[name]
        choice = req.jobs.get(name)
        model = choice.model if choice else spec["default"]["model"]
        effort = (choice.reasoning_effort if choice
                  else spec["default"]["reasoning_effort"])
        if name == "strategy":
            out[name] = JobConfig(
                model=model, reasoning_effort=effort,
                max_completion_tokens=config.MAX_COMPLETION_TOKENS[
                    "planning"][effort])
        else:
            kwargs = next(m["kwargs"] for m in config.EXTRACTION_MODELS
                          if m["id"] == model)
            out[name] = JobConfig(model=model, reasoning_effort=effort,
                                  max_completion_tokens=spec["max_tokens"],
                                  extra_kwargs=dict(kwargs))
    return out


# --- result building -----------------------------------------------------------

def _job_row(job: JobResult) -> dict[str, Any]:
    """A job_calls row (store_v2.finish) with its cost."""
    return {
        "job": job.job, "status": job.status, "model": job.model,
        "reasoning_effort": job.reasoning_effort,
        "prompt_version": job.prompt_version, "calls": job.calls,
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail}
                   for c in job.checks],
        "prompt_tokens": job.prompt_tokens,
        "completion_tokens": job.completion_tokens,
        "reasoning_tokens": job.reasoning_tokens,
        "cost": _cost(job.model, job.prompt_tokens, job.completion_tokens)
        if job.model else 0.0,
        "latency_s": job.latency_s, "error": job.error,
        "answer": job.answer, "output": job.output,
        "messages": job.messages}


def _totals(rows: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    cost = round(sum(r["cost"] for r in rows), 6)
    budget = dict(config.BUDGET)
    return {"cost": cost, "seconds": round(seconds, 3),
            "over_budget": cost > budget["usd"] or seconds > budget["seconds"],
            "budget": budget}


def new_partial() -> dict[str, Any]:
    return {"t0": time.monotonic(), "jobs": None}


def _from_jobs(partial: dict[str, Any]) -> dict[str, Any]:
    """Result of a run that did not finish: what the jobs collected."""
    jobs = partial.get("jobs")
    rows = [_job_row(j) for j in jobs] if jobs else []
    settings = None
    if jobs and jobs[0].job == "extract" and jobs[0].output is not None:
        settings = jobs[0].output
    return {"settings": settings, "prepared_items": None,
            "prepared_boxes": None, "packer": None, "model": None,
            "source": None, "reason": None, "final": None,
            "preferences_model": [], "preferences_packer": [],
            "verdicts": [], "totals": _totals(
                rows, time.monotonic() - partial["t0"]) if rows else None,
            "svgs": {}, "jobs": rows}


def partial_result(partial: dict[str, Any]) -> dict[str, Any]:
    return _from_jobs(partial)


def cancelled_result(exc: Exception, partial: dict[str, Any]) -> dict[str, Any]:
    if isinstance(exc, RunCancelledV2):
        partial["jobs"] = exc.jobs
    return _from_jobs(partial)


def error_result(exc: Exception, partial: dict[str, Any]) -> dict[str, Any]:
    return _from_jobs(partial)


def _build(res: V2Result, partial: dict[str, Any]) -> dict[str, Any]:
    items_p, boxes_p, settings = res.items_p, res.boxes_p, res.settings
    svgs: dict[str, str] = {}

    def side(flat, errors, key):
        data, plan = _side(flat, list(errors), [], items_p, boxes_p, settings)
        if plan is not None:
            svgs[key] = render_svg(plan)
        return data

    packer = side(res.packer_flat, res.packer_errors, "packer")
    model = side(res.model_flat, res.model_errors, "model")

    final = None
    if res.final_flat is not None:
        # plan.notes = the cut notes only (the flat's own notes are dropped)
        plan = final_plan(res.final_flat.model_copy(update={"notes": []}),
                          items_p, boxes_p, settings)
        final = {"source": res.source, "reason": res.reason,
                 "plan": plan.model_dump(mode="json"), "notes": res.notes,
                 "cut_notes": list(plan.notes)}
        svgs["final"] = render_svg(plan)

    rows = [_job_row(j) for j in res.jobs]
    return {
        "settings": settings.model_dump(mode="json"),
        "prepared_items": [{**i, "source_id": i.get("source_id", i["id"])}
                           for i in items_p],
        "prepared_boxes": [dict(b) for b in boxes_p],
        "packer": packer, "model": model,
        "source": res.source, "reason": res.reason, "final": final,
        "preferences_model": res.preferences_model,
        "preferences_packer": res.preferences_packer,
        "verdicts": res.verdicts,
        "totals": _totals(rows, time.monotonic() - partial["t0"]),
        "svgs": svgs, "jobs": rows}


# --- entry point ---------------------------------------------------------------

def run_pipeline_v2(req: RunRequestV2, client, *, run_id: uuid.UUID,
                    meta: dict[str, str], emit: Emit,
                    should_stop: Callable[[], bool],
                    partial: dict[str, Any]) -> dict[str, Any]:
    """Returns the stored result (plus `svgs` and `jobs`, popped by the
    adapter's finish). Raises RunCancelledV2, PipelineError."""
    fake = config.FAKE_LLM or isinstance(client, FakeClient)
    store_v2.set_fake(str(run_id), fake)
    if fake:
        client.echo_soft = True  # F-H exercise the check job in fake runs
    jobs = job_configs(req)
    metadata = {
        "run_id": str(run_id), "request_id": meta["request_id"],
        "client_ip": meta["client_ip"],
        "user_agent": meta["user_agent"][:200], "pipeline": "v2",
        "fake": str(fake).lower(),
        **{f"{name}_model": jobs[name].model for name in JOB_ORDER}}

    def on_context(stage: str, data: tuple) -> None:
        if hasattr(client, "context"):  # FakeClient; real clients ignore it
            client.context = data

    items, boxes = to_pipeline_inputs(req)
    try:
        with trace("packing-playground run v2",
                   trace_id=f"trace_{run_id.hex}",
                   group_id=meta["client_ip"], metadata=metadata):
            res = run_v2(client, items, boxes, req.instructions, jobs,
                         bounds=config.SETTINGS_BOUNDS, on_event=emit,
                         should_stop=should_stop, on_context=on_context)
    except RunCancelledV2 as exc:
        partial["jobs"] = exc.jobs
        raise
    except ConfigErrorV2 as exc:
        partial["jobs"] = exc.jobs
        raise _openai_error(exc.original) from exc
    return _build(res, partial)


class V2Adapter:
    """RunManager adapter for v2 (runs.RunManager(V2_ADAPTER, slot, "v2"))."""

    pipeline = staticmethod(run_pipeline_v2)
    new_partial = staticmethod(new_partial)
    partial_result = staticmethod(partial_result)
    cancelled_result = staticmethod(cancelled_result)
    error_result = staticmethod(error_result)
    @staticmethod
    def set_status(run_id: str, status: str, error: str | None) -> bool:
        return store_v2.set_status(run_id, status, error)

    @staticmethod
    def append_event(run_id: str, events: list[dict]) -> None:
        store_v2.append_event(run_id, events)

    @staticmethod
    def mark_interrupted() -> int:
        return store_v2.mark_interrupted()

    @staticmethod
    def title(req: RunRequestV2) -> str:
        return ", ".join(i.name for i in req.items[:3])

    def create_run(self, run_id: str, req: RunRequestV2,
                   meta: dict[str, str]) -> None:
        store_v2.create_run(
            run_id, req.model_dump_json(exclude_unset=False),
            trace_id=f"trace_{uuid.UUID(run_id).hex}",
            request_id=meta["request_id"], client_ip=meta["client_ip"],
            user_agent=meta["user_agent"], title=self.title(req),
            fake=config.FAKE_LLM)

    @staticmethod
    def outcome(result: dict[str, Any]) -> tuple[str, str | None]:
        """A finished pipeline always succeeds, even with `final: null`."""
        return "succeeded", None

    @staticmethod
    def finish(run_id: str, status: str, error: str | None,
               result: dict[str, Any] | None, events: list[dict]) -> bool:
        svgs = jobs = None
        if result is not None:
            result = dict(result)
            svgs = result.pop("svgs", None)
            jobs = result.pop("jobs", None)
        return store_v2.finish(run_id, status, error, result, svgs, events,
                               jobs)


V2_ADAPTER = V2Adapter()
