"""Plan loop: the model returns a plan, code validates it, errors go back.

Each round the model returns a full FlatResponse as structured output. The
plan is checked in code (validation.validate_plan); errors go back as a user
message asking for a corrected plan. With a packer baseline, the packer's
plan is appended to the last user message as a "Starting plan"
(prompt_builder.starting_plan_text): the model edits a valid draft instead of
searching from scratch, and may return it unchanged. Leaving units over that
the packer placed is an error too (_coverage_errors).

Fix rounds are patches: the feedback names the units to change, adds
code-computed help (repair.repair_help: box size, tops under the unit, a
free spot; spots for units it should place but left out) and asks to copy
every other placement. Progress is repair.gap (units to move + units not
placed + other errors), not the error count; when a reply is further off
than the best plan so far, the model is sent back to that plan.

plan_with_guidance is the "guide" mode: the model returns only Guidance
(box types, item order, one rotation per item type) and
packer.pack_guided places every unit.

Once a plan is valid, validation.quality_hints looks for waste (extra boxes,
a smaller box that may work, stacking while floor space is free). If any are
found, the model gets up to `improve_rounds` extra calls to improve it. The
improved plan is kept only if it is valid and scores better
(validation.plan_score); otherwise the earlier valid plan is returned.

Structured output is used instead of a function tool because OpenAI's
/v1/chat/completions rejects function tools combined with reasoning_effort
(e.g. gpt-5.4-mini). Per round it costs the same: one full plan.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import openai
from openai import OpenAI
from pydantic import ValidationError

from .packer import pack_guided
from .prompt_builder import starting_plan_text
from .repair import gap, repair_help
from .schemas import FlatResponse, Guidance, RotationChoice
from .validation import fill_heights, plan_score, quality_hints, validate_plan

MAX_ERRORS_SHOWN = 20


@dataclass
class PlanResult:
    flat: FlatResponse | None  # best valid plan, else last parsed (or None)
    errors: list[str]  # empty when flat passed validation
    rounds: int  # model calls made, including improvement calls
    hints: list[str] = field(default_factory=list)  # left on returned plan
    improved: bool = False  # an improvement call beat the first valid plan
    source: str = "model"  # "model" or "packer" (code plan was better)
    prompt_tokens: int = 0  # input, summed over all calls
    completion_tokens: int = 0  # output, includes reasoning tokens
    reasoning_tokens: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    # The model's own outcome, captured before the packer can replace it
    model_flat: FlatResponse | None = None
    model_errors: list[str] = field(default_factory=list)
    model_hints: list[str] = field(default_factory=list)
    # a call hit max_completion_tokens; the loop stops (a retry with a
    # longer history hits the same limit)
    cut_off: bool = False

    @property
    def ok(self) -> bool:
        return self.flat is not None and not self.errors

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class RunCancelled(Exception):
    """should_stop() returned true; .result is the partial PlanResult
    (None when cancelled before planning started)."""

    def __init__(self, result: PlanResult | None = None):
        super().__init__("run cancelled")
        self.result = result


def _bullets(lines: list[str]) -> str:
    shown = "\n".join(f"- {line}" for line in lines[:MAX_ERRORS_SHOWN])
    more = len(lines) - MAX_ERRORS_SHOWN
    return shown + (f"\n- ...and {more} more" if more > 0 else "")


def _error_feedback(errors: list[str], seeded: bool = False,
                    help_lines: list[str] = ()) -> str:
    text = f"validate_plan: INVALID ({len(errors)} errors):\n{_bullets(errors)}\n"
    if help_lines:
        text += f"Help (computed in code):\n{_bullets(list(help_lines))}\n"
        text += ("Change only the units named above; copy every other "
                 "placement unchanged. Return the full corrected plan.")
    else:
        text += "Fix exactly these and return the full corrected plan."
    if seeded:
        text += " If you cannot fix them, return the Starting plan unchanged."
    return text


def _back_to_best(best: FlatResponse, best_errors: list[str],
                  worse_gap: int, best_gap: int, help_lines: list[str],
                  seeded: bool = False) -> str:
    return (
        f"Your last plan is further from valid ({worse_gap} units to fix or "
        f"place) than an earlier plan ({best_gap}). Go back to that plan:\n"
        f"{best.model_dump_json()}\n"
        + _error_feedback(best_errors, seeded=seeded, help_lines=help_lines)
    )


def _hint_feedback(hints: list[str]) -> str:
    return (
        f"validate_plan: VALID, but it can likely be improved:\n"
        f"{_bullets(hints)}\n"
        "Return an improved full plan that is still valid. If you cannot "
        "improve it, return the same plan."
    )


_CONFIG_ERRORS = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.NotFoundError,
    openai.BadRequestError,
)


def _count_usage(result: PlanResult, usage) -> dict[str, int]:
    """Add one call's usage to the result; returns this call's counts."""
    counts = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    if usage:
        counts["input_tokens"] = usage.prompt_tokens or 0
        counts["output_tokens"] = usage.completion_tokens or 0
        details = getattr(usage, "completion_tokens_details", None)
        if details and details.reasoning_tokens:
            counts["reasoning_tokens"] = details.reasoning_tokens
        result.prompt_tokens += counts["input_tokens"]
        result.completion_tokens += counts["output_tokens"]
        result.reasoning_tokens += counts["reasoning_tokens"]
    return counts


def _call_failed(result: PlanResult, exc: Exception) -> None:
    result.errors = [f"model call failed: {type(exc).__name__}: "
                     f"{str(exc)[:300]}"]


def _ask(client, model, history, result, create_kwargs,
         on_call=None, response_format=FlatResponse):
    """One model call; appends the reply to history and counts tokens.

    on_call(model, model_config, usage) runs after every call.
    """
    result.rounds += 1
    model_config = ({"reasoning_effort": create_kwargs["reasoning_effort"]}
                    if "reasoning_effort" in create_kwargs else {})
    try:
        response = client.chat.completions.parse(
            model=model,
            messages=history,
            response_format=response_format,
            **create_kwargs,
        )
    except ValidationError as exc:
        result.errors = [f"plan does not match the schema: {exc}"]
        return None
    except _CONFIG_ERRORS:
        raise  # wrong key, model or parameter: the run fails with it
    except (openai.LengthFinishReasonError,
            openai.ContentFilterFinishReasonError) as exc:
        completion = getattr(exc, "completion", None)
        counts = _count_usage(result, getattr(completion, "usage", None))
        if on_call:
            on_call(model, model_config, counts)
        if isinstance(exc, openai.LengthFinishReasonError):
            result.cut_off = True
            result.errors = [
                "model call failed: LengthFinishReasonError: the model used "
                f"all {counts['output_tokens']} output tokens "
                f"({counts['reasoning_tokens']} on reasoning) before "
                "finishing; no retry. Try a lower reasoning effort or the "
                "\"model picks, code places\" mode."]
        else:
            _call_failed(result, exc)
        return None
    except openai.APIError as exc:
        _call_failed(result, exc)
        return None

    counts = _count_usage(result, response.usage)
    if on_call:
        on_call(model, model_config, counts)

    message = response.choices[0].message
    history.append({"role": "assistant", "content": message.content})
    if message.parsed is None:
        result.errors = [f"no plan returned: {message.refusal or 'empty'}"]
    return message.parsed


def _rank(flat: FlatResponse, boxes) -> tuple:
    """Lower is better: leftover units, then plan_score (boxes, volume,
    summed fill height)."""
    return (sum(r.quantity for r in flat.leftovers),
            *plan_score(flat, boxes))


def _coverage_errors(flat, baseline, boxes) -> list[str]:
    """Units the model left over although the code packer placed them.

    validate_plan only checks that placed + leftover = requested, so a plan
    that gives up ("search failed") passes. When the packer proved those
    units fit, that is an error (a fix round), not a quality hint.
    """
    if baseline is None:
        return []

    def left(plan) -> dict[int, int]:
        out: dict[int, int] = {}
        for row in plan.leftovers:
            out[row.id] = out.get(row.id, 0) + row.quantity
        return out

    names = {b["id"]: b["name"] for b in boxes}
    box_type = {b.box: b.id for b in baseline.boxes}
    mine, packer = left(flat), left(baseline)
    errors = []
    for item_id, quantity in sorted(mine.items()):
        extra = quantity - packer.get(item_id, 0)
        if extra <= 0:
            continue
        where = sorted({names[box_type[p.box]]
                        for p in baseline.placements if p.id == item_id})
        errors.append(
            f"item {item_id}: {extra} unit(s) in leftovers fit; a valid "
            f"plan places them in {', '.join(where)}. Place every unit "
            f"that fits; leftovers are only for units that cannot fit.")
    return errors


def _describe(plan, boxes) -> str:
    names = {b["id"]: b["name"] for b in boxes}
    fills = fill_heights(plan)
    parts = [f"{names[b.id]} filled to {fills.get(b.box, 0):g} in"
             for b in plan.boxes]
    left = sum(r.quantity for r in plan.leftovers)
    return (f"{len(plan.boxes)} box(es) ({'; '.join(parts)})"
            + (f", {left} units left over" if left else ""))


def _baseline_hint(flat, baseline, boxes) -> list[str]:
    """Point the model at the code packer's result when it beat this plan."""
    if baseline is None or _rank(baseline, boxes) >= _rank(flat, boxes):
        return []
    return [f"a valid plan exists with {_describe(baseline, boxes)}; yours "
            f"uses {_describe(flat, boxes)}. Lower your stacks: try other listed "
            f"rotations so more units stand side by side on the floor, and "
            f"use the strips left beside each grid."]


