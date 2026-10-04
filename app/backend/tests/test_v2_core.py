"""Pipeline v2 core: preferences, jobs, choose, run_v2 (FakeClient only)."""
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.fake_llm import FakeClient
from app.samples import SAMPLES, SAMPLES_V2
from app.schemas import OrderInput, to_pipeline_inputs
from packing.jobs import check as check_job
from packing.jobs import extract as extract_job
from packing.jobs import notes as notes_job
from packing.jobs import strategy as strategy_job
from packing.jobs.base import JobConfig, call_json, call_parsed
from packing.jobs.check import CheckAnswer, Verdict
from packing.jobs.notes import BoxNotes, NotesAnswer
from packing.jobs.strategy import (Preference, StrategyAnswer,
                                   strategy_answer_from_plan)
from packing.packer import pack, pack_guided, preference_report
from packing.planner import RunCancelled
from packing.schemas import LeftoverRow, PlacementRow, RotationChoice
from packing.settings import (PackingSettings, prepare_boxes, prepare_items,
                              settings_problems)
from packing.v2 import (ConfigErrorV2, RunCancelledV2, VOLUME_MARGIN, choose,
                        run_v2)
from packing.validation import validate_plan

BOUNDS = {"padding": 12.0, "wall_clearance": 12.0, "cut_step_min": 0.01,
          "block_size": 50, "max_boxes": 50}
CFG = JobConfig(model="gpt-4.1-nano")
JOBS = {n: JobConfig(model="m") for n in
        ("extract", "strategy", "check", "notes")}


def order(key):
    sample = next(s for s in SAMPLES + SAMPLES_V2 if s["key"] == key)
    req = OrderInput.model_validate({
        k: sample[k] for k in ("items", "boxes", "instructions")})
    items, boxes = to_pipeline_inputs(req)
    return items, boxes, sample["instructions"]


def prepared(key, settings=None):
    items, boxes, _ = order(key)
    settings = settings or PackingSettings()
    return (prepare_items(items, boxes, settings),
            prepare_boxes(boxes, settings), settings)


def tiny(*specs, box=(10, 8, 8)):
    """specs: (name, (x, y, z), quantity). One box type, ids from 1."""
    items = [{"id": n, "name": name, "color": "", "weight": 1.0,
              "dimensions": dict(zip("xyz", dims)), "quantity": q}
             for n, (name, dims, q) in enumerate(specs, start=1)]
    boxes = [{"id": 1, "name": "Box", "weightMax": 100,
              "dimensions": dict(zip("xyz", box))}]
    return items, boxes


def place(items, boxes, order_ids, prefs, boxes_used=(1,)):
    rotations = {i["id"]: (i["dimensions"]["x"], i["dimensions"]["y"],
                           i["dimensions"]["z"]) for i in items}
    flat, errors = pack_guided(items, boxes, PackingSettings(),
                               list(boxes_used), order_ids, rotations, prefs)
    assert not errors, errors
    return {r.seq: r for r in flat.placements}, flat


# --- placement preferences ----------------------------------------------------

@pytest.mark.parametrize("side,pos", [
    ("left", [0, 0, 0]), ("right", [0, 0, 6]),
    ("back", [0, 0, 0]), ("front", [0, 6, 0])])
def test_side_preference_moves_a_unit(side, pos):
    items, boxes = tiny(("Cube", (2, 2, 2), 1))
    rows, _ = place(items, boxes, [1], {1: {"side": side, "level": "any"}})
    assert rows[1].pos == pos


def test_right_and_front_use_wall_points_on_a_second_unit():
    items, boxes = tiny(("Cube", (2, 2, 2), 2))
    rows, _ = place(items, boxes, [1], {1: {"side": "right", "level": "any"}})
    assert [r.pos for r in rows.values()] == [[0, 0, 6], [0, 2, 6]]
    rows, _ = place(items, boxes, [1], {1: {"side": "front", "level": "any"}})
    assert [r.pos for r in rows.values()] == [[0, 6, 0], [0, 6, 2]]


def test_left_beats_default_when_the_default_goes_elsewhere():
    # default order takes (0, 0, z) before (0, y, 0): "left" key picks small z
    items, boxes = tiny(("Block", (2, 2, 2), 1), ("Cube", (2, 2, 2), 1))
    rows, _ = place(items, boxes, [1, 2], {2: {"side": "left",
                                               "level": "any"}})
    assert rows[2].pos[2] == 0 or rows[2].pos[1] > 0


def test_level_orders_item_types_stably():
    items, boxes = tiny(("A", (2, 2, 2), 1), ("B", (2, 2, 2), 1),
                        ("C", (2, 2, 2), 1))
    prefs = {1: {"side": "any", "level": "top"},
             3: {"side": "any", "level": "bottom"}}
    _, flat = place(items, boxes, [1, 2, 3], prefs)
    assert [r.id for r in sorted(flat.placements, key=lambda r: r.seq)] == [
        3, 2, 1]


