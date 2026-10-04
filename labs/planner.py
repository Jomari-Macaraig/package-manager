"""Plan loop: the model returns a plan, code validates it, errors go back.

Each round the model returns a full FlatResponse as structured output. The
plan is checked in code (validation.validate_plan); errors go back as a user
message asking for a corrected plan.

Once a plan is valid, validation.quality_hints looks for waste (extra boxes,
a smaller box that may work, stacking while floor space is free). If any are
found, the model gets up to `improve_rounds` extra calls to improve it. The
improved plan is kept only if it is valid and scores better
(validation.plan_score); otherwise the earlier valid plan is returned.

Structured output is used instead of a function tool because OpenAI's
/v1/chat/completions rejects function tools combined with reasoning_effort
(e.g. gpt-5.4-mini). Per round it costs the same: one full plan.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from schemas import FlatResponse
from validation import fill_heights, plan_score, quality_hints, validate_plan

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

    @property
    def ok(self) -> bool:
        return self.flat is not None and not self.errors

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _bullets(lines: list[str]) -> str:
    shown = "\n".join(f"- {line}" for line in lines[:MAX_ERRORS_SHOWN])
    more = len(lines) - MAX_ERRORS_SHOWN
    return shown + (f"\n- ...and {more} more" if more > 0 else "")


def _error_feedback(errors: list[str]) -> str:
    return (
        f"validate_plan: INVALID ({len(errors)} errors):\n{_bullets(errors)}\n"
        "Fix exactly these and return the full corrected plan."
    )


def _hint_feedback(hints: list[str]) -> str:
    return (
        f"validate_plan: VALID, but it can likely be improved:\n"
        f"{_bullets(hints)}\n"
        "Return an improved full plan that is still valid. If you cannot "
        "improve it, return the same plan."
    )


def _ask(client, model, history, result, create_kwargs) -> FlatResponse | None:
    """One model call; appends the reply to history and counts tokens."""
    result.rounds += 1
    try:
        response = client.chat.completions.parse(
            model=model,
            messages=history,
            response_format=FlatResponse,
            **create_kwargs,
        )
    except ValidationError as exc:
        result.errors = [f"plan does not match the schema: {exc}"]
        return None

    usage = response.usage
    if usage:
        result.prompt_tokens += usage.prompt_tokens or 0
        result.completion_tokens += usage.completion_tokens
        details = usage.completion_tokens_details
        if details and details.reasoning_tokens:
            result.reasoning_tokens += details.reasoning_tokens

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


def _baseline_hint(flat, baseline, boxes) -> list[str]:
    """Point the model at the code packer's result when it beat this plan."""
    if baseline is None or _rank(baseline, boxes) >= _rank(flat, boxes):
        return []

    def describe(plan):
        names = {b["id"]: b["name"] for b in boxes}
        fills = fill_heights(plan)
        parts = [f"{names[b.id]} filled to {fills.get(b.box, 0):g} in"
                 for b in plan.boxes]
        left = sum(r.quantity for r in plan.leftovers)
        return (f"{len(plan.boxes)} box(es) ({'; '.join(parts)})"
                + (f", {left} units left over" if left else ""))

    return [f"a valid plan exists with {describe(baseline)}; yours uses "
            f"{describe(flat)}. Lower your stacks: try other listed "
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
    **create_kwargs: Any,
) -> PlanResult:
    """Run plan → validate → fix rounds, then optional improvement rounds.

    max_rounds:     calls allowed to reach a first valid plan.
    improve_rounds: extra calls spent on quality hints once a plan is valid
                    (0 disables). Total calls <= max_rounds + improve_rounds.
    messages should use prompt_builder.SYSTEM_PROMPT.
    settings:       PackingSettings; its box/order/carrier rules are
                    validated too (item rules ride on the prepared items).
    baseline:       a valid plan from packer.pack. It is the target in the
                    hints, and it is returned instead (source="packer") when
                    it beats the model's plan or the model has none.
    create_kwargs pass through to chat.completions.parse
    (e.g. reasoning_effort="low").
    """
    history = [dict(m) for m in messages]
    result = PlanResult(flat=None, errors=[], rounds=0, history=history)

    for _ in range(max_rounds):
        flat = _ask(client, model, history, result, create_kwargs)
        if flat is not None:
            result.flat = flat
            result.errors = validate_plan(flat, items, boxes, settings)
            if not result.errors:
                break
        history.append({"role": "user",
                        "content": _error_feedback(result.errors)})
    else:
        return _prefer_baseline(result, baseline, boxes)

    result.hints = (_baseline_hint(result.flat, baseline, boxes)
                    + quality_hints(result.flat, items, boxes, settings))
    for _ in range(improve_rounds):
        if not result.hints:
            break
        history.append({"role": "user",
                        "content": _hint_feedback(result.hints)})
        candidate = _ask(client, model, history, result, create_kwargs)
        if (candidate is None or validate_plan(candidate, items, boxes, settings)
                or _rank(candidate, boxes) >= _rank(result.flat, boxes)):
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