def plan_with_validation(
    client: OpenAI,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    *,
    max_rounds: int = 3,
    improve_rounds: int = 1,
    settings=None,
    baseline: FlatResponse | None = None,
    seed: bool = True,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_call: Callable[[str, dict[str, Any], dict[str, int]], None] | None = None,
    **create_kwargs: Any,
) -> PlanResult:
    """Run plan → validate → fix rounds, then optional improvement rounds.

    max_rounds:     calls allowed to reach a first valid plan.
    improve_rounds: extra calls spent on quality hints once a plan is valid
                    (0 disables). Total calls <= max_rounds + improve_rounds.
    messages should use prompt_builder.SYSTEM_PROMPT.
    settings:       PackingSettings; its box/order/carrier rules are
                    validated too (item rules ride on the prepared items).
    baseline:       a valid plan from packer.pack. It is appended to the last
                    user message as the Starting plan, it is the target in the
                    hints, and it is returned instead (source="packer") when
                    it beats the model's plan or the model has none.
    seed:           with a baseline, send it as the Starting plan to improve
                    (True) or let the model plan from scratch (False); the
                    baseline is still the target and the fallback.
    on_event:       called after every call with {"type": "round", "n",
                    "kind", "valid", "errors_count"}.
    should_stop:    checked before every call; true raises RunCancelled with
                    the partial result.
    on_call:        called after every call with (model, model_config,
                    usage counts); model_config holds reasoning_effort only.
    create_kwargs pass through to chat.completions.parse
    (e.g. reasoning_effort="low").
    """
    history = [dict(m) for m in messages]
    seeded = seed and baseline is not None
    if seeded:
        last = history[-1]
        last["content"] = (f"{last['content']}\n\n"
                           f"{starting_plan_text(baseline, boxes)}")
    result = PlanResult(flat=None, errors=[], rounds=0, history=history)

    def ask() -> FlatResponse | None:
        if should_stop is not None and should_stop():
            raise RunCancelled(result)
        return _ask(client, model, history, result, create_kwargs, on_call)

    def emit(kind: str, valid: bool, errors_count: int) -> None:
        if on_event is not None:
            on_event({"type": "round", "n": result.rounds, "kind": kind,
                      "valid": valid, "errors_count": errors_count})

    # closest invalid plan so far: (plan, errors, repair.gap)
    best: tuple[FlatResponse, list[str], int] | None = None
    for _ in range(max_rounds):
        flat = ask()
        if flat is None:
            emit("fix", False, len(result.errors))
            if result.cut_off:
                return _prefer_baseline(result, baseline, boxes)
            history.append({"role": "user", "content": _error_feedback(
                result.errors, seeded=seeded)})
            continue
        result.flat = flat
        result.errors = (validate_plan(flat, items, boxes, settings)
                         or _coverage_errors(flat, baseline, boxes))
        emit("fix", not result.errors, len(result.errors))
        if not result.errors:
            break
        distance = gap(flat, items, result.errors, baseline)
        if best is not None and distance > best[2]:
            content = _back_to_best(
                best[0], best[1], distance, best[2],
                repair_help(best[0], items, boxes, best[1], settings,
                            baseline),
                seeded=seeded)
        else:
            best = (flat, result.errors, distance)
            content = _error_feedback(
                result.errors, seeded=seeded,
                help_lines=repair_help(flat, items, boxes, result.errors,
                                       settings, baseline))
        history.append({"role": "user", "content": content})
    else:
        if best is not None:  # report the closest plan, not the last
            result.flat, result.errors, _ = best
        return _prefer_baseline(result, baseline, boxes)

    result.hints = (_baseline_hint(result.flat, baseline, boxes)
                    + quality_hints(result.flat, items, boxes, settings))
    for _ in range(improve_rounds):
        if not result.hints:
            break
        history.append({"role": "user",
                        "content": _hint_feedback(result.hints)})
        candidate = ask()
        if candidate is None:
            emit("improve", False, len(result.errors))
            result.errors = []  # failed improvement; keep the valid plan
            break
        errs = (validate_plan(candidate, items, boxes, settings)
                or _coverage_errors(candidate, baseline, boxes))
        emit("improve", not errs, len(errs))
        if errs or _rank(candidate, boxes) >= _rank(result.flat, boxes):
            result.errors = []  # failed improvement; keep the valid plan
            break
        result.flat = candidate
        result.improved = True
        result.hints = (_baseline_hint(candidate, baseline, boxes)
                        + quality_hints(candidate, items, boxes, settings))

    return _prefer_baseline(result, baseline, boxes)


