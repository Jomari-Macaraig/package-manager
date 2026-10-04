"""A job's prompt, answer schema or user-message template cannot change
without a version bump: one pinned hash per (job, PROMPT_VERSION,
USER_TEMPLATE_VERSION).

If this fails after an intended change: bump the job's PROMPT_VERSION (for a
prompt or schema change) and/or USER_TEMPLATE_VERSION (for the user message),
then replace the key and hash below with the ones printed by
`python tests/test_prompt_versions.py`.
"""
import hashlib
import json

import pytest

from app.samples import SAMPLES_V2
from app.schemas import OrderInput, to_pipeline_inputs
from packing.jobs import check, extract, notes, strategy
from packing.packer import pack, preference_report
from packing.settings import PackingSettings, prepare_boxes, prepare_items

PINNED = {
    ("extract", "extract-v1", "extract-user-v1"): "94c738865afdde3f5bd37911bb20ef24b1fcef02c0b41925f01e8fd31a6771b8",
    ("strategy", "strategy-v1", "strategy-user-v1"): "85c6784e43281527d2a4e6dae7c2d904dd1c843665299fa9a16c1826922c38fb",
    ("check", "check-v1", "check-user-v1"): "3b29d76ee902b20be86be97d60e1fafc9379b249e8ada9a49acbefa7aab9a5b6",
    ("notes", "notes-v1", "notes-user-v1"): "8e17f19d8eb1a792814869e76f9776ee082975d52893777ccc67b0c38d9cea4f",
}


def _sample_g():
    sample = next(s for s in SAMPLES_V2 if s["key"] == "G")
    req = OrderInput.model_validate({
        k: sample[k] for k in ("items", "boxes", "instructions")})
    items, boxes = to_pipeline_inputs(req)
    settings = PackingSettings(soft_instructions=[sample["instructions"]])
    items_p = prepare_items(items, boxes, settings)
    boxes_p = prepare_boxes(boxes, settings)
    estimates: list = []
    plan, errors = pack(items_p, boxes_p, settings, estimates)
    assert not errors
    return sample["instructions"], items, items_p, boxes_p, settings, \
        estimates, plan


def _digest(system: str, schema: dict, user: str) -> str:
    text = system + json.dumps(schema, sort_keys=True) + user
    return hashlib.sha256(text.encode()).hexdigest()


def current() -> dict:
    text, items, items_p, boxes_p, settings, estimates, plan = _sample_g()
    prefs = {1: {"side": "right", "level": "any"},
             3: {"side": "left", "level": "any"}}
    report = preference_report(plan, items_p, boxes_p, prefs)
    jobs = {
        "extract": (extract, extract.build_messages(text, items)),
        "strategy": (strategy, strategy.build_messages(
            items_p, boxes_p, settings, estimates)),
        "check": (check, check.build_messages(
            [text], plan, items_p, boxes_p, prefs, report)),
        "notes": (notes, notes.build_messages(plan, items_p, boxes_p, [text])),
    }
    return {
        (name, mod.PROMPT_VERSION, mod.USER_TEMPLATE_VERSION): _digest(
            mod.SYSTEM_PROMPT, mod.ANSWER_SCHEMA, messages[1]["content"])
        for name, (mod, messages) in jobs.items()}


def test_every_job_has_a_pinned_hash_for_its_current_versions():
    assert set(current()) == set(PINNED), (
        "a version changed: update PINNED (see the module docstring)")


@pytest.mark.parametrize("key", sorted(PINNED))
def test_prompt_schema_and_user_template_match_the_pinned_version(key):
    assert current()[key] == PINNED[key], (
        f"{key[0]}: the prompt, answer schema or user-message template "
        f"changed without a version bump")


if __name__ == "__main__":
    for key, digest in current().items():
        print(f'    {key!r}: "{digest}",')
