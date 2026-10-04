"""box_estimates, repair help, patch-style fix rounds, guide mode."""
from app.fake_llm import FakeClient
from packing.packer import pack, pack_guided
from packing.planner import (guidance_from_plan, plan_with_guidance,
                             plan_with_validation)
from packing.prompt_builder import (GUIDE_SYSTEM_PROMPT, SYSTEM_PROMPT,
                                    build_user_prompt)
from packing.repair import gap, repair_help, units_short, units_to_move
from packing.schemas import Guidance, LeftoverRow, PlacementRow, RotationChoice
from packing.validation import validate_plan
from test_planner import setup


def broken(baseline, seq=14, pos=(0.0, 0.0, 0.0)):
    """The packer's plan with one unit moved onto another (overlap)."""
    return baseline.model_copy(update={"placements": [
        r.model_copy(update={"pos": list(pos)}) if r.seq == seq else r
        for r in baseline.placements]})


def test_estimates_one_row_per_box_type():
    items_p, boxes_p, settings, baseline, _ = setup("A")
    est = []
    flat, _ = pack(items_p, boxes_p, settings, est)
    assert flat == baseline
    by = {e["id"]: e for e in est}
    assert set(by) == {b["id"] for b in boxes_p}
    assert by[3] == {"id": 3, "fits_all": True, "units": 14,
                     "fill_height": 9.0, "shipped_volume": 1764.0}
    assert by[1]["fits_all"] is False


def test_user_prompt_has_box_estimates_and_prompt_drops_ideal_box_rule():
    items_p, boxes_p, settings, _, _ = setup("A")
    est = []
    pack(items_p, boxes_p, settings, est)
    text = build_user_prompt(items_p, boxes_p, "", [], est)
    table = text.split("box_estimates")[1]
    assert "id|name|fits_all|units|fill_height|shipped_volume" in table
    assert "3|L-14|true|14|9|1764" in table
    assert "1|S-10|false|4|5|\n" in table  # no volume unless fits_all
    assert "even if it is a bigger box" not in SYSTEM_PROMPT
    assert "box_estimates" in SYSTEM_PROMPT


def test_compact_facts_drop_the_noise():
    items_p, boxes_p, settings, _, _ = setup("A")
    text = build_user_prompt(items_p, boxes_p)
    assert "id|name|weight|x|y|z|quantity" in text  # no color column
    assert "rotations (5 rows)\nid|rotations\n1|4.5×3.75×3.75; " in text
    for gone in ("fits (", "item_stats", "box_stats", "totals:",
                 "single_box_candidates", "ideal_fill_height"):
        assert gone not in text and gone not in SYSTEM_PROMPT
    verbose = build_user_prompt(items_p, boxes_p, verbose_facts=True)
    assert "fits (24 rows)" in verbose and "ideal_fill_height" in verbose
    assert "id|name|color|weight" in verbose


def test_compact_facts_list_unpackable_and_only_fitting_rotations():
    items_p, boxes_p, settings, _, _ = setup("D")
    text = build_user_prompt(items_p, boxes_p)
    assert "unpackable (1 rows)" in text and "3|no allowed rotation" in text
    rotations = text.split("rotations (")[1].split("\n\n")[0]
    assert "\n3|" not in rotations  # the stand fits nowhere
    assert "\n1|9.5×3.25×4.5; " in rotations


def test_units_to_move_takes_the_later_unit_of_an_overlap():
    errors = ["box 1 seq 9 (id 5) overlaps box 1 seq 11 (id 1)",
              "box 1 seq 10 (id 4): not fully supported at x=5; ...",
              "box 2: contents weigh 80 lb"]
    assert units_to_move(errors) == [(1, 11), (1, 10)]


def test_repair_help_finds_a_free_spot_that_validates():
    items_p, boxes_p, settings, baseline, _ = setup("A")
    bad = broken(baseline)
    errors = validate_plan(bad, items_p, boxes_p, settings)
    assert errors
    lines = repair_help(bad, items_p, boxes_p, errors, settings)
    assert lines[0] == "box 1 (L-14) inside: x 10, y 14, z 14"
    assert any("box 1 seq 14 (id 3) is at x 0–4" in l for l in lines)
    spot = next(l for l in lines if "fits at pos" in l)
    pos = [float(v) for v in spot.split("pos [")[1].split("]")[0].split(",")]
    dims = [float(v) for v in spot.split("dims [")[1].split("]")[0].split(",")]
    fixed = broken(bad, 14, pos).model_copy(update={"placements": [
        r.model_copy(update={"pos": pos, "dims": dims}) if r.seq == 14 else r
        for r in bad.placements]})
    assert validate_plan(fixed, items_p, boxes_p, settings) == []


