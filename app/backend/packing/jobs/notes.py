"""Job 4, notes: loading steps for the person packing, per box.

Skipped when there is no final plan. The fallback is code's template notes
("Load N × Name" per item type, in seq order).
"""

import re
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, Field

from ..precompute import EPS
from ..schemas import FlatResponse
from ..tabular import to_table
from ..validation import _overlap
from .base import (Check, JobConfig, JobResult, call_parsed, failed_checks,
                   skipped_result)

JOB = "notes"
PROMPT_VERSION = "notes-v1"
USER_TEMPLATE_VERSION = "notes-user-v1"
MIN_STEPS, MAX_STEPS = 1, 8

SYSTEM_PROMPT = """\
You write loading steps for the person packing each box of a finished packing plan.

For every box write 1 to 8 short imperative steps, in loading order (seq 1 first). Use only the item names listed for that box and state how many units each step loads ("Load 3 × Kettle"). Mention where a unit goes only when it helps ("on top of the shoe boxes"). Follow the user's instructions where they concern loading. Do not state cut-down heights or box sizes; code adds them. Return exactly one entry per box number."""


class BoxNotes(BaseModel):
    box: int = Field(description="Physical box number")
    steps: list[str] = Field(description="1-8 short imperative steps")


class NotesAnswer(BaseModel):
    boxes: list[BoxNotes] = Field(description="One entry per box")


ANSWER_SCHEMA = NotesAnswer.model_json_schema()


def _normalise(name: str) -> str:
    """Lower-case, parentheses and their contents dropped, spaces collapsed:
    "Tape block of 4 (1×1×4)" -> "tape block of 4"."""
    name = re.sub(r"\([^)]*\)", " ", name.lower())
    return re.sub(r"\s+", " ", name).strip()


def _units_by_box(flat: FlatResponse, items) -> dict[int, Counter]:
    names = {i["id"]: i["name"] for i in items}
    out: dict[int, Counter] = {b.box: Counter() for b in flat.boxes}
    for row in flat.placements:
        out[row.box][names[row.id]] += 1
    return out


def template_notes(flat: FlatResponse, items) -> list[dict]:
    """Code's notes: per box, "Load N × Name" per item type in the order
    each type is first loaded."""
    names = {i["id"]: i["name"] for i in items}
    notes = []
    for box in sorted(flat.boxes, key=lambda b: b.box):
        counts: dict[str, int] = {}
        for row in sorted((r for r in flat.placements if r.box == box.box),
                          key=lambda r: r.seq):
            counts[names[row.id]] = counts.get(names[row.id], 0) + 1
        notes.append({"box": box.box, "steps": [
            f"Load {n} × {name}" for name, n in counts.items()]})
    return notes


def build_messages(flat: FlatResponse, items, boxes,
                   soft_instructions: Sequence[str]) -> list[dict]:
    names = {i["id"]: i["name"] for i in items}
    box_names = {b["id"]: b["name"] for b in boxes}
    box_rows = [{"box": b.box, "name": box_names[b.id]}
                for b in sorted(flat.boxes, key=lambda b: b.box)]
    units = []
    for row in sorted(flat.placements, key=lambda r: (r.box, r.seq)):
        under = sorted(
            o.seq for o in flat.placements
            if o.box == row.box and o is not row
            and _overlap(row, o, (1, 2))
            and abs(o.pos[0] + o.dims[0] - row.pos[0]) <= EPS)
        units.append({"box": row.box, "seq": row.seq,
                      "item": names[row.id],
                      "support": ("floor" if row.pos[0] <= EPS else
                                  "on seq " + ",".join(map(str, under)))})
    wishes = "\n".join(f"- {t}" for t in soft_instructions) or "- none"
    content = (to_table("boxes", box_rows) + "\n\n"
               + to_table("units", units)
               + f"\n\nInstructions:\n{wishes}")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content}]


