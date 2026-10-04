"""The notebook pipeline (labs/manager.ipynb cells 9, 12, 23) as stages.

extract -> prepare -> packer -> plan -> finalize. Only extract and plan call
a model (the stages a later Agents SDK port would replace). Each stage runs
in an Agents SDK custom_span inside one trace.
"""

import logging
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import openai
from agents import custom_span, trace  # type: ignore[attr-defined]
from pydantic import ValidationError

from packing.packer import pack
from packing.planner import (PlanResult, RunCancelled, _rank,
                             plan_with_validation)
from packing.prompt_builder import SYSTEM_PROMPT, build_user_prompt
from packing.render_svg import render_svg
from packing.schemas import BoxRow, FlatResponse
from packing.settings import (PackingSettings, extract_settings, final_plan,
                              prepare_boxes, prepare_items, rules_text)

from . import config, tracing
from .schemas import RunRequest, to_pipeline_inputs

log = logging.getLogger(__name__)

MAX_ERRORS_SAVED = 50

AUTH_MESSAGE = "OpenAI authentication failed — check OPENAI_API_KEY"
_CONFIG_ERRORS = (openai.AuthenticationError, openai.PermissionDeniedError,
                  openai.NotFoundError, openai.BadRequestError)

Emit = Callable[[dict[str, Any]], None]


class PipelineError(Exception):
    """A failure with a user-facing message (never a traceback or repr)."""


__all__ = ["PipelineError", "RunCancelled", "run_pipeline", "partial_result",
           "new_partial", "renumber_boxes"]


# --- helpers ----------------------------------------------------------------

def new_partial() -> dict[str, Any]:
    return {"settings": None, "prepared_items": None, "prepared_boxes": None,
            "packer": None, "extraction": None, "planning": None,
            "timings": {"extract_s": None, "packer_s": None, "plan_s": None,
                        "total_s": None},
            "history": {"extraction": [], "planning": []},
            "svgs": {"model": None, "packer": None}}


def _cost(model: str, prompt: int, completion: int) -> float:
    price_in, price_out = config.PRICES.get(model, (0.0, 0.0))
    return round((prompt * price_in + completion * price_out) / 1_000_000, 6)


def _call_stats(model: str, effort: str | None, prompt: int, completion: int,
                reasoning: int) -> dict[str, Any]:
    # reasoning tokens are already inside completion tokens
    return {"model": model, "reasoning_effort": effort,
            "prompt_tokens": prompt, "completion_tokens": completion,
            "reasoning_tokens": reasoning,
            "cost": _cost(model, prompt, completion)}


def _build_stats(partial: dict[str, Any]) -> dict[str, Any] | None:
    ext, plan = partial["extraction"], partial["planning"]
    timings = partial["timings"]
    if ext is None and plan is None and all(
            v is None for v in timings.values()):
        return None
    calls = [c for c in (ext, plan) if c]
    return {
        "extraction": ext, "planning": plan,
        "total_tokens": sum(c["prompt_tokens"] + c["completion_tokens"]
                            for c in calls),
        "total_cost": round(sum(c["cost"] for c in calls), 6),
        "timings": dict(timings),
    }


def partial_result(partial: dict[str, Any]) -> dict[str, Any]:
    """Result fields for a run that did not finish: what was collected."""
    return {
        "settings": partial["settings"],
        "prepared_items": partial["prepared_items"],
        "prepared_boxes": partial["prepared_boxes"],
        "final": None, "model": None, "packer": partial["packer"],
        "stats": _build_stats(partial),
        "history": partial["history"], "svgs": partial["svgs"],
    }


def renumber_boxes(flat: FlatResponse) -> FlatResponse:
    """Physical boxes numbered 1..n (sorted by original number); placements
    follow."""
    mapping = {old: new for new, old in enumerate(
        sorted(b.box for b in flat.boxes), start=1)}
    return flat.model_copy(update={
        "boxes": [BoxRow(box=mapping[b.box], id=b.id)
                  for b in sorted(flat.boxes, key=lambda b: b.box)],
        "placements": [row.model_copy(update={"box": mapping[row.box]})
                       for row in flat.placements],
    })


