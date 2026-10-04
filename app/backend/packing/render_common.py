"""Shared helpers for the plan renderers (visualize.py and render_*.py).

All renderers draw in view coordinates (u, v, w) = (Z span, Y depth,
X height), so height is "up" like most 3D libraries expect. Plan axes are
X = height, Y = depth (back to front), Z = span (left to right).
"""

import textwrap
from dataclasses import dataclass
from itertools import cycle

import matplotlib

matplotlib.use("Agg")

from matplotlib.colors import is_color_like, to_hex  # noqa: E402
from matplotlib.pyplot import get_cmap  # noqa: E402

from .schemas import Boxes, Response  # noqa: E402

Vec = tuple[float, float, float]


@dataclass
class Cuboid:
    seq: int
    name: str
    color: str  # hex
    lo: Vec  # (u, v, w) min corner
    hi: Vec  # (u, v, w) max corner

    @property
    def center(self) -> Vec:
        return tuple((a + b) / 2 for a, b in zip(self.lo, self.hi))

    @property
    def size(self) -> Vec:
        return tuple(b - a for a, b in zip(self.lo, self.hi))


def item_colors(plan: Response) -> dict[str, str]:
    """Item name -> hex color; input colors win, invalid ones get tab10."""
    fallback = cycle(get_cmap("tab10").colors)
    colors: dict[str, str] = {}
    items = [i for b in plan.boxes for i in b.items]
    items += [i for group in plan.leftover_items for i in group.items]
    for item in items:
        if item.name not in colors:
            color = item.color if is_color_like(item.color) else next(fallback)
            colors[item.name] = to_hex(color)
    return colors


def box_size(box: Boxes) -> Vec:
    d = box.dimensions
    return (d.z, d.y, d.x)


def box_title(box: Boxes) -> str:
    d = box.dimensions
    return f"{box.name} ({d.x:g}×{d.y:g}×{d.z:g})"


def box_cuboids(
    box: Boxes, colors: dict[str, str], upto: int | None = None
) -> list[Cuboid]:
    """Packed units in loading order, optionally only the first `upto`."""
    cuboids = []
    for item in sorted(box.items, key=lambda i: i.packed_index):
        if upto is not None and item.packed_index > upto:
            continue
        p, e = item.placement_position, item.dimensions
        lo = (p.z, p.y, p.x)
        cuboids.append(Cuboid(
            seq=item.packed_index, name=item.name, color=colors[item.name],
            lo=lo, hi=(lo[0] + e.z, lo[1] + e.y, lo[2] + e.x),
        ))
    return cuboids


def leftover_lines(plan: Response) -> list[str]:
    return [
        f"{item.name} ×{item.quantity}: {group.reason}"
        for group in plan.leftover_items
        for item in group.items
    ]


def footer_lines(plan: Response, width: int = 100) -> list[str]:
    """Wrapped footer text: what wasn't packed, then the packing notes."""
    lines: list[str] = []
    leftovers = leftover_lines(plan)
    if leftovers:
        lines += textwrap.wrap("Not packed — " + "; ".join(leftovers), width)
    if plan.notes:
        lines.append("Packing notes:")
        for n, note in enumerate(plan.notes, start=1):
            lines += textwrap.wrap(f"{n}. {note}", width,
                                   subsequent_indent="   ")
    return lines


def add_footer(image, lines: list[str], font_size: int = 14):
    """Return a PIL image with the footer lines drawn in a band below it."""
    from matplotlib import font_manager
    from PIL import Image, ImageDraw, ImageFont

    if not lines:
        return image
    # PIL's built-in font lacks "—" and "×"; matplotlib ships DejaVu Sans
    font = ImageFont.truetype(font_manager.findfont("DejaVu Sans"), font_size)
    line_height = font_size + 6
    band = Image.new("RGB", (image.width, line_height * len(lines) + 16),
                     "white")
    draw = ImageDraw.Draw(band)
    for n, line in enumerate(lines):
        draw.text((10, 8 + n * line_height), line, fill="black", font=font)
    out = Image.new("RGB", (image.width, image.height + band.height), "white")
    out.paste(image.convert("RGB"), (0, 0))
    out.paste(band, (0, image.height))
    return out


def paint_order(
    bounds: list[tuple[Vec, Vec]], grad: Vec, depth: list[float]
) -> list[int]:
    """Back-to-front order for non-overlapping axis-aligned cuboids.

    grad[k] is how view depth (larger = farther) changes along axis k. For
    each pair separated along some axis, the one on the far side of the
    separating plane is drawn first; ties and cycles fall back to depth.
    Only needed by painter-style renderers (matplotlib, SVG); libraries with
    a depth buffer (Plotly, VTK, three.js) don't need it.
    """
    n = len(bounds)
    after: dict[int, set[int]] = {i: set() for i in range(n)}
    indegree = [0] * n
    for a in range(n):
        for b in range(a + 1, n):
            (alo, ahi), (blo, bhi) = bounds[a], bounds[b]
            best = None
            for k in range(3):
                if ahi[k] <= blo[k] + 1e-9:
                    low, high = a, b
                elif bhi[k] <= alo[k] + 1e-9:
                    low, high = b, a
                else:
                    continue
                if best is None or abs(grad[k]) > abs(grad[best[0]]):
                    best = (k, low, high)
            if best is None or grad[best[0]] == 0:
                continue
            k, low, high = best
            far, near = (high, low) if grad[k] > 0 else (low, high)
            after[far].add(near)
            indegree[near] += 1

    order: list[int] = []
    ready = sorted((i for i in range(n) if indegree[i] == 0),
                   key=lambda i: -depth[i])
    while ready:
        i = ready.pop(0)
        order.append(i)
        for j in after[i]:
            indegree[j] -= 1
            if indegree[j] == 0:
                ready.append(j)
        ready.sort(key=lambda i: -depth[i])
    remaining = sorted(set(range(n)) - set(order), key=lambda i: -depth[i])
    return order + remaining


def mirror_depth(lo: Vec, hi: Vec, depth: float) -> tuple[Vec, Vec]:
    """Flip v so (u, v, w) becomes right-handed for VTK-style renderers.

    The plan frame (Z right, Y toward the viewer, X up) is left-handed, so a
    right-handed library shows it mirrored. After the flip v=0 is the front;
    put the camera at negative v to look at the front.
    """
    return (lo[0], depth - hi[1], lo[2]), (hi[0], depth - lo[1], hi[2])
