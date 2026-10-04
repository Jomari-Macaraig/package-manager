"""Pipeline v2 test gaps: every code check of every job (one passing and one
failing case), every degradation path through run_v2, choose edges, the call
helpers' error mapping and a few feedback/eval edges. FakeClient only."""
from types import SimpleNamespace

import openai
import pytest

from app.fake_llm import FakeClient
from packing.jobs import check as check_job
from packing.jobs import extract as extract_job
from packing.jobs import notes as notes_job
from packing.jobs import strategy as strategy_job
from packing.jobs.base import call_json, call_parsed
from packing.jobs.check import CheckAnswer, Verdict
from packing.jobs.notes import BoxNotes, NotesAnswer
from packing.jobs.strategy import (Preference, StrategyAnswer,
                                   strategy_answer_from_plan)
from packing.schemas import RotationChoice
from packing.settings import (BlockRule, PackingSettings, prepare_boxes,
                              prepare_items)
from packing.packer import pack
from packing.planner import RunCancelled
from app import store, store_v2
from packing.v2 import ConfigErrorV2, RunCancelledV2, choose, run_v2
from test_v2_api import (RID, all_jobs, db, group, make_job,  # noqa: F401
                         make_run, start_v2, wait_v2)
from test_v2_core import (BOUNDS, CFG, JOBS, CheckAnswer as _CA,  # noqa: F401
                          g_plans, http_error, length_error, notes_inputs,
                          order, prepared, run_check, run_notes,
                          run_strategy, strategy_inputs, tiny)


def verdict_checks(checks):
    return {c.name: c.passed for c in checks}


# --- extract: each check, one passing and one failing ---------------------------

def extract_checks(parsed):
    items, boxes, _ = order("A")
    return verdict_checks(extract_job.checks(parsed, items, boxes, BOUNDS))


NAMES = ["parsed", "known_ids", "in_bounds", "applies", "not_empty"]


def test_extract_checks_all_pass_for_a_good_answer():
    result = extract_checks(PackingSettings(padding=0.5))
    assert list(result) == NAMES and all(result.values())


@pytest.mark.parametrize("name,settings", [
    ("known_ids", PackingSettings(upright=[99])),
    ("in_bounds", PackingSettings(padding=BOUNDS["padding"] + 1)),
    ("in_bounds", PackingSettings(blocks=[BlockRule(item_id=1, size=2),
                                          BlockRule(item_id=1, size=3)])),
    ("applies", PackingSettings(wall_clearance=6)),
    ("not_empty", PackingSettings()),
])
def test_extract_each_check_fails_alone(name, settings):
    result = extract_checks(settings)
    assert result[name] is False
    failed = [n for n, ok in result.items() if not ok]
    # an unknown id also breaks prepare_items, so "applies" follows it
    assert failed == ([name, "applies"] if name == "known_ids" else [name])


def test_extract_parsed_failure_reports_only_parsed():
    assert extract_checks(None) == {"parsed": False}


def test_extract_not_empty_passes_with_only_a_soft_instruction():
    assert extract_checks(PackingSettings(soft_instructions=["x"]))[
        "not_empty"] is True


def test_extract_in_bounds_edge_is_inclusive():
    assert extract_checks(PackingSettings(padding=BOUNDS["padding"]))[
        "in_bounds"] is True


# --- strategy: each check, one passing and one failing --------------------------

def single_cube_setup(ratio):
    items, boxes = tiny(("Cube", (2, 2, 2), 1), box=(10, 8, 8))
    boxes.append({"id": 2, "name": "Wide", "weightMax": 100,
                  "dimensions": {"x": 10, "y": 8, "z": 8 * ratio}})
    boxes.append({"id": 3, "name": "Tiny", "weightMax": 100,
                  "dimensions": {"x": 3, "y": 3, "z": 3}})
    settings = PackingSettings()
    items_p = prepare_items(items, boxes, settings)
    boxes_p = prepare_boxes(boxes, settings)
    baseline, errors = pack(items_p[:], [b for b in boxes_p if b["id"] == 1],
                            settings)
    assert not errors
    return items_p, boxes_p, settings, baseline


def answer_for(items_p, boxes, quantity_units=1):
    return StrategyAnswer(
        boxes=boxes, order=[i["id"] for i in items_p],
        rotations=[RotationChoice(id=i["id"], dims=tuple(
            i["dimensions"][a] for a in "xyz")) for i in items_p],
        preferences=[], reason="x")


def evaluate(answer, setup, baseline="packer"):
    items_p, boxes_p, settings, base = setup
    return strategy_job._evaluate(
        answer, items_p, boxes_p, settings,
        base if baseline == "packer" else None)


