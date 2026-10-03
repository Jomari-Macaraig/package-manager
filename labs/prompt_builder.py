"""Build the system and user prompts from what the input actually contains.

Rules for optional information (orientation limits, fragility, policies,
units, validate_plan feedback) are only included when that information is
present, so smaller models aren't distracted by rules with nothing to act on.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from precompute import precompute, precompute_tables
from tabular import to_table

DIMENSION_HEADERS = {
    "dimensions.x": "x",
    "dimensions.y": "y",
    "dimensions.z": "z",
}

_ROLE = """\
You are a 3D packing planner. Return a packing plan as JSON matching the \
schema. It drives a packing diagram, so all values must be exact."""

_PRIORITIES = """\
Priorities, in order:
1. Pack every unit. If impossible, maximize units packed.
2. Fewest boxes.
3. Least total internal box volume.
4. Lowest fill height per box: boxes may be cut down, so keep all free space \
on top.
5. Balanced, stable load that is simple to pack."""

_COORDINATES = """\
Coordinates: origin at the box's inside back-left-bottom corner. X = height \
(up), Y = depth (back to front), Z = span (left to right). Dimensions are \
X × Y × Z. With pos [x, y, z] and dims [dx, dy, dz], a unit occupies \
[x, x+dx] × [y, y+dy] × [z, z+dz]."""

_INPUT = """\
Input tables have a header row, then one row per record. x|y|z are \
dimensions; box dimensions are INTERNAL; weightMax is the box's contents \
weight limit."""

_PRECOMPUTED = """\
Precomputed facts (exact; use them, do not recompute):
- fits: for each item × box pair that fits, max_by_weight is how many units \
the box weight limit allows, and each rotation is listed as \
dx×dy×dz=P/layer×L: P units fit side by side on the floor, L layers stack. \
Only listed rotations fit.
- unpackable: item types that fit no box in any allowed rotation.
- single_box_candidates: box types whose volume and weight limit could hold \
every packable unit. Necessary, not sufficient: you must still find an \
arrangement. A flagged candidate has an item type that outnumbers its best \
single-rotation grid in that box; it can only work by mixing rotations."""

_PROCEDURE_PRECOMPUTED = """\
Procedure:
1. Put every unpackable item type in leftovers with its reason. Do not try \
to place it.
2. Choose boxes: use the first unflagged single_box_candidate. Try a \
flagged one only if no unflagged candidate exists. If no single box can \
hold everything, use the fewest boxes, filling the largest box type first.
3. Place item types from largest unit_volume to smallest; heaviest at the \
bottom.
4. For each item type, pick one listed rotation for that box, preferring \
small dx (low layers) with enough P/layer. Place its units in a grid from \
the lowest free position: fill along Z, then Y, then start the next layer \
in X.
5. Finish each layer across the Y–Z footprint before starting the next, so \
every unit above the floor rests fully on unit tops.
6. Number seq in placement order, 1..n per box."""

_PROCEDURE_PLAIN = """\
Procedure:
1. For each item type, check every allowed rotation against each box; item \
types that fit no box go to leftovers. Do not judge fit by volume alone.
2. Choose boxes: the smallest single box that could hold everything, else \
the fewest boxes, filling the largest box type first.
3. Place item types from largest unit volume to smallest; heaviest at the \
bottom.
4. For each item type, pick one rotation, preferring small dx (low layers). \
Place its units in a grid from the lowest free position: fill along Z, then \
Y, then start the next layer in X.
5. Finish each layer across the Y–Z footprint before starting the next, so \
every unit above the floor rests fully on unit tops.
6. Number seq in placement order, 1..n per box."""

_RULES = """\
Rules:
- Units are rigid axis-aligned boxes, one placement row per unit.
- dims must be a 90-degree rotation (permutation) of the item's [x, y, z].
- Every unit stays inside the box and never overlaps another unit.
- A unit above the floor must rest with its whole Y–Z footprint on unit tops \
at exactly its bottom X.
- Boxes load only from the top: each unit is lowered straight down along X \
in its final orientation without passing through a wall or an earlier unit, \
and nothing moves afterwards. seq is that loading order.
- A box's contents weigh at most its weightMax.
- Invent nothing: no measurements, box types, or limits not in the input."""

_UPRIGHT = """\
- Items marked upright keep their X (height) axis vertical: dx must equal \
the item's x; only Y and Z may swap."""

_FRAGILE = """\
- Nothing may rest on an item marked fragile; place fragile items on top."""

_OUTPUT = """\
Output JSON only:
- boxes: one row per physical box used: box (1, 2, ...) and the box type's \
refId as ref_id.
- placements: one row per packed unit: box, seq, item refId as ref_id, pos \
as the min corner [x, y, z], dims as the rotated extents [x, y, z].
- leftovers: one row per unpacked item type and reason: ref_id, quantity, \
and the specific reason, stating whether impossibility was verified or the \
search failed.
Do not repeat names, colors, or weights; they are joined from the input by \
ref_id."""

_SELF_CHECK = """\
Before returning, verify that placed plus leftover units equal the \
requested quantity for every item, and recheck containment, overlaps, \
weight limits, support, and top-loading order."""

_VALIDATOR = """\
Your plan is checked in code by validate_plan: containment, overlaps, \
rotations, support, top-loading order, weight limits, and unit counts. If it \
fails, you will get the exact errors; fix exactly those and return the full \
corrected plan. A plan that passes is final."""


def build_system_prompt(
    items: Sequence[Mapping[str, Any]],
    *,
    policies: Sequence[str] = (),
    length_unit: str | None = None,
    weight_unit: str | None = None,
    precomputed: bool = True,
    with_validator: bool = False,
) -> str:
    """Assemble the system prompt, mentioning optional rules only when used.

    items:          scanned for optional flags (`upright`, `fragile`).
    policies:       extra packing policies, added verbatim as rules.
    length_unit / weight_unit: stated units; otherwise output null.
    precomputed:    the user prompt includes precompute tables
                    (see build_user_prompt).
    with_validator: plans are checked by validate_plan and errors sent back
                    (see planner.plan_with_validation).
    """
    rules = [_RULES]
    if any(item.get("upright") for item in items):
        rules.append(_UPRIGHT)
    if any(item.get("fragile") for item in items):
        rules.append(_FRAGILE)
    rules.extend(f"- {policy}" for policy in policies)

    if length_unit or weight_unit:
        units = (
            f"Units: lengths in {length_unit or 'unspecified (null)'}, "
            f"weights in {weight_unit or 'unspecified (null)'}. Set "
            f"length_unit and weight_unit to match; do not convert."
        )
    else:
        units = (
            "Units are not stated: set length_unit and weight_unit to null "
            "and keep source values."
        )

    sections = [
        _ROLE,
        _PRIORITIES,
        _COORDINATES,
        _INPUT,
        *([_PRECOMPUTED] if precomputed else []),
        _PROCEDURE_PRECOMPUTED if precomputed else _PROCEDURE_PLAIN,
        "\n".join(rules),
        units,
        _OUTPUT,
        _VALIDATOR if with_validator else _SELF_CHECK,
    ]
    return "\n\n".join(sections)


def build_user_prompt(
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    *,
    precomputed: bool = True,
) -> str:
    """Input tables, plus precomputed facts unless precomputed=False."""
    sections = [
        to_table("items", items, rename=DIMENSION_HEADERS),
        to_table("boxes", boxes, rename=DIMENSION_HEADERS),
    ]
    if precomputed:
        sections.append(precompute_tables(precompute(items, boxes)))
    return "\n\n".join(sections)