def test_top_takes_the_highest_valid_spot():
    items, boxes = tiny(("Tall", (5, 3, 3), 1), ("Short", (2, 3, 3), 1),
                        ("Cube", (2, 2, 2), 1), box=(10, 3, 8))
    rows, _ = place(items, boxes, [1, 2, 3], {})
    assert rows[3].pos[0] == 0  # no preference: the floor
    rows, _ = place(items, boxes, [1, 2, 3],
                    {3: {"side": "any", "level": "top"}})
    assert rows[3].pos == [5, 0, 0]  # on the tall item


def test_bottom_is_order_only_and_preferences_never_break_rules():
    items, boxes = tiny(("Cube", (2, 2, 2), 3))
    for side in ("left", "right", "back", "front"):
        for level in ("any", "bottom", "top"):
            flat, errors = pack_guided(
                items, boxes, PackingSettings(), [1], [1],
                {1: (2, 2, 2)}, {1: {"side": side, "level": level}})
            assert errors == []


def test_bad_preferences_are_choice_errors():
    items, boxes = tiny(("Cube", (2, 2, 2), 1))
    flat, errors = pack_guided(items, boxes, PackingSettings(), [1], [1],
                               {}, {9: {"side": "left", "level": "any"},
                                    1: {"side": "middle", "level": "any"}})
    assert flat is None and len(errors) == 2


@pytest.mark.parametrize("key", ["A", "B", "C", "D", "E"])
def test_no_preferences_means_todays_placements(key):
    items_p, boxes_p, settings = prepared(key)
    baseline, _ = pack(items_p, boxes_p, settings)
    answer = strategy_answer_from_plan(baseline)
    args = (items_p, boxes_p, settings, answer.boxes, answer.order,
            {r.id: r.dims for r in answer.rotations})
    plain, _ = pack_guided(*args)
    assert pack_guided(*args, None)[0] == plain
    assert pack_guided(*args, {})[0] == plain
    anys = {i["id"]: {"side": "any", "level": "any"} for i in items_p}
    assert pack_guided(*args, anys)[0] == plain


def test_preference_report_counts_and_details():
    items, boxes = tiny(("Cube", (2, 2, 2), 2))
    _, flat = place(items, boxes, [1], {})
    rows = preference_report(flat, items, boxes,
                             {1: {"side": "left", "level": "any"}})
    assert rows == [{"id": 1, "name": "Cube", "side": "left", "level": "any",
                     "honoured": 2, "total": 2,
                     "detail": "2/2 on the left half"}]
    rows = preference_report(flat, items, boxes,
                             {1: {"side": "right", "level": "bottom"}})
    assert rows[0]["honoured"] == 0
    assert rows[0]["detail"] == "0/2 on the right half and on the floor"
    assert preference_report(flat, items, boxes,
                             {1: {"side": "any", "level": "any"}}) == []


def test_preference_report_top_allows_stacked_top_units():
    items, boxes = tiny(("Base", (2, 8, 8), 1), ("Vase", (2, 2, 2), 3))
    _, flat = place(items, boxes, [1, 2],
                    {2: {"side": "any", "level": "top"}})
    [row] = preference_report(flat, items, boxes,
                              {2: {"side": "any", "level": "top"}})
    assert (row["honoured"], row["total"]) == (3, 3)
    # the base is "under" the vases: a base that asked for top is not
    [row] = preference_report(flat, items, boxes,
                              {1: {"side": "any", "level": "top"}})
    assert row["honoured"] == 0 and row["detail"] == "0/1 on top"


def test_preference_report_back_front_and_bottom():
    items, boxes = tiny(("Cube", (2, 2, 2), 1))
    _, flat = place(items, boxes, [1], {})
    prefs = {1: {"side": "back", "level": "bottom"}}
    assert preference_report(flat, items, boxes, prefs)[0]["honoured"] == 1
    prefs = {1: {"side": "front", "level": "any"}}
    assert preference_report(flat, items, boxes, prefs)[0]["honoured"] == 0


# --- call helpers ---------------------------------------------------------------

def http_error(cls, status):
    return cls("boom", response=httpx.Response(
        status, request=httpx.Request("POST", "http://x")), body=None)


def length_error():
    completion = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=500, completion_tokens=100,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=40)))
    return openai.LengthFinishReasonError(completion=completion)


def filter_error():
    completion = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=50, completion_tokens=0,
        completion_tokens_details=None))
    return openai.ContentFilterFinishReasonError.__new__(
        openai.ContentFilterFinishReasonError)


