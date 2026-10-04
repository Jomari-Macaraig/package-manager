import re
import uuid
from types import SimpleNamespace

import httpx
import openai
import pytest
from agents import set_trace_processors, set_tracing_disabled
from agents.tracing import TracingProcessor

from app.fake_llm import FakeClient
from app.pipeline import (PipelineError, RunCancelled, new_partial,
                          renumber_boxes, run_pipeline)
from app.schemas import to_pipeline_inputs
from packing.packer import pack
from packing.planner import RunCancelled as PlannerRunCancelled
from packing.render_svg import render_svg
from packing.schemas import (FlatResponse, LeftoverRow, Response,
                             BoxRow, PlacementRow)
from packing.settings import PackingSettings, prepare_boxes, prepare_items

from conftest import sample_request

META = {"request_id": "11111111-1111-4111-8111-111111111111",
        "client_ip": "10.1.2.3", "user_agent": "pytest/1.0"}


def run(req, client=None, *, should_stop=lambda: False, run_id=None):
    events, partial = [], new_partial()
    client = client or FakeClient()
    try:
        result = run_pipeline(req, client, run_id=run_id or uuid.uuid4(),
                              meta=META, emit=events.append,
                              should_stop=should_stop, partial=partial)
    except Exception as exc:
        exc.events, exc.partial = events, partial
        raise
    return result, events, partial, client


def settings_json(**fields) -> str:
    return PackingSettings(**fields).model_dump_json()


def prepared(key="A"):
    items, boxes = to_pipeline_inputs(sample_request(key))
    settings = PackingSettings()
    return (prepare_items(items, boxes, settings),
            prepare_boxes(boxes, settings), settings)


def leftovers_only(key="A") -> FlatResponse:
    items_p, _, _ = prepared(key)
    return FlatResponse(
        boxes=[], placements=[], notes=[],
        leftovers=[LeftoverRow(id=i["id"], quantity=i["quantity"],
                               reason="scripted") for i in items_p])


def http_error(cls, status):
    request = httpx.Request("POST", "http://x")
    return cls("boom", response=httpx.Response(status, request=request),
               body=None)


@pytest.mark.parametrize("key,boxes", [("A", 1), ("B", 2)])
def test_samples_a_and_b(key, boxes):
    result, events, _, _ = run(sample_request(key))
    assert result["final"] is not None
    assert result["model"]["ok"] and result["packer"]["ok"]
    assert result["model"]["rank"] and result["packer"]["rank"]
    assert result["packer"]["rank"][1] == boxes
    assert result["svgs"]["model"].startswith("<svg")
    assert result["svgs"]["packer"].startswith("<svg")
    plan = result["final"]["plan"]
    assert len(plan["boxes"]) == boxes
    numbers = sorted(int(n) for n in re.findall(
        r"[Bb]ox (\d+)", " ".join(plan["notes"])))
    assert numbers == list(range(1, boxes + 1))
    assert [e["type"] for e in events][:3] == ["extracted", "prepared",
                                               "packer"]
    assert events[-1] == {"type": "chosen", "source": "model"}


def test_sample_c_nothing_fits():
    result, _, _, _ = run(sample_request("C"))
    plan = result["final"]["plan"]
    assert plan["boxes"] == []
    rows = [i for g in plan["leftover_items"] for i in g["items"]]
    assert len(rows) == 4
    assert sum(i["quantity"] for i in rows) == 5


def test_null_color_uses_tab10():
    body_items = sample_request("A").items
    req = sample_request("A")
    req.items[0].color = None
    result, _, _, _ = run(req)
    assert result["final"] is not None
    assert result["prepared_items"][0]["color"] == ""
    svg = result["svgs"]["model"]
    assert "#1f77b4" in svg  # tab10 first color
    assert body_items  # sanity


def test_empty_instructions_skip_extraction():
    result, events, _, client = run(sample_request("A", ""))
    assert client.create_calls == []
    assert result["stats"]["extraction"] is None
    assert events[0] == {"type": "extracted", "tokens": 0, "skipped": True}