def test_fix_feedback_is_a_patch_with_help():
    items_p, boxes_p, settings, baseline, messages = setup("A")
    client = FakeClient(script=[broken(baseline)])  # then the packer's plan
    client.context = (items_p, boxes_p, settings, baseline)
    result = plan_with_validation(client, "m", messages, items_p, boxes_p,
                                  settings=settings, baseline=baseline)
    feedback = result.history[3]["content"]
    assert "Help (computed in code):" in feedback
    assert "fits at pos" in feedback
    assert "copy every other placement unchanged" in feedback
    assert result.ok


def test_worse_reply_sends_the_model_back_to_its_best_plan():
    items_p, boxes_p, settings, baseline, messages = setup("A")
    one = broken(baseline)
    many = baseline.model_copy(update={"placements": [
        r.model_copy(update={"pos": [0.0, 0.0, 0.0]})
        for r in baseline.placements]})
    client = FakeClient(script=[one, many, many])
    client.context = (items_p, boxes_p, settings, baseline)
    result = plan_with_validation(client, "m", messages, items_p, boxes_p,
                                  settings=settings, baseline=baseline,
                                  seed=False)
    back = result.history[5]["content"]
    assert back.startswith("Your last plan is further from valid")
    assert one.model_dump_json() in back
    # the closest plan is the one reported for the model side
    assert result.model_flat == one
    assert len(result.model_errors) == len(
        validate_plan(one, items_p, boxes_p, settings))
    assert result.source == "packer"


def test_pack_guided_from_the_packer_choices_is_valid():
    items_p, boxes_p, settings, baseline, _ = setup("A")
    g = guidance_from_plan(baseline)
    assert g.boxes == [3]
    flat, errors = pack_guided(items_p, boxes_p, settings, g.boxes, g.order,
                               {r.id: r.dims for r in g.rotations})
    assert errors == [] and not flat.leftovers


def test_pack_guided_rejects_bad_choices():
    items_p, boxes_p, settings, _, _ = setup("A")
    flat, errors = pack_guided(items_p, boxes_p, settings, [99], [1, 77],
                               {1: [1, 2, 3]})
    assert flat is None
    assert "unknown box type id 99" in errors
    assert "unknown item id 77 in order" in errors
    assert any(e.startswith("item 1: rotation [1, 2, 3] is not allowed")
               for e in errors)


def test_pack_guided_leaves_over_what_does_not_fit():
    items_p, boxes_p, settings, _, _ = setup("A")
    flat, errors = pack_guided(items_p, boxes_p, settings, [1], [], {})
    assert flat.leftovers and errors == []  # leftovers alone are valid


def guide_messages(items_p, boxes_p):
    return [{"role": "system", "content": GUIDE_SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(items_p, boxes_p)}]


def test_guide_mode_places_from_choices():
    items_p, boxes_p, settings, baseline, _ = setup("A")
    client = FakeClient()
    client.context = (items_p, boxes_p, settings, baseline)
    events = []
    result = plan_with_guidance(client, "m", guide_messages(items_p, boxes_p),
                                items_p, boxes_p, settings=settings,
                                baseline=baseline, on_event=events.append)
    assert client.parse_calls[0]["response_format"] is Guidance
    assert result.model_flat is not None and not result.model_errors
    assert "Placed by code from the model's choices" in result.model_flat.notes[-1]
    assert events[0]["kind"] == "fix" and events[0]["valid"] is True


def test_guide_mode_feeds_back_bad_choices_then_recovers():
    items_p, boxes_p, settings, baseline, _ = setup("A")
    bad = Guidance(boxes=[1], order=[], notes=[],
                   rotations=[RotationChoice(id=1, dims=[1, 2, 3])])
    small = Guidance(boxes=[1], order=[], rotations=[], notes=[])
    client = FakeClient(script=[bad, small])  # then the packer's choices
    client.context = (items_p, boxes_p, settings, baseline)
    result = plan_with_guidance(client, "m", guide_messages(items_p, boxes_p),
                                items_p, boxes_p, settings=settings,
                                baseline=baseline)
    assert "is not allowed" in result.history[3]["content"]
    # S-10 alone leaves units over that the packer placed: coverage error
    assert "Placement from your choices: 1 box(es) (S-10" in \
        result.history[5]["content"]
    assert result.rounds == 3 and result.model_flat is not None
    assert not result.model_errors