def _openai_error(exc: openai.APIError) -> PipelineError:
    if isinstance(exc, openai.AuthenticationError):
        return PipelineError(AUTH_MESSAGE)
    return PipelineError(f"{type(exc).__name__}: {str(exc)[:300]}")


@contextmanager
def _stage(name: str):
    """A custom_span holding counts/timings only; yields its data dict."""
    data: dict[str, Any] = {}
    with custom_span(name, data={}) as span:
        start = time.monotonic()
        try:
            yield data
        finally:
            data["duration_s"] = round(time.monotonic() - start, 3)
            span.span_data.data.update(data)


class _Spy:
    """Records the last extraction call so tokens/history survive a parse
    failure."""

    def __init__(self, client) -> None:
        self._client = client
        self.messages: list[dict[str, Any]] = []
        self.usage = None
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.messages = list(kwargs.get("messages", []))
        response = self._client.chat.completions.create(**kwargs)
        self.usage = getattr(response, "usage", None)
        return response


def _usage_counts(usage) -> tuple[int, int, int]:
    if usage is None:
        return 0, 0, 0
    details = getattr(usage, "completion_tokens_details", None)
    return (usage.prompt_tokens or 0, usage.completion_tokens or 0,
            (details.reasoning_tokens or 0) if details else 0)


def _check_settings(settings: PackingSettings) -> None:
    b = config.SETTINGS_BOUNDS
    problems = []
    if settings.padding > b["padding"]:
        problems.append(f"padding {settings.padding:g} in")
    if any(r.padding > b["padding"] for r in settings.padding_by_item):
        problems.append("per-item padding")
    if settings.wall_clearance > b["wall_clearance"]:
        problems.append(f"wall clearance {settings.wall_clearance:g} in")
    if (settings.cut_step is not None
            and settings.cut_step < b["cut_step_min"]):
        problems.append(f"cut step {settings.cut_step:g} in")
    if any(r.size > b["block_size"] for r in settings.blocks):
        problems.append("block size")
    if settings.max_boxes is not None and settings.max_boxes > b["max_boxes"]:
        problems.append(f"max boxes {settings.max_boxes}")
    if problems:
        raise PipelineError("Settings out of range: " + ", ".join(problems))
    ids = [r.item_id for r in settings.blocks]
    if len(ids) != len(set(ids)):
        raise PipelineError(
            "Settings contain duplicate block rules for the same item")


# --- stages -----------------------------------------------------------------

def _extract(req: RunRequest, client, items, should_stop, partial,
             emit: Emit) -> PackingSettings:
    if should_stop():
        raise RunCancelled()
    model = req.extraction.model
    kwargs = dict(next(m["kwargs"] for m in config.EXTRACTION_MODELS
                       if m["id"] == model))
    effort = kwargs.get("reasoning_effort")
    spy = _Spy(client)
    start = time.monotonic()
    skipped = not req.instructions.strip()
    with _stage("extract") as data:
        data["model"] = model
        data["skipped"] = skipped
        try:
            settings, usage, messages = extract_settings(
                spy, model, req.instructions, items,
                max_completion_tokens=config.MAX_COMPLETION_TOKENS[
                    "extraction"], **kwargs)
        except openai.APIError as exc:
            _save_extraction(partial, model, effort, spy.usage, spy.messages,
                             start)
            raise _openai_error(exc) from exc
        except (ValidationError, TypeError, ValueError) as exc:
            _save_extraction(partial, model, effort, spy.usage, spy.messages,
                             start)
            log.warning("extraction reply rejected: %r", exc)
            reason = _short_reason(exc)
            raise PipelineError(
                f"Could not interpret the instructions: {reason}") from exc
        if usage is not None:
            tracing.record_generation(
                model, {"reasoning_effort": effort} if effort else {},
                dict(zip(("input_tokens", "output_tokens",
                          "reasoning_tokens"), _usage_counts(usage))))
        _save_extraction(partial, model, effort, usage, messages, start)
        data["tokens"] = sum(_usage_counts(usage)[:2])
        _check_settings(settings)
    partial["settings"] = settings.model_dump(mode="json")
    emit({"type": "extracted", "tokens": sum(_usage_counts(usage)[:2]),
          "skipped": skipped})
    return settings


