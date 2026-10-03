"""Facts about items and boxes computed in code so the model doesn't have to.

Smaller models get fit arithmetic wrong ("does 23.38 fit in 12.75 in any
rotation?"), so the checklist work is done here and handed to the model as
tables: which rotations fit which box, how many units fit per layer, which
items fit nowhere, and which box types could hold everything.

Optional item keys honoured here: `upright` (bool) keeps the item's X
(height) axis vertical, so only rotations about X are allowed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import permutations
from math import floor, prod
from typing import Any

from tabular import to_table

EPS = 1e-9

Dims = tuple[float, float, float]


@dataclass
class Rotation:
    dims: Dims  # (dx, dy, dz) in plan axes: X height, Y depth, Z span
    per_layer: int  # units side by side on the box floor (Y–Z)
    layers: int  # layers that stack within the box height (X)

    @property
    def capacity(self) -> int:
        return self.per_layer * self.layers


@dataclass
class Fit:
    item_ref: int
    box_ref: int
    rotations: list[Rotation]  # only rotations that fit, best capacity first
    max_by_weight: int | None  # units the box weight limit allows


@dataclass
class Precomputed:
    fits: list[Fit]
    unpackable: dict[int, str]  # item refId -> reason
    item_stats: list[dict[str, Any]]
    box_stats: list[dict[str, Any]]
    totals: dict[str, float]
    # Unflagged first, then smallest volume first.
    single_box_candidates: list[int] = field(default_factory=list)
    # box refId -> shortfalls, e.g. "Tape: grid fits 16 of 20". A flagged
    # candidate can only work if mixing rotations beats the per-rotation grid.
    candidate_flags: dict[int, list[str]] = field(default_factory=dict)

    @property
    def unflagged_candidates(self) -> list[int]:
        return [b for b in self.single_box_candidates
                if not self.candidate_flags.get(b)]


def _dims(record: Mapping[str, Any]) -> Dims:
    d = record["dimensions"]
    return (d["x"], d["y"], d["z"])


def item_rotations(item: Mapping[str, Any]) -> list[Dims]:
    """Distinct axis-aligned orientations the item may take."""
    x, y, z = _dims(item)
    if item.get("upright"):
        candidates = [(x, y, z), (x, z, y)]
    else:
        candidates = list(permutations((x, y, z)))
    return list(dict.fromkeys(candidates))


def _units_along(space: float, size: float) -> int:
    return floor(space / size + EPS)


def _rotation_in(box: Dims, rot: Dims) -> Rotation:
    return Rotation(
        dims=rot,
        per_layer=_units_along(box[1], rot[1]) * _units_along(box[2], rot[2]),
        layers=_units_along(box[0], rot[0]),
    )


def precompute(
    items: Sequence[Mapping[str, Any]], boxes: Sequence[Mapping[str, Any]]
) -> Precomputed:
    fits: list[Fit] = []
    unpackable: dict[int, str] = {}
    largest_box_side = max(max(_dims(b)) for b in boxes)

    for item in items:
        item_fits = []
        for box in boxes:
            rotations = [
                r for r in (_rotation_in(_dims(box), rot)
                            for rot in item_rotations(item))
                if r.capacity > 0
            ]
            if not rotations:
                continue
            rotations.sort(key=lambda r: (-r.capacity, r.dims[0]))
            weight_max = box.get("weightMax")
            max_by_weight = (
                floor(weight_max / item["weight"] + EPS)
                if weight_max is not None and item["weight"] > 0
                else None
            )
            item_fits.append(
                Fit(item["refId"], box["refId"], rotations, max_by_weight)
            )
        if item_fits:
            fits.extend(item_fits)
        else:
            size = "×".join(f"{v:g}" for v in _dims(item))
            unpackable[item["refId"]] = (
                f"no allowed rotation of {size} fits any box "
                f"(largest box side {largest_box_side:g})"
            )

    item_stats = []
    for item in items:
        unit_volume = prod(_dims(item))
        item_stats.append({
            "ref_id": item["refId"],
            "name": item["name"],
            "qty": item["quantity"],
            "unit_volume": round(unit_volume, 3),
            "total_volume": round(unit_volume * item["quantity"], 3),
            "total_weight": round(item["weight"] * item["quantity"], 3),
            "fits_a_box": item["refId"] not in unpackable,
        })

    box_stats = [
        {
            "ref_id": box["refId"],
            "name": box["name"],
            "volume": round(prod(_dims(box)), 3),
            "weightMax": box.get("weightMax"),
        }
        for box in boxes
    ]

    packable = [s for s in item_stats if s["fits_a_box"]]
    totals = {
        "packable_units": sum(s["qty"] for s in packable),
        "packable_volume": round(sum(s["total_volume"] for s in packable), 3),
        "packable_weight": round(sum(s["total_weight"] for s in packable), 3),
    }

    # Necessary, not sufficient: every packable item type fits the box type
    # and the totals are within its volume and weight limit.
    fit_pairs = {(f.item_ref, f.box_ref) for f in fits}
    candidates = [
        b for b in box_stats
        if all((s["ref_id"], b["ref_id"]) in fit_pairs for s in packable)
        and totals["packable_volume"] <= b["volume"] + EPS
        and (b["weightMax"] is None
             or totals["packable_weight"] <= b["weightMax"] + EPS)
    ]

    # Flag candidates where an item type outnumbers its best single-rotation
    # grid in that box. Not a hard exclusion: mixed rotations can fit more.
    best_grid = {
        (f.item_ref, f.box_ref): max(r.capacity for r in f.rotations)
        for f in fits
    }
    flags = {
        b["ref_id"]: [
            f"{s['name']}: grid fits {best_grid[s['ref_id'], b['ref_id']]}"
            f" of {s['qty']}"
            for s in packable
            if best_grid[s["ref_id"], b["ref_id"]] < s["qty"]
        ]
        for b in candidates
    }
    candidates.sort(key=lambda b: (bool(flags[b["ref_id"]]), b["volume"]))

    return Precomputed(
        fits=fits,
        unpackable=unpackable,
        item_stats=item_stats,
        box_stats=box_stats,
        totals=totals,
        single_box_candidates=[b["ref_id"] for b in candidates],
        candidate_flags={ref: notes for ref, notes in flags.items() if notes},
    )


def _fmt_rotation(r: Rotation) -> str:
    dims = "×".join(f"{v:g}" for v in r.dims)
    return f"{dims}={r.per_layer}/layer×{r.layers}"


def precompute_tables(pre: Precomputed) -> str:
    """Render precomputed facts as prompt tables."""
    fit_rows = [
        {
            "item": f.item_ref,
            "box": f.box_ref,
            "max_by_weight": f.max_by_weight,
            "rotations": ", ".join(_fmt_rotation(r) for r in f.rotations),
        }
        for f in pre.fits
    ]
    sections = [
        to_table("item_stats", pre.item_stats),
        to_table("box_stats", pre.box_stats),
        to_table("fits", fit_rows),
    ]
    if pre.unpackable:
        sections.append(to_table("unpackable", [
            {"ref_id": ref, "reason": reason}
            for ref, reason in pre.unpackable.items()
        ]))
    totals = ", ".join(f"{k}={v:g}" for k, v in pre.totals.items())
    candidates = ", ".join(
        f"{ref} (flagged: {'; '.join(pre.candidate_flags[ref])})"
        if ref in pre.candidate_flags else str(ref)
        for ref in pre.single_box_candidates
    ) or "none"
    sections.append(f"totals: {totals}")
    sections.append(
        f"single_box_candidates (unflagged first, then smallest): {candidates}"
    )
    return "\n\n".join(sections)