def test_call_parsed_ok_and_usage():
    answer = CheckAnswer(verdicts=[])
    client = FakeClient(script=[answer])
    out = call_parsed(client, JobConfig("m", "low", 77, {"x": 1}),
                      [{"role": "user", "content": "hi"}], CheckAnswer)
    assert out.parsed == answer and out.error is None
    assert out.usage == {"input_tokens": 1000, "output_tokens": 200,
                         "reasoning_tokens": 0}
    call = client.parse_calls[0]
    assert (call["model"], call["reasoning_effort"],
            call["max_completion_tokens"], call["x"]) == ("m", "low", 77, 1)


def test_call_parsed_error_mapping():
    out = call_parsed(FakeClient(script=[length_error()]), CFG, [], CheckAnswer)
    assert out.parsed is None and out.cut_off
    assert out.error.startswith("LengthFinishReasonError")
    assert out.usage["output_tokens"] == 100
    assert out.usage["reasoning_tokens"] == 40

    out = call_parsed(FakeClient(script=[None]), CFG, [], CheckAnswer)
    assert out.error == "no answer returned: no plan"  # refusal

    api = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    out = call_parsed(FakeClient(script=[api]), CFG, [], CheckAnswer)
    assert out.error.startswith("APIConnectionError") and not out.cut_off

    try:
        CheckAnswer.model_validate({"verdicts": 1})
    except Exception as exc:
        invalid = exc
    out = call_parsed(FakeClient(script=[invalid]), CFG, [], CheckAnswer)
    assert out.error.startswith("answer does not match the schema")

    out = call_parsed(FakeClient(script=[filter_error()]), CFG, [],
                      CheckAnswer)
    assert out.parsed is None and out.error  # content filter: an error


@pytest.mark.parametrize("cls,status", [(openai.AuthenticationError, 401),
                                        (openai.NotFoundError, 404),
                                        (openai.PermissionDeniedError, 403),
                                        (openai.BadRequestError, 400)])
def test_config_errors_are_reraised(cls, status):
    with pytest.raises(cls):
        call_parsed(FakeClient(script=[http_error(cls, status)]), CFG, [],
                    CheckAnswer)
    with pytest.raises(cls):
        call_json(FakeClient(create_script=[http_error(cls, status)]), CFG,
                  [], {}, PackingSettings.model_validate_json)


def test_call_json_ok_invalid_empty_and_api_error():
    ok = call_json(FakeClient(), CFG, [], {}, PackingSettings.model_validate_json)
    assert ok.parsed == PackingSettings() and ok.usage["input_tokens"] == 1000
    bad = call_json(FakeClient(create_script=["not json"]), CFG, [], {},
                    PackingSettings.model_validate_json)
    assert bad.parsed is None and bad.error.startswith("answer rejected")
    assert bad.reply == "not json" and bad.usage["input_tokens"] == 1000
    empty = call_json(FakeClient(create_script=[None]), CFG, [], {},
                      PackingSettings.model_validate_json)
    assert empty.error.startswith("no answer returned")
    api = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    out = call_json(FakeClient(create_script=[api]), CFG, [], {},
                    PackingSettings.model_validate_json)
    assert out.error.startswith("APIConnectionError")


# --- extract ---------------------------------------------------------------------

def run_extract(content, key="A", text="pad it"):
    items, boxes, _ = order(key)
    return extract_job.run_extract(
        FakeClient(create_script=[content]), CFG, text, items, boxes, BOUNDS)


def test_extract_skipped_without_instructions():
    items, boxes, _ = order("A")
    client = FakeClient()
    r = extract_job.run_extract(client, CFG, "  ", items, boxes, BOUNDS)
    assert (r.status, r.calls, r.checks) == ("skipped", 0, [])
    assert r.model == "gpt-4.1-nano" and client.create_calls == []
    assert PackingSettings.model_validate(r.output) == PackingSettings()


def test_extract_ok_passes_all_checks():
    r = run_extract(PackingSettings(padding=0.5).model_dump_json())
    assert r.status == "ok" and r.error is None
    assert [c.name for c in r.checks] == [
        "parsed", "known_ids", "in_bounds", "applies", "not_empty"]
    assert all(c.passed for c in r.checks)
    assert r.output["padding"] == 0.5 and r.answer == r.output


def failing(r):
    return [c.name for c in r.checks if not c.passed]


