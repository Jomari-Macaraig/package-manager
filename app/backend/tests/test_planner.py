import pytest

from app.fake_llm import FakeClient
from app.pipeline import _cost
from app.schemas import RunRequest, to_pipeline_inputs
from conftest import sample_body, sample_request
from packing.packer import pack
from packing.planner import RunCancelled, plan_with_validation
from packing.prompt_builder import SYSTEM_PROMPT, build_user_prompt
from packing.schemas import FlatResponse, LeftoverRow
from packing.settings import (PackingSettings, extract_settings,
                              prepare_boxes, prepare_items)
from pydantic import ValidationError


def setup(key="A"):
    items, boxes = to_pipeline_inputs(sample_request(key))
    settings = PackingSettings()
    items_p = prepare_items(items, boxes, settings)
    boxes_p = prepare_boxes(boxes, settings)
    baseline, errors = pack(items_p, boxes_p, settings)
    assert not errors
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(
                    items_p, boxes_p, "", [])}]
    return items_p, boxes_p, settings, baseline, messages


def gave_up(items_p):
    """Every unit in leftovers: passes validate_plan, but the packer
    placed them all."""
    return FlatResponse(boxes=[], placements=[], notes=[], leftovers=[
        LeftoverRow(id=i["id"], quantity=i["quantity"], reason="x")
        for i in items_p])


def worse(baseline, boxes_p):
    """Valid and complete but worse: the packer's placements in the largest
    box type, so the shipped volume is higher."""
    used = {b["id"]: b for b in boxes_p}[baseline.boxes[0].id]
    largest = max(boxes_p, key=lambda b: b["dimensions"]["y"]
                  * b["dimensions"]["z"])
    assert all(largest["dimensions"][a] >= used["dimensions"][a]
               for a in "xyz") and largest["id"] != used["id"]
    return baseline.model_copy(update={"boxes": [
        b.model_copy(update={"id": largest["id"]}) for b in baseline.boxes]})


def plan(client, **kwargs):
    items_p, boxes_p, settings, baseline, messages = setup()
    kwargs.setdefault("baseline", baseline)
    client.context = (items_p, boxes_p, settings, kwargs["baseline"])
    return items_p, plan_with_validation(
        client, "m", messages, items_p, boxes_p, settings=settings,
        reasoning_effort="low", **kwargs)


def test_on_event_for_fix_and_improve_calls():
    _, boxes_p, _, baseline, _ = setup()
    w = worse(baseline, boxes_p)
    events = []
    _, result = plan(FakeClient(script=[w, w]),
                     on_event=events.append)
    assert [(e["kind"], e["n"], e["valid"]) for e in events] == [
        ("fix", 1, True), ("improve", 2, True)]
    assert result.rounds == 2 and result.improved is False
    assert result.model_flat is not None and result.model_errors == []
    assert result.model_hints
    assert result.source == "packer"


def test_failed_improvement_call_event_and_model_kept():
    _, boxes_p, _, baseline, _ = setup()
    w = worse(baseline, boxes_p)
    events = []
    _, result = plan(FakeClient(script=[w, None]),
                     on_event=events.append)
    assert events[1] == {"type": "round", "n": 2, "kind": "improve",
                         "valid": False, "errors_count": 1}
    assert result.model_errors == []  # failed improvement keeps valid plan
    assert result.model_flat == w


def test_improvement_wins_when_better():
    _, boxes_p, _, baseline, _ = setup()
    events = []
    _, result = plan(FakeClient(script=[worse(baseline, boxes_p)]),  # then baseline
                     on_event=events.append)
    assert result.improved is True and result.source == "model"
    assert events[1]["kind"] == "improve" and events[1]["valid"] is True


def test_giving_up_on_packable_units_is_a_fix_round():
    items_p = setup()[0]
    events = []
    client = FakeClient(script=[gave_up(items_p)])  # then the packer's plan
    _, result = plan(client, on_event=events.append)
    assert [(e["kind"], e["valid"]) for e in events] == [
        ("fix", False), ("fix", True)]
    assert result.ok and result.source == "model"
    feedback = result.history[3]["content"]
    assert "validate_plan: INVALID" in feedback
    assert "item 2: 4 unit(s) in leftovers fit" in feedback
    assert "L-14" in feedback


def test_baseline_is_sent_as_starting_plan():
    items_p, boxes_p, settings, baseline, messages = setup()
    _, result = plan(FakeClient(script=[gave_up(items_p)]))
    sent = result.history[1]["content"]
    assert sent.startswith(messages[1]["content"])  # caller's list untouched
    assert "Starting plan (from a code packer" in sent
    assert "L-14 filled to" in sent
    assert baseline.model_dump_json(exclude={"notes"}) in sent
    assert "return the Starting plan unchanged" in result.history[3]["content"]


