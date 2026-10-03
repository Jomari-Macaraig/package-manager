# labs

Experimentation notebook for an LLM-driven 3D box packing planner.

## Workflow

Green = plain code (free, exact). Orange = model call (costs tokens).

```mermaid
flowchart TD
    input["items + boxes dicts<br/>(keyed by refId)"]

    subgraph prompts["prompt_builder.py"]
        pre["precompute.py<br/>rotations that fit, units/layer,<br/>unpackable, totals, single-box candidates"]
        user["build_user_prompt<br/>to_table: items, boxes, precompute tables"]
        sys["build_system_prompt<br/>procedure + optional sections<br/>(upright, fragile, policies, units, validator)"]
    end

    subgraph loop["planner.py — plan_with_validation"]
        model["Model call<br/>chat.completions.parse<br/>response_format = FlatResponse"]
        parse{"Parses as<br/>FlatResponse?"}
        validate["validation.py — validate_plan<br/>rotation, containment, overlap, seq,<br/>support, top-loading, fragile, weight, counts"]
        valid{"Errors?"}
        feedback["Append plan + user message<br/>INVALID: up to 20 errors, fix and resend"]
        rounds{"rounds &lt; max_rounds?"}
    end

    hints{"quality_hints:<br/>extra box, smaller box,<br/>free floor while stacking?"}
    improve["Model call (≤ improve_rounds)<br/>VALID but improvable: hints"]
    better{"Valid and better<br/>plan_score?"}
    ok["PlanResult ok=True<br/>flat = best valid plan, hints, improved"]
    fail["PlanResult ok=False<br/>flat = last plan (or None) + errors<br/>caller decides: raise, escalate, or warn"]
    unflat["schemas.unflatten<br/>join names/colors/weights by refId → nested Response"]
    render["visualize.render_plan<br/>matplotlib → figure / SVG / PNG"]

    input --> pre --> user
    input --> sys
    user --> model
    sys --> model
    model --> parse
    parse -- no --> feedback
    parse -- yes --> validate --> valid
    valid -- none --> hints
    hints -- none --> ok
    hints -- some --> improve --> better
    better -- "yes: keep it, re-check hints" --> hints
    better -- "no: keep earlier plan" --> ok
    valid -- some --> feedback
    feedback --> rounds
    rounds -- yes --> model
    rounds -- no --> fail
    ok --> unflat --> render

    classDef code fill:#d9f2d9,stroke:#3a7a3a,color:#000
    classDef llm fill:#ffe0b3,stroke:#b36b00,color:#000
    class pre,user,sys,parse,validate,valid,feedback,rounds,unflat,render,input code
    class hints,better code
    class model,improve llm
```

The loop returns as soon as a plan passes, so later rounds are only spent on
fixes. Each extra round resends the growing history and makes the model
write a full plan again (see "Offloading work from the model" below).

## Key files

- `manager.ipynb` — builds the prompt from `items` / `boxes` dicts (both keyed
  by `refId`), calls the model (OpenAI or local Ollama at
  `http://localhost:11434/v1`) with `response_format=FlatResponse`, then
  `unflatten(flat, items, boxes)` to get the nested `Response`.
- `schemas.py` — nested `Response` plan models, flat `FlatResponse` output
  models, and `unflatten`.
- `system_prompts.py` — system prompt versions (`PACKAGE_MANAGER_SYSTEM_PROMPT_V*`).
  V4 pairs with `FlatResponse`; V1–V3 describe the nested `Response`.
- `tabular.py` — token-reducing serializer for prompt input.
- `visualize.py` — matplotlib renderer for a nested `Response` plan.
- `render_common.py` — shared renderer helpers: colors, unit cuboids in view
  coords, `paint_order`, `mirror_depth`, titles, leftover lines.
- `render_plotly.py`, `render_svg.py`, `render_pyvista.py`, `render_vedo.py`,
  `render_threejs.py` — alternative renderers (see "Other renderers").
- `precompute.py` — fit checklist computed in code (rotations per box, units
  per layer, unpackable items, totals, single-box candidates) as prompt tables.
- `validation.py` — `validate_plan(flat, items, boxes) -> list[str]`, pure-code
  rule checks (empty list = valid); `quality_hints` for valid-but-wasteful
  plans; `plan_score` to compare plans.
- `prompt_builder.py` — `build_system_prompt` / `build_user_prompt`.
- `planner.py` — `plan_with_validation`: plan → validate → fix loop.

## Flat output (`schemas.py`)

The model returns only what it decides, as flat rows:
`boxes` (`box`, `ref_id`), `placements` (`box`, `seq`, `ref_id`, `pos` [x, y, z]
min corner, `dims` [x, y, z] rotated extents — one row per unit), `leftovers`
(`ref_id`, `quantity`, `reason`). `unflatten` joins name/color/weight/box
dimensions/`weightMax` back from the input records by `refId`, injects the
fixed coordinate system, groups leftovers by reason, and raises `ValueError`
on unknown refIds or placements in undeclared boxes. Roughly halves output
tokens and the response schema size versus the nested `Response`.

## Prompt input format (`tabular.py`)

