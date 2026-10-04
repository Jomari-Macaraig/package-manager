"""Packing settings: one schema read by both the LLM and the code.

The LLM only maps free text onto these fields (extract_settings); the code
applies them:
- prepare_items: item flags, padding, blocks, heavy-on-floor (before planning)
- prepare_boxes: wall clearance (before planning)
- rules_text: plain-language "Packing rules" for the prompt
- validation.validate_plan / quality_hints: enforce them (pass settings)
- final_plan: code-computed cut-down notes (after planning)
Anything that isn't a setting stays a soft instruction for the planner. Set
fields directly to skip the LLM.

Adding a setting = a field here + the code that applies it.
"""

import warnings
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import permutations
from math import ceil, prod
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from schemas import unflatten
from validation import cut_height, fill_heights


class BlockRule(BaseModel):
    item_id: int = Field(description="id of the item type, from the items list")
    size: int = Field(
        ge=2,
        description=(
            "Most units per indivisible block. Units are split into the "
            "fewest blocks of at most this size, balanced (e.g. 18 units at "
            "size 7 -> 6, 6, 6)."
        ),
    )
    arrangement: Literal["stack", "compact"] = Field(
        "stack",
        description=(
            "How units sit inside a block. stack: face to face on their "
            "largest faces in one line, like a stack of plates (default). "
            "compact: the most compact grid, chosen by code."
        ),
    )
    grid: list[Annotated[int, Field(ge=1)]] | None = Field(
        None,
        min_length=3,
        max_length=3,
        description=(
            "Explicit units along the item's x, y, z inside a block, e.g. "
            "[1, 2, 2] for 2 by 2 on one level. Used only for blocks with "
            "exactly that many units; others use arrangement. null when not "
            "stated."
        ),
    )


class LoadLimit(BaseModel):
    item_id: int = Field(description="id of the item type")
    max_weight: float = Field(gt=0, description="Most pounds allowed on top")


class ItemPadding(BaseModel):
    item_id: int = Field(description="id of the item type")
    padding: float = Field(ge=0, description="Inches on every side")


class PackingSettings(BaseModel):
    # --- items ---
    padding: float = Field(
        0, ge=0,
        description=("Inches of packing material on every side of each item; "
                     "each item dimension grows by 2 × padding. 0 when not "
                     "stated."),
    )
    padding_by_item: list[ItemPadding] = Field(
        default_factory=list,
        description="Per-item padding that replaces `padding` for that item.",
    )
    blocks: list[BlockRule] = Field(
        default_factory=list,
        description=("Items whose units must stay together as solid, "
                     "indivisible blocks. Empty when not stated."),
    )
    upright: list[int] = Field(
        default_factory=list,
        description=("Item ids that must stay this side up: height (x) stays "
                     "vertical; they may turn but not tip over."),
    )
    fixed_orientation: list[int] = Field(
        default_factory=list,
        description="Item ids that must not be rotated at all.",
    )
    no_stack_on: list[int] = Field(
        default_factory=list,
        description="Item ids that must have nothing on top (fragile).",
    )
    max_load_on_top: list[LoadLimit] = Field(
        default_factory=list,
        description="Item ids with a maximum weight allowed on top of them.",
    )
    floor_only_over_weight: float | None = Field(
        None, gt=0,
        description=("Units (or blocks) heavier than this many pounds must "
                     "stand on the box floor. null when not stated."),
    )
    # --- boxes ---
    wall_clearance: float = Field(
        0, ge=0,
        description=("Inches kept free between items and each side wall "
                     "(for void fill). 0 when not stated."),
    )
    cut_step: float | None = Field(
        None, gt=0,
        description=("Round each box's cut-down height up to a multiple of "
                     "this many inches. null when not stated."),
    )
    min_cut_height: float | None = Field(
        None, gt=0,
        description=("A box can't be cut down below this height (inches). "
                     "null when not stated."),
    )
    carrier_max_weight: float | None = Field(
        None, gt=0,
        description="Carrier limit per box in pounds, including tare. null "
                    "when not stated.",
    )
    carrier_max_length: float | None = Field(
        None, gt=0,
        description="Carrier limit on a box's longest side (inches). null "
                    "when not stated.",
    )
    carrier_max_girth: float | None = Field(
        None, gt=0,
        description=("Carrier limit on length + girth, where girth = 2 × "
                     "(the other two sides), inches. null when not stated."),
    )
    min_fill: float | None = Field(
        None, gt=0, le=1,
        description=("Each box should be at least this full by volume after "
                     "cutting down, 0-1 (e.g. 0.6). null when not stated."),
    )
    # --- order ---
    max_boxes: int | None = Field(
        None, ge=1,
        description="Most boxes for the whole order. null when not stated.",
    )
    keep_together: list[list[int]] = Field(
        default_factory=list,
        description="Groups of item ids that must ship in the same box.",
    )
    keep_apart: list[list[int]] = Field(
        default_factory=list,
        description="Groups of item ids that must never share a box.",
    )
    balance_boxes: bool = Field(
        False,
        description="Spread weight evenly across boxes. false when not stated.",
    )
    # --- everything else ---
    soft_instructions: list[str] = Field(
        default_factory=list,
        description=("Every instruction that is not one of the settings "
                     "above, kept as written."),
    )


