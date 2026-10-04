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
"""

from collections.abc import Mapping, Sequence
from itertools import product
from typing import Any

from .precompute import EPS, item_rotations
from .schemas import FlatResponse, PlacementRow
from .validation import _covered, _overlap, cut_height, validate_plan

MAX_COMBOS = 64  # rotation combinations tried per box; beyond that, greedy


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


def _fill_box(box, groups, rotation, apart):
    """Place units of each (item, count) group in order with the given
    rotation per item id. Returns (placed units, unplaced counts)."""
    size = (box["dimensions"]["x"], box["dimensions"]["y"],
            box["dimensions"]["z"])
    weight_max = box.get("weightMax")
    placed: list[dict] = []
    points = {(0.0, 0.0, 0.0)}
    unplaced: dict[int, int] = {}
    for item, count in groups:
        dims = rotation[item["id"]]
        for _ in range(count):
            for pos in sorted(points):
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
) -> tuple[FlatResponse, list[str]]:
    """Plan with code only. items/boxes are the prepared ones.

    Returns (plan, validation errors). Errors can remain for rules the
    greedy packer doesn't steer by (e.g. keep_together).
    """
    apart = [set(g) for g in getattr(settings, "keep_apart", []) or []]
    max_boxes = getattr(settings, "max_boxes", None)
    remaining = {i["id"]: i["quantity"] for i in items if i["quantity"] > 0}
    by_id = {i["id"]: i for i in items}
    stock = {b["id"]: b.get("stock") for b in boxes}

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
