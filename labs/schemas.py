"""Packing plan schemas.

`Response` is the nested plan used downstream (visualizer, inspection).
`FlatResponse` is what the model returns: flat rows that carry only what the
model decides (box choice, placement, rotation). `unflatten` rebuilds a
`Response`, joining name/color/weight/box limits back from the input records
by ref_id.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

LengthUnit = Literal["in", "cm", "mm", "m"]
WeightUnit = Literal["lb", "kg", "g"]

# [x, y, z]; a list rather than a tuple because OpenAI strict structured
# outputs reject tuple schemas (prefixItems).
Position = Annotated[
    list[Annotated[float, Field(ge=0)]], Field(min_length=3, max_length=3)
]
Extent = Annotated[
    list[Annotated[float, Field(gt=0)]], Field(min_length=3, max_length=3)
]


# --- Nested plan ----------------------------------------------------------


class Dimensions(BaseModel):
    x: float = Field(gt=0, description="Vertical extent: bottom to top (height)")
    y: float = Field(gt=0, description="Depth extent: back to front")
    z: float = Field(gt=0, description="Horizontal extent: left to right")


class PlacementPosition(BaseModel):
    x: float = Field(
        ge=0,
        description="X coordinate of the item's minimum-coordinate corner",
    )
    y: float = Field(
        ge=0,
        description="Y coordinate of the item's minimum-coordinate corner",
    )
    z: float = Field(
        ge=0,
        description="Z coordinate of the item's minimum-coordinate corner",
    )


class CoordinateSystem(BaseModel):
    origin: Literal["inside_back_left_bottom"]
    x_direction: Literal["bottom_to_top"]
    y_direction: Literal["back_to_front"]
    z_direction: Literal["left_to_right"]
    position_reference: Literal["minimum_coordinate_corner"]


class Item(BaseModel):
    ref_id: int = Field(description="Source item type ID; shared by identical units")
    color: str = Field(description="Color used in the packing visualization")
    name: str = Field(description="Item name")
    weight: float = Field(
        ge=0,
        description="Weight of one unit, in the response's weight_unit",
    )
    dimensions: Dimensions = Field(
        description="Original dimensions of one unit, in length_unit",
    )
    quantity: int = Field(ge=1, description="Number of units")


class PackedItem(Item):
    quantity: Literal[1] = Field(
        description="Exactly one physical unit per placement record",
    )
    dimensions: Dimensions = Field(
        description=(
            "Axis-aligned dimensions AFTER rotation into the packed orientation. "
            "Together with placement_position, these define the occupied volume."
        ),
    )
    packed_index: int = Field(
        ge=0,
        description="Packing order index; unique within this box",
    )
    placement_position: PlacementPosition = Field(
        description=(
            "Minimum-coordinate corner of this unit, relative to the box origin, "
            "in length_unit"
        ),
    )


class Boxes(BaseModel):
    dimensions: Dimensions = Field(
        description="Usable INTERNAL box dimensions, in length_unit",
    )
    ref_id: int = Field(description="Source box type ID")
    name: str = Field(description="Box name")
    max_contents_weight: float | None = Field(
        gt=0,
        description=(
            "Maximum allowed contents weight, in weight_unit. "
            "Copy from source data; use null when unknown."
        ),
    )
    items: list[PackedItem] = Field(description="Individually placed units")


class LeftoverItems(BaseModel):
    items: list[Item] = Field(description="Unpacked items and their quantities")
    reason: str = Field(description="Constraint preventing these items from packing")


class Response(BaseModel):
    length_unit: LengthUnit | None = Field(
        description="Unit for all dimensions and positions; null when unspecified",
    )
    weight_unit: WeightUnit | None = Field(
        description="Unit for all weights; null when unspecified",
    )
    coordinate_system: CoordinateSystem
    boxes: list[Boxes]
    leftover_items: list[LeftoverItems]


# --- Flat plan (model output) ---------------------------------------------


class BoxRow(BaseModel):
    box: int = Field(ge=1, description="Physical box number, unique per box used")
    ref_id: int = Field(description="Box type refId")


class PlacementRow(BaseModel):
    box: int = Field(description="Physical box number from boxes")
    seq: int = Field(ge=1, description="Loading order within the box")
    ref_id: int = Field(description="Item type refId")
    pos: Position = Field(description="Min corner [x, y, z]")
    dims: Extent = Field(description="Rotated extents [x, y, z]")


class LeftoverRow(BaseModel):
    ref_id: int = Field(description="Item type refId")
    quantity: int = Field(ge=1, description="Units not packed")
    reason: str = Field(description="Specific constraint; verified or search failed")


class FlatResponse(BaseModel):
    length_unit: LengthUnit | None
    weight_unit: WeightUnit | None
    boxes: list[BoxRow]
    placements: list[PlacementRow] = Field(description="One row per unit")
    leftovers: list[LeftoverRow]


COORDINATE_SYSTEM = CoordinateSystem(
    origin="inside_back_left_bottom",
    x_direction="bottom_to_top",
    y_direction="back_to_front",
    z_direction="left_to_right",
    position_reference="minimum_coordinate_corner",
)


def _by_ref_id(records: Sequence[Mapping[str, Any]], kind: str) -> dict:
    lookup = {record["refId"]: record for record in records}
    if len(lookup) != len(records):
        raise ValueError(f"duplicate refId in input {kind}")
    return lookup


def _lookup(table: dict, ref_id: int, kind: str) -> Mapping[str, Any]:
    if ref_id not in table:
        raise ValueError(f"model referenced unknown {kind} refId {ref_id}")
    return table[ref_id]


def _item(source: Mapping[str, Any], **overrides: Any) -> dict[str, Any]:
    return {
        "ref_id": source["refId"],
        "color": source["color"],
        "name": source["name"],
        "weight": source["weight"],
        "dimensions": source["dimensions"],
        "quantity": source["quantity"],
    } | overrides


def unflatten(
    flat: FlatResponse,
    items: Sequence[Mapping[str, Any]],
    boxes: Sequence[Mapping[str, Any]],
) -> Response:
    """Rebuild the nested Response from flat rows plus the input records."""
    item_types = _by_ref_id(items, "items")
    box_types = _by_ref_id(boxes, "boxes")

    placements_by_box: dict[int, list[PlacementRow]] = defaultdict(list)
    for row in flat.placements:
        placements_by_box[row.box].append(row)

    unknown_boxes = placements_by_box.keys() - {b.box for b in flat.boxes}
    if unknown_boxes:
        raise ValueError(f"placements reference undeclared boxes {unknown_boxes}")

    nested_boxes = []
    for box_row in sorted(flat.boxes, key=lambda b: b.box):
        box_type = _lookup(box_types, box_row.ref_id, "box")
        packed = [
            _item(
                _lookup(item_types, row.ref_id, "item"),
                quantity=1,
                dimensions=dict(zip("xyz", row.dims)),
                packed_index=row.seq,
                placement_position=dict(zip("xyz", row.pos)),
            )
            for row in sorted(placements_by_box[box_row.box], key=lambda r: r.seq)
        ]
        nested_boxes.append({
            "dimensions": box_type["dimensions"],
            "ref_id": box_type["refId"],
            "name": box_type["name"],
            "max_contents_weight": box_type.get("weightMax"),
            "items": packed,
        })

    leftovers_by_reason: dict[str, list[dict]] = defaultdict(list)
    for row in flat.leftovers:
        source = _lookup(item_types, row.ref_id, "item")
        leftovers_by_reason[row.reason].append(
            _item(source, quantity=row.quantity)
        )

    return Response.model_validate({
        "length_unit": flat.length_unit,
        "weight_unit": flat.weight_unit,
        "coordinate_system": COORDINATE_SYSTEM,
        "boxes": nested_boxes,
        "leftover_items": [
            {"items": grouped, "reason": reason}
            for reason, grouped in leftovers_by_reason.items()
        ],
    })