def checks(parsed: NotesAnswer | None, flat: FlatResponse,
           items) -> list[Check]:
    """parsed, one_entry_per_box, step_count, mentions_items, counts_match."""
    if parsed is None:
        return [Check("parsed", False, "the reply is not a valid answer")]
    boxes = sorted(b.box for b in flat.boxes)
    got = sorted(b.box for b in parsed.boxes)
    out = [Check("parsed", True),
           Check("one_entry_per_box", got == boxes,
                 "" if got == boxes else f"boxes {got}, expected {boxes}")]
    counts = _units_by_box(flat, items)
    bad_steps, missing, wrong = [], [], []
    for entry in parsed.boxes:
        if not MIN_STEPS <= len(entry.steps) <= MAX_STEPS:
            bad_steps.append(f"box {entry.box}: {len(entry.steps)} steps")
        # one expected total per normalised name (the text can't tell two
        # items with the same name apart)
        expected: Counter = Counter()
        shown: dict[str, str] = {}
        for name, n in counts.get(entry.box, {}).items():
            expected[_normalise(name)] += n
            shown.setdefault(_normalise(name), name)
        seen: set[str] = set()
        said: dict[str, list[int]] = {}
        for step in entry.steps:
            text = _normalise(step)
            found = []  # (position, name, quantity or None)
            # longest names first; blank each match so "Jar" skips "Jar Lid"
            for norm in sorted(expected, key=len, reverse=True):
                pattern = (r"(?:(?<![\d.])(\d+)\s*(?:[×x]\s*)?)?(?<!\w)"
                           + re.escape(norm) + r"(?:es|s)?(?!\w)")
                for m in re.finditer(pattern, text):
                    found.append((m.start(), norm,
                                  int(m.group(1)) if m.group(1) else None))
                text = re.sub(pattern, lambda m: " " * len(m.group()), text)
            seen.update(n for _, n, _ in found)
            # only the step's first item mention is its subject and carries a
            # counted quantity; later numbers are references ("the 3 kettles")
            if found:
                _, norm, qty = min(found, key=lambda f: f[0])
                if qty is not None:
                    said.setdefault(norm, []).append(qty)
        for norm in expected:
            name = shown[norm]
            if norm not in seen:
                missing.append(f"box {entry.box}: {name}")
                continue
            given = said.get(norm, [])
            if given and sum(given) != expected[norm]:
                wrong.append(
                    f"box {entry.box}: {name}: steps say "
                    f"{' + '.join(map(str, given))}"
                    f"{' = ' + str(sum(given)) if len(given) > 1 else ''}"
                    f", box has {expected[norm]}")
    out.append(Check("step_count", not bad_steps, "; ".join(bad_steps)))
    out.append(Check("mentions_items", not missing, "; ".join(missing)))
    out.append(Check("counts_match", not wrong, "; ".join(wrong)))
    return out


def run_notes(client, cfg: JobConfig, flat: FlatResponse | None, items,
              boxes, soft_instructions: Sequence[str]) -> JobResult:
    if flat is None:
        return skipped_result(JOB, cfg, PROMPT_VERSION, [])
    messages = build_messages(flat, items, boxes, soft_instructions)
    result = JobResult(job=JOB, status="ok", output=None, model=cfg.model,
                       reasoning_effort=cfg.reasoning_effort,
                       prompt_version=PROMPT_VERSION, messages=list(messages))
    outcome = call_parsed(client, cfg, messages, NotesAnswer)
    result.add(outcome)
    parsed: NotesAnswer | None = outcome.parsed
    result.answer = parsed.model_dump(mode="json")["boxes"] if parsed else None
    result.checks = checks(parsed, flat, items)
    if outcome.error is None and all(c.passed for c in result.checks):
        result.output = sorted(result.answer, key=lambda b: b["box"])
        return result
    result.status = "fallback"
    result.error = outcome.error or (
        f"checks failed: {failed_checks(result.checks)}")
    result.output = template_notes(flat, items)
    return result
