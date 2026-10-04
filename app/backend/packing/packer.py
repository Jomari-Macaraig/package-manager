"""Deterministic packer: a valid, low plan from code, no LLM.

Greedy extreme-point packing. Each unit goes to the lowest, then
back-most, then left-most free corner where it fits; every rule is checked
as it is placed (containment, overlap, full support, top-loading, weight,
item flags, keep_apart). For each item type the allowed rotations are tried
(all combinations when few, else one type at a time, plus an "everything
flat" pass: smallest side up) and the arrangement with the fewest unplaced
units, then the lowest fill height, then the fewest units standing on edge,
wins. Boxes are opened one at a time: the box type that holds everything in
the least *shipped* volume (cut-down height × floor area; a bigger box
filled low can beat a small one filled to the top), else the one that holds
the most.

The result is a FlatResponse checked with validation.validate_plan, used by
planner.plan_with_validation as a target ("too high" hint) and a fallback.
pack(..., estimates=[]) also reports how one box of each type did on the
whole order (box_estimates in the prompt). pack_guided places units from
the model's choices (box types, item order, one rotation per item type) and,
optionally, placement preferences (side: left/right/back/front, level:
bottom/top); preference_report measures how many units honour them.
"""

from collections.abc import Callable, Mapping, Sequence
from itertools import product
from typing import Any

from .precompute import EPS, item_rotations, precompute
from .schemas import FlatResponse, PlacementRow
from .validation import _covered, _overlap, cut_height, validate_plan

MAX_COMBOS = 64  # rotation combinations tried per box; beyond that, greedy
SIDES = ("any", "left", "right", "back", "front")
LEVELS = ("any", "bottom", "top")
_LEVEL_RANK = {"bottom": 0, "any": 1, "top": 2}


def _source(item) -> int:
    return item.get("source_id", item["id"])


def _vol(dims) -> float:
    return dims[0] * dims[1] * dims[2]


def _fits_here(item, pos, dims, placed, size, weight_max, apart) -> bool:
    """Can a unit of `item` sit at pos with dims, given the units placed?"""
    if any(p + d > s + EPS for p, d, s in zip(pos, dims, size)):
        return False
    if item.get("floor_only") and pos[0] > EPS:
        return False
    if weight_max is not None and (
            sum(u["item"]["weight"] for u in placed) + item["weight"]
            > weight_max + EPS):
        return False
    src = _source(item)
    for group in apart:
        if src in group and any(_source(u["item"]) in group
                                and _source(u["item"]) != src
                                for u in placed):
            return False

    probe = PlacementRow(box=0, seq=1, id=0, pos=list(pos), dims=list(dims))
    top = pos[0] + dims[0]
    supports = []
    for u in placed:
        row = u["row"]
        if _overlap(probe, row, (0, 1, 2)):
            return False
        footprint = _overlap(probe, row, (1, 2))
        if footprint and row.pos[0] >= top - EPS:
            return False  # can't be lowered past a unit already above
        if footprint and abs(row.pos[0] + row.dims[0] - pos[0]) <= 1e-6:
            supports.append(row)
            if u["item"].get("no_stack"):
                return False
    if pos[0] > EPS and not _covered(probe, supports):
        return False

    # load limits of everything underneath
    for u in placed:
        limit = u["item"].get("max_load")
        row = u["row"]
        if limit is None or pos[0] < row.pos[0] + row.dims[0] - EPS:
            continue
        if not _overlap(probe, row, (1, 2)):
            continue
        r_top = row.pos[0] + row.dims[0]
        load = sum(o["item"]["weight"] for o in placed
                   if o is not u and o["row"].pos[0] >= r_top - EPS
                   and _overlap(row, o["row"], (1, 2)))
        if load + item["weight"] > limit + EPS:
            return False
    return True


