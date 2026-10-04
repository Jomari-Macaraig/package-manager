"""Check a flat packing plan against the packing rules.

`validate_plan` returns a list of human-readable errors (empty = valid).
Messages name units by box and seq so a model can fix them directly.

Item rules come as flags on the prepared item dicts (settings.prepare_items):
`fixed`, `upright`, `no_stack`, `max_load`, `floor_only`, `source_id`; box
stock as `stock` on the box dicts. Box and order rules come from an optional
`settings` object (settings.PackingSettings; read with getattr so this module
doesn't import settings).
"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations
from math import ceil
from typing import Any

from .precompute import EPS, item_rotations, precompute
from .schemas import FlatResponse, PlacementRow


def _lo_hi(row: PlacementRow, axis: int) -> tuple[float, float]:
    return row.pos[axis], row.pos[axis] + row.dims[axis]


def _overlap(a: PlacementRow, b: PlacementRow, axes) -> bool:
    for axis in axes:
        a_lo, a_hi = _lo_hi(a, axis)
        b_lo, b_hi = _lo_hi(b, axis)
        if a_lo >= b_hi - EPS or b_lo >= a_hi - EPS:
            return False
    return True


def _covered(row: PlacementRow, supports: list[PlacementRow]) -> bool:
    """Is row's Y–Z footprint fully covered by the supports' footprints?"""
    (y0, y1), (z0, z1) = _lo_hi(row, 1), _lo_hi(row, 2)
    ys = sorted({y0, y1, *(v for s in supports for v in _lo_hi(s, 1)
                          if y0 < v < y1)})
    zs = sorted({z0, z1, *(v for s in supports for v in _lo_hi(s, 2)
                          if z0 < v < z1)})
    for ya, yb in zip(ys, ys[1:]):
        for za, zb in zip(zs, zs[1:]):
            cy, cz = (ya + yb) / 2, (za + zb) / 2
            if not any(
                s.pos[1] <= cy <= s.pos[1] + s.dims[1]
                and s.pos[2] <= cz <= s.pos[2] + s.dims[2]
                for s in supports
            ):
                return False
    return True


def _name(row: PlacementRow) -> str:
    return f"box {row.box} seq {row.seq} (id {row.id})"


def _source(item: Mapping[str, Any]) -> int:
    """Original item id (blocks get new ids but keep their source)."""
    return item.get("source_id", item["id"])


def cut_height(fill: float, box_height: float, settings=None) -> float:
    """Fill height rounded up to cut_step, at least min_cut_height, at most
    the box height."""
    step = getattr(settings, "cut_step", None)
    lowest = getattr(settings, "min_cut_height", None)
    cut = fill
    if step:
        cut = ceil(round(fill / step, 9)) * step
    if lowest:
        cut = max(cut, lowest)
    return min(cut, box_height)


def validate_plan(
    flat: FlatResponse,
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    settings=None,
) -> list[str]:
    errors: list[str] = []
    item_types = {i["id"]: i for i in items}
    box_types = {b["id"]: b for b in boxes}

    declared: dict[int, Mapping[str, Any]] = {}
    for row in flat.boxes:
        if row.box in declared:
            errors.append(f"box {row.box} is declared twice")
        elif row.id not in box_types:
            errors.append(f"box {row.box} uses unknown box id {row.id}")
        else:
            declared[row.box] = box_types[row.id]

    by_box: dict[int, list[PlacementRow]] = defaultdict(list)
    for row in flat.placements:
        if row.box not in declared:
            errors.append(f"{_name(row)}: box {row.box} is not declared in boxes")
            continue
        if row.id not in item_types:
            errors.append(f"{_name(row)}: unknown item id")
            continue
        by_box[row.box].append(row)

    for box_no, rows in sorted(by_box.items()):
        box = declared[box_no]
        size = (box["dimensions"]["x"], box["dimensions"]["y"],
                box["dimensions"]["z"])

        seqs = sorted(r.seq for r in rows)
        if seqs != list(range(1, len(rows) + 1)):
            errors.append(
                f"box {box_no}: seq must be 1..{len(rows)} with no gaps or "
                f"repeats, got {seqs}"
            )

        for row in rows:
            item = item_types[row.id]
            allowed = item_rotations(item)
            if not any(all(abs(a - b) <= 1e-6 for a, b in zip(row.dims, rot))
                       for rot in allowed):
                kind = ("its fixed orientation" if item.get("fixed")
                        else "an upright rotation" if item.get("upright")
                        else "a rotation")
                errors.append(
                    f"{_name(row)}: dims {row.dims} are not {kind} "
                    f"of {item['name']} {list(allowed[0])}"
                )
            if item.get("floor_only") and row.pos[0] > EPS:
                errors.append(
                    f"{_name(row)}: {item['name']} is heavy and must stand "
                    f"on the floor (x=0), not at x={row.pos[0]:g}"
                )
            for axis, label in enumerate("xyz"):
                lo, hi = _lo_hi(row, axis)
                if hi > size[axis] + EPS:
                    errors.append(
                        f"{_name(row)}: extends to {label}={hi:g}, past the box "
                        f"inside {label}={size[axis]:g}"
                    )

        for a, b in combinations(rows, 2):
            if _overlap(a, b, (0, 1, 2)):
                errors.append(f"{_name(a)} overlaps {_name(b)}")

        for row in rows:
            bottom = row.pos[0]
            if bottom <= EPS:
                continue
            resting_on = [
                s for s in rows
                if s is not row
                and abs(s.pos[0] + s.dims[0] - bottom) <= 1e-6
                and _overlap(row, s, (1, 2))
            ]
            earlier = [s for s in resting_on if s.seq < row.seq]
            if not _covered(row, resting_on):
                errors.append(
                    f"{_name(row)}: not fully supported at x={bottom:g}; its "
                    f"whole Y–Z footprint must rest on item tops"
                )
            elif not _covered(row, earlier):
                errors.append(
                    f"{_name(row)}: loaded before the items it rests on; give "
                    f"it a higher seq than its supports"
                )
            for s in resting_on:
                if item_types[s.id].get("no_stack"):
                    errors.append(
                        f"{_name(row)} rests on {_name(s)}, which must have "
                        f"nothing on top"
                    )

        for row in rows:
            limit = item_types[row.id].get("max_load")
            if limit is None:
                continue
            top = row.pos[0] + row.dims[0]
            # conservative: everything above the footprint counts fully
            load = sum(item_types[o.id]["weight"] for o in rows
                       if o is not row and o.pos[0] >= top - EPS
                       and _overlap(row, o, (1, 2)))
            if load > limit + EPS:
                errors.append(
                    f"{_name(row)}: carries {load:g} lb above it, over its "
                    f"max load on top of {limit:g} lb"
                )

        for row in rows:
            top = row.pos[0] + row.dims[0]
            for other in rows:
                if (other.seq < row.seq and other.pos[0] >= top - EPS
                        and _overlap(row, other, (1, 2))):
                    errors.append(
                        f"{_name(row)}: can't be lowered into place, "
                        f"{_name(other)} is already above it"
                    )

        weight_max = box.get("weightMax")
        weight = sum(item_types[r.id]["weight"] for r in rows)
        if weight_max is not None and weight > weight_max + EPS:
            errors.append(
                f"box {box_no}: contents weigh {weight:g}, over weightMax "
                f"{weight_max:g}"
            )

    used = Counter(row.id for row in flat.boxes)
    for box_id, count in used.items():
        stock = box_types.get(box_id, {}).get("stock")
        if stock is not None and count > stock:
            errors.append(
                f"uses {count} of box type {box_id} "
                f"({box_types[box_id]['name']}), but only {stock} in stock"
            )

    if settings is not None:
        errors += _settings_errors(flat, item_types, declared, by_box,
                                   settings)

    placed = Counter(r.id for r in flat.placements)
    left = Counter()
    for row in flat.leftovers:
        if row.id not in item_types:
            errors.append(f"leftovers: unknown item id {row.id}")
        else:
            left[row.id] += row.quantity
    for ref, item in item_types.items():
        if placed[ref] + left[ref] != item["quantity"]:
            errors.append(
                f"{item['name']} (id {ref}): placed {placed[ref]} + leftover "
                f"{left[ref]} != requested {item['quantity']}"
            )

    return errors


def _settings_errors(flat, item_types, declared, by_box, settings) -> list:
    """Order and carrier rules from PackingSettings."""
    errors = []
    max_boxes = getattr(settings, "max_boxes", None)
    if max_boxes is not None and len(flat.boxes) > max_boxes:
        errors.append(f"uses {len(flat.boxes)} boxes; at most {max_boxes} "
                      f"allowed (leave the rest as leftovers)")

    sources_in_box = {
        box_no: {_source(item_types[r.id]) for r in rows}
        for box_no, rows in by_box.items()
    }
    for group in getattr(settings, "keep_together", []) or []:
        boxes_used = sorted(b for b, src in sources_in_box.items()
                            if src & set(group))
        if len(boxes_used) > 1:
            errors.append(f"items {sorted(group)} must be in one box, but "
                          f"are spread over boxes {boxes_used}")
    for group in getattr(settings, "keep_apart", []) or []:
        for box_no, src in sorted(sources_in_box.items()):
            together = sorted(src & set(group))
            if len(together) > 1:
                errors.append(f"box {box_no}: items {together} must never "
                              f"share a box")

    max_weight = getattr(settings, "carrier_max_weight", None)
    max_length = getattr(settings, "carrier_max_length", None)
    max_girth = getattr(settings, "carrier_max_girth", None)
    fills = fill_heights(flat)
    for box_no, rows in sorted(by_box.items()):
        box = declared[box_no]
        d = box["dimensions"]
        weight = (sum(item_types[r.id]["weight"] for r in rows)
                  + (box.get("weightTare") or 0))
        if max_weight is not None and weight > max_weight + EPS:
            errors.append(f"box {box_no}: ships at {weight:g} lb with tare, "
                          f"over the carrier limit of {max_weight:g} lb")
        shipped = sorted([cut_height(fills[box_no], d["x"], settings),
                          d["y"], d["z"]], reverse=True)
        if max_length is not None and shipped[0] > max_length + EPS:
            errors.append(f"box {box_no}: longest side {shipped[0]:g} in is "
                          f"over the carrier limit of {max_length:g} in")
        girth = shipped[0] + 2 * (shipped[1] + shipped[2])
        if max_girth is not None and girth > max_girth + EPS:
            errors.append(f"box {box_no}: length + girth {girth:g} in is over "
                          f"the carrier limit of {max_girth:g} in")
    return errors


def fill_heights(flat: FlatResponse) -> dict[int, float]:
    """Highest occupied X per box."""
    heights: dict[int, float] = defaultdict(float)
    for r in flat.placements:
        heights[r.box] = max(heights[r.box], r.pos[0] + r.dims[0])
    return dict(heights)


def _floor_spot(
    rows: list[PlacementRow], size: tuple[float, float, float],
    rotations: list[tuple[float, float, float]],
) -> tuple[tuple[float, float, float], tuple[float, float]] | None:
    """A free floor position (y, z) where one of the rotations fits."""
    ys = sorted({0.0, *(r.pos[1] + r.dims[1] for r in rows)})
    zs = sorted({0.0, *(r.pos[2] + r.dims[2] for r in rows)})
    for rot in rotations:
        if rot[0] > size[0] + EPS:
            continue
        for y in ys:
            for z in zs:
                if y + rot[1] > size[1] + EPS or z + rot[2] > size[2] + EPS:
                    continue
                probe = PlacementRow(box=0, seq=1, id=0, pos=[0, y, z],
                                     dims=list(rot))
                if not any(_overlap(probe, r, (0, 1, 2)) for r in rows):
                    return rot, (y, z)
    return None


def quality_hints(
    flat: FlatResponse,
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    settings=None,
) -> list[str]:
    """Ways a *valid* plan could be better; empty when none are found.

    - More than one box used while an unflagged single-box candidate exists.
    - Units stacked above the floor while the floor still has room for one.
    """
    hints: list[str] = []
    item_types = {i["id"]: i for i in items}
    box_types = {b["id"]: b for b in boxes}
    pre = precompute(items, boxes)
    candidates = pre.unflagged_candidates

    if len(flat.boxes) > 1 and candidates:
        best = box_types[candidates[0]]
        hints.append(
            f"uses {len(flat.boxes)} boxes, but box type {best['id']} "
            f"({best['name']}) may hold every packable unit; try packing "
            f"everything into one {best['name']}, filling its whole floor "
            f"before stacking"
        )

    by_box: dict[int, list[PlacementRow]] = defaultdict(list)
    for row in flat.placements:
        by_box[row.box].append(row)
    for box_row in flat.boxes:
        rows = by_box.get(box_row.box, [])
        box = box_types.get(box_row.id)
        if box is None:
            continue
        size = (box["dimensions"]["x"], box["dimensions"]["y"],
                box["dimensions"]["z"])
        stacked_refs = {r.id for r in rows if r.pos[0] > EPS}
        for ref in sorted(stacked_refs):
            spot = _floor_spot(rows, size, item_rotations(item_types[ref]))
            if spot:
                (dx, dy, dz), (y, z) = spot
                top = max(r.pos[0] + r.dims[0] for r in rows)
                hints.append(
                    f"box {box_row.box}: a {item_types[ref]['name']} "
                    f"({dx:g}×{dy:g}×{dz:g}) fits on the free floor at "
                    f"y={y:g}, z={z:g}, but units are stacked up to x={top:g};"
                    f" fill the floor before stacking"
                )
                break

    min_fill = getattr(settings, "min_fill", None)
    if min_fill:
        fills = fill_heights(flat)
        for box_row in flat.boxes:
            rows = by_box.get(box_row.box, [])
            d = box_types[box_row.id]["dimensions"]
            shipped = cut_height(fills.get(box_row.box, 0), d["x"],
                                 settings) * d["y"] * d["z"]
            used = sum(r.dims[0] * r.dims[1] * r.dims[2] for r in rows)
            if shipped and used / shipped < min_fill - 1e-9:
                hints.append(
                    f"box {box_row.box} is {used / shipped:.0%} full after "
                    f"cutting down; aim for at least {min_fill:.0%} (a "
                    f"smaller box or fewer boxes)"
                )

    if getattr(settings, "balance_boxes", False) and len(flat.boxes) > 1:
        weights = {b.box: sum(item_types[r.id]["weight"]
                              for r in by_box.get(b.box, []))
                   for b in flat.boxes}
        mean = sum(weights.values()) / len(weights)
        if max(weights.values()) - min(weights.values()) > 0.25 * mean:
            spread = ", ".join(f"box {b}: {w:g} lb"
                               for b, w in sorted(weights.items()))
            hints.append(f"box weights are uneven ({spread}); move units so "
                         f"each box weighs about {mean:g} lb")
    return hints


def plan_score(
    flat: FlatResponse, boxes: Sequence[Mapping[str, Any]]
) -> tuple[int, float, float]:
    """Lower is better: box count, shipped volume (each box cut down to its
    fill height × floor area), summed fill height."""
    dims = {b["id"]: b["dimensions"] for b in boxes}
    fills = fill_heights(flat)
    shipped = sum(
        fills.get(b.box, 0) * dims[b.id]["y"] * dims[b.id]["z"]
        for b in flat.boxes
    )
    return len(flat.boxes), round(shipped, 6), sum(fills.values())
