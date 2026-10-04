"""Job 3, check: judge whether the soft instructions are honoured.

The model reads the instructions, a table of the model plan and code facts
(packer.preference_report lines) and gives one verdict per instruction.
Skipped with no soft instructions or no valid model plan; the fallback is
`cannot_tell` for every instruction.
"""

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..packer import _nothing_above
from ..precompute import EPS
from ..schemas import FlatResponse
from ..tabular import to_table
from ..validation import _overlap
from .base import (Check, JobConfig, JobResult, call_parsed, failed_checks,
                   skipped_result)

JOB = "check"
PROMPT_VERSION = "check-v1"
USER_TEMPLATE_VERSION = "check-user-v1"

SYSTEM_PROMPT = """\
You judge whether a packing plan honours the user's instructions. You get the numbered instructions (from 0), a table of the plan and code facts about where units went.

For each instruction answer one verdict:
- honoured: the plan follows it.
- not_honoured: the plan clearly does not.
- cannot_tell: the plan and the facts do not show it, or the instruction is not about the packing.
Give a short reason that cites the plan or a code fact.

Coordinates: X = height (up), Y = depth (back to front), Z = span (left to right); left = small Z, back = small Y. pos is a unit's min corner, dims its extents. The code facts are computed exactly: trust them over your own reading of the table. Answer cannot_tell rather than guess. Return exactly one verdict per instruction index."""


class Verdict(BaseModel):
    index: int = Field(description="Instruction number, from 0")
    verdict: Literal["honoured", "not_honoured", "cannot_tell"]
    reason: str = Field(description="Short reason")


class CheckAnswer(BaseModel):
    verdicts: list[Verdict] = Field(
        description="One verdict per instruction")


ANSWER_SCHEMA = CheckAnswer.model_json_schema()


def _plan_rows(flat: FlatResponse, items, boxes,
               preferences: Mapping[int, Mapping[str, str]]) -> list[dict]:
    names = {i["id"]: i["name"] for i in items}
    box_names = {b["id"]: b["name"] for b in boxes}
    box_type = {b.box: b.id for b in flat.boxes}
    top_ids = {i for i, p in preferences.items()
               if p.get("level", "any") == "top"}
    rows = []
    for row in sorted(flat.placements, key=lambda r: (r.box, r.seq)):
        mates = [r for r in flat.placements if r.box == row.box]
        under = sorted(
            o.seq for o in mates
            if o is not row and _overlap(row, o, (1, 2))
            and abs(o.pos[0] + o.dims[0] - row.pos[0]) <= EPS)
        rows.append({
            "box": row.box, "name": box_names[box_type[row.box]],
            "seq": row.seq, "item": names[row.id],
            "pos": "×".join(f"{v:g}" for v in row.pos),
            "dims": "×".join(f"{v:g}" for v in row.dims),
            "support": ("floor" if row.pos[0] <= EPS else
                        "on seq " + ",".join(map(str, under))),
            "on_top": _nothing_above(row, mates, top_ids)})
    return rows


def build_messages(soft_instructions: Sequence[str], flat: FlatResponse,
                   items, boxes, preferences: Mapping[int, Mapping[str, str]],
                   report: Sequence[Mapping[str, Any]]) -> list[dict]:
    numbered = "\n".join(f"{n}: {text}"
                         for n, text in enumerate(soft_instructions))
    facts = "\n".join(
        f"- {r['name']} (side {r['side']}, level {r['level']}): {r['detail']}"
        for r in report) or "- none"
    content = (f"Instructions:\n{numbered}\n\n"
               + to_table("plan", _plan_rows(flat, items, boxes, preferences))
               + f"\n\nCode facts:\n{facts}")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content}]


def checks(parsed: CheckAnswer | None, count: int) -> list[Check]:
    if parsed is None:
        return [Check("parsed", False, "the reply is not a valid answer")]
    seen = sorted(v.index for v in parsed.verdicts)
    ok = seen == list(range(count))
    return [Check("parsed", True),
            Check("covers_all", ok, "" if ok else
                  f"indices {seen}, expected 0..{count - 1}")]


def cannot_tell(count: int, reason: str) -> list[dict]:
    return [{"index": n, "verdict": "cannot_tell", "reason": reason}
            for n in range(count)]


def run_check(client, cfg: JobConfig, soft_instructions: Sequence[str],
              flat: FlatResponse | None, items, boxes,
              preferences: Mapping[int, Mapping[str, str]],
              report: Sequence[Mapping[str, Any]]) -> JobResult:
    if not soft_instructions or flat is None:
        return skipped_result(JOB, cfg, PROMPT_VERSION, [])
    messages = build_messages(soft_instructions, flat, items, boxes,
                              preferences, report)
    result = JobResult(job=JOB, status="ok", output=None, model=cfg.model,
                       reasoning_effort=cfg.reasoning_effort,
                       prompt_version=PROMPT_VERSION, messages=list(messages))
    outcome = call_parsed(client, cfg, messages, CheckAnswer)
    result.add(outcome)
    parsed: CheckAnswer | None = outcome.parsed
    result.answer = parsed.model_dump(mode="json")["verdicts"] if parsed else None
    result.checks = checks(parsed, len(soft_instructions))
    if outcome.error is None and all(c.passed for c in result.checks):
        result.output = sorted(result.answer, key=lambda v: v["index"])
        return result
    result.status = "fallback"
    result.error = outcome.error or (
        f"checks failed: {failed_checks(result.checks)}")
    result.output = cannot_tell(len(soft_instructions), "check unavailable")
    return result