def _short_reason(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ()))
        reason = f"{where}: {first.get('msg', 'invalid reply')}".strip(": ")
    else:
        reason = str(exc) or type(exc).__name__
    return reason[:200]


def _save_extraction(partial, model, effort, usage, messages, start) -> None:
    partial["timings"]["extract_s"] = round(time.monotonic() - start, 3)
    partial["history"]["extraction"] = list(messages)
    if usage is not None:
        p, c, r = _usage_counts(usage)
        partial["extraction"] = _call_stats(model, effort, p, c, r)


def _prepare(items, boxes, settings, partial, emit: Emit):
    with _stage("prepare") as data:
        try:
            boxes_p = prepare_boxes(boxes, settings)
            for box in boxes_p:
                if box["dimensions"]["y"] <= 0 or box["dimensions"]["z"] <= 0:
                    raise PipelineError(
                        f"Wall clearance leaves no room in box {box['name']}")
            items_p = prepare_items(items, boxes, settings)
        except ValueError as exc:
            raise PipelineError(f"Invalid settings: {exc}") from exc
        units = sum(i["quantity"] for i in items_p)
        if units > config.CAPS["max_units"]:
            raise PipelineError(
                f"Order has {units} units after preparation; the cap is "
                f"{config.CAPS['max_units']}")
        rules = rules_text(items_p, boxes_p, settings)
        prompt = build_user_prompt(
            items_p, boxes_p, "\n".join(settings.soft_instructions), rules)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}]
        data.update(item_types=len(items_p), units=units)
    partial["prepared_items"] = [
        {**i, "source_id": i.get("source_id", i["id"])} for i in items_p]
    partial["prepared_boxes"] = [dict(b) for b in boxes_p]
    emit({"type": "prepared", "item_types": len(items_p), "units": units})
    return items_p, boxes_p, messages


def _side(flat: FlatResponse | None, errors: list[str], hints: list[str],
          items_p, boxes_p, settings) -> tuple[dict[str, Any], Any]:
    """PlanSide dict + the nested Response (None when invalid)."""
    if flat is None or errors:
        return ({"ok": False, "rank": None,
                 "errors": errors[:MAX_ERRORS_SAVED], "hints": [],
                 "plan": None, "box_count": 0}, None)
    flat = renumber_boxes(flat)
    plan = final_plan(flat, items_p, boxes_p, settings)
    return ({"ok": True, "rank": list(_rank(flat, boxes_p)), "errors": [],
             "hints": list(hints), "plan": plan.model_dump(mode="json"),
             "box_count": len(plan.boxes)}, plan)


def _packer(items_p, boxes_p, settings, client, should_stop, partial,
            emit: Emit):
    if should_stop():
        raise RunCancelled()
    with _stage("packer") as data:
        start = time.monotonic()
        baseline, errors = pack(items_p, boxes_p, settings)
        partial["timings"]["packer_s"] = round(time.monotonic() - start, 3)
        valid = not errors
        if hasattr(client, "context"):  # FakeClient
            client.context = (items_p, boxes_p, settings, baseline)
        side, plan = _side(baseline if valid else None, list(errors), [],
                           items_p, boxes_p, settings)
        data.update(ok=valid, boxes=side["box_count"],
                    errors_count=len(errors))
    partial["packer"] = side
    if plan is not None:
        partial["svgs"]["packer"] = render_svg(plan)
    emit({"type": "packer", "ok": valid, "boxes": side["box_count"],
          "errors_count": len(errors)})
    return (baseline if valid else None), errors


