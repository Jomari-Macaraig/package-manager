"""vedo (VTK) renderer: same idea as render_pyvista with vedo's API.

Renders off screen and returns a PIL image. Pass off_screen=False for an
interactive window.

    img = render_vedo(plan)             # PIL.Image
"""

import textwrap

import numpy as np
import vedo
from PIL import Image

from render_common import (
    box_cuboids, box_size, box_title, item_colors, leftover_lines,
    mirror_depth,
)
from schemas import Response

PANEL = (700, 650)  # px per box
_INSET = 0.02  # small gap so neighbouring units show their edges


def _box(lo, hi, **kwargs) -> vedo.Box:
    size = [b - a for a, b in zip(lo, hi)]
    return vedo.Box(pos=[(a + b) / 2 for a, b in zip(lo, hi)],
                    length=size[0], width=size[1], height=size[2], **kwargs)


def render_vedo(
    plan: Response, upto: int | None = None, off_screen: bool = True
) -> Image.Image | None:
    """One renderer per box; returns the screenshot when off_screen."""
    colors = item_colors(plan)
    panels = max(len(plan.boxes), 1)
    plotter = vedo.Plotter(N=panels, offscreen=off_screen, sharecam=False,
                           size=(PANEL[0] * panels, PANEL[1]), bg="white")

    for n, box in enumerate(plan.boxes):
        size = box_size(box)
        actors = [
            _box((0, 0, 0), size, c="black").wireframe().linewidth(2),
            _box((0, 0, -0.05), (size[0], size[1], 0), c="#e5e7eb"),
        ]
        cuboids = box_cuboids(box, colors, upto)
        # right-handed VTK: mirror depth so Z runs left to right on screen
        placed = [mirror_depth(c.lo, c.hi, size[1]) for c in cuboids]
        for c, (lo, hi) in zip(cuboids, placed):
            unit = _box([v + _INSET for v in lo], [v - _INSET for v in hi],
                        c=c.color)
            actors.append(unit.linewidth(1).lc("black"))
        if cuboids:
            tops = vedo.Points(np.array(
                [((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hi[2])
                 for lo, hi in placed]
            ), r=1).alpha(0)
            tops.pointdata["seq"] = np.array([c.seq for c in cuboids])
            actors.append(tops.labels2d("seq", justify="center", scale=1.1,
                                        frame=False, c="black", bc="white"))

        actors.append(vedo.Text2D(box_title(box), pos="top-center", s=1.1,
                                  c="black"))
        if n == panels - 1:
            for i, (name, color) in enumerate(colors.items()):
                actors.append(vedo.Text2D(name, pos=(0.8, 0.95 - 0.045 * i),
                                          s=0.9, c=color))
        lines = leftover_lines(plan)
        if lines and n == 0:
            actors.append(vedo.Text2D(
                "\n".join(textwrap.wrap("Not packed — " + "; ".join(lines),
                                        90)),
                pos="bottom-left", s=0.6, c="black",
            ))

        # front-right-top view; after mirror_depth the front is at low v
        center = [s / 2 for s in size]
        reach = max(size) * 2.6
        plotter.at(n).show(
            *actors, resetcam=False, interactive=False,
            camera={
                "pos": (center[0] + reach * 0.45, center[1] - reach * 0.9,
                        center[2] + reach * 0.55),
                "focal_point": center,
                "viewup": (0, 0, 1),
            },
        )

    if not off_screen:
        plotter.interactive()
        plotter.close()
        return None
    image = plotter.screenshot(asarray=True)
    plotter.close()
    return Image.fromarray(image)