def test_extract_each_check_can_fail_with_the_soft_fallback():
    fallback = PackingSettings(soft_instructions=["pad it"]).model_dump(
        mode="json")
    cases = {
        "parsed": "nope",
        "known_ids": PackingSettings(upright=[99]).model_dump_json(),
        "in_bounds": PackingSettings(padding=50).model_dump_json(),
        "applies": PackingSettings(wall_clearance=6).model_dump_json(),
        "not_empty": PackingSettings().model_dump_json(),
    }
    for name, content in cases.items():
        r = run_extract(content)
        assert r.status == "fallback" and name in failing(r), name
        assert r.output == fallback and r.error
        assert r.calls == 1 and r.prompt_tokens == 1000


def test_extract_call_error_falls_back_and_keeps_usage_zero():
    api = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    r = run_extract(api)
    assert r.status == "fallback" and failing(r) == ["parsed"]
    assert r.error.startswith("APIConnectionError")


def test_settings_problems_takes_bounds_and_flags_duplicate_blocks():
    from packing.settings import BlockRule, DUPLICATE_BLOCKS
    assert settings_problems(PackingSettings(), BOUNDS) == []
    s = PackingSettings(padding=13, max_boxes=60)
    assert len(settings_problems(s, BOUNDS)) == 2
    assert settings_problems(s, {**BOUNDS, "padding": 20, "max_boxes": 99}) == []
    dup = PackingSettings(blocks=[BlockRule(item_id=1, size=2),
                                  BlockRule(item_id=1, size=3)])
    assert settings_problems(dup, BOUNDS) == [DUPLICATE_BLOCKS]


# --- strategy --------------------------------------------------------------------

def strategy_inputs(key):
    items_p, boxes_p, settings = prepared(key)
    estimates = []
    baseline, errors = pack(items_p, boxes_p, settings, estimates)
    assert not errors
    return items_p, boxes_p, settings, estimates, baseline


def run_strategy(key, script):
    items_p, boxes_p, settings, estimates, baseline = strategy_inputs(key)
    client = FakeClient(script=script)
    client.context = (items_p, boxes_p, settings, baseline)
    return strategy_job.run_strategy(
        client, JobConfig("m", "low", 100), items_p, boxes_p, settings,
        estimates, baseline), client, baseline


def test_strategy_ok_first_call_with_all_checks():
    run, client, baseline = run_strategy("A", [])
    r = run.result
    assert r.status == "ok" and r.calls == 1 and run.flat is not None
    assert [c.name for c in r.checks] == [
        "parsed", "choices_valid", "plan_valid", "no_extra_leftovers",
        "no_extra_boxes", "volume_within_margin", "preferences_honoured"]
    assert all(c.passed for c in r.checks)
    assert r.checks[-1].detail == "none"
    assert r.output == r.answer and run.errors == []
    assert client.parse_calls[0]["response_format"] is StrategyAnswer


def test_strategy_failed_preferences_check_does_not_make_the_plan_invalid():
    items_p, boxes_p, settings, est, baseline = strategy_inputs("G")
    answer = strategy_answer_from_plan(baseline).model_copy(update={
        "preferences": [Preference(id=i, side="left", level="any")
                        for i in (1, 2, 3)]})  # cannot all fit on the left
    run, _, _ = run_strategy("G", [answer])
    assert run.result.status == "ok"  # still a valid plan
    assert failing(run.result) == ["preferences_honoured"]


def test_strategy_second_call_after_invalid_choices_then_ok():
    _, _, _, _, baseline = strategy_inputs("A")
    bad = strategy_answer_from_plan(baseline).model_copy(
        update={"boxes": [99]})
    good = strategy_answer_from_plan(baseline)
    run, client, _ = run_strategy("A", [bad, good])
    assert run.result.status == "ok" and run.result.calls == 2
    feedback = client.parse_calls[1]["messages"][-1]["content"]
    assert "unknown box type id 99" in feedback
    roles = [m["role"] for m in client.parse_calls[1]["messages"]]
    assert roles == ["system", "user", "assistant", "user"]


def test_strategy_extra_leftovers_use_both_calls_then_fall_back():
    _, _, _, _, baseline = strategy_inputs("A")
    first = strategy_answer_from_plan(baseline)
    short = first.model_copy(update={"boxes": [first.boxes[0]],
                                     "order": first.order[:1]})
    # an empty box list places nothing: every unit is an extra leftover
    empty = first.model_copy(update={"boxes": []})
    run, client, _ = run_strategy("A", [empty, empty])
    r = run.result
    assert r.status == "fallback" and r.calls == 2 and run.flat is None
    assert "no_extra_leftovers" in failing(r)
    assert run.errors and r.error.startswith("no valid plan")
    # the fallback output is the packer's implied choices
    assert r.output["boxes"] == first.boxes and r.output["preferences"] == []
    assert len(client.parse_calls) == 2
    assert short  # (unused variant kept for readability of the setup)


