# labs

Experimentation notebook for the LLM-driven 3D box packing planner. The
pipeline itself lives in `app/backend/packing/` — see
[../app/backend/packing/CLAUDE.md](../app/backend/packing/CLAUDE.md). Labs
imports it (`from packing.planner import ...`) through the root project's
editable path dependency; labs keeps no copies of pipeline modules.

## Key files

- `manager.ipynb` — sample orders (dispenser, golf, golf balls, A, B, C),
  prompts cell (extract settings → prepare → rules → prompt), report/PRICES,
  model cell (`START` = "improve" | "scratch" | "guide" in the prompts cell,
  which also runs the packer first for `ESTIMATES`/box_estimates and the
  baseline; `plan_with_validation` or `plan_with_guidance` → `final_plan`), renderer
  cells, sample cases. Uses OpenAI (or Ollama at `http://localhost:11434/v1`,
  or OpenRouter).
- `system_prompts.py` — old system prompt versions
  (`PACKAGE_MANAGER_SYSTEM_PROMPT_V*`; V6 = before box_estimates, V7 =
  before the noise trim; both go with `verbose_facts=True` in `run_case`),
  kept for comparison; the live prompt is
  `packing.prompt_builder.SYSTEM_PROMPT`.
- `visualize.py` — matplotlib renderer for a nested `Response` plan.
- `render_plotly.py`, `render_pyvista.py`, `render_vedo.py`,
  `render_threejs.py` — alternative renderers (see "Other renderers"); they
  use `packing.render_common`. The SVG renderer is `packing.render_svg`.
- `_archive/evaluation/` — archived Paccurate comparison; its `evaluate.py`
  still imports the old flat modules and no longer runs as-is.

## Token usage and cost

`PlanResult` sums `prompt_tokens` (input), `completion_tokens` (output, which
includes reasoning) and `reasoning_tokens` over every call in the loop;
`total_tokens` = input + output. The notebook's `report(result, model)` prints
them plus a cost estimate from its `PRICES` table (USD per 1M tokens, input /
output; looked up 2026-10-04: gpt-5.4-nano 0.20 / 1.25, gpt-5.4-mini 0.75 /
4.50; free/local models 0). Unknown models print "add to PRICES".

## Env vars

- `OPENAI_API_KEY` (from `.env`) — only when using the OpenAI client.
- `OPENROUTER_API_KEY` (from `.env`) — for open-source models via OpenRouter
  (`base_url="https://openrouter.ai/api/v1"`, same OpenAI client). Pass
  effort as `extra_body={"reasoning": {"effort": "low"}, "provider":
  {"require_parameters": True}}`; `require_parameters` routes only to
  providers that support structured outputs. See the OpenRouter notebook cell.

## Visualizer (`visualize.py`)

`render_plan(plan, upto=None, path=None)` draws one 3D panel per box:
black wireframe for the internal box, each packed unit as a shaded block in
the item's input color (tab10 fallback if not a valid matplotlib color), the
`packed_index` on each unit, a legend, and a footer listing leftovers.

Blocks are not `bar3d`: mplot3d has no depth buffer and sorts each collection
by average depth, so a tall unit next to a stack of thin ones was drawn over
them (the dispenser showed through the tape stacks). Instead `_paint_order`
orders units back to front by which side of their separating plane faces the
camera (topological sort, depth fallback), each unit draws only its three
camera-facing faces, `ax.computed_zorder = False` keeps that order, and box
edges are split into back (under units) and front (over units). The order is
computed for the default view, so interactive rotation can show artifacts.
Plan X is height, so everything is drawn at (z, y, x) to put height on
matplotlib's vertical axis. `upto=N` shows only the first N loading steps;
`path` saves the figure (extension picks svg/png/pdf). Step labels are
projected to 2D so bars can't hide them, which means they don't follow
interactive rotation.

### Footer: leftovers and packing notes

Every renderer shows a footer from `render_common.footer_lines(plan, width)`:
"Not packed — …" for leftovers, then "Packing notes:" with the plan's `notes`
numbered. matplotlib, Plotly, SVG and three.js draw it as text below the
boxes; PyVista and vedo images get a white band added under the render by
`render_common.add_footer` (DejaVu Sans from matplotlib, since PIL's default
font lacks "—" and "×").

### Other renderers

All take a nested `Response` plus optional `upto`, and draw in view coords
(u, v, w) = (Z span, Y depth, X height) from `render_common`. Shared view:
front-right-top, so the box origin (back-left-bottom) is the far corner and
Z runs left to right.

| Module | Function → returns | Notes |
|---|---|---|
| `render_plotly.py` | `render_plotly(plan)` → `go.Figure` | Interactive in notebook, hover info, real depth buffer. `fig.to_image(format="png"/"svg")` needs kaleido + Chrome; `fig.to_html(include_plotlyjs="cdn")` for a page. Y range reversed + camera at scene −y so Z isn't mirrored. Units inset 0.03 so edge lines aren't hidden. |
| `packing.render_svg` | `render_svg(plan, upto=None, box=None)` → SVG `str` | No extra deps, ~13 KB for the test plan (matplotlib SVG ~88 KB), real text labels; best fit for the image API. Orthographic, back-to-front via `paint_order`, three faces per unit. |
| `render_pyvista.py` | `render_pyvista(plan, off_screen=True)` → `PIL.Image` | Lit VTK render. Uses `mirror_depth` (VTK is right-handed; the plan frame is left-handed). Legend is positioned text: `add_legend` overflowed the window. |
| `render_vedo.py` | `render_vedo(plan, off_screen=True)` → `PIL.Image` | Same as PyVista via vedo; its default font drops "—" and "×". |
| `render_threejs.py` | `render_threejs(plan)` → HTML `str`; `threejs_iframe(page)` for notebooks | Orbit controls, step slider (`upto` = starting step), CSS2D labels. three.js 0.170.0 from jsDelivr CDN, so viewing needs internet. Maps (u, w, v) → three (x, y, z), right-handed, no mirroring. |