STRATEGY_NAMES = ["choices_valid", "plan_valid", "no_extra_leftovers",
                  "no_extra_boxes", "volume_within_margin",
                  "preferences_honoured"]


def test_strategy_checks_all_pass():
    setup = single_cube_setup(1.0)
    flat, errors, checks, valid, _ = evaluate(
        answer_for(setup[0], [1]), setup)
    assert valid and not errors and flat is not None
    assert [c.name for c in checks] == STRATEGY_NAMES
    assert all(c.passed for c in checks)


def test_strategy_choices_valid_fails_with_no_plan():
    setup = single_cube_setup(1.0)
    flat, errors, checks, valid, report = evaluate(
        answer_for(setup[0], [99]), setup)
    assert flat is None and not valid and report == []
    assert [(c.name, c.passed) for c in checks] == [("choices_valid", False)]
    assert "unknown box type id 99" in checks[0].detail
    assert errors == ["unknown box type id 99"]


def test_strategy_plan_valid_fails_when_validation_reports_errors(monkeypatch):
    setup = single_cube_setup(1.0)
    real = strategy_job.pack_guided

    def invalid(*args, **kwargs):
        flat, _ = real(*args, **kwargs)
        return flat, ["boxes overlap"]
    monkeypatch.setattr(strategy_job, "pack_guided", invalid)
    flat, errors, checks, valid, report = evaluate(
        answer_for(setup[0], [1]), setup)
    assert not valid and errors == ["boxes overlap"] and report == []
    named = verdict_checks(checks)
    assert named["choices_valid"] is True and named["plan_valid"] is False
    assert "no_extra_boxes" not in named  # later checks only on a valid plan
    assert checks[1].detail == "boxes overlap"


def test_strategy_no_extra_leftovers_fails_and_makes_the_plan_invalid():
    setup = single_cube_setup(1.0)
    flat, errors, checks, valid, _ = evaluate(answer_for(setup[0], []), setup)
    named = verdict_checks(checks)
    assert named["plan_valid"] is True and named["no_extra_leftovers"] is False
    assert not valid and errors and "leftovers fit" in errors[0]


def test_strategy_no_extra_boxes_fails_but_the_plan_stays_valid():
    items, boxes = tiny(("Cube", (2, 2, 2), 3), box=(10, 8, 8))
    boxes.append({"id": 3, "name": "Tiny", "weightMax": 100,
                  "dimensions": {"x": 3, "y": 3, "z": 3}})
    settings = PackingSettings()
    items_p = prepare_items(items, boxes, settings)
    boxes_p = prepare_boxes(boxes, settings)
    baseline, _ = pack(items_p, [b for b in boxes_p if b["id"] == 1], settings)
    assert len(baseline.boxes) == 1
    flat, errors, checks, valid, _ = strategy_job._evaluate(
        answer_for(items_p, [3, 3, 3]), items_p, boxes_p, settings, baseline)
    named = verdict_checks(checks)
    assert named["no_extra_boxes"] is False and valid and not errors
    assert "3 boxes, the packer 1" in next(
        c.detail for c in checks if c.name == "no_extra_boxes")
    assert named["volume_within_margin"] is True  # smaller boxes ship less


def test_strategy_volume_margin_passes_at_1_15_and_fails_at_1_16():
    setup = single_cube_setup(1.15)
    _, _, checks, valid, _ = evaluate(answer_for(setup[0], [2]), setup)
    assert verdict_checks(checks)["volume_within_margin"] is True and valid
    setup = single_cube_setup(1.16)
    _, _, checks, valid, _ = evaluate(answer_for(setup[0], [2]), setup)
    named = verdict_checks(checks)
    assert named["volume_within_margin"] is False and valid
    assert named["no_extra_boxes"] is True


def test_strategy_without_a_packer_plan_the_comparison_checks_pass():
    setup = single_cube_setup(1.0)
    _, _, checks, valid, _ = evaluate(
        answer_for(setup[0], [1]), setup, baseline=None)
    named = verdict_checks(checks)
    assert valid and named["no_extra_boxes"] and named["volume_within_margin"]
    assert next(c for c in checks if c.name == "no_extra_boxes"
                ).detail == "no packer plan"