def test_strategy_no_answer_retries_once_and_cut_off_does_not():
    run, client, _ = run_strategy("A", [None, None])
    assert run.result.status == "fallback" and run.result.calls == 2
    assert failing(run.result) == ["parsed"]
    assert run.errors[0].startswith("model call failed")
    run, client, _ = run_strategy("A", [length_error()])
    assert run.result.calls == 1 and run.result.status == "fallback"
    assert run.result.completion_tokens == 100  # usage kept
    assert run.result.error.startswith("LengthFinishReasonError")


def test_strategy_config_error_propagates():
    with pytest.raises(openai.AuthenticationError):
        run_strategy("A", [http_error(openai.AuthenticationError, 401)])


# --- check -----------------------------------------------------------------------

def check_inputs():
    items_p, boxes_p, settings, est, baseline = strategy_inputs("G")
    prefs = {1: {"side": "right", "level": "any"},
             3: {"side": "left", "level": "any"}}
    report = preference_report(baseline, items_p, boxes_p, prefs)
    return items_p, boxes_p, baseline, prefs, report


def run_check(script, soft=("Keep the jars on the left.",), flat=True):
    items_p, boxes_p, baseline, prefs, report = check_inputs()
    client = FakeClient(script=script)
    return check_job.run_check(client, CFG, list(soft),
                               baseline if flat else None, items_p, boxes_p,
                               prefs, report), client


def test_check_skipped_without_instructions_or_plan():
    for kwargs in ({"soft": ()}, {"flat": False}):
        r, client = run_check([], **kwargs)
        assert r.status == "skipped" and r.output == [] and r.calls == 0
        assert client.parse_calls == []


def test_check_ok_and_message_content():
    answer = CheckAnswer(verdicts=[Verdict(index=0, verdict="honoured",
                                           reason="jars left")])
    r, client = run_check([answer])
    assert r.status == "ok" and [c.name for c in r.checks] == [
        "parsed", "covers_all"]
    assert r.output == [{"index": 0, "verdict": "honoured",
                         "reason": "jars left"}]
    user = client.parse_calls[0]["messages"][1]["content"]
    assert "0: Keep the jars on the left." in user
    assert "Code facts:" in user and "4/5 on the right half" in user
    assert "box|name|seq|item|pos|dims|support|on_top" in user


def test_check_covers_all_failures_fall_back_to_cannot_tell():
    twice = Verdict(index=0, verdict="honoured", reason="x")
    for verdicts in ([], [twice, twice], [Verdict(index=1, verdict="honoured",
                                                  reason="x")]):
        r, _ = run_check([CheckAnswer(verdicts=verdicts)])
        assert r.status == "fallback" and failing(r) == ["covers_all"]
        assert [v["verdict"] for v in r.output] == ["cannot_tell"]


def test_check_parse_failure_falls_back():
    r, _ = run_check([None], soft=("a", "b"))
    assert r.status == "fallback" and failing(r) == ["parsed"]
    assert len(r.output) == 2


# --- notes -----------------------------------------------------------------------

def notes_inputs():
    items_p, boxes_p, settings, est, baseline = strategy_inputs("G")
    return items_p, boxes_p, baseline


def good_notes(baseline, items_p):
    return NotesAnswer(boxes=[BoxNotes(**n) for n in
                              notes_job.template_notes(baseline, items_p)])


def run_notes(script, flat=True):
    items_p, boxes_p, baseline = notes_inputs()
    client = FakeClient(script=script)
    return notes_job.run_notes(client, CFG, baseline if flat else None,
                               items_p, boxes_p, ["Jars left."]), client


def test_template_notes_groups_items_per_box_in_load_order():
    items_p, _, baseline = notes_inputs()
    notes = notes_job.template_notes(baseline, items_p)
    assert [n["box"] for n in notes] == [1, 2]
    assert all(s.startswith("Load ") for n in notes for s in n["steps"])
    total = sum(int(s.split()[1]) for n in notes for s in n["steps"])
    assert total == 10


def test_notes_skipped_without_final_plan():
    r, _ = run_notes([], flat=False)
    assert (r.status, r.output, r.calls) == ("skipped", [], 0)


def test_notes_ok_with_template_like_answer():
    items_p, _, baseline = notes_inputs()
    r, client = run_notes([good_notes(baseline, items_p)])
    assert r.status == "ok" and all(c.passed for c in r.checks)
    assert [c.name for c in r.checks] == [
        "parsed", "one_entry_per_box", "step_count", "mentions_items",
        "counts_match"]
    assert r.output == notes_job.template_notes(baseline, items_p)
    assert "Jars left." in client.parse_calls[0]["messages"][1]["content"]