EXTRACT_PROMPT = """\
You turn a user's packing instructions into settings. Lengths are inches, \
weights are pounds.
Set a field only when the text gives a clear value for it; if it is vague \
("a bit of padding"), leave the field at its default and keep the sentence \
in soft_instructions instead. Put every instruction that is not a setting \
into soft_instructions, as written. Do not put an instruction into \
soft_instructions when a setting already captures it (e.g. "cut down in \
0.5 in steps" is only cut_step). Do not invent values. Use the item ids \
from the items list to refer to items."""


def extract_settings(
    client,
    model: str,
    text: str,
    items: Sequence[Mapping[str, Any]] = (),
    **create_kwargs: Any,
) -> PackingSettings:
    """One small LLM call: free text -> PackingSettings.

    items: listed by id and name so the model can map "tapes" to an id.
    """
    if not text or not text.strip():
        return PackingSettings()
    listing = "\n".join(f"{i['id']}: {i['name']}" for i in items)
    content = text.strip() + (f"\n\nItems (id: name):\n{listing}"
                              if listing else "")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": EXTRACT_PROMPT},
                  {"role": "user", "content": content}],
        response_format={"type": "json_schema", "json_schema": {
            "name": "PackingSettings", "strict": True, "schema": _SCHEMA}},
        **create_kwargs,
    )
    return PackingSettings.model_validate_json(
        response.choices[0].message.content)


def _strict_schema() -> dict:
    """PackingSettings as a strict schema; strict mode has no defaults."""
    from openai.lib._pydantic import to_strict_json_schema

    def strip(node):
        if isinstance(node, dict):
            node.pop("default", None)
            for value in node.values():
                strip(value)
        elif isinstance(node, list):
            for value in node:
                strip(value)

    schema = to_strict_json_schema(PackingSettings)
    strip(schema)  # nested models (BlockRule, ...) have defaults too
    return schema


_SCHEMA = _strict_schema()


# --- Items ------------------------------------------------------------------

def apply_padding(
    items: Sequence[Mapping[str, Any]],
    padding: float,
    by_item: Mapping[int, float] | None = None,
) -> list[dict[str, Any]]:
    """Copies of the items with every dimension grown by 2 × padding
    (by_item overrides padding for those item ids)."""
    by_item = by_item or {}
    padded = []
    for item in items:
        pad = by_item.get(item["id"], padding)
        dims = {axis: item["dimensions"][axis] + 2 * pad
                for axis in ("x", "y", "z")}
        padded.append({**item, "dimensions": dims})
    return padded


def split_balanced(quantity: int, size: int) -> list[int]:
    """Fewest blocks of at most `size`, sizes differing by at most 1.

    11 at 4 -> [4, 4, 3]; 18 at 7 -> [6, 6, 6].
    """
    count = ceil(quantity / size)
    base, extra = divmod(quantity, count)
    return [base + 1] * extra + [base] * (count - extra)


def _fits_a_box(dims, boxes) -> bool:
    return any(
        all(d <= s + 1e-9 for d, s in zip(rot, (b["dimensions"]["x"],
                                                 b["dimensions"]["y"],
                                                 b["dimensions"]["z"])))
        for b in boxes for rot in permutations(dims)
    )


def block_shape(
    unit: Mapping[str, float],
    n: int,
    boxes,
    arrangement: str = "stack",
    grid: Sequence[int] | None = None,
):
    """Grid (units along x, y, z) and outer dims of a block of n units.

    An explicit grid wins when it holds exactly n units; otherwise the
    arrangement: stack = one line along the unit's thinnest axis (largest
    faces together); compact = smallest outer surface. If the result fits
    no box, falls back to compact with a warning.
    """
    sizes = (unit["x"], unit["y"], unit["z"])
    chosen = None
    if grid and prod(grid) == n:
        chosen = tuple(grid)
    elif arrangement == "stack":
        thin = sizes.index(min(sizes))
        chosen = tuple(n if axis == thin else 1 for axis in range(3))
    if chosen:
        dims = tuple(count * size for count, size in zip(chosen, sizes))
        if _fits_a_box(dims, boxes):
            return chosen, dims
        warnings.warn(f"block of {n} as {'×'.join(map(str, chosen))} fits "
                      f"no box; using the most compact shape instead")
    return _compact_shape(sizes, n, boxes)