def _fill_box(box, groups, rotation, apart, point_order=None,
              extra_points=None):
    """Place units of each (item, count) group in order with the given
    rotation per item id. Returns (placed units, unplaced counts).

    point_order / extra_points (preferences): per item id, a sort key
    `(pos, dims, size) -> tuple` for the candidate points and a function
    `(dims, size, points) -> set` adding candidate points. Both None: the
    candidates are the extreme points in (x, y, z) order, as before.
    """
    size = (box["dimensions"]["x"], box["dimensions"]["y"],
            box["dimensions"]["z"])
    weight_max = box.get("weightMax")
    placed: list[dict] = []
    points = {(0.0, 0.0, 0.0)}
    unplaced: dict[int, int] = {}
    for item, count in groups:
        dims = rotation[item["id"]]
        order = (point_order or {}).get(item["id"])
        extra = (extra_points or {}).get(item["id"])
        for _ in range(count):
            candidates = points | extra(dims, size, points) if extra else points
            if order is None:
                ordered = sorted(candidates)
            else:
                ordered = sorted(candidates, key=lambda p: (
                    *order(p, dims, size), p))
            for pos in ordered:
                if _fits_here(item, pos, dims, placed, size, weight_max,
                              apart):
                    row = PlacementRow(box=0, seq=len(placed) + 1,
                                       id=item["id"], pos=list(pos),
                                       dims=list(dims))
                    placed.append({"item": item, "row": row})
                    x, y, z = pos
                    points.discard(pos)
                    points |= {(x + dims[0], y, z), (x, y + dims[1], z),
                               (x, y, z + dims[2])}
                    break
            else:
                unplaced[item["id"]] = unplaced.get(item["id"], 0) + 1
    return placed, unplaced


def _standing(placed) -> int:
    """Units not lying flat (their height isn't their smallest side)."""
    return sum(1 for u in placed
               if u["row"].dims[0] > min(u["row"].dims) + EPS)


def _flat_rotations(item) -> list:
    """Allowed rotations with the smallest side up (else all allowed)."""
    rotations = item_rotations(item)
    flat = [r for r in rotations if r[0] <= min(r) + EPS]
    return flat or rotations


def _score(placed, unplaced, groups) -> tuple:
    """Lower is better: unplaced volume, fill height, units standing on
    edge, then summed tops."""
    by_id = {item["id"]: item for item, _ in groups}
    left = sum(n * _vol(item_rotations(by_id[i])[0])
               for i, n in unplaced.items())
    tops = [u["row"].pos[0] + u["row"].dims[0] for u in placed]
    return (round(left, 6), round(max(tops, default=0), 6),
            _standing(placed), round(sum(tops), 6))


def _pack_one_box(box, groups, apart):
    """Best single-box fill over rotation choices and two type orders."""
    best = None
    orders = [
        sorted(groups, key=lambda g: -_vol(item_rotations(g[0])[0])),
        sorted(groups, key=lambda g: -max(item_rotations(g[0])[0])),
    ]
    passes = [(order, [item_rotations(item) for item, _ in order])
              for order in orders]
    passes += [(order, [_flat_rotations(item) for item, _ in order])
               for order in orders]
    for order, options in passes:
        if _count_combos(options) <= MAX_COMBOS:
            choices = product(*options)
            for combo in choices:
                rotation = {item["id"]: rot
                            for (item, _), rot in zip(order, combo)}
                placed, unplaced = _fill_box(box, order, rotation, apart)
                key = _score(placed, unplaced, order)
                if best is None or key < best[0]:
                    best = (key, placed, unplaced)
        else:
            # one type at a time; later types start from their lowest rotation
            rotation = {item["id"]: min(opts, key=lambda r: r[0])
                        for (item, _), opts in zip(order, options)}
            for (item, _), opts in zip(order, options):
                trials = []
                for rot in opts:
                    trial = {**rotation, item["id"]: rot}
                    placed, unplaced = _fill_box(box, order, trial, apart)
                    trials.append((_score(placed, unplaced, order), rot,
                                   placed, unplaced))
                key, rot, placed, unplaced = min(trials, key=lambda t: t[0])
                rotation[item["id"]] = rot
                if best is None or key < best[0]:
                    best = (key, placed, unplaced)
    return best[1], best[2]