@pytest.mark.parametrize("name,mutate", [
    ("one_entry_per_box", lambda n: NotesAnswer(boxes=n.boxes[:1])),
    ("step_count", lambda n: NotesAnswer(boxes=[
        BoxNotes(box=b.box, steps=[]) for b in n.boxes])),
    ("step_count", lambda n: NotesAnswer(boxes=[
        BoxNotes(box=b.box, steps=b.steps * 9) for b in n.boxes])),
    ("mentions_items", lambda n: NotesAnswer(boxes=[
        BoxNotes(box=b.box, steps=["Pack carefully"]) for b in n.boxes])),
    ("counts_match", lambda n: NotesAnswer(boxes=[
        BoxNotes(box=b.box, steps=[s.replace("Load ", "Load 7") for s in
                                   b.steps]) for b in n.boxes])),
])
def test_notes_each_check_can_fail(name, mutate):
    items_p, _, baseline = notes_inputs()
    r, _ = run_notes([mutate(good_notes(baseline, items_p))])
    assert r.status == "fallback" and name in failing(r)
    assert r.output == notes_job.template_notes(baseline, items_p)


def test_notes_name_normalisation_and_plural():
    assert notes_job._normalise("Tape block of 4 (1×1×4)") == "tape block of 4"
    items_p, _, baseline = notes_inputs()
    plural = NotesAnswer(boxes=[BoxNotes(box=b.box, steps=[
        s.replace("×", "x").replace(" Kettle", " kettles").replace(
            " Jar", " jars") for s in b.steps])
        for b in good_notes(baseline, items_p).boxes])
    r, _ = run_notes([plural])
    assert r.status == "ok", [c for c in r.checks if not c.passed]


# --- choose ----------------------------------------------------------------------

def g_plans():
    items_p, boxes_p, settings, est, baseline = strategy_inputs("G")
    prefs = {1: {"side": "right", "level": "any"},
             3: {"side": "left", "level": "any"}}
    answer = strategy_answer_from_plan(baseline).model_copy(update={
        "boxes": [1, 2], "order": [2, 1, 3],
        "preferences": [Preference(id=1, side="right", level="any"),
                        Preference(id=3, side="left", level="any")]})
    rotations = {r.id: r.dims for r in strategy_answer_from_plan(
        baseline).rotations}
    model, errors = pack_guided(items_p, boxes_p, settings, answer.boxes,
                                answer.order, rotations,
                                {k: v for k, v in prefs.items()})
    assert not errors
    return (items_p, boxes_p, baseline, model, prefs,
            preference_report(model, items_p, boxes_p, prefs),
            preference_report(baseline, items_p, boxes_p, prefs))


HONOURED = [{"index": 0, "verdict": "honoured", "reason": ""}]


def test_choose_rule_1_and_2():
    _, boxes_p, baseline, model, *_ = g_plans()
    assert choose(None, None, [], [], [], [], boxes_p) == (
        "none", "no valid plan")
    assert choose(model, None, [], [], [], [], boxes_p) == (
        "model", "only valid plan")
    assert choose(None, baseline, [], [], [], [], boxes_p) == (
        "packer", "only valid plan")


def test_choose_rule_3_honours_the_instructions():
    _, boxes_p, baseline, model, prefs, rep_m, rep_p = g_plans()
    soft = ["Jars left"]
    assert all(r["honoured"] == r["total"] for r in rep_m)
    assert any(r["honoured"] < r["total"] for r in rep_p)
    assert choose(model, baseline, HONOURED, soft, rep_m, rep_p, boxes_p) == (
        "model", "honours the instructions")


def test_choose_rule_3_needs_every_condition():
    _, boxes_p, baseline, model, prefs, rep_m, rep_p = g_plans()
    soft = ["Jars left"]

    def rule3(**kw):
        args = dict(model=model, packer=baseline, verdicts=HONOURED,
                    soft=soft, rep_m=rep_m, rep_p=rep_p)
        args.update(kw)
        return choose(args["model"], args["packer"], args["verdicts"],
                      args["soft"], args["rep_m"], args["rep_p"], boxes_p)

    assert rule3()[1] == "honours the instructions"
    # no soft instructions / a cannot_tell / not_honoured / no verdicts
    assert rule3(soft=[])[1] != "honours the instructions"
    cannot = [{"index": 0, "verdict": "cannot_tell", "reason": ""}]
    assert rule3(verdicts=cannot)[1] != "honours the instructions"
    assert rule3(verdicts=[])[1] != "honours the instructions"
    # the model plan has no preference rows
    assert rule3(rep_m=[])[1] != "honours the instructions"
    # a model row not fully honoured
    half = [{**rep_m[0], "honoured": 0}] + rep_m[1:]
    assert rule3(rep_m=half)[1] != "honours the instructions"
    # the packer already honours the preferences: metrics decide
    packer_ok = [{**r, "honoured": r["total"]} for r in rep_p]
    assert rule3(rep_p=packer_ok)[1] != "honours the instructions"


