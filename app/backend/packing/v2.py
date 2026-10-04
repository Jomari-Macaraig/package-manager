"""Pipeline v2: four narrow model jobs, everything else in code.

extract -> prepare -> packer -> strategy -> place + validate -> check ->
choose -> notes -> finalize (the app adapter does the last step). Pure code
plus hooks: no FastAPI, `app` or `agents` imports; the app passes bounds,
token limits and prices in.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .jobs import check as check_job
from .jobs import extract as extract_job
from .jobs import notes as notes_job
from .jobs import strategy as strategy_job
from .jobs.base import VOLUME_MARGIN, JobConfig, JobResult, skipped_result
from .packer import pack, preference_report
from .planner import _CONFIG_ERRORS, RunCancelled, _rank
from .schemas import FlatResponse
from .settings import PackingSettings, prepare_boxes, prepare_items
from .validation import plan_score

JOB_ORDER = ("extract", "strategy", "check", "notes")
PROMPT_VERSIONS = {"extract": extract_job.PROMPT_VERSION,
                   "strategy": strategy_job.PROMPT_VERSION,
                   "check": check_job.PROMPT_VERSION,
                   "notes": notes_job.PROMPT_VERSION}


class RunCancelledV2(RunCancelled):
    """Cancelled; .jobs holds all 4 jobs (unrun ones skipped, "cancelled")."""

    def __init__(self, jobs: list[JobResult]) -> None:
        super().__init__(None)
        self.jobs = jobs


class ConfigErrorV2(Exception):
    """An OpenAI configuration error (auth, permission, not found, bad
    request) in a job; .jobs holds the finished jobs plus the rest as
    skipped with "not run"."""

    def __init__(self, original: Exception, jobs: list[JobResult]) -> None:
        super().__init__(str(original))
        self.original = original
        self.jobs = jobs


@dataclass
class V2Result:
    settings: PackingSettings
    items_p: list[dict]
    boxes_p: list[dict]
    estimates: list[dict]
    packer_flat: FlatResponse | None
    packer_errors: list[str]
    model_flat: FlatResponse | None
    model_errors: list[str]
    preferences_model: list[dict]
    preferences_packer: list[dict]
    verdicts: list[dict]
    source: str  # "model" | "packer" | "none"
    reason: str
    final_flat: FlatResponse | None
    notes: list[dict]  # [{box, steps}]
    jobs: list[JobResult] = field(default_factory=list)  # 4, pipeline order


def _leftover(flat: FlatResponse) -> int:
    return sum(r.quantity for r in flat.leftovers)


def choose(model_flat: FlatResponse | None, packer_flat: FlatResponse | None,
           verdicts: Sequence[Mapping[str, Any]],
           soft_instructions: Sequence[str],
           prefs_model: Sequence[Mapping[str, Any]],
           prefs_packer: Sequence[Mapping[str, Any]],
           boxes) -> tuple[str, str]:
    """(source, reason); the first rule that matches wins. model_flat and
    packer_flat are valid plans or None."""
    if model_flat is None and packer_flat is None:
        return "none", "no valid plan"
    if model_flat is None:
        return "packer", "only valid plan"
    if packer_flat is None:
        return "model", "only valid plan"
    if (soft_instructions
            and verdicts and all(v["verdict"] == "honoured" for v in verdicts)
            and prefs_model
            and all(r["honoured"] == r["total"] for r in prefs_model)
            and any(r["honoured"] < r["total"] for r in prefs_packer)
            and _leftover(model_flat) <= _leftover(packer_flat)
            and len(model_flat.boxes) <= len(packer_flat.boxes)
            and plan_score(model_flat, boxes)[1]
            <= VOLUME_MARGIN * plan_score(packer_flat, boxes)[1] + 1e-9):
        return "model", "honours the instructions"
    mine, theirs = _rank(model_flat, boxes), _rank(packer_flat, boxes)
    if mine == theirs and not model_flat.placements:
        return "model", "same as packer"
    if mine <= theirs:
        return "model", "better metrics"
    return "packer", "packer has better metrics"


def run_v2(client, items, boxes, instructions: str,
           jobs: Mapping[str, JobConfig], *, bounds: Mapping[str, float],
           on_event: Callable[[dict[str, Any]], None] | None = None,
           should_stop: Callable[[], bool] | None = None,
           on_context: Callable[[str, tuple], None] | None = None,
           ) -> V2Result:
    """Run the pipeline. jobs: a JobConfig for each of the 4 jobs.

    on_event gets {"type": "job", ...} after each job, {"type": "packer"}
    and {"type": "chosen"}. on_context("packer" | "final", (items_p,
    boxes_p, settings, flat)) lets the app point FakeClient at a plan.
    Raises RunCancelledV2 (should_stop) or ConfigErrorV2.
    """
    done: dict[str, JobResult] = {}

    def all_jobs(error: str) -> list[JobResult]:
        return [done.get(name) or skipped_result(
            name, jobs[name], PROMPT_VERSIONS[name], error=error)
            for name in JOB_ORDER]

    def stop() -> None:
        if should_stop is not None and should_stop():
            raise RunCancelledV2(all_jobs("cancelled"))

    def emit(event: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(event)

    def finished(result: JobResult) -> JobResult:
        done[result.job] = result
        emit({"type": "job", "job": result.job, "status": result.status,
              "passed": sum(c.passed for c in result.checks),
              "total": len(result.checks)})
        return result

    def guarded(call: Callable[[], Any]):
        try:
            return call()
        except _CONFIG_ERRORS as exc:
            partial = getattr(exc, "job_result", None)
            if partial is not None:  # earlier calls were made and paid for
                partial.status = "fallback"
                partial.error = f"{type(exc).__name__}: {str(exc)[:300]}"
                done[partial.job] = partial
            raise ConfigErrorV2(exc, all_jobs("not run")) from exc

    # 1. extract
    stop()
    result = finished(guarded(lambda: extract_job.run_extract(
        client, jobs["extract"], instructions, items, boxes, bounds)))
    settings = PackingSettings.model_validate(result.output)

    # 2. prepare (the extract checks made sure it applies)
    boxes_p = prepare_boxes(boxes, settings)
    items_p = prepare_items(items, boxes, settings)

    # 3. packer
    stop()
    estimates: list[dict] = []
    baseline, packer_errors = pack(items_p, boxes_p, settings, estimates)
    packer_flat = baseline if not packer_errors else None
    if on_context is not None:
        on_context("packer", (items_p, boxes_p, settings, packer_flat))
    emit({"type": "packer", "ok": packer_flat is not None,
          "boxes": len(packer_flat.boxes) if packer_flat else 0})

    # 4. strategy, 5. place + validate (inside the job)
    stop()
    run = guarded(lambda: strategy_job.run_strategy(
        client, jobs["strategy"], items_p, boxes_p, settings, estimates,
        packer_flat))
    finished(run.result)
    model_flat = run.flat
    prefs_model = run.report if model_flat is not None else []
    prefs_packer = (preference_report(packer_flat, items_p, boxes_p,
                                      run.preferences)
                    if packer_flat is not None else [])

    # 6. check
    stop()
    soft = list(settings.soft_instructions)
    result = finished(guarded(lambda: check_job.run_check(
        client, jobs["check"], soft, model_flat, items_p, boxes_p,
        run.preferences, prefs_model)))
    verdicts = [{**v, "instruction": soft[v["index"]]}
                for v in (result.output or []) if 0 <= v["index"] < len(soft)]

    # 7. choose
    source, reason = choose(model_flat, packer_flat, verdicts, soft,
                            prefs_model, prefs_packer, boxes_p)
    final_flat = {"model": model_flat, "packer": packer_flat,
                  "none": None}[source]
    emit({"type": "chosen", "source": source, "reason": reason})

    # 8. notes
    stop()
    if final_flat is not None and on_context is not None:
        on_context("final", (items_p, boxes_p, settings, final_flat))
    result = finished(guarded(lambda: notes_job.run_notes(
        client, jobs["notes"], final_flat, items_p, boxes_p, soft)))

    model_errors = [] if model_flat is not None else (
        run.errors or ["no plan from the model"])
    return V2Result(
        settings=settings, items_p=items_p, boxes_p=boxes_p,
        estimates=estimates, packer_flat=packer_flat,
        packer_errors=list(packer_errors), model_flat=model_flat,
        model_errors=model_errors, preferences_model=prefs_model,
        preferences_packer=prefs_packer, verdicts=verdicts, source=source,
        reason=reason, final_flat=final_flat, notes=list(result.output or []),
        jobs=all_jobs("not run"))