`to_table(name, records, columns=None, rename=None, delimiter="|")` flattens
nested dicts to dotted keys (`dimensions.x`) and emits one header row plus one
row per record, instead of repeating keys per record. In the notebook,
`dimensions.*` are renamed to `x|y|z`; roughly halves the input size versus
Python repr. Each record must be a mapping — a stray trailing comma
(`item = {...},`) makes a tuple and raises `TypeError`.

## Env vars

- `OPENAI_API_KEY` (from `.env`) — only when using the OpenAI client.

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

### Other renderers

All take a nested `Response` plus optional `upto`, and draw in view coords
(u, v, w) = (Z span, Y depth, X height) from `render_common`. Shared view:
front-right-top, so the box origin (back-left-bottom) is the far corner and
Z runs left to right.

| Module | Function → returns | Notes |
|---|---|---|
| `render_plotly.py` | `render_plotly(plan)` → `go.Figure` | Interactive in notebook, hover info, real depth buffer. `fig.to_image(format="png"/"svg")` needs kaleido + Chrome; `fig.to_html(include_plotlyjs="cdn")` for a page. Y range reversed + camera at scene −y so Z isn't mirrored. Units inset 0.03 so edge lines aren't hidden. |
| `render_svg.py` | `render_svg(plan)` → SVG `str` | No extra deps, ~13 KB for the test plan (matplotlib SVG ~88 KB), real text labels; best fit for the image API. Orthographic, back-to-front via `paint_order`, three faces per unit. |
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

## Planned: image API (not built yet)

Decisions so far for an endpoint that returns the plan image:

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

## Offloading work from the model

Goal: let cheaper models (e.g. `gpt-5.4-mini` at `reasoning_effort="low"`)
match stronger ones by moving arithmetic and checking into code.

- **Precompute** (`precompute.py`): for each item × box pair, the rotations
  that fit, as `dx×dy×dz=P/layer×L` (P units per floor layer, L layers), and
  `max_by_weight`; item types that fit no box (`unpackable`); per-item and
  per-box volume/weight; packable totals; `single_box_candidates` (box types
  whose volume and weight limit could hold everything — necessary, not
  sufficient). Candidates are **flagged** when an item type outnumbers its best
  single-rotation grid in that box (e.g. Wide Low: "Tape: grid fits 16 of 20")
  and sorted unflagged first, then by volume. Flagged, not dropped: mixed
  rotations can beat the grid count. `pre.unflagged_candidates` lists the rest.
- **Validation** (`validation.py`): no model involved. Checks declared boxes,
  known refIds, dims are an allowed rotation, containment, pairwise overlaps,
  seq is 1..n per box, full Y–Z support for elevated units (and that supports
  load first), top-loading (no earlier unit above), fragile items carry
  nothing, weightMax, and placed + leftover = requested. Errors name units as
  `box N seq M (ref R)`. `fill_heights(flat)` gives max occupied X per box.
- **Repair loop** (`planner.py`): each round the model returns a full plan as
  structured output (`response_format=FlatResponse`); `validate_plan` checks
  it and errors go back as a user message (max 20 shown) asking for a full
  corrected plan. Stops at the first passing plan or after `max_rounds`
  (default 3) with `ok=False` and the last plan + errors — the caller must
  check `result.ok`. `PlanResult` has `flat`, `errors`, `ok`, `rounds`, token
  counts, and `history`. Not a function tool: `/v1/chat/completions` rejects
  function tools with `reasoning_effort` for `gpt-5.4-mini` ("use /v1/responses
  or set reasoning_effort to 'none'"). Structured output costs the same per
  round and works on any OpenAI-compatible backend.
- **Quality hints + improve round** (`validation.quality_hints`,
  `planner.py`): a valid plan can still be wasteful (seen: `gpt-5.4-mini` at
  `low` stacked tapes on the dispenser, left half of Medium's floor empty, and
  spilled 8 tapes into a second box). Hints flag: more than one box while an
  unflagged single-box candidate exists; a smaller unflagged candidate than
  the box used; units stacked above the floor while a free floor spot fits
  one. With hints, the model gets up to `improve_rounds` (default 1) extra
  calls; a reply replaces the plan only if valid and better by
  `plan_score` = (box count, total box volume, summed fill height), else the
  earlier plan is kept. `improve_rounds=0` disables it; `PlanResult.hints`
  still reports them.
- **Prompt builder** (`prompt_builder.py`): step-by-step procedure (unpackable
  → leftovers, smallest single-box candidate first, large/heavy items first,
  grid fill Z → Y → next X layer, finish layers, seq in order). Optional
  sections only when present: `upright` / `fragile` item flags (scanned from
  items), `policies`, `length_unit` / `weight_unit`, `precomputed` facts, and
  `with_validator` instructions (otherwise a self-check paragraph).
  `PACKAGE_MANAGER_SYSTEM_PROMPT_V*` in `system_prompts.py` are kept for
  comparison.

Optional item keys honoured by precompute + validation + prompt:
`upright: bool` (dx must equal the item's x), `fragile: bool` (nothing rests on it).