def two_floor_plans(ratio):
    """Model plan in box type 2 and packer plan in box type 1: same fill,
    the model's floor is `ratio` times the packer's."""
    items, boxes = tiny(("Cube", (2, 2, 2), 1), box=(10, 8, 8))
    boxes.append({"id": 2, "name": "Wide", "weightMax": 100,
                  "dimensions": {"x": 10, "y": 8, "z": 8 * ratio}})
    flat_p = place(items, boxes, [1], {})[1]
    rows, flat_m = place(items, boxes, [1], {}, boxes_used=(2,))
    return boxes, flat_m, flat_p


ROW_OK = [{"honoured": 1, "total": 1}]
ROW_BAD = [{"honoured": 0, "total": 1}]


def rule3(model, packer, boxes):
    return choose(model, packer, HONOURED, ["x"], ROW_OK, ROW_BAD, boxes)


def test_choose_rule_3_volume_margin_1_15_vs_1_16():
    boxes, model, packer = two_floor_plans(1.15)
    assert rule3(model, packer, boxes) == ("model", "honours the instructions")
    boxes, model, packer = two_floor_plans(1.16)
    assert rule3(model, packer, boxes)[1] != "honours the instructions"


def test_choose_rule_3_extra_box_or_extra_leftover_falls_to_metrics():
    boxes, model, packer = two_floor_plans(1.0)
    assert rule3(model, packer, boxes)[1] == "honours the instructions"
    extra = model.model_copy(update={"boxes": [
        *model.boxes, model.boxes[0].model_copy(update={"box": 2})]})
    assert rule3(extra, packer, boxes) == ("packer", "packer has better metrics")
    left = model.model_copy(update={"leftovers": [
        LeftoverRow(id=1, quantity=1, reason="x")]})
    assert rule3(left, packer, boxes) == ("packer", "packer has better metrics")


def test_choose_rule_4_metrics_and_tie_goes_to_model():
    _, boxes_p, baseline, model, *_ = g_plans()
    assert choose(baseline, baseline, [], [], [], [], boxes_p) == (
        "model", "better metrics")  # equal ranks, something packed
    _, boxes2, _, _, base_a = strategy_inputs("A")
    big = base_a.model_copy(update={"boxes": [
        b.model_copy(update={"id": 5}) for b in base_a.boxes]})
    assert choose(big, base_a, [], [], [], [], boxes2) == (
        "packer", "packer has better metrics")
    assert choose(base_a, big, [], [], [], [], boxes2) == (
        "model", "better metrics")


def test_choose_same_as_packer_when_nothing_is_packed():
    items_p, boxes_p, settings = prepared("C")
    baseline, errors = pack(items_p, boxes_p, settings)
    assert not errors and not baseline.placements
    assert choose(baseline, baseline, [], [], [], [], boxes_p) == (
        "model", "same as packer")


# --- run_v2 ----------------------------------------------------------------------

def v2(key, client=None, **kwargs):
    items, boxes, instructions = order(key)
    client = client or FakeClient()
    client.echo_soft = True
    events = []

    def on_context(stage, data):
        client.context = data

    result = run_v2(client, items, boxes, instructions, JOBS, bounds=BOUNDS,
                    on_event=events.append, on_context=on_context, **kwargs)
    return result, events, client


@pytest.mark.parametrize("key", list("ABCDEFGH"))
def test_run_v2_with_fake_client_on_every_sample(key):
    result, events, client = v2(key)
    assert [j.job for j in result.jobs] == [
        "extract", "strategy", "check", "notes"]
    has_text = bool(order(key)[2])
    statuses = {j.job: j.status for j in result.jobs}
    assert statuses["extract"] == ("ok" if has_text else "skipped")
    assert statuses["strategy"] == "ok"
    assert statuses["check"] == ("ok" if has_text else "skipped")
    assert statuses["notes"] == "ok"  # even an empty plan gets (empty) notes
    assert result.source in ("model", "packer", "none")
    assert result.source == "none" or result.final_flat is not None
    if result.final_flat is not None:
        items_p, boxes_p = result.items_p, result.boxes_p
        assert validate_plan(result.final_flat, items_p, boxes_p,
                             result.settings) == []
    assert [e["type"] for e in events] == [
        "job", "packer", "job", "job", "chosen", "job"]
    assert [e["job"] for e in events if e["type"] == "job"] == [
        "extract", "strategy", "check", "notes"]
    assert events[-2]["source"] == result.source