def _count_combos(options) -> int:
    total = 1
    for opts in options:
        total *= len(opts)
    return total


def pack(
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    settings=None,
    estimates: list[dict[str, Any]] | None = None,
) -> tuple[FlatResponse, list[str]]:
    """Plan with code only. items/boxes are the prepared ones.

    Returns (plan, validation errors). Errors can remain for rules the
    greedy packer doesn't steer by (e.g. keep_together).
    estimates: if a list is given, one row per usable box type is appended:
    how a single box of that type does on the whole order (fits_all = every
    packable unit placed, units placed, fill height, cut-down shipped
    volume). Free: the first box choice computes it anyway. Items that fit
    no box (precompute's unpackable) don't count against fits_all.
    """
    apart = [set(g) for g in getattr(settings, "keep_apart", []) or []]
    max_boxes = getattr(settings, "max_boxes", None)
    remaining = {i["id"]: i["quantity"] for i in items if i["quantity"] > 0}
    by_id = {i["id"]: i for i in items}
    stock = {b["id"]: b.get("stock") for b in boxes}

    unpackable = (set(precompute(items, boxes).unpackable)
                  if estimates is not None else set())
    box_rows, placements = [], []
    while remaining and (max_boxes is None or len(box_rows) < max_boxes):
        groups = [(by_id[i], n) for i, n in remaining.items()]
        options = []
        for box in boxes:
            if stock[box["id"]] is not None and stock[box["id"]] <= 0:
                continue
            placed, unplaced = _pack_one_box(box, groups, apart)
            if not placed:
                continue
            packed_volume = sum(_vol(u["row"].dims) for u in placed)
            d = box["dimensions"]
            fill = max(u["row"].pos[0] + u["row"].dims[0] for u in placed)
            shipped = cut_height(fill, d["x"], settings) * d["y"] * d["z"]
            # everything fits: least shipped (cut-down) volume, then fewest
            # units on edge; else: most volume packed
            key = ((0, round(shipped, 6), _standing(placed)) if not unplaced
                   else (1, -packed_volume, 0))
            options.append((key, box, placed))
            if estimates is not None and not box_rows:
                estimates.append({
                    "id": box["id"],
                    "fits_all": not set(unplaced) - unpackable,
                    "units": len(placed), "fill_height": round(fill, 3),
                    "shipped_volume": round(shipped, 3)})
        if not options:
            break
        _, box, placed = min(options, key=lambda o: o[0])
        number = len(box_rows) + 1
        box_rows.append({"box": number, "id": box["id"]})
        if stock[box["id"]] is not None:
            stock[box["id"]] -= 1
        for seq, u in enumerate(placed, start=1):
            row = u["row"]
            placements.append({"box": number, "seq": seq, "id": row.id,
                               "pos": row.pos, "dims": row.dims})
            remaining[row.id] -= 1
        remaining = {i: n for i, n in remaining.items() if n > 0}

    leftovers = [{"id": i, "quantity": n,
                  "reason": "no room left in the available boxes (code "
                            "packer; search failed, not verified impossible)"}
                 for i, n in remaining.items()]
    flat = FlatResponse.model_validate({
        "boxes": box_rows, "placements": placements, "leftovers": leftovers,
        "notes": ["Placed by the code packer: load units in seq order, "
                  "bottom layer first."],
    })
    return flat, validate_plan(flat, items, boxes, settings)


