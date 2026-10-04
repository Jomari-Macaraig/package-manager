"""Sample orders A, B, C from labs/manager.ipynb cells 5-7, D, the
original dispenser order from cell 2, and E (cell 8), a two-box order
where the code packer leaves room for the model to do better."""

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
    {
        "key": "D",
        "title": "D: Dispenser and tapes (original)",
        "description": ("The first notebook order. The dispenser and 20 "
                        "tapes fit one box; the 28 in stands fit no box and "
                        "are left over."),
        "items": [
            _item("Dispenser", "green", 4, 9.5, 3.25, 4.5, 1),
            _item("Tape", "blue", 0.3, 4.75, 4.75, 1, 20),
            _item("Stand", "red", 17, 28, 4.38, 23.38, 3),
        ],
        "boxes": [
            _box("Small", 65, 12, 6, 6.75),
            _box("Medium", 80, 14.5, 6.75, 12.75),
            _box("Wide Low", 70, 12, 12, 4.25),
        ],
        "instructions": "",
    },
    {
        "key": "E",
        "title": "E: Two boxes, room to beat the packer",
        "description": ("Needs two boxes. The packer fills Large to the top "
                        "and puts one kettle alone in Small (4,680 in³ "
                        "shipped); Medium + Large with everything on the "
                        "floor ships 3,584 in³."),
        "items": [
            _item("Kettle", "silver", 2.2, 8, 7, 7, 5),
            _item("Shoe Box", "black", 2.5, 5, 7, 12, 2),
            _item("Jar", "goldenrod", 1.5, 6, 4, 4, 3),
        ],
        "boxes": [
            _box("Small", 40, 8, 10, 12),
            _box("Medium", 50, 10, 12, 16),
            _box("Large", 60, 12, 16, 20),
        ],
        "instructions": "",
    },
]


# Instruction samples for v2 (F-H). Each has a soft instruction that the
# packer ignores, and a guided placement with the matching preferences that
# honours it within 1.15 x the packer's shipped volume (tests/test_v2_api.py).
SAMPLES_V2: list[dict[str, Any]] = [
    {
        "key": "F",
        "title": "F: Heavy at the back",
        "description": ("One Medium box. The packer puts the pans at the "
                        "back but both bowls end up in the back half too; "
                        "preferences can put the bowls in front."),
        "items": [
            _item("Cast Iron Pan", "dimgray", 4.5, 3, 10, 10, 3),
            _item("Glass Bowl", "lightcyan", 1.2, 4, 7, 6, 2),
        ],
        "boxes": [
            _box("Medium", 50, 10, 12, 16),
            _box("Large", 60, 12, 16, 20),
        ],
        "instructions": ("Put the cast iron pans at the back and the glass "
                         "bowls at the front."),
    },
    {
        "key": "G",
        "title": "G: Jars left, kettles right",
        "description": ("Sample E's items in Wide + Medium boxes. The packer "
                        "honours kettles 4/5 and jars 2/3; a guided plan "
                        "honours all of them at the same shipped volume."),
        "items": [
            _item("Kettle", "silver", 2.2, 8, 7, 7, 5),
            _item("Shoe Box", "black", 2.5, 5, 7, 12, 2),
            _item("Jar", "goldenrod", 1.5, 6, 4, 4, 3),
        ],
        "boxes": [
            _box("Wide", 60, 10, 16, 26),
            _box("Medium", 50, 10, 12, 16),
        ],
        "instructions": ("Keep the jars on the left side and the kettles on "
                         "the right."),
    },
    {
        "key": "H",
        "title": "H: Fragile on top",
        "description": ("The packer puts the tray on the four vases; the "
                        "vases need to go on top of the tray instead."),
        "items": [
            _item("Vase", "orchid", 2, 4, 4, 5, 4),
            _item("Tray", "tan", 3, 1, 8, 10, 1),
        ],
        "boxes": [
            _box("Small", 40, 8, 8, 12),
            _box("Large", 60, 14, 14, 18),
        ],
        "instructions": "The vases are fragile: put them on top of everything.",
    },
]