def test_extraction_kwargs_per_model_type():
    _, _, _, client = run(sample_request("A", "pad 0.1 in"))
    kwargs = client.create_calls[0]
    assert "reasoning_effort" not in kwargs
    assert kwargs["max_completion_tokens"] == 2000
    req = sample_request("A", "pad 0.1 in",
                         extraction={"model": "gpt-5.4-nano"})
    result, _, _, client = run(req)
    assert client.create_calls[0]["reasoning_effort"] == "none"
    assert result["stats"]["extraction"]["reasoning_effort"] == "none"
    assert client.parse_calls[0]["max_completion_tokens"] == 16000
    assert client.parse_calls[0]["reasoning_effort"] == "low"


def test_render_svg_single_box():
    result, _, _, _ = run(sample_request("B"))
    plan = Response.model_validate(result["packer"]["plan"])
    full = render_svg(plan)
    only2 = render_svg(plan, box=2)
    assert plan.boxes[1].name in only2 and plan.boxes[0].name not in only2 \
        or plan.boxes[0].name == plan.boxes[1].name
    assert len(only2) < len(full)
    with pytest.raises(IndexError):
        render_svg(plan, box=99)
    with pytest.raises(IndexError):
        render_svg(plan, box=0)


def test_invalid_first_reply_then_fix_round():
    bad = FlatResponse(boxes=[], placements=[], leftovers=[], notes=[])
    result, events, _, _ = run(sample_request("A"), FakeClient(script=[bad]))
    rounds = [e for e in events if e["type"] == "round"]
    assert rounds[0] == {"type": "round", "n": 1, "kind": "fix",
                         "valid": False, "errors_count": rounds[0][
                             "errors_count"]}
    assert rounds[0]["errors_count"] > 0
    assert rounds[1]["valid"] is True and rounds[1]["n"] == 2
    assert result["model"]["rounds"] == 2 and result["final"]["source"] \
        == "model"


def test_should_stop_before_round_two():
    bad = FlatResponse(boxes=[], placements=[], leftovers=[], notes=[])
    events = []

    def stop():
        return any(e["type"] == "round" for e in events)

    partial = new_partial()
    with pytest.raises(RunCancelled) as info:
        run_pipeline(sample_request("A"), FakeClient(script=[bad]),
                     run_id=uuid.uuid4(), meta=META, emit=events.append,
                     should_stop=stop, partial=partial)
    assert RunCancelled is PlannerRunCancelled
    result = info.value.result
    assert result.rounds == 1 and result.history
    assert result.prompt_tokens == 1000
    # partial holds what finished before the cancel
    assert partial["settings"] is not None
    assert partial["prepared_items"] and partial["prepared_boxes"]
    assert partial["packer"]["ok"] is True
    assert partial["planning"]["prompt_tokens"] == 1000
    assert partial["history"]["planning"]


def test_cancel_before_extraction():
    with pytest.raises(RunCancelled) as info:
        run(sample_request("A"), should_stop=lambda: True)
    assert info.value.result is None


def test_api_connection_error_packer_wins():
    exc = openai.APIConnectionError(
        request=httpx.Request("POST", "http://x"))
    client = FakeClient(script=[exc, exc, exc])
    result, events, _, _ = run(sample_request("A"), client)
    assert result["final"]["source"] == "packer"
    assert result["model"]["ok"] is False
    assert result["model"]["plan"] is None
    assert result["model"]["errors"][0].startswith(
        "model call failed: APIConnectionError")
    assert "Code packer's plan used" in " ".join(result["final"]["plan"][
        "notes"])
    assert [e["valid"] for e in events if e["type"] == "round"] == [False] * 3


@pytest.mark.parametrize("cls,status", [(openai.AuthenticationError, 401),
                                        (openai.NotFoundError, 404),
                                        (openai.PermissionDeniedError, 403),
                                        (openai.BadRequestError, 400)])
def test_config_errors_fail_the_run(cls, status):
    client = FakeClient(script=[http_error(cls, status)])
    with pytest.raises(PipelineError) as info:
        run(sample_request("A"), client)
    if cls is openai.AuthenticationError:
        assert "OPENAI_API_KEY" in str(info.value)
    else:
        assert str(info.value).startswith(cls.__name__)
    assert info.value.partial["packer"]["ok"] is True  # saved so far