def plates_only(baseline):
    """Like run 9aa6c76f round 1: the plates placed, the rest left over."""
    keep = [r for r in baseline.placements if r.id == 2]
    return baseline.model_copy(update={
        "placements": keep,
        "leftovers": [LeftoverRow(id=i, quantity=n, reason="search failed")
                      for i, n in ((1, 4), (3, 3), (4, 1), (5, 2))]})


def test_gap_counts_units_not_errors():
    items_p, boxes_p, settings, baseline, _ = setup("A")
    one = broken(baseline)
    nothing = baseline.model_copy(update={"placements": [], "leftovers": []})
    e_one = validate_plan(one, items_p, boxes_p, settings)
    e_nothing = validate_plan(nothing, items_p, boxes_p, settings)
    assert len(e_nothing) <= len(e_one) + 4  # few errors...
    # ...but 14 units off, plus the now-empty declared box
    assert gap(nothing, items_p, e_nothing, baseline) == 15
    assert gap(one, items_p, e_one, baseline) == 1
    assert units_short(plates_only(baseline), items_p, baseline) == {
        1: 4, 3: 3, 4: 1, 5: 2}


def test_help_places_left_out_units_and_the_spots_validate():
    items_p, boxes_p, settings, baseline, _ = setup("A")
    plan = plates_only(baseline)
    lines = repair_help(plan, items_p, boxes_p, [], settings, baseline)
    spots = [l for l in lines if "not placed): fits in box 1" in l]
    assert len(spots) == 10
    rows = list(plan.placements)
    for line in spots:
        item = int(line.split()[1])
        pos = [float(v) for v in line.split("pos [")[1].split("]")[0].split(",")]
        dims = [float(v) for v in line.split("dims [")[1].split("]")[0].split(",")]
        rows.append(PlacementRow(box=1, seq=len(rows) + 1, id=item, pos=pos,
                                 dims=dims))
    filled = plan.model_copy(update={"placements": rows, "leftovers": []})
    assert validate_plan(filled, items_p, boxes_p, settings) == []
    assert lines[-1].startswith("Load each added unit after")


def test_empty_declared_box_is_an_error():
    items_p, boxes_p, settings, baseline, _ = setup("A")
    extra = baseline.model_copy(update={"boxes": [
        *baseline.boxes, baseline.boxes[0].model_copy(update={"box": 2})]})
    assert validate_plan(extra, items_p, boxes_p, settings) == [
        "box 2 is declared but holds no units; remove it from boxes"]


def test_estimates_ignore_items_that_fit_no_box():
    """Sample D: the stands fit no box, so Medium still "fits all"."""
    items_p, boxes_p, settings, _, _ = setup("D")
    est = []
    pack(items_p, boxes_p, settings, est)
    by = {e["id"]: e for e in est}
    assert by[2]["fits_all"] is True and by[2]["units"] == 21
    assert by[1]["fits_all"] is False


def test_guide_mode_stops_after_a_cut_off():
    import openai
    from types import SimpleNamespace
    items_p, boxes_p, settings, baseline, _ = setup("A")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5,
                            completion_tokens_details=None)
    err = openai.LengthFinishReasonError(
        completion=SimpleNamespace(usage=usage))
    client = FakeClient(script=[err, err, err])
    client.context = (items_p, boxes_p, settings, baseline)
    result = plan_with_guidance(client, "m", guide_messages(items_p, boxes_p),
                                items_p, boxes_p, settings=settings,
                                baseline=baseline)
    assert len(client.parse_calls) == 1 and result.cut_off
    assert result.source == "packer"


def test_sample_e_leaves_room_to_beat_the_packer():
    """Two boxes; Medium + Large with every unit on the floor ships 23%
    less than the packer's Large (full) + Small (one kettle)."""
    items_p, boxes_p, settings, baseline, _ = setup("E")
    names = {b["id"]: b["name"] for b in boxes_p}
    assert [names[b.id] for b in baseline.boxes] == ["Large", "Small"]
    better, errors = pack_guided(items_p, boxes_p, settings, [2, 3],
                                 [1, 2, 3], {1: [7, 7, 8], 2: [7, 5, 12],
                                             3: [6, 4, 4]})
    assert errors == [] and not better.leftovers
    assert all(p.pos[0] == 0 for p in better.placements)  # one layer
    from packing.planner import _rank
    assert _rank(better, boxes_p)[2] == 3584 < _rank(baseline, boxes_p)[2]