def test_strategy_preferences_honoured_passes_and_fails():
    items_p, boxes_p, settings, est, baseline = strategy_inputs("G")
    good = strategy_answer_from_plan(baseline).model_copy(update={
        "boxes": [1, 2], "order": [2, 1, 3],
        "preferences": [Preference(id=1, side="right", level="any"),
                        Preference(id=3, side="left", level="any")]})
    _, _, checks, valid, report = strategy_job._evaluate(
        good, items_p, boxes_p, settings, baseline)
    named = verdict_checks(checks)
    assert valid and named["preferences_honoured"] is True
    assert report and all(r["honoured"] == r["total"] for r in report)
    impossible = strategy_answer_from_plan(baseline).model_copy(update={
        "preferences": [Preference(id=i, side="left", level="any")
                        for i in (1, 2, 3)]})
    _, _, checks, valid, _ = strategy_job._evaluate(
        impossible, items_p, boxes_p, settings, baseline)
    named = verdict_checks(checks)
    assert valid and named["preferences_honoured"] is False
    assert all(v for k, v in named.items() if k != "preferences_honoured")


def test_strategy_unparsed_reply_has_only_the_parsed_check():
    run, _, _ = run_strategy("A", [None, None])
    assert [(c.name, c.passed) for c in run.result.checks] == [
        ("parsed", False)]


def test_strategy_fallback_output_is_the_packers_implied_choices_on_a_cut_off():
    run, _, baseline = run_strategy("A", [length_error()])
    assert run.result.status == "fallback" and run.flat is None
    assert run.result.output == strategy_answer_from_plan(
        baseline, "code packer's plan").model_dump(mode="json")
    assert run.errors[0].startswith("model call failed")


# --- check: each check -------------------------------------------------------------

def v(index, verdict="honoured"):
    return Verdict(index=index, verdict=verdict, reason="r")


def test_check_parsed_and_covers_all_pass_for_every_instruction_exactly_once():
    checks = check_job.checks(CheckAnswer(verdicts=[v(1), v(0)]), 2)
    assert [(c.name, c.passed) for c in checks] == [
        ("parsed", True), ("covers_all", True)]


def test_check_covers_all_fails_on_a_missing_a_duplicate_and_an_extra_index():
    for verdicts in ([v(0)], [v(0), v(0)], [v(0), v(1), v(2)], [v(1), v(2)]):
        checks = check_job.checks(CheckAnswer(verdicts=verdicts), 2)
        assert checks[0].passed and not checks[1].passed
        assert "expected 0..1" in checks[1].detail


def test_check_parsed_fails_alone():
    checks = check_job.checks(None, 2)
    assert [(c.name, c.passed) for c in checks] == [("parsed", False)]


def test_check_cut_off_falls_back_to_cannot_tell_with_usage_kept():
    r, _ = run_check([length_error()], soft=("a", "b", "c"))
    assert r.status == "fallback" and r.error.startswith(
        "LengthFinishReasonError")
    assert [x["verdict"] for x in r.output] == ["cannot_tell"] * 3
    assert [x["index"] for x in r.output] == [0, 1, 2]
    assert r.completion_tokens == 100 and r.calls == 1


def test_check_output_is_sorted_by_index():
    r, _ = run_check([CheckAnswer(verdicts=[v(1), v(0)])], soft=("a", "b"))
    assert r.status == "ok" and [x["index"] for x in r.output] == [0, 1]


# --- notes: each check (parsed and the call-error fallback) ---------------------------

def test_notes_parsed_failure_falls_back_to_the_template():
    items_p, _, baseline = notes_inputs()
    r, _ = run_notes([None])
    assert r.status == "fallback" and [
        (c.name, c.passed) for c in r.checks] == [("parsed", False)]
    assert r.output == notes_job.template_notes(baseline, items_p)


def test_notes_cut_off_falls_back_to_the_template_with_usage_kept():
    items_p, _, baseline = notes_inputs()
    r, _ = run_notes([length_error()])
    assert r.status == "fallback" and r.completion_tokens == 100
    assert r.output == notes_job.template_notes(baseline, items_p)


def test_notes_each_check_passes_alone_on_the_template():
    items_p, _, baseline = notes_inputs()
    parsed = NotesAnswer(boxes=[BoxNotes(**n) for n in
                                notes_job.template_notes(baseline, items_p)])
    checks = notes_job.checks(parsed, baseline, items_p)
    assert [c.name for c in checks] == [
        "parsed", "one_entry_per_box", "step_count", "mentions_items",
        "counts_match"]
    assert all(c.passed for c in checks)


def test_notes_step_count_edges_are_one_and_eight():
    items_p, _, baseline = notes_inputs()
    boxes = sorted(b.box for b in baseline.boxes)
    step = "Load 1 × Jar"

    def named(n):
        parsed = NotesAnswer(boxes=[BoxNotes(box=b, steps=[step] * n)
                                    for b in boxes])
        return verdict_checks(notes_job.checks(parsed, baseline, items_p))

    assert named(1)["step_count"] and named(8)["step_count"]
    assert not named(0)["step_count"] and not named(9)["step_count"]


