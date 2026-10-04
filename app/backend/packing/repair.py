"""Code-computed help for fix rounds.

validate_plan names the broken units ("box 1 seq 9 (id 5) overlaps box 1
seq 11 (id 1)"). repair_help picks the unit to move for each error (the
later-loaded one for an overlap), keeps every other unit where it is, and
tells the model what it got wrong and where the unit would fit: the box's
inside size, the stack tops under the unit's footprint, and a free spot
found with the packer's placement check. The model edits only those units.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .packer import _fits_here
from .precompute import EPS, item_rotations
from .schemas import FlatResponse, PlacementRow
from .validation import _overlap

MAX_UNITS_HELPED = 10
_REF = re.compile(r"box (\d+) seq (\d+) \(id (\d+)\)")


def _g(v: float) -> str:
    return f"{v:g}"


def units_to_move(errors: Sequence[str]) -> list[tuple[int, int]]:
    """(box, seq) of the unit to move per error, in first-seen order."""
    movers: dict[tuple[int, int], None] = {}
    for error in errors:
        refs = [(int(b), int(s)) for b, s, _ in _REF.findall(error)]
        if refs:
            movers[max(refs, key=lambda r: r[1])] = None
    return list(movers)


def _tops_under(row: PlacementRow, others: list[PlacementRow]) -> str:
    """Stack tops under row's Y–Z footprint, at or below its bottom."""
    tops: dict[float, list[int]] = {}
    for o in others:
        top = o.pos[0] + o.dims[0]
        if top <= row.pos[0] + EPS and _overlap(row, o, (1, 2)):
            tops.setdefault(round(top, 6), []).append(o.seq)
    if not tops:
        return "nothing under it but the floor (x=0)"
    return "; ".join(f"x={_g(t)} (seq {', '.join(map(str, sorted(s)))})"
                     for t, s in sorted(tops.items(), reverse=True))


def _candidate_points(rows: list[PlacementRow]) -> list[tuple]:
    points = {(0.0, 0.0, 0.0)}
    for r in rows:
        x, y, z = r.pos
        dx, dy, dz = r.dims
        points |= {(x + dx, y, z), (x, y + dy, z), (x, y, z + dz),
                   (0.0, y + dy, z), (0.0, y, z + dz)}
    return sorted(points)


def _free_spot(item, row: PlacementRow, fixed: list[dict], box,
               apart) -> tuple[tuple, tuple] | None:
    """Lowest, back-most, left-most spot where the unit fits with the fixed
    units as they are; every spot in its current rotation first."""
    size = (box["dimensions"]["x"], box["dimensions"]["y"],
            box["dimensions"]["z"])
    rotations = item_rotations(item)
    current = tuple(row.dims)
    rotations.sort(key=lambda r: not all(
        abs(a - b) <= EPS for a, b in zip(r, current)))
    points = _candidate_points([u["row"] for u in fixed])
    for dims in rotations:
        for pos in points:
            if _fits_here(item, pos, dims, fixed, size, box.get("weightMax"),
                          apart):
                return pos, dims
    return None


def units_short(flat: FlatResponse, items: Sequence[Mapping[str, Any]],
                baseline: FlatResponse | None = None) -> dict[int, int]:
    """Units per item id that the plan neither places nor may leave over:
    requested - placed - (leftovers the packer also had; 0 without one)."""
    def count(rows) -> dict[int, int]:
        out: dict[int, int] = {}
        for r in rows:
            out[r.id] = out.get(r.id, 0) + getattr(r, "quantity", 1)
        return out

    placed = count(flat.placements)
    allowed = count(baseline.leftovers) if baseline is not None else {}
    short = {}
    for item in items:
        n = item["quantity"] - placed.get(item["id"], 0) - allowed.get(
            item["id"], 0)
        if n > 0:
            short[item["id"]] = n
    return short


def gap(flat: FlatResponse, items, errors: Sequence[str],
        baseline: FlatResponse | None = None) -> int:
    """How far a plan is from valid, in units: units to move plus units not
    placed that should be, plus errors naming no unit (weight, box rules).
    Lower is closer; used to keep the best invalid plan."""
    moving = units_to_move(errors)
    other = sum(1 for e in errors if not _REF.search(e)
                and not e.startswith("item ") and "!= requested" not in e)
    return len(moving) + sum(units_short(flat, items, baseline).values()) + other