def test_extraction_auth_error():
    client = FakeClient(create_script=[http_error(
        openai.AuthenticationError, 401)])
    with pytest.raises(PipelineError, match="OPENAI_API_KEY"):
        run(sample_request("A", "pad it"), client)


def test_length_error_counts_tokens_no_model_plan():
    completion = SimpleNamespace(usage=SimpleNamespace(
        prompt_tokens=500, completion_tokens=100,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=40)))
    err = openai.LengthFinishReasonError(completion=completion)
    result, _, _, _ = run(sample_request("A"),
                          FakeClient(script=[err, err, err]))
    assert result["model"]["ok"] is False
    assert result["model"]["errors"][0].startswith(
        "model call failed: LengthFinishReasonError")
    planning = result["stats"]["planning"]
    assert planning["prompt_tokens"] == 1500
    assert planning["completion_tokens"] == 300
    assert planning["reasoning_tokens"] == 120
    assert result["final"]["source"] == "packer"


@pytest.mark.parametrize("reply", ["not json {", None, '{"padding": -1}'])
def test_malformed_extraction_reply(reply):
    client = FakeClient(create_script=[reply])
    with pytest.raises(PipelineError) as info:
        run(sample_request("A", "pad it"), client)
    assert str(info.value).startswith("Could not interpret the instructions")
    # tokens of the failed call survive
    assert info.value.partial["extraction"]["prompt_tokens"] == 1000


def test_wall_clearance_zeroes_box():
    client = FakeClient(create_script=[settings_json(wall_clearance=5)])
    with pytest.raises(PipelineError, match="Wall clearance leaves no room"):
        run(sample_request("A", "5 in clearance"), client)


def test_duplicate_block_rules_rejected():
    rule = {"item_id": 1, "size": 2, "arrangement": "stack", "grid": None}
    client = FakeClient(create_script=[settings_json(blocks=[rule, rule])])
    with pytest.raises(PipelineError, match="duplicate block"):
        run(sample_request("A", "blocks"), client)


def test_huge_padding_rejected():
    client = FakeClient(create_script=[settings_json(padding=1e9)])
    with pytest.raises(PipelineError, match="out of range"):
        run(sample_request("A", "pad"), client)


def test_unknown_item_id_in_settings():
    client = FakeClient(create_script=[settings_json(upright=[99])])
    with pytest.raises(PipelineError, match="unknown item ids"):
        run(sample_request("A", "upright"), client)


def test_settings_applied_and_extraction_stats():
    client = FakeClient(create_script=[settings_json(
        padding=0.125, upright=[1], soft_instructions=["be nice"])])
    result, events, _, _ = run(sample_request("A", "pad"), client)
    assert result["settings"]["padding"] == 0.125
    assert result["prepared_items"][0]["upright"] is True
    assert result["prepared_items"][0]["dimensions"]["x"] == 4.5 + 0.25
    ext = result["stats"]["extraction"]
    assert ext["model"] == "gpt-4.1-nano" and ext["reasoning_effort"] is None
    assert ext["prompt_tokens"] == 1000 and ext["cost"] == pytest.approx(
        (1000 * 0.10 + 200 * 0.40) / 1e6)
    assert events[0]["skipped"] is False and events[0]["tokens"] == 1200
    # reasoning tokens are inside completion tokens: not added to the cost
    planning = result["stats"]["planning"]
    assert result["stats"]["total_tokens"] == 2 * 1200 or planning
    assert result["stats"]["total_cost"] == pytest.approx(
        ext["cost"] + planning["cost"])


def test_model_side_kept_when_packer_wins():
    worse = leftovers_only("A")
    client = FakeClient(script=[worse, worse])  # fix round, improve round
    result, _, _, _ = run(sample_request("A"), client)
    assert result["final"]["source"] == "packer"
    model = result["model"]
    assert model["ok"] is True
    assert model["plan"]["boxes"] == []
    assert model["rank"][0] > result["packer"]["rank"][0]
    assert model["hints"]  # the model's own hints, not cleared by the packer
    assert model["errors"] == []
    assert "Code packer's plan used" in " ".join(result["final"]["plan"][
        "notes"])
    assert "Code packer's plan used" not in " ".join(
        result["packer"]["plan"]["notes"])


