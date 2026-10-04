from packing.metrics import METRICS, compare, plan_metrics, tally
from packing.settings import final_plan
from test_planner import setup, worse


def test_metrics_for_sample_a_packer_plan():
    items_p, boxes_p, settings, baseline, _ = setup("A")
    m = plan_metrics(final_plan(baseline, items_p, boxes_p, settings))
    assert set(m) == set(METRICS)
    assert m["boxes"] == 1 and m["leftover_units"] == 0
    assert m["units_packed"] == 14
    assert m["max_fill_height"] == 9            # L-14, fill 9 in
    assert m["shipped_volume"] == 9 * 14 * 14   # fill × floor
    assert m["box_volume"] == 10 * 14 * 14
    assert 0 < m["fill_rate"] <= 100
    assert 0 <= m["cog_offset"] <= 100 and 0 < m["cog_height"] < 100
    assert m["weight_spread"] == 0              # one box


def test_compare_verdicts_follow_direction():
    items_p, boxes_p, settings, baseline, _ = setup("A")
    good = plan_metrics(final_plan(baseline, items_p, boxes_p, settings))
    bad = plan_metrics(final_plan(worse(baseline, boxes_p), items_p,
                                  boxes_p, settings))
    rows = {r["metric"]: r for r in compare(bad, good)}
    assert rows["box_volume"]["verdict"] == "worse"
    assert rows["boxes"]["verdict"] == "same"
    assert rows["units_packed"]["verdict"] is None      # info only
    assert rows["box_volume"]["delta"] == bad["box_volume"] - good["box_volume"]
    flipped = {r["metric"]: r["verdict"] for r in compare(good, bad)}
    assert flipped["box_volume"] == "better"
    assert tally(compare(good, good)) == {
        "better": 0, "worse": 0,
        "same": sum(d != "info" for _, _, d in METRICS.values())}


def test_compare_with_a_missing_side():
    rows = compare(None, {"boxes": 1})
    assert all(r["verdict"] is None and r["model"] is None for r in rows)


def test_empty_plan_metrics():
    items_p, boxes_p, settings, _, _ = setup("C")
    from packing.packer import pack
    flat, _ = pack(items_p, boxes_p, settings)
    m = plan_metrics(final_plan(flat, items_p, boxes_p, settings))
    assert m["boxes"] == 0 and m["leftover_units"] == 5
    assert m["fill_rate"] == 0 and m["units_on_floor"] == 0