def _prefer_baseline(result: PlanResult, baseline, boxes) -> PlanResult:
    """Return the packer's plan when the model has no valid plan or a worse
    one. Its notes say the model's plan (and any soft instructions) wasn't
    used."""
    result.model_flat = result.flat
    result.model_errors = list(result.errors)
    result.model_hints = list(result.hints)
    if baseline is None:
        return result
    if result.ok and _rank(result.flat, boxes) <= _rank(baseline, boxes):
        return result
    why = ("the model's plan was invalid" if not result.ok
           else "it beat the model's plan")
    result.flat = baseline.model_copy(update={"notes": [
        *baseline.notes,
        f"Code packer's plan used because {why}; Additional instructions "
        f"were not applied.",
    ]})
    result.errors = []
    result.hints = []
    result.source = "packer"
    return result


def guidance_from_plan(flat: FlatResponse) -> Guidance:
    """The choices a plan implies: its box types, item ids in first-placed
    order, and each item's first rotation (FakeClient and tests)."""
    order, rotations = [], {}
    for row in sorted(flat.placements, key=lambda r: (r.box, r.seq)):
        if row.id not in rotations:
            order.append(row.id)
            rotations[row.id] = row.dims
    return Guidance(
        boxes=[b.id for b in flat.boxes], order=order,
        rotations=[RotationChoice(id=i, dims=d) for i, d in rotations.items()],
        notes=list(flat.notes))