def _plan(req: RunRequest, client, messages, items_p, boxes_p, settings,
          baseline, should_stop, partial, emit: Emit) -> PlanResult:
    model, effort = req.planning.model, req.planning.reasoning_effort
    tally = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}

    def count(_model, _config, usage) -> None:
        for key in tally:
            tally[key] += usage.get(key, 0)

    def save_stats() -> None:
        partial["planning"] = _call_stats(
            model, effort, tally["input_tokens"], tally["output_tokens"],
            tally["reasoning_tokens"])

    start = time.monotonic()
    with _stage("plan") as data:
        data.update(model=model, reasoning_effort=effort)
        try:
            result = plan_with_validation(
                client, model, messages, items_p, boxes_p,
                settings=settings, baseline=baseline,
                max_rounds=config.LOOP["max_rounds"],
                improve_rounds=config.LOOP["improve_rounds"],
                on_event=emit, should_stop=should_stop,
                on_call=tracing.on_call_hook(count),
                reasoning_effort=effort,
                max_completion_tokens=config.MAX_COMPLETION_TOKENS[
                    "planning"])
        except RunCancelled as exc:
            partial["timings"]["plan_s"] = round(time.monotonic() - start, 3)
            if exc.result is not None:
                partial["history"]["planning"] = list(exc.result.history)
                partial["planning"] = _call_stats(
                    model, effort, exc.result.prompt_tokens,
                    exc.result.completion_tokens, exc.result.reasoning_tokens)
            raise
        except _CONFIG_ERRORS as exc:
            partial["timings"]["plan_s"] = round(time.monotonic() - start, 3)
            save_stats()
            raise _openai_error(exc) from exc
        partial["timings"]["plan_s"] = round(time.monotonic() - start, 3)
        partial["history"]["planning"] = list(result.history)
        partial["planning"] = _call_stats(
            model, effort, result.prompt_tokens, result.completion_tokens,
            result.reasoning_tokens)
        data.update(rounds=result.rounds, source=result.source,
                    ok=result.ok)
    return result


def _finalize(result: PlanResult, items_p, boxes_p, settings, partial,
              should_stop, emit: Emit) -> dict[str, Any]:
    if should_stop():
        raise RunCancelled()
    with _stage("finalize") as data:
        model_ok = result.model_flat is not None and not result.model_errors
        model_side, model_plan = _side(
            result.model_flat if model_ok else None, result.model_errors,
            result.model_hints, items_p, boxes_p, settings)
        model_side.update(rounds=result.rounds, improved=result.improved)
        if model_plan is not None:
            partial["svgs"]["model"] = render_svg(model_plan)

        final = None
        if result.ok:
            flat = renumber_boxes(result.flat)
            final = {"source": result.source,
                     "plan": final_plan(flat, items_p, boxes_p,
                                        settings).model_dump(mode="json")}
        data.update(final=result.source if final else None)
    emit({"type": "chosen", "source": final["source"] if final else None})
    return {"final": final, "model": model_side}


# --- entry point ------------------------------------------------------------

def run_pipeline(req: RunRequest, client, *, run_id: uuid.UUID,
                 meta: dict[str, str], emit: Emit,
                 should_stop: Callable[[], bool],
                 partial: dict[str, Any]) -> dict[str, Any]:
    """Notebook pipeline; returns the Run result fields plus history and
    svgs. Raises RunCancelled, PipelineError (user-facing message).
    `partial` is filled as stages finish (settings, prepared, packer side,
    stats, extraction history; planning history/tokens from
    RunCancelled.result) so cancelled/failed runs still save them."""
    started = time.monotonic()
    metadata = {
        "run_id": str(run_id), "request_id": meta["request_id"],
        "client_ip": meta["client_ip"],
        "user_agent": meta["user_agent"][:200],
        "extraction_model": req.extraction.model,
        "planning_model": req.planning.model,
        "reasoning_effort": req.planning.reasoning_effort or "",
    }
    try:
        with trace("packing-playground run", trace_id=f"trace_{run_id.hex}",
                   group_id=meta["client_ip"], metadata=metadata):
            items, boxes = to_pipeline_inputs(req)
            settings = _extract(req, client, items, should_stop, partial,
                                emit)
            items_p, boxes_p, messages = _prepare(items, boxes, settings,
                                                  partial, emit)
            baseline, _errors = _packer(items_p, boxes_p, settings, client,
                                        should_stop, partial, emit)
            result = _plan(req, client, messages, items_p, boxes_p, settings,
                           baseline, should_stop, partial, emit)
            outcome = _finalize(result, items_p, boxes_p, settings, partial,
                                should_stop, emit)
    finally:
        partial["timings"]["total_s"] = round(time.monotonic() - started, 3)
    out = partial_result(partial)
    out.update(outcome)
    return out
