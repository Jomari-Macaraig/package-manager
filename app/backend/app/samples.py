"""Sample orders A, B, C from labs/manager.ipynb cells 5-7."""

from typing import Any


def _item(name, color, weight, x, y, z, quantity) -> dict[str, Any]:
    return {"name": name, "color": color, "weight": weight,
            "x": x, "y": y, "z": z, "quantity": quantity}


def _box(name, weight_max, x, y, z) -> dict[str, Any]:
    return {"name": name, "weightMax": weight_max, "x": x, "y": y, "z": z,
            "stock": None}


SAMPLES: list[dict[str, Any]] = [
    {
        "key": "A",
        "title": "A: Home goods",
        "description": "Mixed sizes; should fit one box.",
        "items": [
            _item("Mug", "teal", 0.9, 4.5, 3.75, 3.75, 4),
            _item("Dinner Plate", "ivory", 1.6, 1.25, 11, 11, 4),
            _item("Candle Jar", "goldenrod", 1.1, 4, 3.5, 3.5, 3),
            _item("Cookbook", "firebrick", 2.4, 1.5, 8, 10, 1),
            _item("Kitchen Towel Set", "lightblue", 0.7, 2.5, 6, 8, 2),
        ],
        "boxes": [
            _box("S-10", 40, 6, 10, 10),
            _box("M-12", 50, 8, 12, 12),
            _box("L-14", 60, 10, 14, 14),
            _box("Flat-16", 50, 6, 12, 16),
            _box("XL-18", 70, 12, 16, 18),
        ],
        "instructions": "",
    },
    {
        "key": "B",
        "title": "B: Office / electronics",
        "description": "Heavier, bulkier; likely 2+ boxes.",
        "items": [
            _item("Monitor (boxed)", "black", 14, 16, 6, 25, 1),
            _item("Keyboard (boxed)", "slategray", 2.2, 2, 6, 18, 2),
            _item("Mouse (boxed)", "silver", 0.4, 2.5, 4, 6, 3),
            _item("Paper Ream", "white", 5, 2.25, 8.5, 11, 5),
            _item("Desk Lamp (boxed)", "orange", 3.1, 5, 6, 15, 1),
            _item("Cable Pack", "darkgray", 0.3, 1, 3, 5, 6),
        ],
        "boxes": [
            _box("M-12", 50, 8, 12, 12),
            _box("Long-20", 50, 8, 10, 20),
            _box("L-18", 60, 12, 14, 18),
            _box("Monitor-28", 40, 18, 8, 28),
            _box("XL-24", 70, 16, 18, 24),
        ],
        "instructions": "",
    },
    {
        "key": "C",
        "title": "C: Nothing fits",
        "description": ("Expect 0 boxes; everything is left over. The rug, "
                        "umbrellas and headboard are too big; the anvil is "
                        "too heavy."),
        "items": [
            _item("Rolled Rug", "sienna", 12, 8, 8, 60, 1),
            _item("Patio Umbrella", "darkgreen", 9, 6, 6, 52, 2),
            _item("Headboard", "tan", 30, 4, 40, 58, 1),
            _item("Anvil", "black", 120, 6, 6, 10, 1),
        ],
        "boxes": [
            _box("S-10", 40, 6, 10, 10),
            _box("M-12", 50, 8, 12, 12),
            _box("L-18", 60, 12, 14, 18),
            _box("XL-24", 70, 16, 18, 24),
        ],
        "instructions": "",
    },
]