Painter-style renderers (matplotlib, SVG) need `paint_order`; Plotly, VTK,
and three.js use a depth buffer. Dependencies: `plotly`, `kaleido`,
`pyvista`, `vedo` (pulls in `vtk`).

### Rendering without writing a file

Omit `path` and nothing touches disk; in a notebook the figure shows inline.
For bytes or text in memory, `savefig` into a `BytesIO`:

```python
from io import BytesIO
import matplotlib.pyplot as plt

fig = render_plan(plan)
buf = BytesIO()
fig.savefig(buf, format="svg", bbox_inches="tight")  # or format="png"
plt.close(fig)

svg_text = buf.getvalue().decode()  # SVG string; PNG: use buf.getvalue()
```

Show an in-memory SVG in the notebook: `IPython.display.SVG(svg_text)`.

## Image API notes

The playground backend implements this as `GET /api/runs/{id}/render`
(SVG, one box, `upto`) and `/svg` (download) — see `app/CLAUDE.md`. The
original decisions:

- Default to **SVG**, with `?format=svg|png`. SVG stays sharp and keeps step
  numbers as text; PNG suits email, PDF, mobile, thumbnails, and very large
  plans (SVG grows per item; PNG size depends on pixel size). For the 21-unit
  test plan: SVG 88 KB, PNG 74 KB.
- Return **raw bytes as the response body** with `image/svg+xml` or
  `image/png`, not base64 in JSON (~33% larger, client must decode). If the
  plan JSON is also needed, return it with an `image_url` instead.
- Server rendering: `matplotlib.use("Agg")`, render into `BytesIO` as above,
  and always `plt.close(fig)` — pyplot keeps figures in memory until closed.
  pyplot isn't thread-safe; under concurrency use a process pool or build
  figures with `matplotlib.figure.Figure` instead of pyplot.
- Plans render deterministically, so cache the image per plan.
- Alternative for a web UI: return only the plan JSON and draw it client-side
  (e.g. three.js) for rotate/zoom; keep server rendering for exports.

## Evaluation: model vs code packer

Each sample order cell (dispenser, golf, golf balls, A, B, C, E) also registers
itself in `ORDERS` (created in cell 0), so the cell runs all orders. The
evaluation cell (after the run-all cell) runs every `SETUPS` entry (model,
`reasoning_effort`, `start` = "improve" / "scratch" / "guide", optional
`system` prompt override such as `PACKAGE_MANAGER_SYSTEM_PROMPT_V6`, the
prompt before the 2026-10-05 fixes) over `EVAL` = `ORDERS` + rule `CASES`
(without `basic`, which repeats the last order). `run_case` returns
`(result, plan, settings, rows)` where rows are `packing.metrics.compare`
of the model's own plan vs the packer's. It prints one row per (setup, case)
with better/worse/same counts and key metrics "model vs packer", then a
per-setup summary: model valid, beats packer (better somewhere, worse
nowhere), loses somewhere, same as packer, tokens. Costs one planner run
per (setup, case); checked offline with `FakeClient`.

## Sample cases (evaluation)

The notebook's last cells replace the old Paccurate evaluation (archived in
`_archive/evaluation/`): `CASES` holds one small order per rule (basic,
padding_cut, blocks, upright, fixed, no_stack, max_load, floor_only,
padding_by_item [free text], stock, wall_clearance, carrier_limits, min_cut,
min_fill, max_boxes, keep_together, keep_apart, balance, soft_instructions
[free text]) over shared `BOXES` (Small/Medium/Large). `run_case(case, model)`
runs settings → prepare → prompt → `plan_with_validation` → `final_plan`;
the run-all cell prints a table (ok, rounds, boxes, leftover, tokens, cost,
first error); the last cell draws one case as SVG. Each case costs one
planner run (plus one extraction call for text cases).

System prompt priorities (after pack-all, fewest boxes, least box volume):
maximize floor use across the Y–Z footprint so free space stays on top for
cutting the box down, then a centered center of gravity (spread left-right
and front-back, low in X); procedure step 6 spreads a partial top layer
instead of crowding a corner.

Extra instructions: `build_user_prompt(items, boxes, instructions)` appends
free text (policies, rules, preferences) **before** the data as "Additional
instructions:" (moved to the front 2026-10-04: at the end it wasn't being
followed); empty/whitespace adds nothing. The system prompt says to follow
them unless they conflict with the Rules, which always win. They are not
checked by `validate_plan`. Notebook: set `INSTRUCTIONS` in the prompts cell.

Floating units (2026-10-04): a run returned `ok=False` with 12 "not fully
supported" errors after the model centered the dispenser and stacked tapes
half on it; the notebook drew the rejected plan anyway. Fixes: the Rules now
say "No floating units" explicitly, procedure step 5 says to check support
before placing above the floor, priority 5 limits centering to the top layer,
and the notebook's model cell raises instead of drawing when `result.ok` is
False.