def test_notes_one_entry_per_box_fails_for_duplicate_and_unknown_boxes():
    items_p, _, baseline = notes_inputs()
    template = notes_job.template_notes(baseline, items_p)
    dup = NotesAnswer(boxes=[BoxNotes(**template[0])] * 2)
    unknown = NotesAnswer(boxes=[BoxNotes(box=9, steps=["Load 1 × Jar"])])
    for parsed in (dup, unknown):
        assert not verdict_checks(
            notes_job.checks(parsed, baseline, items_p))["one_entry_per_box"]


# --- call helpers: error mapping ------------------------------------------------------

def filter_error():
    exc = openai.ContentFilterFinishReasonError.__new__(
        openai.ContentFilterFinishReasonError)
    exc.completion = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=50, completion_tokens=7,
        completion_tokens_details=None))
    return exc


def test_call_parsed_content_filter_keeps_usage_and_is_not_cut_off():
    out = call_parsed(FakeClient(script=[filter_error()]), CFG, [], CheckAnswer)
    assert out.parsed is None and not out.cut_off
    assert out.error.startswith("ContentFilterFinishReasonError")
    assert (out.usage["input_tokens"], out.usage["output_tokens"]) == (50, 7)


def test_call_parsed_cut_off_flag_and_error_prefix():
    out = call_parsed(FakeClient(script=[length_error()]), CFG, [], CheckAnswer)
    assert out.cut_off and out.usage["input_tokens"] == 500


@pytest.mark.parametrize("cls,status", [(openai.RateLimitError, 429),
                                        (openai.InternalServerError, 500)])
def test_call_parsed_other_api_status_errors_are_error_strings(cls, status):
    out = call_parsed(FakeClient(script=[http_error(cls, status)]), CFG, [],
                      CheckAnswer)
    assert out.parsed is None and not out.cut_off
    assert out.error.startswith(cls.__name__)
    assert out.usage == {"input_tokens": 0, "output_tokens": 0,
                         "reasoning_tokens": 0}


def test_call_parsed_refusal_text_is_in_the_error():
    out = call_parsed(FakeClient(script=[None]), CFG, [], CheckAnswer)
    assert out.parsed is None and out.error == "no answer returned: no plan"
    assert out.usage["input_tokens"] == 1000  # usage of the refused call


def test_call_parsed_empty_answer_without_refusal_says_empty():
    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(
            parse=lambda **kw: SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    parsed=None, content=None, refusal=None))],
                usage=None)))
    out = call_parsed(Client(), CFG, [], CheckAnswer)
    assert out.error == "no answer returned: empty"
    assert out.usage["input_tokens"] == 0


def test_call_json_cut_off_content_filter_and_empty_mapping():
    out = call_json(FakeClient(create_script=[length_error()]), CFG, [], {},
                    PackingSettings.model_validate_json)
    assert out.cut_off and out.error.startswith("LengthFinishReasonError")
    assert out.usage["output_tokens"] == 100
    out = call_json(FakeClient(create_script=[filter_error()]), CFG, [], {},
                    PackingSettings.model_validate_json)
    assert not out.cut_off and out.usage["output_tokens"] == 7
    out = call_json(FakeClient(create_script=[""]), CFG, [], {},
                    PackingSettings.model_validate_json)
    assert out.error.startswith("no answer returned") and out.parsed is None


# --- run_v2: every degradation path ------------------------------------------------------

def run_hooked(key, client, hooks=None, **kwargs):
    """run_v2 with after-job hooks: hooks[job](client) runs once that job's
    event is out (to script the next job's answer)."""
    items, boxes, instructions = order(key)
    client.echo_soft = True
    events = []

    def on_event(event):
        events.append(event)
        if event["type"] == "job" and event["job"] in (hooks or {}):
            hooks[event["job"]](client)

    return run_v2(client, items, boxes, instructions, JOBS, bounds=BOUNDS,
                  on_event=on_event,
                  on_context=lambda s, d: setattr(client, "context", d),
                  **kwargs), events


def test_extract_fallback_keeps_the_text_as_a_soft_instruction_and_goes_on():
    client = FakeClient(create_script=["not json"])
    result, _ = run_hooked("G", client)
    extract = result.jobs[0]
    assert extract.status == "fallback" and extract.output == {
        **PackingSettings().model_dump(mode="json"),
        "soft_instructions": [order("G")[2].strip()]}
    assert result.settings.soft_instructions == [order("G")[2].strip()]
    assert [j.status for j in result.jobs[1:]] == ["ok", "ok", "ok"]
    assert [v["verdict"] for v in result.verdicts] == ["honoured"]