def test_run_v2_instruction_samples_exercise_check_but_not_the_prefs_check():
    result, _, _ = v2("G")
    check = next(j for j in result.jobs if j.job == "check")
    assert check.status == "ok" and len(result.verdicts) == 1
    assert result.verdicts[0]["instruction"].startswith("Keep the jars")
    strategy = next(j for j in result.jobs if j.job == "strategy")
    assert strategy.status == "ok"
    # the fake strategy has no preferences: nothing to honour, check passes
    assert result.preferences_model == []


def test_run_v2_scripted_strategy_wins_on_the_instructions_rule_3():
    items_p, boxes_p, baseline, model, prefs, rep_m, rep_p = g_plans()
    answer = strategy_answer_from_plan(baseline).model_copy(update={
        "boxes": [1, 2], "order": [2, 1, 3],
        "preferences": [Preference(id=1, side="right", level="any"),
                        Preference(id=3, side="left", level="any")]})
    result, _, _ = v2("G", FakeClient(script=[answer]))
    assert (result.source, result.reason) == (
        "model", "honours the instructions")
    assert all(r["honoured"] == r["total"] for r in result.preferences_model)
    assert any(r["honoured"] < r["total"] for r in result.preferences_packer)
    assert result.final_flat == result.model_flat


def test_run_v2_strategy_fallback_still_finishes():
    result, _, _ = v2("A", FakeClient(script=[None, None]))
    strategy = result.jobs[1]
    assert strategy.status == "fallback" and result.model_flat is None
    assert result.model_errors and result.source == "packer"
    assert result.reason == "only valid plan"
    assert result.jobs[3].status == "ok"  # notes still ran


def test_run_v2_cancel_gives_four_jobs():
    items, boxes, instructions = order("G")
    stops = iter([False, False, False, True])  # before the 4th stage check
    with pytest.raises(RunCancelledV2) as caught:
        run_v2(FakeClient(), items, boxes, instructions, JOBS, bounds=BOUNDS,
               should_stop=lambda: next(stops, True))
    assert isinstance(caught.value, RunCancelled)
    jobs = caught.value.jobs
    assert [j.job for j in jobs] == ["extract", "strategy", "check", "notes"]
    assert [j.status for j in jobs][2:] == ["skipped", "skipped"]
    assert {j.error for j in jobs[2:]} == {"cancelled"}
    assert jobs[0].status in ("ok", "fallback")
    with pytest.raises(RunCancelledV2) as caught:
        run_v2(FakeClient(), items, boxes, instructions, JOBS, bounds=BOUNDS,
               should_stop=lambda: True)
    assert all(j.status == "skipped" and j.error == "cancelled"
               and j.model == "m" for j in caught.value.jobs)


def test_run_v2_config_error_keeps_finished_jobs_and_marks_the_rest():
    items, boxes, instructions = order("G")
    auth = http_error(openai.AuthenticationError, 401)
    client = FakeClient(script=[auth])  # the strategy call
    client.echo_soft = True
    with pytest.raises(ConfigErrorV2) as caught:
        run_v2(client, items, boxes, instructions, JOBS, bounds=BOUNDS,
               on_context=lambda s, d: setattr(client, "context", d))
    assert isinstance(caught.value.original, openai.AuthenticationError)
    jobs = caught.value.jobs
    assert [j.job for j in jobs] == ["extract", "strategy", "check", "notes"]
    assert jobs[0].status in ("ok", "fallback")
    assert [(j.status, j.error) for j in jobs[1:]] == [
        ("skipped", "not run")] * 3


def test_fake_default_answers_by_format():
    items_p, boxes_p, settings, est, baseline = strategy_inputs("G")
    client = FakeClient()
    client.context = (items_p, boxes_p, PackingSettings(
        soft_instructions=["a", "b"]), baseline)
    out = call_parsed(client, CFG, [], CheckAnswer)
    assert [v.verdict for v in out.parsed.verdicts] == ["honoured"] * 2
    out = call_parsed(client, CFG, [], StrategyAnswer)
    assert out.parsed.preferences == [] and out.parsed.reason == "fake"
    out = call_parsed(client, CFG, [], NotesAnswer)
    assert len(out.parsed.boxes) == len(baseline.boxes)
    # a scripted FlatResponse still becomes choices for the strategy format
    out = call_parsed(FakeClient(script=[baseline]), CFG, [], StrategyAnswer)
    assert out.parsed.boxes == [b.id for b in baseline.boxes]
    # echo_soft keeps the instruction text
    client = FakeClient()
    client.echo_soft = True
    out = call_json(client, CFG, [
        {"role": "user", "content": "Jars left.\n\nItems (id: name):\n1: Jar"}],
        {}, PackingSettings.model_validate_json)
    assert out.parsed.soft_instructions == ["Jars left."]
    assert PlacementRow and RotationChoice  # (imports used by other tests)