def pack_guided(
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    settings,
    box_ids: Sequence[int],
    order: Sequence[int],
    rotations: Mapping[int, Sequence[float]],
    preferences: Mapping[int, Mapping[str, str]] | None = None,
) -> tuple[FlatResponse | None, list[str]]:
    """Place units from the model's choices with the packer's greedy fill.

    Boxes of the listed types are opened in turn; each takes the remaining
    units in `order` (item ids; unlisted ones go last) with one rotation per
    item type; every unit goes to the lowest, back-most, left-most free
    spot. Returns (plan, errors): errors for invalid choices (no plan) or
    from validate_plan; units that fit nowhere are leftovers.

    preferences: item id -> {"side": any|left|right|back|front, "level":
    any|bottom|top}. Levels order the item types (bottom first, then
    unlisted, then top; the model's order within each group); sides and top
    change the candidate spots of that item (see _hooks). Every placement is
    still checked, so a preference never breaks a rule.
    """
    by_id = {i["id"]: i for i in items}
    box_by_id = {b["id"]: b for b in boxes}
    errors = [f"unknown box type id {b}" for b in box_ids
              if b not in box_by_id]
    errors += [f"unknown item id {i} in order" for i in order
               if i not in by_id]
    preferences = preferences or {}
    for item_id, pref in preferences.items():
        if item_id not in by_id:
            errors.append(f"unknown item id {item_id} in preferences")
        elif (pref.get("side", "any") not in SIDES
              or pref.get("level", "any") not in LEVELS):
            errors.append(f"item {item_id}: invalid preference {dict(pref)}")
    rotation = {}
    for item in items:
        dims = rotations.get(item["id"])
        allowed = item_rotations(item)
        if dims is None:
            rotation[item["id"]] = allowed[0]
            continue
        match = next((r for r in allowed
                      if all(abs(a - b) <= EPS for a, b in zip(r, dims))),
                     None)
        if match is None:
            errors.append(
                f"item {item['id']}: rotation {list(dims)} is not allowed; "
                f"allowed: {', '.join(str(list(r)) for r in allowed)}")
        rotation[item["id"]] = match or allowed[0]
    if errors:
        return None, errors

    apart = [set(g) for g in getattr(settings, "keep_apart", []) or []]
    seen = list(dict.fromkeys(order))
    sequence = seen + [i["id"] for i in items if i["id"] not in seen]
    sequence.sort(key=lambda i: _LEVEL_RANK[
        preferences.get(i, {}).get("level", "any")])  # stable
    point_order, extra_points = _hooks(preferences)
    remaining = {i: by_id[i]["quantity"] for i in sequence
                 if by_id[i]["quantity"] > 0}
    box_rows, placements = [], []
    for type_id in box_ids:
        if not remaining:
            break
        groups = [(by_id[i], n) for i, n in remaining.items()]
        placed, _unplaced = _fill_box(box_by_id[type_id], groups, rotation,
                                      apart, point_order, extra_points)
        if not placed:
            continue
        number = len(box_rows) + 1
        box_rows.append({"box": number, "id": type_id})
        for seq, u in enumerate(placed, start=1):
            row = u["row"]
            placements.append({"box": number, "seq": seq, "id": row.id,
                               "pos": row.pos, "dims": row.dims})
            remaining[row.id] -= 1
        remaining = {i: n for i, n in remaining.items() if n > 0}

    leftovers = [{"id": i, "quantity": n,
                  "reason": "no room left in the chosen boxes (code placement "
                            "from the model's choices; search failed)"}
                 for i, n in remaining.items()]
    flat = FlatResponse.model_validate({
        "boxes": box_rows, "placements": placements, "leftovers": leftovers,
        "notes": []})
    return flat, validate_plan(flat, items, boxes, settings)


# --- Placement preferences ---------------------------------------------------
# Coordinates: z runs left to right, y back to front, x up (schemas.py), so
# left = small z, back = small y.

def _hooks(preferences: Mapping[int, Mapping[str, str]]):
    """Per item id: the candidate sort key and the extra wall-anchored
    candidate points (see _fill_box). Items with nothing to steer get none."""
    point_order: dict[int, Callable] = {}
    extra_points: dict[int, Callable] = {}
    for item_id, pref in preferences.items():
        side, level = pref.get("side", "any"), pref.get("level", "any")
        if side != "any" or level == "top":
            point_order[item_id] = _order_key(side, level == "top")
        if side != "any":
            extra_points[item_id] = _extra_points(side)
    return point_order, extra_points


