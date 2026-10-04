"""Static isometric-style SVG renderer with no extra dependencies.

Orthographic view from the front-right-top, so the box origin
(back-left-bottom) is the far corner and Z runs left to right. Units are
drawn back to front with render_common.paint_order, three faces each.
Returns the SVG as a string: small, sharp, real text labels — suited to the
image API.

    svg = render_svg(plan)
    IPython.display.SVG(svg)    # notebook
    svg.encode()                # response body, media type image/svg+xml
"""

import math
from html import escape

from matplotlib.colors import to_rgb

from render_common import (
    box_cuboids, box_size, box_title, footer_lines, item_colors, paint_order,
)
from schemas import Response

AZIMUTH = math.radians(30)  # camera swung toward +u (right)
ELEVATION = math.radians(25)
PANEL = 520  # px per box drawing
LEGEND = 150  # px column for the legend
SHADE = (0.62, 0.8, 1.0)  # face brightness by axis: u side, v front, w top

_D = (math.sin(AZIMUTH) * math.cos(ELEVATION),
      math.cos(AZIMUTH) * math.cos(ELEVATION),
      math.sin(ELEVATION))  # toward the camera
_RIGHT = (math.cos(AZIMUTH), -math.sin(AZIMUTH), 0.0)
_UP = (-math.sin(ELEVATION) * math.sin(AZIMUTH),
       -math.sin(ELEVATION) * math.cos(AZIMUTH),
       math.cos(ELEVATION))
_GRAD = tuple(-d for d in _D)  # depth change per axis (all negative)


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def _project(p) -> tuple[float, float]:
    return _dot(p, _RIGHT), -_dot(p, _UP)  # SVG y grows downward


def _shade(color: str, factor: float) -> str:
    r, g, b = (min(255, round(c * factor * 255)) for c in to_rgb(color))
    return f"#{r:02x}{g:02x}{b:02x}"


def _face(lo, hi, axis: int, at: float):
    i, j = (k for k in range(3) if k != axis)
    quad = []
    for a, b in ((lo[i], lo[j]), (hi[i], lo[j]), (hi[i], hi[j]),
                 (lo[i], hi[j])):
        p = [0.0, 0.0, 0.0]
        p[axis], p[i], p[j] = at, a, b
        quad.append(p)
    return quad


def _box_edges(size):
    """12 edges as (start, end, is_front)."""
    edges = []
    for k in range(3):
        i, j = (a for a in range(3) if a != k)
        for vi in (0.0, size[i]):
            for vj in (0.0, size[j]):
                start, end = [0.0] * 3, [0.0] * 3
                start[i] = end[i] = vi
                start[j] = end[j] = vj
                start[k], end[k] = 0.0, size[k]
                # all grads are negative, so the max-side faces face us
                edges.append((start, end, vi == size[i] or vj == size[j]))
    return edges


def _panel(box, colors, upto, x_offset: float) -> tuple[list[str], float]:
    size = box_size(box)
    corners = [(u, v, w) for u in (0, size[0]) for v in (0, size[1])
               for w in (0, size[2])]
    xs, ys = zip(*(_project(c) for c in corners))
    scale = (PANEL - 40) / max(max(xs) - min(xs), max(ys) - min(ys))
    left, top = min(xs), min(ys)

    def pt(p) -> str:
        x, y = _project(p)
        return f"{x_offset + 20 + (x - left) * scale:.1f}," \
               f"{50 + (y - top) * scale:.1f}"

    def poly(quad, fill, stroke, width, extra="") -> str:
        points = " ".join(pt(p) for p in quad)
        return (f'<polygon points="{points}" fill="{fill}" stroke="{stroke}"'
                f' stroke-width="{width}"{extra}/>')

    out = [f'<text x="{x_offset + PANEL / 2:.1f}" y="28" '
           f'text-anchor="middle" font-size="16">{escape(box_title(box))}'
           f'</text>']

    # inside walls: floor, back wall, left wall
    for axis in range(3):
        out.append(poly(_face((0, 0, 0), size, axis, 0.0), "#f3f4f6",
                        "#9ca3af", 0.8))
    for start, end, front in _box_edges(size):
        if not front:
            out.append(f'<line x1="{pt(start).split(",")[0]}" '
                       f'y1="{pt(start).split(",")[1]}" '
                       f'x2="{pt(end).split(",")[0]}" '
                       f'y2="{pt(end).split(",")[1]}" '
                       f'stroke="#6b7280" stroke-width="1"/>')

    cuboids = box_cuboids(box, colors, upto)
    bounds = [(c.lo, c.hi) for c in cuboids]
    depth = [-_dot(c.center, _D) for c in cuboids]
    for n in paint_order(bounds, _GRAD, depth):
        c = cuboids[n]
        for axis in range(3):
            out.append(poly(_face(c.lo, c.hi, axis, c.hi[axis]),
                            _shade(c.color, SHADE[axis]), "#111827", 0.6))

    for start, end, front in _box_edges(size):
        if front:
            (x1, y1), (x2, y2) = (pt(start).split(","), pt(end).split(","))
            out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                       f'stroke="#111827" stroke-width="1.6"/>')

    for c in cuboids:
        x, y = pt((c.center[0], c.center[1], c.hi[2])).split(",")
        out.append(f'<text x="{x}" y="{y}" text-anchor="middle" '
                   f'dominant-baseline="middle" font-size="11" '
                   f'paint-order="stroke" stroke="white" stroke-width="3">'
                   f'{c.seq}</text>')
    return out, PANEL


def render_svg(plan: Response, upto: int | None = None) -> str:
    """One isometric panel per box, legend, and leftovers footer."""
    colors = item_colors(plan)
    body: list[str] = []
    x = 0.0
    for box in plan.boxes:
        parts, width = _panel(box, colors, upto, x)
        body += parts
        x += width
    width = max(x, PANEL) + LEGEND

    for n, (name, color) in enumerate(colors.items()):
        y = 50 + n * 20
        body.append(f'<rect x="{width - LEGEND + 10}" y="{y}" width="14" '
                    f'height="14" fill="{color}"/>')
        body.append(f'<text x="{width - LEGEND + 30}" y="{y + 11}" '
                    f'font-size="13">{escape(name)}</text>')

    lines = footer_lines(plan, width=int(width / 6.5))
    height = PANEL + 40 + 16 * len(lines)
    for n, line in enumerate(lines):
        body.append(f'<text x="10" y="{PANEL + 40 + n * 16}" font-size="12">'
                    f'{escape(line)}</text>')

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" '
        f'height="{height:.0f}" viewBox="0 0 {width:.0f} {height:.0f}" '
        f'font-family="Helvetica, Arial, sans-serif">'
        f'<rect width="100%" height="100%" fill="white"/>'
        + "".join(body) + "</svg>"
    )