def test_gapped_box_numbers_renumbered():
    items_p, boxes_p, settings = prepared("B")
    baseline, errors = pack(items_p, boxes_p, settings)
    assert not errors and len(baseline.boxes) == 2
    mapping = {1: 2, 2: 5}
    gapped = baseline.model_copy(update={
        "boxes": [BoxRow(box=mapping[b.box], id=b.id)
                  for b in baseline.boxes],
        "placements": [p.model_copy(update={"box": mapping[p.box]})
                       for p in baseline.placements]})
    result, _, _, _ = run(sample_request("B"), FakeClient(script=[gapped]))
    assert result["model"]["ok"]
    notes = " ".join(result["model"]["plan"]["notes"])
    numbers = {int(n) for n in re.findall(r"[Bb]ox (\d+)", notes)}
    assert numbers == {1, 2}
    assert len(result["model"]["plan"]["boxes"]) == 2
    assert {b.box for b in renumber_boxes(gapped).boxes} == {1, 2}
    assert {p.box for p in renumber_boxes(gapped).placements} == {1, 2}


def test_no_valid_plan_gives_no_final():
    bad = FlatResponse(boxes=[], placements=[], leftovers=[], notes=[])
    # unreachable via the fake pack(); simulate: invalid model, no packer
    # plan, by making the packer fail through a settings conflict
    # (keep_together of items that cannot share a box).
    # Simplest: the model side alone is invalid and the baseline is None.
    from app import pipeline as pl
    original = pl.pack
    try:
        pl.pack = lambda *a, **k: (None, ["packer failed"])
        result, _, _, _ = run(sample_request("A"),
                              FakeClient(script=[bad, bad, bad]))
    finally:
        pl.pack = original
    assert result["final"] is None
    assert result["packer"]["ok"] is False
    assert result["packer"]["errors"] == ["packer failed"]
    assert result["model"]["ok"] is False and result["model"]["errors"]


class Recorder(TracingProcessor):
    def __init__(self):
        self.traces, self.spans = [], []

    def on_trace_start(self, trace):
        pass

    def on_trace_end(self, trace):
        self.traces.append(trace.export())

    def on_span_start(self, span):
        pass

    def on_span_end(self, span):
        self.spans.append(span.export())

    def shutdown(self):
        pass

    def force_flush(self):
        pass


@pytest.fixture
def recorder():
    from agents.tracing import get_trace_provider  # noqa: F401
    rec = Recorder()
    set_trace_processors([rec])  # replaces the exporters: no network
    set_tracing_disabled(False)
    yield rec
    set_trace_processors([])
    set_tracing_disabled(True)


def test_trace_id_metadata_and_spans(recorder):
    run_id = uuid.uuid4()
    client = FakeClient(create_script=[settings_json(padding=0.1)])
    run(sample_request("A", "pad"), client, run_id=run_id)
    assert len(recorder.traces) == 1
    trace = recorder.traces[0]
    assert trace["id"] == f"trace_{run_id.hex}"
    meta = trace["metadata"]
    assert meta["run_id"] == str(run_id)
    assert meta["client_ip"] == "10.1.2.3" and meta["user_agent"] == "pytest/1.0"
    assert meta["request_id"] == META["request_id"]
    assert meta["extraction_model"] == "gpt-4.1-nano"
    assert meta["planning_model"] == "gpt-5.4-nano"
    assert meta["reasoning_effort"] == "low"
    assert all(isinstance(v, str) for v in meta.values())
    assert trace["group_id"] == "10.1.2.3"
    gens = [s for s in recorder.spans
            if s["span_data"]["type"] == "generation"]
    assert len(gens) == 2  # extraction + one planning call
    for span in gens:
        data = span["span_data"]
        assert data["input"] is None and data["output"] is None
        assert data["usage"]["input_tokens"] == 1000
    planning = [s for s in gens if s["span_data"]["model"] == "gpt-5.4-nano"]
    assert planning[0]["span_data"]["model_config"] == {
        "reasoning_effort": "low"}
    customs = {s["span_data"]["name"] for s in recorder.spans
               if s["span_data"]["type"] == "custom"}
    assert customs == {"extract", "prepare", "packer", "plan", "finalize"}
