"""PyVista (VTK) renderer: lit 3D scene with a real depth buffer.

Renders off screen and returns a PIL image, so it works in notebooks and on
servers. Pass off_screen=False to open an interactive window instead.

    img = render_pyvista(plan)          # PIL.Image; img.save(buf, "PNG")
"""

import textwrap

import numpy as np
import pyvista as pv
from PIL import Image

from render_common import (
    box_cuboids, box_size, box_title, item_colors, leftover_lines,
    mirror_depth,
)
from schemas import Response

PANEL = (700, 650)  # px per box
_INSET = 0.02  # small gap so neighbouring units show their edges


def render_pyvista(
    plan: Response, upto: int | None = None, off_screen: bool = True
) -> Image.Image | None:
    """One subplot per box; returns the screenshot when off_screen."""
    colors = item_colors(plan)
    panels = max(len(plan.boxes), 1)
    plotter = pv.Plotter(shape=(1, panels), off_screen=off_screen,
                         window_size=(PANEL[0] * panels, PANEL[1]))
    plotter.set_background("white")

    for n, box in enumerate(plan.boxes):
        plotter.subplot(0, n)
        size = box_size(box)
        plotter.add_mesh(pv.Box(bounds=(0, size[0], 0, size[1], 0, size[2])),
                         style="wireframe", color="black", line_width=2)
        plotter.add_mesh(  # floor, so units visibly stand on something
            pv.Box(bounds=(0, size[0], 0, size[1], -0.05, 0)),
            color="#e5e7eb",
        )
        cuboids = box_cuboids(box, colors, upto)
        # right-handed VTK: mirror depth so Z runs left to right on screen
        placed = [mirror_depth(c.lo, c.hi, size[1]) for c in cuboids]
        for c, (lo, hi) in zip(cuboids, placed):
            lo = [v + _INSET for v in lo]
            hi = [v - _INSET for v in hi]
            plotter.add_mesh(
                pv.Box(bounds=(lo[0], hi[0], lo[1], hi[1], lo[2], hi[2])),
                color=c.color, show_edges=True, edge_color="black",
                line_width=1,
            )
        if cuboids:
            plotter.add_point_labels(
                np.array([((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, hi[2])
                          for lo, hi in placed]),
                [str(c.seq) for c in cuboids], font_size=11,
                point_size=0, shape_opacity=0.7, show_points=False,
                always_visible=False,
            )
        plotter.add_text(box_title(box), position="upper_edge", font_size=12,
                         color="black")
        if n == panels - 1:
            # VTK's add_legend scales text to its box height and overflows
            # the window edge, so place colored names at fixed pixels.
            for i, (name, color) in enumerate(colors.items()):
                plotter.add_text(
                    name, position=(PANEL[0] - 140, PANEL[1] - 70 - 24 * i),
                    font_size=11, color=color,
                )
        lines = leftover_lines(plan)
        if lines and n == 0:
            plotter.add_text(
                "\n".join(textwrap.wrap("Not packed — " + "; ".join(lines),
                                        90)),
                position="lower_left", font_size=8, color="black",
            )
        # front-right-top view; after mirror_depth the front is at low v
        center = [s / 2 for s in size]
        reach = max(size) * 2.6
        plotter.camera_position = [
            (center[0] + reach * 0.45, center[1] - reach * 0.9,
             center[2] + reach * 0.55),
            center,
            (0, 0, 1),
        ]

    if not off_screen:
        plotter.show()
        return None
    image = plotter.screenshot(return_img=True)
    plotter.close()
    return Image.fromarray(image)