def test_extract_not_empty_fallback_inside_run_v2():
    client = FakeClient(create_script=[PackingSettings().model_dump_json()])
    result, _ = run_hooked("G", client)
    extract = result.jobs[0]
    assert extract.status == "fallback"
    assert [c.name for c in extract.checks if not c.passed] == ["not_empty"]
    assert result.settings.soft_instructions == [order("G")[2].strip()]


def test_strategy_fallback_uses_the_packer_and_skips_nothing_else():
    client = FakeClient(script=[None, None])
    result, events = run_hooked("G", client)
    assert result.jobs[1].status == "fallback"
    assert (result.source, result.reason) == ("packer", "only valid plan")
    assert result.final_flat is result.packer_flat
    assert result.jobs[2].status == "skipped"  # no model plan to check
    assert result.jobs[3].status == "ok"
    assert result.verdicts == []
    assert events[-1]["type"] == "job"


def test_check_fallback_gives_cannot_tell_verdicts_and_the_plan_is_still_chosen():
    client = FakeClient()
    result, _ = run_hooked("G", client, hooks={
        "strategy": lambda c: setattr(c, "script", [None])})
    check = result.jobs[2]
    assert check.status == "fallback"
    assert [v["verdict"] for v in result.verdicts] == ["cannot_tell"]
    assert result.verdicts[0]["instruction"].startswith("Keep the jars")
    assert result.source in ("model", "packer")
    assert result.reason != "honours the instructions"
    assert result.final_flat is not None and result.jobs[3].status == "ok"


def test_notes_fallback_gives_template_notes():
    client = FakeClient()
    result, _ = run_hooked("G", client, hooks={
        "check": lambda c: setattr(c, "script", [None])})
    notes = result.jobs[3]
    assert notes.status == "fallback"
    assert result.notes == notes_job.template_notes(
        result.final_flat, result.items_p)
    assert all(s.startswith("Load ") for n in result.notes for s in n["steps"])


@pytest.mark.parametrize("stage", ["extract", "strategy", "check", "notes"])
def test_config_error_in_each_stage_gives_four_jobs(stage):
    auth = http_error(openai.AuthenticationError, 401)
    client = FakeClient()
    hooks = {}
    if stage == "extract":
        client.create_script = [auth]
    elif stage == "strategy":
        client.script = [auth]
    elif stage == "check":
        hooks["strategy"] = lambda c: setattr(c, "script", [auth])
    else:
        hooks["check"] = lambda c: setattr(c, "script", [auth])
    with pytest.raises(ConfigErrorV2) as caught:
        run_hooked("G", client, hooks=hooks)
    jobs = caught.value.jobs
    assert [j.job for j in jobs] == ["extract", "strategy", "check", "notes"]
    assert isinstance(caught.value.original, openai.AuthenticationError)
    finished = ["extract", "strategy", "check", "notes"].index(stage)
    assert all(j.status in ("ok", "fallback") for j in jobs[:finished])
    assert [(j.status, j.error) for j in jobs[finished:]] == [
        ("skipped", "not run")] * (4 - finished)
    assert all(j.model == "m" for j in jobs)


@pytest.mark.parametrize("cls,status", [(openai.PermissionDeniedError, 403),
                                        (openai.NotFoundError, 404),
                                        (openai.BadRequestError, 400)])
def test_every_config_error_class_maps_to_config_error_v2(cls, status):
    client = FakeClient(script=[http_error(cls, status)])
    with pytest.raises(ConfigErrorV2) as caught:
        run_hooked("G", client)
    assert isinstance(caught.value.original, cls)
    assert len(caught.value.jobs) == 4


@pytest.mark.parametrize("stop_at,done", [(0, 0), (1, 1), (2, 1), (3, 2),
                                          (4, 3)])
def test_cancel_before_each_stage_gives_four_jobs(stop_at, done):
    items, boxes, instructions = order("G")
    calls = iter(range(100))
    client = FakeClient()
    client.echo_soft = True
    with pytest.raises(RunCancelledV2) as caught:
        run_v2(client, items, boxes, instructions, JOBS, bounds=BOUNDS,
               on_context=lambda s, d: setattr(client, "context", d),
               should_stop=lambda: next(calls) >= stop_at)
    assert isinstance(caught.value, RunCancelled)
    jobs = caught.value.jobs
    assert [j.job for j in jobs] == ["extract", "strategy", "check", "notes"]
    assert [j.status != "skipped" or j.error != "cancelled"
            for j in jobs[:done]] == [True] * done
    assert [(j.status, j.error) for j in jobs[done:]] == [
        ("skipped", "cancelled")] * (4 - done)
    assert all(j.model == "m" for j in jobs)


