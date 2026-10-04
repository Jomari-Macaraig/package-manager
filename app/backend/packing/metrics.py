"""Plan metrics and model-vs-packer comparison (evaluation).

plan_metrics(plan) scores a finished nested Response: boxes, leftovers,
volumes, fill, weight and center of gravity. compare(model, packer) lines
two metric dicts up with a verdict per metric, so a run (or a batch of
sample cases) shows where the model beats the code packer and where it
loses. Pure code, no model calls.
"""

from collections.abc import Mapping
from typing import Any, Literal

from .schemas import Response

Direction = Literal["lower", "higher", "info"]

# name -> (label, unit, which way is better)
METRICS: dict[str, tuple[str, str, Direction]] = {
    "leftover_units": ("Leftover units", "", "lower"),
    "boxes": ("Boxes", "", "lower"),
    "shipped_volume": ("Shipped volume (cut down)", "in³", "lower"),
    "box_volume": ("Box volume (uncut)", "in³", "lower"),
    "fill_rate": ("Fill rate (units / shipped)", "%", "higher"),
    "max_fill_height": ("Tallest fill height", "in", "lower"),
    "units_on_floor": ("Units on the floor", "%", "higher"),
    "cog_offset": ("CoG off center (worst box)", "%", "lower"),
    "cog_height": ("CoG height (worst box)", "%", "lower"),
    "weight_spread": ("Weight spread across boxes", "lb", "lower"),
    "total_weight": ("Packed weight", "lb", "info"),
    "units_packed": ("Units packed", "", "info"),
}

TOLERANCE = 0.01  # differences up to this are "same" (values are rounded)


def plan_metrics(plan: Response) -> dict[str, float]:
    """Metrics for one plan (lower/higher-is-better per METRICS).

    shipped_volume: each box cut down to its fill height × floor area (the
    same measure the planner ranks by; cut_step rounding is not applied).
    cog_offset: horizontal distance of a box's center of gravity from the
    floor center, as % of half the floor diagonal. cog_height: its height as
    % of the fill height (heavy at the bottom is lower). Both take the worst
    box. units_on_floor: % of packed units standing at x = 0.
    """
    shipped = box_volume = unit_volume = max_fill = 0.0
    cog_offset = cog_height = 0.0
    on_floor = packed = 0
    weights: list[float] = []
    for box in plan.boxes:
        d = box.dimensions
        box_volume += d.x * d.y * d.z
        fill = max((u.placement_position.x + u.dimensions.x
                    for u in box.items), default=0.0)
        max_fill = max(max_fill, fill)
        shipped += fill * d.y * d.z
        weight = sum(u.weight for u in box.items)
        weights.append(weight)
        packed += len(box.items)
        on_floor += sum(1 for u in box.items if u.placement_position.x == 0)
        unit_volume += sum(u.dimensions.x * u.dimensions.y * u.dimensions.z
                           for u in box.items)
        if weight <= 0 or fill <= 0:
            continue

        def center(axis: str) -> float:
            return sum(u.weight * (getattr(u.placement_position, axis)
                                   + getattr(u.dimensions, axis) / 2)
                       for u in box.items) / weight

        half_diagonal = ((d.y / 2) ** 2 + (d.z / 2) ** 2) ** 0.5
        off = ((center("y") - d.y / 2) ** 2
               + (center("z") - d.z / 2) ** 2) ** 0.5
        cog_offset = max(cog_offset, 100 * off / half_diagonal)
        cog_height = max(cog_height, 100 * center("x") / fill)

    leftover = sum(i.quantity for group in plan.leftover_items
                   for i in group.items)
    values = {
        "leftover_units": leftover,
        "boxes": len(plan.boxes),
        "shipped_volume": shipped,
        "box_volume": box_volume,
        "fill_rate": 100 * unit_volume / shipped if shipped else 0.0,
        "max_fill_height": max_fill,
        "units_on_floor": 100 * on_floor / packed if packed else 0.0,
        "cog_offset": cog_offset,
        "cog_height": cog_height,
        "weight_spread": max(weights) - min(weights) if weights else 0.0,
        "total_weight": sum(weights),
        "units_packed": packed,
    }
    return {k: round(v, 2) for k, v in values.items()}


def compare(model: Mapping[str, float] | None,
            packer: Mapping[str, float] | None) -> list[dict[str, Any]]:
    """One row per metric: label, unit, direction, both values, the model's
    delta, and verdict "better" / "worse" / "same" for the model ("" when
    either side is missing or the metric is informational)."""
    rows = []
    for name, (label, unit, direction) in METRICS.items():
        m = model.get(name) if model else None
        p = packer.get(name) if packer else None
        delta = verdict = None
        if m is not None and p is not None:
            delta = round(m - p, 2)
            if direction != "info":
                gain = -delta if direction == "lower" else delta
                verdict = ("same" if abs(gain) <= TOLERANCE
                           else "better" if gain > 0 else "worse")
        rows.append({"metric": name, "label": label, "unit": unit,
                     "direction": direction, "model": m, "packer": p,
                     "delta": delta, "verdict": verdict})
    return rows


def tally(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Count of better / worse / same verdicts in compare() rows."""
    counts = {"better": 0, "worse": 0, "same": 0}
    for row in rows:
        if row["verdict"] in counts:
            counts[row["verdict"]] += 1
    return counts
