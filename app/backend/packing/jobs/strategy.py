"""Job 2, strategy: the model picks box types, item order, rotations and
simple placement preferences; code places every unit (packer.pack_guided),
validates it and compares it with the packer's plan.

A second call happens only when the first does not parse or gives invalid
choices, an invalid plan or extra leftovers (planner._guidance_feedback goes
back). With no valid plan after at most two calls, the packer's plan is used
(status "fallback").
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..packer import pack_guided, preference_report
from ..planner import _CONFIG_ERRORS, _coverage_errors, _guidance_feedback
from ..prompt_builder import build_user_prompt
from ..schemas import FlatResponse, RotationChoice
from ..settings import PackingSettings, rules_text
from ..validation import plan_score
from .base import (VOLUME_MARGIN, Check, JobConfig, JobResult, call_parsed)

JOB = "strategy"
PROMPT_VERSION = "strategy-v1"
USER_TEMPLATE_VERSION = "strategy-user-v1"
MAX_CALLS = 2

SYSTEM_PROMPT = """\
You are a 3D packing planner. You choose; code places. Return your choices as JSON matching the schema:
- boxes: the box type id for each physical box, in the order code opens them.
- order: item type ids in loading order (bottom first).
- rotations: one rotation [x, y, z] per item type, copied from the rotations table.
- preferences: where an item type should go, only when an Additional instruction asks for it. side: left, right, back, front or any; level: bottom, top or any. Empty list if none.
- reason: one sentence on why you chose these boxes, order and rotations.

How code places: it opens your boxes in order. Item types are placed in your order, except that those with level bottom go first and those with level top go last. Each unit goes to the lowest, then back-most, then left-most free spot where it fits inside the box, overlaps nothing, rests fully on the floor or on unit tops at one height, and keeps every Packing rule. A side preference makes the unit take the spot nearest that side: left = small Z, right = large Z, back = small Y, front = large Y. Level top puts the unit on the highest valid spot, with nothing but other top units above it; level bottom puts it on the floor. Units that fit in no box are left over. The result is checked by validate_plan and compared with a code packer's own plan.

Priorities, in order:
1. Pack every unit. If impossible, maximize units packed.
2. Fewest boxes.
3. Least shipped volume: each box is cut down to its contents, so what ships is the cut-down height × the floor area.
4. Follow the Additional instructions through preferences, without costing more boxes or much more shipped volume.
5. Low, even fill.

Coordinates: X = height (up), Y = depth (back to front), Z = span (left to right). Dimensions and rotations are [x, y, z]; box dimensions are INTERNAL.

Input (tables: a header row, then one row per record):
- Packing rules (if any): hard rules, checked in code. Additional instructions (if any): wishes to follow through your choices and preferences unless they conflict with a rule.
- items and boxes (weightMax = contents limit; stock = boxes available, blank = unlimited).
- rotations: for each item, the [x, y, z] rotations that fit at least one box.
- unpackable: items that fit no box (code leaves them over).
- box_estimates: what the code packer reached with one box of each type on the whole order: fits_all, units placed, fill_height, shipped_volume. Start from the fits_all box with the lowest shipped_volume.

How to choose:
1. Box: the fits_all box type with the lowest shipped_volume, unless you can see a lower arrangement elsewhere. Several boxes only if no type fits all.
2. Order: item types with large, flat footprints first (they form the base), then items that make columns of equal height, small items last.
3. Rotations: lay items flat (smallest side up) when they get floor of their own; stand flat items on edge when the floor runs short.