# --- choose: remaining edges ------------------------------------------------------------------

def rule3_args(**override):
    _, boxes_p, baseline, model, prefs, rep_m, rep_p = g_plans()
    args = dict(model=model, packer=baseline,
                verdicts=[{"index": 0, "verdict": "honoured", "reason": ""}],
                soft=["Jars left"], rep_m=rep_m, rep_p=rep_p)
    args.update(override)
    return args, boxes_p


def choose_with(**override):
    args, boxes_p = rule3_args(**override)
    return choose(args["model"], args["packer"], args["verdicts"],
                  args["soft"], args["rep_m"], args["rep_p"], boxes_p)


def test_choose_rule_3_holds_with_the_defaults():
    assert choose_with() == ("model", "honours the instructions")


def test_choose_rule_3_a_not_honoured_or_mixed_verdict_fails():
    not_honoured = [{"index": 0, "verdict": "not_honoured", "reason": ""}]
    mixed = [{"index": 0, "verdict": "honoured", "reason": ""},
             {"index": 1, "verdict": "cannot_tell", "reason": ""}]
    for verdicts in (not_honoured, mixed):
        source, reason = choose_with(verdicts=verdicts, soft=["a", "b"])
        assert reason != "honours the instructions"
        assert source in ("model", "packer")


def test_choose_rule_3_all_verdicts_must_be_honoured_not_just_one():
    two = [{"index": 0, "verdict": "honoured", "reason": ""},
           {"index": 1, "verdict": "honoured", "reason": ""}]
    assert choose_with(verdicts=two, soft=["a", "b"])[1] == (
        "honours the instructions")


def test_choose_rule_3_needs_a_model_preference_row_not_an_empty_list():
    assert choose_with(rep_m=[])[1] != "honours the instructions"


def test_choose_neither_valid_and_single_valid_plans_ignore_verdicts():
    _, boxes_p, baseline, model, *_ = g_plans()
    cannot = [{"index": 0, "verdict": "cannot_tell", "reason": ""}]
    assert choose(None, None, cannot, ["x"], [], [], boxes_p) == (
        "none", "no valid plan")
    assert choose(None, baseline, cannot, ["x"], [], [], boxes_p) == (
        "packer", "only valid plan")
    assert choose(model, None, cannot, ["x"], [], [], boxes_p) == (
        "model", "only valid plan")


def test_choose_rule_4_applies_when_the_model_has_fewer_leftovers_but_no_wish():
    # without soft instructions rule 3 never fires; metrics decide
    args, boxes_p = rule3_args(soft=[], verdicts=[])
    source, reason = choose(args["model"], args["packer"], [], [],
                            args["rep_m"], args["rep_p"], boxes_p)
    assert reason in ("better metrics", "packer has better metrics")


# --- feedback and eval edges (API) -----------------------------------------------------------

def test_feedback_accepts_a_fallback_job_and_a_cancelled_run(client, db):
    rid = make_run(status="cancelled", jobs=all_jobs(
        strategy=make_job("strategy", "fallback"),
        check=make_job("check", "skipped"),
        notes=make_job("notes", "skipped")))
    url = f"/api/v2/runs/{rid}/feedback"
    r = client.put(f"{url}/strategy", json={"rating": "down"})
    assert r.status_code == 200 and r.json()["rating"] == "down"
    assert client.put(f"{url}/check", json={"rating": "up"}
                      ).status_code == 422  # skipped
    assert client.put(f"{url}/extract", json={"rating": None, "comment": "c"}
                      ).json()["rating"] is None


def test_feedback_validation_order_and_errors(client, db):
    assert client.put("/api/v2/runs/not-a-uuid/feedback/notes",
                      json={"rating": "up"}).status_code == 404  # bad id
    assert client.put(f"/api/v2/runs/{RID}/feedback/notes",
                      json={"rating": "up"}).status_code == 404  # unknown run
    rid = make_run(jobs=all_jobs(check=make_job("check", output=[
        {"index": 0, "verdict": "honoured", "reason": ""}])))
    url = f"/api/v2/runs/{rid}/feedback"
    assert client.put(f"{url}/check", json={"wrong_verdicts": [1]}
                      ).status_code == 422  # out of range
    assert client.put(f"{url}/check", json={"wrong_verdicts": [-1]}
                      ).status_code == 422
    assert client.put(f"{url}/strategy", json={"wrong_verdicts": [0]}
                      ).status_code == 422  # not the check job
    ok = client.put(f"{url}/check", json={"rating": None, "wrong_verdicts": [0]})
    assert ok.status_code == 200 and ok.json()["rating"] is None
    assert ok.json()["wrong_verdicts"] == [0]
    # an empty list is allowed on any job (nothing marked wrong)
    assert client.put(f"{url}/strategy", json={"wrong_verdicts": []}
                      ).status_code == 200