def test_scratch_mode_keeps_baseline_as_fallback_only():
    items_p = setup()[0]
    _, result = plan(FakeClient(script=[gave_up(items_p)]), seed=False)
    assert "Starting plan" not in result.history[1]["content"]
    assert "Starting plan" not in result.history[3]["content"]
    assert "item 2: 4 unit(s) in leftovers fit" in result.history[3]["content"]


def test_no_starting_plan_without_baseline():
    items_p = setup()[0]
    _, result = plan(FakeClient(script=[gave_up(items_p)]), baseline=None)
    assert "Starting plan" not in result.history[1]["content"]


def test_giving_up_every_round_leaves_no_model_plan():
    items_p = setup()[0]
    g = gave_up(items_p)
    _, result = plan(FakeClient(script=[g, g, g]))
    assert result.rounds == 3 and result.source == "packer"
    assert result.model_errors and all(
        "in leftovers fit" in e for e in result.model_errors)


def test_leftovers_the_packer_also_leaves_are_fine():
    items_p, boxes_p, settings, baseline, messages = setup("C")
    assert not baseline.placements  # nothing fits in Sample C
    client = FakeClient(script=[gave_up(items_p)])
    client.context = (items_p, boxes_p, settings, baseline)
    result = plan_with_validation(client, "m", messages, items_p, boxes_p,
                                  settings=settings, baseline=baseline)
    assert result.ok and result.rounds == 1


def test_exhausted_loop_keeps_model_errors():
    bad = FlatResponse(boxes=[], placements=[], leftovers=[], notes=[])
    _, result = plan(FakeClient(script=[bad, bad, bad]), baseline=None)
    assert result.ok is False and result.rounds == 3
    assert result.model_errors == result.errors and result.errors
    assert result.model_flat is bad or result.model_flat == bad


def test_on_call_gets_allowlisted_config_and_usage():
    calls = []
    plan(FakeClient(), on_call=lambda *a: calls.append(a))
    model, config, usage = calls[0]
    assert model == "m"
    assert config == {"reasoning_effort": "low"}
    assert usage == {"input_tokens": 1000, "output_tokens": 200,
                     "reasoning_tokens": 0}


def test_should_stop_raises_with_partial_result():
    with pytest.raises(RunCancelled) as info:
        plan(FakeClient(), should_stop=lambda: True)
    assert info.value.result.rounds == 0
    assert info.value.result.history  # the messages so far


def test_extract_settings_triple_return():
    client = FakeClient()
    settings, usage, messages = extract_settings(client, "m", "  ", [])
    assert settings == PackingSettings() and usage is None and messages == []
    assert client.create_calls == []
    settings, usage, messages = extract_settings(
        client, "m", "pad", [{"id": 1, "name": "Mug"}],
        max_completion_tokens=5)
    assert usage.prompt_tokens == 1000
    assert messages[1]["content"].endswith("1: Mug")
    assert client.create_calls[0]["max_completion_tokens"] == 5


def test_cost_math_counts_reasoning_once():
    # 1M in + 1M out at gpt-5.4-nano prices; reasoning is inside completion
    assert _cost("gpt-5.4-nano", 1_000_000, 1_000_000) == pytest.approx(1.45)
    assert _cost("unknown-model", 10, 10) == 0.0


def test_to_pipeline_inputs_shapes():
    body = sample_body("A")
    body["items"][0]["color"] = None
    body["boxes"][1]["stock"] = 3
    req = RunRequest.model_validate(body)
    items, boxes = to_pipeline_inputs(req)
    assert [i["id"] for i in items] == [1, 2, 3, 4, 5]
    assert [b["id"] for b in boxes] == [1, 2, 3, 4, 5]
    assert items[0]["color"] == ""
    assert items[0]["dimensions"] == {"x": 4.5, "y": 3.75, "z": 3.75}
    assert "stock" not in boxes[0] and boxes[1]["stock"] == 3
    assert boxes[0]["weightMax"] == 40


def test_name_trimmed_and_unique_case_insensitive():
    body = sample_body("A")
    body["items"][0]["name"] = "  Mug  "
    assert RunRequest.model_validate(body).items[0].name == "Mug"
    body["items"][1]["name"] = "mug"
    with pytest.raises(ValidationError, match="duplicate item name"):
        RunRequest.model_validate(body)


def test_effort_must_be_supported_for_model():
    body = sample_body("A")
    body["planning"]["reasoning_effort"] = "extreme"
    with pytest.raises(ValidationError):
        RunRequest.model_validate(body)