def _guidance_feedback(errors: list[str], flat, boxes) -> str:
    placed = (f"Placement from your choices: {_describe(flat, boxes)}.\n"
              if flat is not None else "")
    return (f"{placed}INVALID ({len(errors)} errors):\n{_bullets(errors)}\n"
            "Change the box types, the item order or the rotations and "
            "return new choices.")


def plan_with_guidance(
    client: OpenAI,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    *,
    max_rounds: int = 3,
    settings=None,
    baseline: FlatResponse | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    on_call: Callable[[str, dict[str, Any], dict[str, int]], None] | None = None,
    **create_kwargs: Any,
) -> PlanResult:
    """Guide mode: the model chooses, code places.

    Each round the model returns Guidance (box types, item order, one
    rotation per item type; messages should use
    prompt_builder.GUIDE_SYSTEM_PROMPT) and packer.pack_guided places every
    unit. Invalid choices or a placement that fails validation go back as
    feedback. A valid placement that ranks below the packer's baseline gets
    another round to beat it. The best valid placement is kept; the model's
    notes go on it. Hooks and the baseline fallback work as in
    plan_with_validation (round kinds: "fix" until valid, then "improve").
    """
    history = [dict(m) for m in messages]
    result = PlanResult(flat=None, errors=[], rounds=0, history=history)
    best: FlatResponse | None = None

    def emit(kind: str, valid: bool, errors_count: int) -> None:
        if on_event is not None:
            on_event({"type": "round", "n": result.rounds, "kind": kind,
                      "valid": valid, "errors_count": errors_count})

    for _ in range(max_rounds):
        if should_stop is not None and should_stop():
            raise RunCancelled(result)
        kind = "fix" if best is None else "improve"
        guidance = _ask(client, model, history, result, create_kwargs,
                        on_call, response_format=Guidance)
        if guidance is None:
            emit(kind, False, len(result.errors))
            if result.cut_off:
                if best is not None:
                    result.flat, result.errors = best, []
                break
            if best is not None:
                result.errors = []  # keep the valid placement
            history.append({"role": "user",
                            "content": _error_feedback(result.errors or [
                                "no choices returned"])})
            continue
        flat, errors = pack_guided(
            items, boxes, settings, guidance.boxes, guidance.order,
            {r.id: r.dims for r in guidance.rotations})
        if flat is not None:
            flat = flat.model_copy(update={"notes": [
                *guidance.notes,
                "Placed by code from the model's choices (box types, item "
                "order, rotations): load units in seq order."]})
            errors = errors or _coverage_errors(flat, baseline, boxes)
        emit(kind, not errors, len(errors))
        if errors:
            if best is None:
                result.flat, result.errors = flat, errors
            history.append({"role": "user", "content":
                            _guidance_feedback(errors, flat, boxes)})
            continue
        if best is not None and _rank(flat, boxes) < _rank(best, boxes):
            result.improved = True
        if best is None or _rank(flat, boxes) < _rank(best, boxes):
            best = flat
        result.flat, result.errors = best, []
        if baseline is None or _rank(best, boxes) <= _rank(baseline, boxes):
            break
        history.append({"role": "user", "content": (
            f"Valid: {_describe(flat, boxes)}. The code packer reached "
            f"{_describe(baseline, boxes)}. Try other box types, rotations "
            f"or item order to beat it, or return the same choices.")})
    if best is not None:
        result.hints = quality_hints(best, items, boxes, settings)
    return _prefer_baseline(result, baseline, boxes)