def test_feedback_on_running_run_is_409_even_for_unknown_job_rows(client, gated=None):
    store.init()
    store_v2.init()
    store_v2.create_run(RID, "{}", trace_id="t", request_id="r",
                        client_ip="ip", user_agent="ua", title="x", fake=False)
    r = client.put(f"/api/v2/runs/{RID}/feedback/notes", json={"rating": "up"})
    assert r.status_code == 409


def test_eval_per_check_share_counts_only_calls_that_have_the_check(client, db):
    make_run(jobs=all_jobs(strategy=make_job(
        "strategy", checks=[("parsed", True), ("plan_valid", False)])))
    make_run(jobs=all_jobs(strategy=make_job(
        "strategy", "fallback", checks=[("parsed", False)])))
    [g] = group(client, job="strategy")
    assert g["per_check"] == {"parsed": 0.5, "plan_valid": 0.0}
    assert g["all_checks_rate"] == 0.0 and g["ok_rate"] == 0.5


def test_eval_skipped_rows_do_not_dilute_rates_or_latency(client, db):
    make_run(jobs=all_jobs(check=make_job("check", latency=2.0, cost=0.04,
                                          checks=[("parsed", True)])))
    make_run(jobs=all_jobs(check=make_job("check", "skipped", latency=99.0,
                                          cost=9.0)))
    [g] = group(client, job="check")
    assert (g["calls"], g["skipped"]) == (1, 1)
    assert g["ok_rate"] == 1.0 and g["fallback_rate"] == 0.0
    assert g["p50_latency_s"] == g["p95_latency_s"] == 2.0
    assert g["avg_cost"] == pytest.approx(0.04)


def test_eval_percentiles_on_twenty_values_use_nearest_rank(client, db):
    for n in range(1, 21):
        make_run(jobs=all_jobs(notes=make_job("notes", latency=float(n))))
    [g] = group(client, job="notes")
    assert g["calls"] == 20
    assert (g["p50_latency_s"], g["p95_latency_s"]) == (10.0, 19.0)


def test_eval_wrong_verdict_rate_is_none_without_reviewed_runs(client, db):
    verdicts = [{"index": 0, "verdict": "honoured", "reason": ""}]
    make_run(jobs=all_jobs(check=make_job("check", output=verdicts)))
    [g] = group(client, job="check")
    assert g["wrong_verdict_rate"] is None


def test_eval_model_won_rate_none_without_succeeded_runs(client, db):
    make_run(status="failed", source="model")
    [g] = group(client, job="strategy")
    assert g["model_won_rate"] is None


def test_eval_since_and_job_filters_combine(client, db):
    make_run(created_at="2026-03-01T10:00:00.000Z")
    make_run(created_at="2026-03-02T10:00:00.000Z")
    assert [g["job"] for g in group(client, since="2026-03-02T00:00:00Z")] == [
        "extract", "strategy", "check", "notes"]
    assert group(client, job="notes", since="2026-03-03T00:00:00Z") == []


# --- notes: split steps, word boundaries, plurals -------------------------------------

def name_flat(units):
    """One box holding units = [(item id, name)...]; returns (flat, items)."""
    from packing.schemas import FlatResponse
    rows = [{"box": 1, "seq": i + 1, "id": uid, "pos": [0, 0, 0],
             "dims": [1, 1, 1]} for i, (uid, _) in enumerate(units)]
    flat = FlatResponse(boxes=[{"box": 1, "id": 1}], placements=rows,
                        leftovers=[], notes=[])
    items = [{"id": uid, "name": n} for uid, n in dict(units).items()]
    return flat, items


def notes_named(steps, units):
    flat, items = name_flat(units)
    parsed = NotesAnswer(boxes=[BoxNotes(box=1, steps=steps)])
    return {c.name: c for c in notes_job.checks(parsed, flat, items)}


KETTLES = [(1, "Kettle")] * 3


def test_notes_counts_match_sums_split_steps():
    c = notes_named(["Load 2 × Kettle on the floor",
                     "Load 1 × Kettle on top"], KETTLES)
    assert c["counts_match"].passed and c["mentions_items"].passed