If your choices fail, you get the placement result and errors; change box types, order, rotations or preferences and return new choices."""


class Preference(BaseModel):
    id: int = Field(description="Item type id")
    side: Literal["any", "left", "right", "back", "front"] = Field(
        description="Preferred side of the box (left = small Z, back = small Y)")
    level: Literal["any", "bottom", "top"] = Field(
        description="bottom = on the floor, top = above everything else")


class StrategyAnswer(BaseModel):
    boxes: list[int] = Field(
        description="Box type id for each physical box, in opening order")
    order: list[int] = Field(
        description="Item type ids in loading order (bottom first)")
    rotations: list[RotationChoice] = Field(
        description="One rotation per item type")
    preferences: list[Preference] = Field(
        description="Placement preferences asked for by the instructions")
    reason: str = Field(description="One sentence on the choices")


ANSWER_SCHEMA = StrategyAnswer.model_json_schema()


@dataclass
class StrategyRun:
    result: JobResult
    flat: FlatResponse | None  # the valid model plan, else None
    errors: list[str]  # why there is none (empty when valid)
    preferences: dict[int, dict[str, str]] = field(default_factory=dict)
    report: list[dict[str, Any]] = field(default_factory=list)  # model plan


def strategy_answer_from_plan(flat: FlatResponse,
                              reason: str = "fake") -> StrategyAnswer:
    """The choices a plan implies (no preferences): FakeClient's answer and
    the fallback output."""
    order, rotations = [], {}
    for row in sorted(flat.placements, key=lambda r: (r.box, r.seq)):
        if row.id not in rotations:
            order.append(row.id)
            rotations[row.id] = row.dims
    return StrategyAnswer(
        boxes=[b.id for b in flat.boxes], order=order,
        rotations=[RotationChoice(id=i, dims=d)
                   for i, d in rotations.items()],
        preferences=[], reason=reason)


def build_messages(items_p, boxes_p, settings: PackingSettings,
                   estimates) -> list[dict]:
    prompt = build_user_prompt(
        items_p, boxes_p, "\n".join(settings.soft_instructions),
        rules_text(items_p, boxes_p, settings), estimates)
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}]


def preferences_of(answer: StrategyAnswer) -> dict[int, dict[str, str]]:
    return {p.id: {"side": p.side, "level": p.level}
            for p in answer.preferences}


def _evaluate(answer: StrategyAnswer, items_p, boxes_p, settings, baseline):
    """Place the answer and run the checks after `parsed`.
    Returns (flat | None, errors, checks, valid, report)."""
    prefs = preferences_of(answer)
    flat, errors = pack_guided(
        items_p, boxes_p, settings, answer.boxes, answer.order,
        {r.id: r.dims for r in answer.rotations}, prefs)
    if flat is None:
        return None, errors, [Check("choices_valid", False,
                                    "; ".join(errors[:3]))], False, []
    checks = [Check("choices_valid", True),
              Check("plan_valid", not errors, "; ".join(errors[:3]))]
    coverage = _coverage_errors(flat, baseline, boxes_p)
    checks.append(Check("no_extra_leftovers", not coverage,
                        "; ".join(coverage[:3])))
    report: list[dict[str, Any]] = []
    if not errors:
        if baseline is None:
            checks += [Check("no_extra_boxes", True, "no packer plan"),
                       Check("volume_within_margin", True, "no packer plan")]
        else:
            mine, theirs = len(flat.boxes), len(baseline.boxes)
            checks.append(Check("no_extra_boxes", mine <= theirs,
                                f"{mine} boxes, the packer {theirs}"))
            vol, base_vol = (plan_score(flat, boxes_p)[1],
                             plan_score(baseline, boxes_p)[1])
            checks.append(Check(
                "volume_within_margin", vol <= VOLUME_MARGIN * base_vol + 1e-9,
                f"{vol:g} in³, the packer {base_vol:g} in³ "
                f"(margin {VOLUME_MARGIN})"))
        report = preference_report(flat, items_p, boxes_p, prefs)
        bad = [r["detail"] for r in report if r["honoured"] < r["total"]]
        checks.append(Check("preferences_honoured", not bad,
                            "; ".join(bad) if bad else (
                                "; ".join(r["detail"] for r in report)
                                or "none")))
    errors = errors or coverage
    return flat, errors, checks, not errors, report


def run_strategy(client, cfg: JobConfig, items_p, boxes_p,
                 settings: PackingSettings, estimates,
                 baseline: FlatResponse | None) -> StrategyRun:
    messages = build_messages(items_p, boxes_p, settings, estimates)
    result = JobResult(job=JOB, status="fallback", output=None,
                       model=cfg.model, reasoning_effort=cfg.reasoning_effort,
                       prompt_version=PROMPT_VERSION, messages=list(messages))
    run = StrategyRun(result=result, flat=None, errors=[])
    for _ in range(MAX_CALLS):
        try:
            outcome = call_parsed(client, cfg, result.messages,
                                  StrategyAnswer)
        except _CONFIG_ERRORS as exc:
            if result.calls:  # keep the earlier calls' usage for the run
                exc.job_result = result
            raise
        result.add(outcome)
        answer: StrategyAnswer | None = outcome.parsed
        if answer is None:
            result.checks = [Check("parsed", False, outcome.error or "")]
            result.error = outcome.error
            run.errors = [f"model call failed: {outcome.error}"]
            if outcome.cut_off:
                break
            feedback = (f"Your reply could not be used: {outcome.error}\n"
                        "Return the choices again.")
        else:
            flat, errors, checks, valid, report = _evaluate(
                answer, items_p, boxes_p, settings, baseline)
            result.answer = answer.model_dump(mode="json")
            result.checks = [Check("parsed", True), *checks]
            run.errors = errors
            if valid:
                run.flat, run.report = flat, report
                run.preferences = preferences_of(answer)
                result.status, result.error = "ok", None
                result.output = result.answer
                return run
            result.error = "no valid plan: " + "; ".join(errors[:3])
            feedback = _guidance_feedback(errors, flat, boxes_p)
        result.messages.append({"role": "user", "content": feedback})
    run.preferences = (preferences_of(StrategyAnswer.model_validate(
        result.answer)) if result.answer else {})
    if baseline is not None:
        result.output = strategy_answer_from_plan(
            baseline, "code packer's plan").model_dump(mode="json")
    return run
