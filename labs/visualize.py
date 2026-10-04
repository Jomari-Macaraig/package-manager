"""Draw a packing plan with matplotlib: one 3D panel per box.

Plan axes are X = height, Y = depth (back to front), Z = span (left to
right). Matplotlib's vertical axis is its third one, so everything is drawn
at (z, y, x) with sizes (dz, dy, dx).
"""

import matplotlib.pyplot as plt
from matplotlib.colors import to_rgb
from matplotlib.figure import Figure
from matplotlib.patches import Patch
from mpl_toolkits.mplot3d import proj3d
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from render_common import footer_lines, item_colors, paint_order
from schemas import Boxes, Response


_SHADE = (0.8, 0.62, 1.0)  # face brightness by plot axis: Z span, Y depth, up


def _depth_gradient(proj, center) -> list[float]:
    """Change in view depth (larger = farther) per unit step along each axis."""
    base = proj3d.proj_transform(*center, proj)[2]
    steps = []
    for axis in range(3):
        point = list(center)
        point[axis] += 1e-3
        steps.append(proj3d.proj_transform(*point, proj)[2] - base)
    return steps


def _visible_faces(lo, hi, grad):
    """The three faces of a cuboid that face the camera, with their axis."""
    faces = []
    for k in range(3):
        v = hi[k] if grad[k] < 0 else lo[k]
        i, j = (axis for axis in range(3) if axis != k)
        quad = []
        for a, b in ((lo[i], lo[j]), (hi[i], lo[j]), (hi[i], hi[j]),
                     (lo[i], hi[j])):
            corner = [0.0, 0.0, 0.0]
            corner[k], corner[i], corner[j] = v, a, b
            quad.append(corner)
        faces.append((k, quad))
    return faces


def _box_edges(ax, size, grad) -> None:
    """Box wireframe: back edges under the items, front edges over them."""
    lo, hi = (0.0, 0.0, 0.0), size
    for k in range(3):
        i, j = (axis for axis in range(3) if axis != k)
        for vi in (lo[i], hi[i]):
            for vj in (lo[j], hi[j]):
                start, end = [0.0] * 3, [0.0] * 3
                start[i] = end[i] = vi
                start[j] = end[j] = vj
                start[k], end[k] = lo[k], hi[k]
                front = ((vi == hi[i]) == (grad[i] < 0)
                         or (vj == hi[j]) == (grad[j] < 0))
                ax.plot(*zip(start, end), color="black", linewidth=1.2,
                        zorder=10_000 if front else 0)


def _draw_box(ax, box: Boxes, colors: dict[str, str], upto: int | None):
    dims = box.dimensions
    size = (dims.z, dims.y, dims.x)

    ax.set_box_aspect(size)
    ax.set_xlim(0, dims.z)
    ax.set_ylim(dims.y, 0)  # back (y=0) away from the viewer
    ax.set_zlim(0, dims.x)
    ax.set_xlabel("Z span")
    ax.set_ylabel("Y depth")
    ax.set_zlabel("X height")
    ax.set_title(f"{box.name} ({dims.x:g}×{dims.y:g}×{dims.z:g})")
    ax.grid(False)

    # mplot3d sorts each collection by its average depth, which draws
    # neighbouring blocks of different sizes in the wrong order. Draw the
    # visible faces ourselves, back to front, for this fixed view. The order
    # doesn't update under interactive rotation.
    ax.computed_zorder = False
    proj = ax.get_proj()
    grad = _depth_gradient(proj, [s / 2 for s in size])
    _box_edges(ax, size, grad)

    items = [
        i for i in sorted(box.items, key=lambda i: i.packed_index)
        if upto is None or i.packed_index <= upto
    ]
    cuboids = []
    for item in items:
        pos, ext = item.placement_position, item.dimensions
        lo = (pos.z, pos.y, pos.x)
        cuboids.append((lo, (lo[0] + ext.z, lo[1] + ext.y, lo[2] + ext.x)))
    depth = [
        proj3d.proj_transform(*[(a + b) / 2 for a, b in zip(lo, hi)], proj)[2]
        for lo, hi in cuboids
    ]

    for zorder, n in enumerate(paint_order(cuboids, grad, depth), start=1):
        lo, hi = cuboids[n]
        rgb = to_rgb(colors[items[n].name])
        faces = _visible_faces(lo, hi, grad)
        ax.add_collection3d(Poly3DCollection(
            [quad for _, quad in faces],
            facecolors=[tuple(c * _SHADE[k] for c in rgb) for k, _ in faces],
            edgecolors="black", linewidths=0.4, alpha=0.95, zorder=zorder,
        ))

    # Project labels to 2D and annotate on top so blocks can't hide them.
    for item, (lo, hi) in zip(items, cuboids):
        x2, y2, _ = proj3d.proj_transform(
            (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hi[2], proj
        )
        ax.annotate(
            str(item.packed_index), (x2, y2), fontsize=7, ha="center",
            va="center", bbox={"boxstyle": "round,pad=0.15", "fc": "white",
                               "ec": "none", "alpha": 0.8},
            zorder=20_000,
        )


def render_plan(
    plan: Response, upto: int | None = None, path: str | None = None
) -> Figure:
    """Render each box in its own 3D panel.

    upto: draw only items with packed_index <= upto (step-by-step views).
    path: also save the figure; the extension picks the format (svg/png/pdf).
    """
    colors = item_colors(plan)
    panels = max(len(plan.boxes), 1)
    fig = plt.figure(figsize=(6 * panels, 6))

    for n, box in enumerate(plan.boxes, start=1):
        _draw_box(fig.add_subplot(1, panels, n, projection="3d"), box,
                  colors, upto)

    fig.legend(
        handles=[Patch(color=c, label=name) for name, c in colors.items()],
        loc="upper right",
    )
    footer = footer_lines(plan, width=110 * len(plan.boxes or [1]))
    if footer:
        # below the axes; bbox_inches="tight" grows the figure to fit it
        fig.text(0.01, 0.0, "\n".join(footer), fontsize=8, va="top")

    if path:
        fig.savefig(path, bbox_inches="tight")
    return fig