def test_notes_counts_match_fails_when_split_steps_sum_wrong():
    c = notes_named(["Load 2 × Kettle", "Load 2 × Kettle"], KETTLES)
    assert not c["counts_match"].passed
    assert c["counts_match"].detail == (
        "box 1: Kettle: steps say 2 + 2 = 4, box has 3")
    assert not notes_named(["Load 2 × Kettle"], KETTLES)[
        "counts_match"].passed


def test_notes_names_match_on_word_boundaries_longest_first():
    units = [(1, "Jar"), (2, "Jar Lid"), (2, "Jar Lid")]
    ok = notes_named(["Load 1 × Jar", "Load 2 × Jar Lid"], units)
    assert ok["mentions_items"].passed and ok["counts_match"].passed
    # "Jar" only appears inside "Jar Lid": it is missing, not found
    lid_only = notes_named(["Load 2 × Jar Lid"], units)
    assert lid_only["mentions_items"].detail == "box 1: Jar"
    # "2 × Jar Lid" must not be read as 2 Jars
    swapped = notes_named(["Load 2 × Jar", "Load 1 × Jar Lid"], units)
    assert not swapped["counts_match"].passed


def test_notes_plurals_still_match():
    c = notes_named(["Load 3 × Kettles"], KETTLES)
    assert c["mentions_items"].passed and c["counts_match"].passed
    assert not notes_named(["Load 2 Kettles"], KETTLES)["counts_match"].passed


# --- strategy: a config error on a later call keeps the first call's usage -------------

def test_config_error_on_strategy_call_two_keeps_call_one_usage():
    auth = http_error(openai.AuthenticationError, 401)
    client = FakeClient()
    # an unusable first reply (so there is a second call), then the error
    client.script = [None, auth]
    with pytest.raises(ConfigErrorV2) as caught:
        run_hooked("G", client)
    jobs = {j.job: j for j in caught.value.jobs}
    s = jobs["strategy"]
    assert s.calls == 1 and s.status == "fallback"
    assert "AuthenticationError" in s.error or "401" in s.error
    assert [(jobs[n].status, jobs[n].error) for n in ("check", "notes")] == [
        ("skipped", "not run")] * 2


# --- fix loop 2: es plurals, back-references, odd names, error cap -------------------

def test_notes_es_plurals_match_and_count():
    c = notes_named(["Load 2 × Shoe Boxes"], [(1, "Shoe Box")] * 2)
    assert c["mentions_items"].passed and c["counts_match"].passed
    c = notes_named(["Load 2 Glasses"], [(1, "Glass")] * 2)
    assert c["mentions_items"].passed and c["counts_match"].passed


def test_notes_back_reference_is_not_summed():
    units = KETTLES + [(2, "Jar")] * 2
    c = notes_named(["Load 3 × Kettle",
                     "Load 2 × Jar on top of the 3 kettles"], units)
    assert c["mentions_items"].passed and c["counts_match"].passed
    # split steps are still summed, a wrong sum still fails
    assert notes_named(["Load 2 × Kettle on the floor",
                        "Load 1 × Kettle on top"], KETTLES)[
        "counts_match"].passed
    assert not notes_named(["Load 2 × Kettle"], KETTLES)[
        "counts_match"].passed
    assert not notes_named(["Load 2 × Kettle", "Load 2 × Kettle"], KETTLES)[
        "counts_match"].passed


def test_notes_jar_vs_jar_lid_with_per_step_counting():
    units = [(1, "Jar"), (2, "Jar Lid"), (2, "Jar Lid")]
    ok = notes_named(["Load 1 × Jar", "Load 2 × Jar Lid on the jar"], units)
    assert ok["mentions_items"].passed and ok["counts_match"].passed
    bad = notes_named(["Load 2 × Jar", "Load 1 × Jar Lid"], units)
    assert not bad["counts_match"].passed


def test_notes_names_with_non_word_edges_match():
    c = notes_named(["Load 2 × #10 Envelope"], [(1, "#10 Envelope")] * 2)
    assert c["mentions_items"].passed and c["counts_match"].passed


def test_strategy_config_error_text_is_capped():
    class Boom(openai.AuthenticationError):
        def __str__(self):
            return "x" * 1000

    client = FakeClient()
    client.script = [None, http_error(Boom, 401)]
    with pytest.raises(ConfigErrorV2) as caught:
        run_hooked("G", client)
    s = {j.job: j for j in caught.value.jobs}["strategy"]
    assert s.status == "fallback" and s.output is None
    assert len(s.error) <= len("Boom: ") + 300