def repair_help(
    flat: FlatResponse,
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    errors: Sequence[str],
    settings=None,
    baseline: FlatResponse | None = None,
) -> list[str]:
    """Help lines for the units named in errors, then spots for units that
    should be placed but aren't (empty when there is nothing to say)."""
    by_item = {i["id"]: i for i in items}
    box_types = {b["id"]: b for b in boxes}
    declared = {b.box: box_types.get(b.id) for b in flat.boxes}
    apart = [set(g) for g in getattr(settings, "keep_apart", []) or []]
    movers = units_to_move(errors)
    moving = set(movers)
    lines: list[str] = []
    boxes_described: set[int] = set()
    suggested: dict[int, list[dict]] = {}

    for box_no, seq in movers[:MAX_UNITS_HELPED]:
        box = declared.get(box_no)
        row = next((r for r in flat.placements
                    if r.box == box_no and r.seq == seq), None)
        if box is None or row is None or row.id not in by_item:
            continue
        if box_no not in boxes_described:
            d = box["dimensions"]
            lines.append(f"box {box_no} ({box['name']}) inside: x {_g(d['x'])}"
                         f", y {_g(d['y'])}, z {_g(d['z'])}")
            boxes_described.add(box_no)
        others = [r for r in flat.placements
                  if r.box == box_no and (r.box, r.seq) not in moving]
        x, y, z = row.pos
        dx, dy, dz = row.dims
        name = f"box {box_no} seq {seq} (id {row.id})"
        lines.append(
            f"{name} is at x {_g(x)}–{_g(x + dx)}, y {_g(y)}–{_g(y + dy)}, "
            f"z {_g(z)}–{_g(z + dz)}; under its footprint: "
            f"{_tops_under(row, others)}")
        fixed = [{"item": by_item[r.id], "row": r} for r in others
                 if r.id in by_item] + suggested.get(box_no, [])
        spot = _free_spot(by_item[row.id], row, fixed, box, apart)
        if spot is None:
            lines.append(f"{name}: no free spot in box {box_no} with the "
                         f"other units where they are; move it to another "
                         f"box or to leftovers, or move other units")
            continue
        pos, dims = spot
        probe = PlacementRow(box=box_no, seq=seq, id=row.id, pos=list(pos),
                             dims=list(dims))
        under = [u["row"].seq for u in fixed
                 if u["row"].box == box_no
                 and abs(u["row"].pos[0] + u["row"].dims[0] - pos[0]) <= 1e-6
                 and _overlap(probe, u["row"], (1, 2))] if pos[0] > EPS else []
        order = (f"; it rests on seq {', '.join(map(str, sorted(under)))}, "
                 f"so its seq must be after those" if under else "")
        lines.append(f"{name}: fits at pos [{', '.join(map(_g, pos))}] dims "
                     f"[{', '.join(map(_g, dims))}]{order}")
        suggested.setdefault(box_no, []).append(
            {"item": by_item[row.id], "row": probe})
    lines += _unplaced_help(flat, by_item, declared, moving, suggested, apart,
                            units_short(flat, items, baseline))
    return lines


def _unplaced_help(flat, by_item, declared, moving, suggested, apart,
                   short: dict[int, int]) -> list[str]:
    """Spots, in the plan's own boxes, for units that should be placed:
    each found spot counts as taken for the next one."""
    if not short:
        return []
    lines = []
    shown = 0
    for item_id, count in short.items():
        item = by_item.get(item_id)
        if item is None:
            continue
        for n in range(count):
            spot = None
            for box_no, box in declared.items():
                if box is None:
                    continue
                fixed = [{"item": by_item[r.id], "row": r}
                         for r in flat.placements
                         if r.box == box_no and (r.box, r.seq) not in moving
                         and r.id in by_item] + suggested.get(box_no, [])
                probe_row = PlacementRow(box=box_no, seq=1, id=item_id,
                                         pos=[0, 0, 0],
                                         dims=list(item_rotations(item)[0]))
                found = _free_spot(item, probe_row, fixed, box, apart)
                if found is not None:
                    spot = (box_no, *found)
                    break
            if spot is None:
                lines.append(
                    f"item {item_id}: {count - n} unit(s) not placed have no "
                    f"free spot in your boxes as they are; add a box (the "
                    f"type the error names) or move other units")
                break
            box_no, pos, dims = spot
            row = PlacementRow(box=box_no, seq=1, id=item_id, pos=list(pos),
                               dims=list(dims))
            suggested.setdefault(box_no, []).append({"item": item, "row": row})
            if shown < MAX_UNITS_HELPED:
                lines.append(
                    f"item {item_id} (unit {n + 1} of {count} not placed): "
                    f"fits in box {box_no} at pos [{', '.join(map(_g, pos))}] "
                    f"dims [{', '.join(map(_g, dims))}]")
            shown += 1
    if shown > MAX_UNITS_HELPED:
        lines.append(f"...and {shown - MAX_UNITS_HELPED} more unit(s) fit; "
                     f"keep placing them at the lowest free spots")
    if lines:
        lines.append("Load each added unit after the units it rests on "
                     "(give it a later seq).")
    return lines