def _compact_shape(sizes, n: int, boxes):
    """Most compact a×b×c grid of n units (smallest outer surface) that
    fits some box; falls back to the most compact one."""
    grids = [(a, b, n // (a * b)) for a in range(1, n + 1) if n % a == 0
             for b in range(1, n // a + 1) if (n // a) % b == 0]
    shapes = []
    for a, b, c in grids:
        dims = (a * sizes[0], b * sizes[1], c * sizes[2])
        surface = 2 * (dims[0] * dims[1] + dims[1] * dims[2]
                       + dims[0] * dims[2])
        shapes.append((surface, (a, b, c), dims))
    shapes.sort()
    for _, grid, dims in shapes:
        if _fits_a_box(dims, boxes):
            return grid, dims
    return shapes[0][1], shapes[0][2]


def apply_blocks(
    items: Sequence[Mapping[str, Any]],
    blocks: Sequence[BlockRule],
    boxes: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Replace each blocked item with one item type per block size.

    Each block is planned as a single solid unit: dims are the grid of its
    units, weight is the units' total; it keeps the item's flags and
    source_id. A block of 1 is just the unit.
    """
    by_id = {i["id"]: dict(i) for i in items}
    next_id = max(by_id) + 1
    out = [dict(i) for i in items]
    for rule in blocks:
        if rule.item_id not in by_id:
            raise ValueError(f"block rule for unknown item id {rule.item_id}")
        item = by_id[rule.item_id]
        out = [i for i in out if i["id"] != rule.item_id]
        sizes = Counter(split_balanced(item["quantity"], rule.size))
        for size, count in sorted(sizes.items(), reverse=True):
            if size == 1:
                out.append({**item, "quantity": count})
                continue
            (a, b, c), (x, y, z) = block_shape(
                item["dimensions"], size, boxes, rule.arrangement, rule.grid)
            out.append({
                **item,
                "id": next_id,
                "name": f"{item['name']} block of {size} ({a}×{b}×{c})",
                "weight": item["weight"] * size,
                "dimensions": {"x": x, "y": y, "z": z},
                "quantity": count,
            })
            next_id += 1
    return out


def _check_ids(items, settings: PackingSettings) -> None:
    known = {i["id"] for i in items}
    ids = [*settings.upright, *settings.fixed_orientation,
           *settings.no_stack_on,
           *(r.item_id for r in settings.max_load_on_top),
           *(r.item_id for r in settings.padding_by_item),
           *(i for g in settings.keep_together for i in g),
           *(i for g in settings.keep_apart for i in g)]
    unknown = sorted(set(ids) - known)
    if unknown:
        raise ValueError(f"settings refer to unknown item ids {unknown}")


def prepare_items(items, boxes, settings: PackingSettings) -> list[dict]:
    """Items as planned and validated.

    Order: item flags (fixed, upright, no_stack, max_load, source_id) ->
    padding (per item or global) -> blocks -> floor_only for units/blocks
    heavier than floor_only_over_weight.
    """
    _check_ids(items, settings)
    loads = {r.item_id: r.max_weight for r in settings.max_load_on_top}
    flagged = []
    for item in items:
        flags = {"source_id": item["id"]}
        if item["id"] in settings.fixed_orientation:
            flags["fixed"] = True
        if item["id"] in settings.upright:
            flags["upright"] = True
        if item["id"] in settings.no_stack_on:
            flags["no_stack"] = True
        if item["id"] in loads:
            flags["max_load"] = loads[item["id"]]
        flagged.append({**item, **flags})

    padded = apply_padding(
        flagged, settings.padding,
        {r.item_id: r.padding for r in settings.padding_by_item})
    out = apply_blocks(padded, settings.blocks, boxes)
    if settings.floor_only_over_weight is not None:
        for item in out:
            if item["weight"] > settings.floor_only_over_weight:
                item["floor_only"] = True
    return out


# --- Boxes ------------------------------------------------------------------

def prepare_boxes(boxes, settings: PackingSettings) -> list[dict]:
    """Boxes as planned.

    - Inside depth and width shrink by 2 × wall_clearance (the height isn't
      reduced; the top is cut down anyway).
    - weightMax (contents) becomes at most carrier_max_weight − weightTare,
      so precompute, prompt and validator all see the tighter limit.
    """
    c = settings.wall_clearance
    out = []
    for b in boxes:
        box = {**b, "dimensions": {"x": b["dimensions"]["x"],
                                   "y": b["dimensions"]["y"] - 2 * c,
                                   "z": b["dimensions"]["z"] - 2 * c}}
        if settings.carrier_max_weight is not None:
            carrier = settings.carrier_max_weight - (b.get("weightTare") or 0)
            own = b.get("weightMax")
            box["weightMax"] = carrier if own is None else min(own, carrier)
        out.append(box)
    return out


# --- Prompt text --------------------------------------------------------------

def rules_text(items, boxes, settings: PackingSettings) -> list[str]:
    """Plain-language hard rules for the prompt, all checked in code.

    items are the prepared items, so blocks are named by their own ids.
    """
    def names(flag):
        return ", ".join(f"{i['id']} ({i['name']})" for i in items
                         if i.get(flag))

    def group(ids):
        return ", ".join(f"{i['id']} ({i['name']})" for i in items
                         if i.get("source_id", i["id"]) in ids)

    lines = []
    if names("fixed"):
        lines.append(f"Do not rotate items {names('fixed')}: use them as "
                     f"listed.")
    if names("upright"):
        lines.append(f"Keep items {names('upright')} this side up: their x "
                     f"stays vertical (only the listed rotations fit).")
    if names("no_stack"):
        lines.append(f"Nothing may rest on items {names('no_stack')}.")
    for item in items:
        if item.get("max_load") is not None:
            lines.append(f"At most {item['max_load']:g} lb in total may sit "
                         f"above item {item['id']} ({item['name']}).")
    if names("floor_only"):
        lines.append(f"Items {names('floor_only')} are heavy: they must stand "
                     f"on the box floor (x = 0).")
    if any(b.get("stock") is not None for b in boxes):
        lines.append("Use at most `stock` boxes of each box type (blank = "
                     "unlimited); a box type with stock 0 can't be used.")
    if settings.max_boxes is not None:
        lines.append(f"Use at most {settings.max_boxes} box(es) in total; "
                     f"put anything that doesn't fit in leftovers.")
    for g in settings.keep_together:
        lines.append(f"Items {group(g)} must ship in the same box.")
    for g in settings.keep_apart:
        lines.append(f"Items {group(g)} must never share a box.")
    limits = []
    if settings.carrier_max_weight:
        limits.append(f"at most {settings.carrier_max_weight:g} lb per box "
                      f"including the box's own weight")
    if settings.carrier_max_length:
        limits.append(f"longest side at most {settings.carrier_max_length:g} "
                      f"in")
    if settings.carrier_max_girth:
        limits.append(f"length + girth at most {settings.carrier_max_girth:g} "
                      f"in (girth = 2 × the other two sides)")
    if limits:
        lines.append("Carrier limits, measured on the box cut down to its "
                     "contents: " + "; ".join(limits) + ".")
    if settings.min_fill:
        lines.append(f"Each box should be at least {settings.min_fill:.0%} "
                     f"full by volume after cutting down.")
    if settings.balance_boxes:
        lines.append("Spread weight evenly across boxes.")
    return lines


# --- After planning -----------------------------------------------------------

def cut_notes(flat, boxes: Sequence[Mapping[str, Any]],
              settings: PackingSettings) -> list[str]:
    """Exact cut-down height per box (validation.cut_height)."""
    heights = {b["id"]: b["dimensions"]["x"] for b in boxes}
    box_type = {b.box: b.id for b in flat.boxes}
    notes = []
    for box_no, fill in sorted(fill_heights(flat).items()):
        height = heights[box_type[box_no]]
        cut = cut_height(fill, height, settings)
        if cut >= height - 1e-9:
            # rounding up (or the contents) reaches the box's own height
            notes.append(f"Box {box_no}: no cut needed (contents reach "
                         f"{fill:g} in of its {height:g} in).")
            continue
        why = []
        if settings.cut_step:
            why.append(f"rounded up to a multiple of {settings.cut_step:g}")
        if settings.min_cut_height and cut > fill:
            why.append(f"no lower than {settings.min_cut_height:g}")
        extra = f", {', '.join(why)}" if why else ""
        notes.append(f"Cut box {box_no} down to {cut:g} in high, removing "
                     f"{height - cut:g} in (contents reach {fill:g} in"
                     f"{extra}).")
    return notes


def final_plan(flat, items, boxes, settings: PackingSettings):
    """Nested plan for display: unflatten + code-computed cut notes first.

    items and boxes should be the prepared ones the plan was validated with.
    """
    plan = unflatten(flat, items, boxes)
    plan.notes = cut_notes(flat, boxes, settings) + plan.notes
    return plan