def _order_key(side: str, top: bool) -> Callable:
    def key(pos, dims, size):
        x, y, z = pos
        k = {"left": (x, z, y),
             "right": (x, -(z + dims[2]), y),
             "back": (x, y, z),
             "front": (x, -(y + dims[1]), z)}.get(side, (x, y, z))
        # top: the highest valid spot first (side order still breaks ties)
        return (-k[0], *k[1:]) if top else k
    return key


def _extra_points(side: str) -> Callable:
    """Points against the wall of the preferred side."""
    def extra(dims, size, points):
        out = set()
        for x, y, z in points:
            if side == "left":
                out.add((x, y, 0.0))
            elif side == "back":
                out.add((x, 0.0, z))
            elif side == "right" and size[2] - dims[2] >= 0:
                out.add((x, y, size[2] - dims[2]))
            elif side == "front" and size[1] - dims[1] >= 0:
                out.add((x, size[1] - dims[1], z))
        return out
    return extra


def _nothing_above(row: PlacementRow, rows: Sequence[PlacementRow],
                   top_ids: set[int]) -> bool:
    """No unit of an item without a top preference sits above this one:
    its Y-Z footprint overlaps and its bottom is at or above this top."""
    top = row.pos[0] + row.dims[0]
    return not any(
        o is not row and o.id not in top_ids
        and _overlap(row, o, (1, 2)) and o.pos[0] >= top - EPS
        for o in rows)


def preference_report(
    flat: FlatResponse,
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    preferences: Mapping[int, Mapping[str, str]],
) -> list[dict[str, Any]]:
    """One row per item with a preference: {id, name, side, level, honoured,
    total, detail}. A unit honours it when every stated part holds, in box
    coordinates (items and boxes are the prepared ones):
    left/right = unit centre in the left/right half (z), back/front = in the
    back/front half (y), bottom = x == 0, top = no unit of an item without a
    top preference sits above it (so top units may stack on each other).
    """
    size = {b["id"]: (b["dimensions"]["x"], b["dimensions"]["y"],
                      b["dimensions"]["z"]) for b in boxes}
    box_type = {b.box: b.id for b in flat.boxes}
    rows_by_box: dict[int, list[PlacementRow]] = {}
    for row in flat.placements:
        rows_by_box.setdefault(row.box, []).append(row)
    top_ids = {i for i, p in preferences.items()
               if p.get("level", "any") == "top"}

    def holds(row: PlacementRow, side: str, level: str) -> bool:
        _, box_y, box_z = size[box_type[row.box]]
        cy, cz = row.pos[1] + row.dims[1] / 2, row.pos[2] + row.dims[2] / 2
        ok = {"left": cz < box_z / 2, "right": cz > box_z / 2,
              "back": cy < box_y / 2, "front": cy > box_y / 2,
              "any": True}[side]
        if level == "bottom":
            ok = ok and row.pos[0] <= EPS
        elif level == "top":
            ok = ok and _nothing_above(row, rows_by_box[row.box], top_ids)
        return ok

    words = {"left": "on the left half", "right": "on the right half",
             "back": "in the back half", "front": "in the front half",
             "bottom": "on the floor", "top": "on top"}
    report = []
    for item in sorted(items, key=lambda i: i["id"]):
        pref = preferences.get(item["id"])
        side = (pref or {}).get("side", "any")
        level = (pref or {}).get("level", "any")
        if side == "any" and level == "any":
            continue
        units = [r for r in flat.placements if r.id == item["id"]]
        honoured = sum(holds(r, side, level) for r in units)
        stated = " and ".join(words[w] for w in (side, level) if w != "any")
        report.append({
            "id": item["id"], "name": item["name"], "side": side,
            "level": level, "honoured": honoured, "total": len(units),
            "detail": f"{honoured}/{len(units)} {stated}"})
    return report
