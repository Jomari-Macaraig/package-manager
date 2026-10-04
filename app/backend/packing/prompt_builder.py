"""Build the system and user prompts.

SYSTEM_PROMPT is fixed text. Every plan is checked in code by
validation.validate_plan (see planner.plan_with_validation), so the prompt
always says so. The user prompt always includes the precomputed facts.
Units are always inches and pounds, so the model never outputs them.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from .precompute import item_rotations, precompute, precompute_tables
from .tabular import to_table
from .validation import fill_heights

# Rule flags on prepared items are stated in the "Packing rules" text,
# so they're kept out of the items table.
HIDDEN_ITEM_KEYS = {"source_id", "fixed", "upright", "no_stack", "max_load",
                    "floor_only"}

DIMENSION_HEADERS = {
    "dimensions.x": "x",
    "dimensions.y": "y",
    "dimensions.z": "z",
}

SYSTEM_PROMPT = """\
You are a 3D packing planner. Return a packing plan as JSON matching the schema; it drives a packing diagram, so every value must be exact.

Priorities, in order:
1. Pack every unit (if impossible, as many as possible).
2. Fewest boxes.
3. Least shipped volume: each box is cut down to its contents, so what ships is fill height × floor area.
4. Low fill: use the floor before stacking; heavy units low; weight centered left to right and front to back.
5. Simple to pack.

Coordinates: origin at the box's inside back-left-bottom corner; X = height (up), Y = depth (back to front), Z = span (left to right). A unit at pos [x, y, z] with dims [dx, dy, dz] occupies [x, x+dx] × [y, y+dy] × [z, z+dz]. Box dimensions are inside dimensions. Inches and pounds.

Input (tables: a header row, then one row per record):
- Packing rules (if any): hard rules, checked in code.
- Additional instructions (if any): preferences; follow them unless they conflict with a rule.
- items and boxes: weightMax is a box's contents limit; stock is how many boxes of that type exist (blank = unlimited).
- rotations: for each item, the [x, y, z] rotations that fit at least one box. Use only these.
- unpackable: items that fit no box; put them in leftovers with that reason.
- box_estimates: what a code packer reached with one box of each type on the whole order (fits_all, units placed, fill_height, shipped_volume). A fits_all box has a known valid arrangement at that fill height.
- Starting plan (if given): a valid plan from that packer. Improve it, or return it unchanged.

Rules (checked in code):
- One placement row per unit; its dims are one of its rotations.
- Every unit is inside its box and overlaps no other unit.
- No floating units: a unit stands on the floor (x = 0) or its whole Y–Z footprint rests on the tops of units ending exactly at its bottom x. Units of different heights side by side are not a flat surface.
- Top loading: seq is the loading order 1..n per box; each unit is lowered straight down, so it loads after the units under it.
- A box's contents weigh at most its weightMax.

How to plan:
1. Box: the fits_all box with the lowest shipped_volume in box_estimates; aim for its fill_height or lower. If none fits all, use the fewest boxes, largest first.
2. Large and heavy items first, on the floor. Build columns of equal height (standing a flat item on its edge can help) and put smaller units on flat tops.
3. As you place, track each column's top height, and check every new unit for overlap and full support against all earlier units.

Output:
- leftovers: one row per item and reason; say whether impossibility was verified or the search failed.
- notes: short loading instructions, and how you applied any Additional instructions. No cut-down heights (code computes them). Empty list if nothing to add.
- Names, colors and weights are joined from the input by id; don't repeat them.

If validate_plan rejects the plan, you get the errors with code-computed help (free spots, the tops under a unit): change only the units named and copy every other placement unchanged. A plan that passes is final."""


GUIDE_SYSTEM_PROMPT = """\
You are a 3D packing planner. You choose; code places. Return your choices as JSON matching the schema:
- boxes: the box type id for each physical box, in the order code opens them.
- order: item type ids in loading order (bottom first).
- rotations: one rotation [x, y, z] per item type, copied from the rotations table.
- notes: short instructions for the person packing, and how you applied any Additional instructions. Empty list if nothing to add.

How code places: it opens your boxes in order. For each item type in your order it places every unit, in your rotation, at the lowest, then back-most, then left-most free spot where the unit fits inside the box, overlaps nothing, rests fully on the floor or on unit tops at one height, and keeps every Packing rule. Units that fit in no box are left over. The result is checked by validate_plan and compared with a code packer's own plan.

Priorities, in order:
1. Pack every unit. If impossible, maximize units packed.
2. Fewest boxes.
3. Least shipped volume: each box is cut down to its contents, so what ships is the cut-down height × the floor area.
4. Low, even fill: free space on top, weight low and centered.
5. Simple to pack.

Coordinates: X = height (up), Y = depth (back to front), Z = span (left to right). Dimensions and rotations are [x, y, z]; box dimensions are INTERNAL.

