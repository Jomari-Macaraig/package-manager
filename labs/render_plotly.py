"""Interactive Plotly renderer: one 3D scene per box.

WebGL has a real depth buffer, so units never draw over each other wrongly,
even while rotating. Hover shows each unit's name, step, position and size.

    fig = render_plotly(plan)
    fig.show()                         # interactive in the notebook
    fig.to_html(include_plotlyjs="cdn")  # shareable page
    fig.to_image(format="svg")         # static bytes; needs kaleido + Chrome
"""

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from packing.render_common import (
    Cuboid, box_cuboids, box_size, box_title, footer_lines, item_colors,
)
from packing.schemas import Response

# Cube corners as (u, v, w) offsets and the 12 triangles of its 6 faces.
_CORNERS = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
            (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
_TRIANGLES = [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7),
              (0, 1, 5), (0, 5, 4), (3, 2, 6), (3, 6, 7),
              (0, 3, 7), (0, 7, 4), (1, 2, 6), (1, 6, 5)]
# Blocks are shrunk by this much so their outlines sit just outside the
# surface; otherwise lines and faces z-fight and stacks look like one solid.
_INSET = 0.03
_EDGES = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4),
          (0, 4), (1, 5), (2, 6), (3, 7)]


def _corners(lo, hi) -> list[tuple[float, float, float]]:
    return [tuple(hi[k] if c[k] else lo[k] for k in range(3))
            for c in _CORNERS]


def _edge_lines(boxes_corners) -> tuple[list, list, list]:
    """Line segments for many cuboids, None-separated for one trace."""
    us, vs, ws = [], [], []
    for corners in boxes_corners:
        for a, b in _EDGES:
            for point in (corners[a], corners[b]):
                us.append(point[0])
                vs.append(point[1])
                ws.append(point[2])
            us.append(None)
            vs.append(None)
            ws.append(None)
    return us, vs, ws


def _unit_mesh(c: Cuboid, show_legend: bool, scene: str) -> go.Mesh3d:
    pts = _corners([v + _INSET for v in c.lo], [v - _INSET for v in c.hi])
    i, j, k = zip(*_TRIANGLES)
    size = "×".join(f"{s:g}" for s in (c.size[2], c.size[1], c.size[0]))
    pos = f"x={c.lo[2]:g}, y={c.lo[1]:g}, z={c.lo[0]:g}"
    return go.Mesh3d(
        x=[p[0] for p in pts], y=[p[1] for p in pts], z=[p[2] for p in pts],
        i=i, j=j, k=k, color=c.color, opacity=1.0, flatshading=True,
        name=c.name, legendgroup=c.name, showlegend=show_legend,
        hovertemplate=(f"<b>{c.name}</b> · step {c.seq}<br>pos {pos}<br>"
                       f"size {size}<extra></extra>"),
        lighting={"ambient": 0.6, "diffuse": 0.6, "specular": 0.1},
        scene=scene,
    )


def render_plotly(plan: Response, upto: int | None = None) -> go.Figure:
    """Build an interactive figure; upto limits to the first N loading steps."""
    colors = item_colors(plan)
    panels = max(len(plan.boxes), 1)
    fig = make_subplots(
        rows=1, cols=panels, specs=[[{"type": "scene"}] * panels],
        subplot_titles=[box_title(b) for b in plan.boxes] or [""],
    )
    legend_done: set[str] = set()

    for n, box in enumerate(plan.boxes, start=1):
        scene = "scene" if n == 1 else f"scene{n}"
        size = box_size(box)
        cuboids = box_cuboids(box, colors, upto)

        us, vs, ws = _edge_lines([_corners((0, 0, 0), size)])
        fig.add_trace(go.Scatter3d(
            x=us, y=vs, z=ws, mode="lines", showlegend=False,
            line={"color": "black", "width": 4}, hoverinfo="skip",
        ), row=1, col=n)
        for c in cuboids:
            fig.add_trace(_unit_mesh(c, c.name not in legend_done, scene),
                          row=1, col=n)
            legend_done.add(c.name)
        if cuboids:
            us, vs, ws = _edge_lines([_corners(c.lo, c.hi) for c in cuboids])
            fig.add_trace(go.Scatter3d(
                x=us, y=vs, z=ws, mode="lines", showlegend=False,
                line={"color": "black", "width": 2},
                hoverinfo="skip",
            ), row=1, col=n)
            fig.add_trace(go.Scatter3d(
                x=[c.center[0] for c in cuboids],
                y=[c.center[1] for c in cuboids],
                z=[c.hi[2] + 0.15 for c in cuboids],
                mode="text", text=[str(c.seq) for c in cuboids],
                textfont={"size": 10, "color": "black"},
                showlegend=False, hoverinfo="skip",
            ), row=1, col=n)

        fig.update_layout({scene: {
            "aspectmode": "data",
            "xaxis": {"title": "Z span", "range": [0, size[0]]},
            # Reversed so the front (max Y) is at scene -y; with the camera
            # there, Z runs left to right on screen like the plan says.
            "yaxis": {"title": "Y depth", "range": [size[1], 0]},
            "zaxis": {"title": "X height", "range": [0, size[2]]},
            # front-right-top: the back-left-bottom origin is the far corner
            "camera": {"eye": {"x": 0.9, "y": -1.9, "z": 1.0}},
        }})

    lines = footer_lines(plan, width=90 * panels)
    footer_px = 18 * len(lines) + 20 if lines else 0
    fig.update_layout(
        height=600 + footer_px, width=600 * panels,
        margin={"l": 0, "r": 0, "t": 40, "b": footer_px},
        legend={"x": 1, "xanchor": "right", "y": 1},
    )
    if lines:
        fig.add_annotation(
            text="<br>".join(lines), showarrow=False, align="left",
            xref="paper", yref="paper", x=0, y=0, xanchor="left",
            yanchor="top", yshift=-10, font={"size": 11},
        )
    return fig
