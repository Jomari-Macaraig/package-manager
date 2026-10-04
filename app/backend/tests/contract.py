"""The contract's TypeScript types transcribed as Python specs, so a test can
check a response field by field (exact keys, value types).

Spec language: a type (str/int/float/bool/None), ("num",) any JSON number,
("opt", s) key may be absent, ("null", s) value may be null, ("list", s),
("tuple", [s...]), ("enum", {...}), ("record", s), or a dict of key -> spec.
"""

NUM = ("num",)


def null(spec):
    return ("null", spec)


def lst(spec):
    return ("list", spec)


def enum(*values):
    return ("enum", set(values))


def opt(spec):
    return ("opt", spec)


DIMENSIONS = {"x": NUM, "y": NUM, "z": NUM}
ITEM = {"id": int, "color": str, "name": str, "weight": NUM,
        "dimensions": DIMENSIONS, "quantity": int}
PACKED_ITEM = {**ITEM, "packed_index": int,
               "placement_position": {"x": NUM, "y": NUM, "z": NUM}}
PLAN_BOX = {"id": int, "name": str, "dimensions": DIMENSIONS,
            "max_contents_weight": NUM, "items": lst(PACKED_ITEM)}
PLAN = {
    "length_unit": enum("in"), "weight_unit": enum("lb"),
    "coordinate_system": ("record", str),
    "boxes": lst(PLAN_BOX),
    "leftover_items": lst({"items": lst(ITEM), "reason": str}),
    "notes": lst(str),
}
RANK = ("tuple", [int, int, NUM, NUM])
PLAN_SIDE = {"ok": bool, "rank": null(RANK), "errors": lst(str),
             "hints": lst(str), "plan": null(PLAN), "box_count": int}
MODEL_SIDE = {**PLAN_SIDE, "rounds": int, "improved": bool}
SETTINGS = {
    "padding": NUM,
    "padding_by_item": lst({"item_id": int, "padding": NUM}),
    "blocks": lst({"item_id": int, "size": int,
                   "arrangement": enum("stack", "compact"),
                   "grid": null(lst(int))}),
    "upright": lst(int), "fixed_orientation": lst(int),
    "no_stack_on": lst(int),
    "max_load_on_top": lst({"item_id": int, "max_weight": NUM}),
    "floor_only_over_weight": null(NUM),
    "wall_clearance": NUM, "cut_step": null(NUM),
    "min_cut_height": null(NUM), "carrier_max_weight": null(NUM),
    "carrier_max_length": null(NUM), "carrier_max_girth": null(NUM),
    "min_fill": null(NUM), "max_boxes": null(int),
    "keep_together": lst(lst(int)), "keep_apart": lst(lst(int)),
    "balance_boxes": bool, "soft_instructions": lst(str),
}
PREPARED_ITEM = {
    "id": int, "source_id": int, "name": str, "color": str, "weight": NUM,
    "dimensions": DIMENSIONS, "quantity": int,
    "fixed": opt(bool), "upright": opt(bool), "no_stack": opt(bool),
    "max_load": opt(NUM), "floor_only": opt(bool),
}
PREPARED_BOX = {"id": int, "name": str, "weightMax": NUM,
                "dimensions": DIMENSIONS, "stock": opt(int)}
CALL_STATS = {"model": str, "reasoning_effort": null(str),
              "prompt_tokens": int, "completion_tokens": int,
              "reasoning_tokens": int, "cost": NUM}
STATUS = enum("running", "succeeded", "failed", "cancelled", "interrupted")
_EV = {"id": int, "t": NUM, "type": str}
EVENTS = {
    "started": {**_EV},
    "extracted": {**_EV, "tokens": int, "skipped": bool},
    "prepared": {**_EV, "item_types": int, "units": int},
    "packer": {**_EV, "ok": bool, "boxes": int, "errors_count": int},
    "round": {**_EV, "n": int, "kind": enum("fix", "improve"),
              "valid": bool, "errors_count": int},
    "chosen": {**_EV, "source": null(enum("model", "packer"))},
    "end": {**_EV, "status": STATUS, "error": null(str)},
}
INPUT_ITEM = {"name": str, "color": null(str), "weight": NUM, "x": NUM,
              "y": NUM, "z": NUM, "quantity": int}
INPUT_BOX = {"name": str, "weightMax": NUM, "x": NUM, "y": NUM, "z": NUM,
             "stock": null(int)}