Input (tables: a header row, then one row per record):
- Packing rules (if any): hard rules, checked in code. Additional instructions (if any): preferences; follow them through your choices unless they conflict with a rule.
- items and boxes (weightMax = contents limit; stock = boxes available, blank = unlimited).
- rotations: for each item, the [x, y, z] rotations that fit at least one box.
- unpackable: items that fit no box (code leaves them over).
- box_estimates: what the code packer reached with one box of each type on the whole order: fits_all, units placed, fill_height, shipped_volume. Start from the fits_all box with the lowest shipped_volume.

How to choose:
1. Box: the fits_all box type with the lowest shipped_volume, unless you can see a lower arrangement elsewhere. Several boxes only if no type fits all.
2. Order: item types with large, flat footprints first (they form the base), then items that make columns of equal height, small items last to fill gaps and tops.
3. Rotations: lay items flat (smallest side up) when they get floor of their own; stand flat items on edge when the floor runs short, so columns reach the same height.

If your choices fail, you get the placement result and errors; change box types, order or rotations and return new choices."""


def starting_plan_text(baseline, boxes: Sequence[Mapping[str, Any]]) -> str:
    """The code packer's valid plan as a draft for the model to improve.

    Plan JSON in the output format (without the packer's notes) so the model
    edits it, or returns it unchanged, instead of searching from scratch.
    """
    names = {b["id"]: b["name"] for b in boxes}
    fills = fill_heights(baseline)
    used = "; ".join(f"box {b.box} = {names[b.id]} filled to "
                     f"{fills.get(b.box, 0):g} in" for b in baseline.boxes)
    left = sum(r.quantity for r in baseline.leftovers)
    plan = baseline.model_dump_json(exclude={"notes"})
    return (
        "Starting plan (from a code packer; it passed validate_plan): "
        f"{used or 'no boxes'}"
        + (f", {left} units left over" if left else "") + ".\n"
        f"{plan}\n"
        "Improve it by the Priorities: fewer boxes, a lower fill height "
        "(less shipped volume), better floor use, a more centered center of "
        "gravity, or closer to any Additional instructions. Edit it rather "
        "than starting over, and keep every Rule. If you cannot improve it, "
        "return it unchanged with your notes."
    )


def build_user_prompt(
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
    instructions: str | None = None,
    rules: Sequence[str] = (),
    estimates: Sequence[Mapping[str, Any]] | None = None,
    verbose_facts: bool = False,
) -> str:
    """Input tables plus precomputed facts.

    rules:        code-generated hard rules (settings.rules_text), first.
    instructions: optional free text (policies, rules, preferences), after
                  the rules and before the data. Not checked by validate_plan.
    estimates:    packer.pack(..., estimates=[]) rows, last (box_estimates).
    verbose_facts: the pre-2026-10-05 facts (fits per item × box with layer
                  counts, item/box stats, totals, candidates,
                  ideal_fill_height, item colors) for comparing with
                  PACKAGE_MANAGER_SYSTEM_PROMPT_V7. Default: compact facts
                  (rotations, unpackable).
    """
    sections = []
    if rules:
        sections.append("Packing rules:\n" + "\n".join(f"- {r}" for r in rules))
    if instructions and instructions.strip():
        sections.append(f"Additional instructions:\n{instructions.strip()}")
    hidden = HIDDEN_ITEM_KEYS if verbose_facts else HIDDEN_ITEM_KEYS | {"color"}
    rows = [{k: v for k, v in i.items() if k not in hidden} for i in items]
    pre = precompute(items, boxes)
    sections += [
        to_table("items", rows, rename=DIMENSION_HEADERS),
        to_table("boxes", boxes, rename=DIMENSION_HEADERS),
        precompute_tables(pre) if verbose_facts else compact_facts(pre, items),
    ]
    if estimates:
        sections.append(estimates_table(estimates, boxes))
    return "\n\n".join(sections)


def compact_facts(pre, items) -> str:
    """rotations (per item, those that fit at least one usable box by size
    and weight) and unpackable (with reasons)."""
    fitting: dict[int, set] = {}
    for fit in pre.fits:
        fitting.setdefault(fit.item_ref, set()).update(
            r.dims for r in fit.rotations)
    rows = [{"id": i["id"], "rotations": "; ".join(
                "×".join(f"{v:g}" for v in dims)
                for dims in item_rotations(i) if dims in fitting[i["id"]])}
            for i in items if i["id"] in fitting]
    sections = [to_table("rotations", rows)]
    if pre.unpackable:
        sections.append(to_table("unpackable", [
            {"id": ref, "reason": reason}
            for ref, reason in pre.unpackable.items()]))
    return "\n\n".join(sections)


def estimates_table(estimates, boxes) -> str:
    """box_estimates: one box of each type, code packer, whole order."""
    names = {b["id"]: b["name"] for b in boxes}
    rows = [{"id": e["id"], "name": names.get(e["id"], ""),
             "fits_all": e["fits_all"], "units": e["units"],
             "fill_height": e["fill_height"],
             "shipped_volume": e["shipped_volume"] if e["fits_all"] else None}
            for e in estimates]
    return to_table("box_estimates", rows)
