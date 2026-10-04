"""Job 1, extract: free-text instructions -> PackingSettings.

PackingSettings has defaults, which strict structured outputs reject, so the
call is chat.completions.create with settings._SCHEMA (call_json). The prompt
stays in settings.py (EXTRACT_PROMPT).
"""

from collections.abc import Mapping, Sequence
from typing import Any

from .. import settings as settings_mod
from ..settings import (PackingSettings, prepare_boxes, prepare_items,
                        settings_problems)
from .base import (Check, JobConfig, JobResult, call_json, failed_checks,
                   skipped_result)

JOB = "extract"
PROMPT_VERSION = "extract-v1"
USER_TEMPLATE_VERSION = "extract-user-v1"
SYSTEM_PROMPT = settings_mod.EXTRACT_PROMPT
ANSWER_SCHEMA = settings_mod._SCHEMA


def build_messages(instructions: str,
                   items: Sequence[Mapping[str, Any]]) -> list[dict]:
    listing = "\n".join(f"{i['id']}: {i['name']}" for i in items)
    content = instructions.strip() + (f"\n\nItems (id: name):\n{listing}"
                                      if listing else "")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content}]


def checks(parsed: PackingSettings | None, items, boxes,
           bounds: Mapping[str, float]) -> list[Check]:
    """parsed, then (when parsed) known_ids, in_bounds, applies, not_empty."""
    if parsed is None:
        return [Check("parsed", False, "the reply is not valid settings")]
    out = [Check("parsed", True)]
    try:
        settings_mod._check_ids(items, parsed)
        out.append(Check("known_ids", True))
    except ValueError as exc:
        out.append(Check("known_ids", False, str(exc)))
    problems = settings_problems(parsed, bounds)
    out.append(Check("in_bounds", not problems, "; ".join(problems)))
    try:
        prepare_items(items, boxes, parsed)
        narrow = [b["name"] for b in prepare_boxes(boxes, parsed)
                  if b["dimensions"]["y"] <= 0 or b["dimensions"]["z"] <= 0]
        out.append(Check("applies", not narrow, (
            f"wall clearance leaves no room in {', '.join(narrow)}"
            if narrow else "")))
    except (ValueError, KeyError, ArithmeticError) as exc:
        out.append(Check("applies", False, str(exc)))
    empty = parsed.model_dump() == PackingSettings().model_dump()
    out.append(Check("not_empty", not empty,
                     "no setting and no soft instruction" if empty else ""))
    return out


def run_extract(client, cfg: JobConfig, instructions: str, items, boxes,
                bounds: Mapping[str, float]) -> JobResult:
    if not instructions or not instructions.strip():
        return skipped_result(JOB, cfg, PROMPT_VERSION,
                              PackingSettings().model_dump(mode="json"))
    messages = build_messages(instructions, items)
    result = JobResult(job=JOB, status="ok", output=None, model=cfg.model,
                       reasoning_effort=cfg.reasoning_effort,
                       prompt_version=PROMPT_VERSION, messages=list(messages))
    outcome = call_json(client, cfg, messages, ANSWER_SCHEMA,
                        PackingSettings.model_validate_json,
                        name="PackingSettings")
    result.add(outcome)
    parsed: PackingSettings | None = outcome.parsed
    result.answer = parsed.model_dump(mode="json") if parsed else None
    result.checks = checks(parsed, items, boxes, bounds)
    if outcome.error is None and all(c.passed for c in result.checks):
        result.output = result.answer
        return result
    result.status = "fallback"
    result.error = outcome.error or (
        f"checks failed: {failed_checks(result.checks)}")
    result.output = PackingSettings(
        soft_instructions=[instructions.strip()]).model_dump(mode="json")
    return result