RUN_REQUEST = {
    "items": lst(INPUT_ITEM), "boxes": lst(INPUT_BOX), "instructions": str,
    "extraction": {"model": str},
    "planning": {"model": str,
                 "reasoning_effort": enum("low", "medium", "high")},
}
RUN = {
    "run_id": str, "trace_id": str, "request_id": str, "status": STATUS,
    "error": null(str), "created_at": str, "finished_at": null(str),
    "client_ip": str, "user_agent": str, "request": RUN_REQUEST,
    "loop": {"max_rounds": enum(3), "improve_rounds": enum(1)},
    "settings": null(SETTINGS),
    "prepared_items": null(lst(PREPARED_ITEM)),
    "prepared_boxes": null(lst(PREPARED_BOX)),
    "final": null({"source": enum("model", "packer"), "plan": PLAN}),
    "model": null(MODEL_SIDE), "packer": null(PLAN_SIDE),
    "stats": null({
        "extraction": null(CALL_STATS), "planning": null(CALL_STATS),
        "total_tokens": int, "total_cost": NUM,
        "timings": {"extract_s": null(NUM), "packer_s": null(NUM),
                    "plan_s": null(NUM), "total_s": null(NUM)}}),
    "events": ("events",),
}
RUN_SUMMARY = {
    "run_id": str, "created_at": str, "finished_at": null(str),
    "status": STATUS, "title": str, "extraction_model": str,
    "planning_model": str, "source": null(enum("model", "packer")),
    "total_cost": null(NUM), "client_ip": str,
}
_MODEL_OPTION = {"id": str, "label": str, "price_in": NUM, "price_out": NUM}
_EFFORT = enum("low", "medium", "high")
CONFIG = {
    "extraction_models": lst({**_MODEL_OPTION, "reasoning": bool}),
    "planning_models": lst({**_MODEL_OPTION, "efforts": lst(_EFFORT)}),
    "defaults": {"extraction_model": str, "planning_model": str,
                 "reasoning_effort": _EFFORT},
    "loop": {"max_rounds": enum(3), "improve_rounds": enum(1)},
    "caps": {"max_item_rows": enum(20), "max_box_rows": enum(10),
             "max_units": enum(150), "max_instruction_chars": enum(2000)},
}
SAMPLE = {"key": enum("A", "B", "C"), "title": str, "description": str,
          "items": lst(INPUT_ITEM), "boxes": lst(INPUT_BOX),
          "instructions": str}


def check(value, spec, path="$") -> None:
    """Raises AssertionError naming the path of the first mismatch."""
    if isinstance(spec, dict):
        assert isinstance(value, dict), f"{path}: expected object"
        required = {k for k, s in spec.items()
                    if not (isinstance(s, tuple) and s[0] == "opt")}
        allowed = set(spec)
        assert required <= set(value) <= allowed, (
            f"{path}: keys {sorted(value)} != {sorted(allowed)}")
        for key, sub in spec.items():
            if key in value:
                check(value[key], sub[1] if isinstance(sub, tuple)
                      and sub[0] == "opt" else sub, f"{path}.{key}")
    elif spec is None:
        assert value is None, f"{path}: expected null"
    elif spec is bool:
        assert isinstance(value, bool), f"{path}: expected bool"
    elif spec is int:
        assert isinstance(value, int) and not isinstance(value, bool), \
            f"{path}: expected int, got {value!r}"
    elif spec is float:
        assert isinstance(value, float), f"{path}: expected float"
    elif spec is str:
        assert isinstance(value, str), f"{path}: expected string"
    elif spec[0] == "num":
        assert isinstance(value, (int, float)) and not isinstance(
            value, bool), f"{path}: expected number, got {value!r}"
    elif spec[0] == "null":
        if value is not None:
            check(value, spec[1], path)
    elif spec[0] == "list":
        assert isinstance(value, list), f"{path}: expected array"
        for n, v in enumerate(value):
            check(v, spec[1], f"{path}[{n}]")
    elif spec[0] == "tuple":
        assert isinstance(value, list) and len(value) == len(spec[1]), \
            f"{path}: expected {len(spec[1])}-tuple"
        for n, (v, s) in enumerate(zip(value, spec[1])):
            check(v, s, f"{path}[{n}]")
    elif spec[0] == "enum":
        assert value in spec[1], f"{path}: {value!r} not in {spec[1]}"
    elif spec[0] == "record":
        assert isinstance(value, dict), f"{path}: expected record"
        for k, v in value.items():
            check(v, spec[1], f"{path}.{k}")
    elif spec[0] == "events":
        assert isinstance(value, list), f"{path}: expected array"
        for n, event in enumerate(value):
            assert event["id"] == n, f"{path}[{n}]: id is not the index"
            check(event, EVENTS[event["type"]], f"{path}[{n}]")
    else:  # pragma: no cover
        raise AssertionError(f"bad spec {spec!r}")
