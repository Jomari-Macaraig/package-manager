"""Check a flat packing plan against the packing rules.

`validate_plan` returns a list of human-readable errors (empty = valid).
Messages name units by box and seq so a model can fix them directly.

Optional item keys honoured here: `upright` (X axis must stay vertical) and
`fragile` (nothing may rest on the item).
"""

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

from precompute import EPS, item_rotations, precompute
from schemas import FlatResponse, PlacementRow


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
    return f"box {row.box} seq {row.seq} (ref {row.ref_id})"


def validate_plan(
    flat: FlatResponse,
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    item_types = {i["refId"]: i for i in items}
    box_types = {b["refId"]: b for b in boxes}

    declared: dict[int, Mapping[str, Any]] = {}
    for row in flat.boxes:
        if row.box in declared:
            errors.append(f"box {row.box} is declared twice")
        elif row.ref_id not in box_types:
            errors.append(f"box {row.box} uses unknown box ref_id {row.ref_id}")
        else:
            declared[row.box] = box_types[row.ref_id]

    by_box: dict[int, list[PlacementRow]] = defaultdict(list)
    for row in flat.placements:
        if row.box not in declared:
            errors.append(f"{_name(row)}: box {row.box} is not declared in boxes")
            continue
        if row.ref_id not in item_types:
            errors.append(f"{_name(row)}: unknown item ref_id")
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
            item = item_types[row.ref_id]
            allowed = item_rotations(item)
            if not any(all(abs(a - b) <= 1e-6 for a, b in zip(row.dims, rot))
                       for rot in allowed):
                kind = "upright rotation" if item.get("upright") else "rotation"
                errors.append(
                    f"{_name(row)}: dims {row.dims} are not an allowed {kind} "
                    f"of {item['name']} {list(allowed[0])}"
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
                if item_types[s.ref_id].get("fragile"):
                    errors.append(
                        f"{_name(row)} rests on fragile {_name(s)}"
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
        weight = sum(item_types[r.ref_id]["weight"] for r in rows)
        if weight_max is not None and weight > weight_max + EPS:
            errors.append(
                f"box {box_no}: contents weigh {weight:g}, over weightMax "
                f"{weight_max:g}"
            )

    placed = Counter(r.ref_id for r in flat.placements)
    left = Counter()
    for row in flat.leftovers:
        if row.ref_id not in item_types:
            errors.append(f"leftovers: unknown item ref_id {row.ref_id}")
        else:
            left[row.ref_id] += row.quantity
    for ref, item in item_types.items():
        if placed[ref] + left[ref] != item["quantity"]:
            errors.append(
                f"{item['name']} (ref {ref}): placed {placed[ref]} + leftover "
                f"{left[ref]} != requested {item['quantity']}"
            )

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
                probe = PlacementRow(box=0, seq=1, ref_id=0, pos=[0, y, z],
                                     dims=list(rot))
                if not any(_overlap(probe, r, (0, 1, 2)) for r in rows):
                    return rot, (y, z)
    return None


def quality_hints(
    flat: FlatResponse,
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
) -> list[str]:
    """Ways a *valid* plan could be better; empty when none are found.

    - More than one box used while an unflagged single-box candidate exists.
    - One box used while a smaller unflagged candidate exists.
    - Units stacked above the floor while the floor still has room for one.
    """
    hints: list[str] = []
    item_types = {i["refId"]: i for i in items}
    box_types = {b["refId"]: b for b in boxes}
    pre = precompute(items, boxes)
    candidates = pre.unflagged_candidates
    volume = {b["ref_id"]: b["volume"] for b in pre.box_stats}

    if len(flat.boxes) > 1 and candidates:
        best = box_types[candidates[0]]
        hints.append(
            f"uses {len(flat.boxes)} boxes, but box type {best['refId']} "
            f"({best['name']}) may hold every packable unit; try packing "
            f"everything into one {best['name']}, filling its whole floor "
            f"before stacking"
        )
    elif len(flat.boxes) == 1 and candidates:
        used = flat.boxes[0].ref_id
        smaller = [c for c in candidates if volume[c] < volume.get(used, 0)]
        if smaller:
            best = box_types[smaller[0]]
            hints.append(
                f"box type {best['refId']} ({best['name']}) is smaller than "
                f"the one used and may hold every packable unit; try it"
            )

    by_box: dict[int, list[PlacementRow]] = defaultdict(list)
    for row in flat.placements:
        by_box[row.box].append(row)
    for box_row in flat.boxes:
        rows = by_box.get(box_row.box, [])
        box = box_types.get(box_row.ref_id)
        if box is None:
            continue
        size = (box["dimensions"]["x"], box["dimensions"]["y"],
                box["dimensions"]["z"])
        stacked_refs = {r.ref_id for r in rows if r.pos[0] > EPS}
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
    return hints


def plan_score(
    flat: FlatResponse, boxes: Sequence[Mapping[str, Any]]
) -> tuple[int, float, float]:
    """Lower is better: box count, total box volume, summed fill height."""
    dims = {b["refId"]: b["dimensions"] for b in boxes}
    volume = sum(
        dims[b.ref_id]["x"] * dims[b.ref_id]["y"] * dims[b.ref_id]["z"]
        for b in flat.boxes
    )
    return len(flat.boxes), volume, sum(fill_heights(flat).values())
