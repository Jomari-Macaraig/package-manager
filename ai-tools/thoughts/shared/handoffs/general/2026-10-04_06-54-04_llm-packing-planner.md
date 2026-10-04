---
date: 2026-10-04T06:54:04+08:00
researcher: Jomari Macaraig
git_commit: a401f6d
branch: main
repository: package-manager
topic: "LLM packing planner (labs/) Implementation Strategy"
tags: [implementation, strategy, llm, packing, prompt-builder, settings, packer, validation, renderers, notebook]
status: complete
last_updated: 2026-10-04
last_updated_by: Jomari Macaraig
type: implementation_strategy
---

# Handoff: general — simple LLM packaging manager (labs/)

## Conversation Summary

The project is a **simple packaging manager**: given items (dims, weight,
quantity) and box types, produce a packing plan (which boxes, where each unit
goes, how to cut boxes down) using an LLM, with everything checkable done in
code. All work lives in `labs/` (Python 3.12, `uv`, `.venv`), driven from
`labs/manager.ipynb`.

How it evolved (chronological):
1. Started from an OpenAI `LengthFinishReasonError` (structured output cut off
   at the length limit). Reduced tokens: tabular input (`tabular.py`), flat
   output schema (`FlatResponse`) + `unflatten()` to the nested `Response`,
   `pos`/`dims` as lists. Local Ollama model `nimble` (a 9B classifier
   fine-tune, num_ctx 8194) burned its context on reasoning — abandoned for
   OpenAI `gpt-5.4-mini` / `gpt-5.4-nano`.
2. Renderers: matplotlib (`visualize.py`, custom painter ordering because
   mplot3d draws wrong), plus Plotly, isometric SVG, PyVista, vedo, three.js
   (`render_*.py`, shared `render_common.py`).
3. Offloading to code: `precompute.py` (fit tables, rotations, unpackable,
   single-box candidates, ideal fill height), `validation.py` (validate_plan,
   quality_hints, plan_score), `planner.py` (plan → validate → fix rounds,
   then improvement rounds), `prompt_builder.py` (fixed `SYSTEM_PROMPT` +
   `build_user_prompt`). Function tools were rejected by OpenAI with
   reasoning_effort on chat completions → switched to structured output +
   error feedback messages.
4. Evaluation against Paccurate (external engine) reference outputs was built
   then **archived** (`labs/_archive/evaluation/`); replaced by notebook sample
   cases.
5. User principle: **"anything that we can do in the code stays in the code"**;
   the LLM maps free text onto a settings schema (`settings.py`,
   `PackingSettings`) and plans placements. Settings implemented: item rules
   (padding, padding_by_item, blocks [balanced split, stack default, grid],
   upright, fixed_orientation, no_stack_on, max_load_on_top,
   floor_only_over_weight), box rules (stock on box dicts, wall_clearance,
   cut_step, min_cut_height, carrier max weight/length/girth, min_fill), order
   rules (max_boxes, keep_together, keep_apart, balance_boxes),
   soft_instructions.
6. Model plans wasted height → built a deterministic **code packer**
   (`packer.py`) used as hint target and fallback. Then "prefer flat" +
   "shipped volume" (cut-down height × floor area) ranking.
7. Last steps: cut-height semantics fix, more notebook sample orders (golf,
   golf balls, A home goods, B office, C nothing-fits), and precompute fix for
   too-heavy items.

Nothing in the latest round was run against a real model — all verification
was offline (fake clients, packer, validator).

## Key User Messages (Chronological)

- "what does this mean LengthFinishReasonError…" → explained, then token reduction.
- "create a flattener/token reduce… go tabular per record" → `tabular.py`.
- "we should also use flat output structure then we can just unflat them" → `FlatResponse` + `unflatten`.
- "can the coordinates and dimension be a list instead" → `pos`/`dims` lists.
- "Please be advise what we're building is merely a simple packaging manager" (KEY constraint).
- Evaluation: wanted same/better/worse box count vs Paccurate; later "skip those types" (generated boxes / cost / rules), then "I don't want to run anymore… remove old evaluation for the meantime" → archived; notebook sample cases replace it.
- "I want to use OpenRouter… Qwen3.8 27B (free)" → `--openrouter`, notebook OpenRouter cell.
- System prompt: "lets remove 7 and 8. We will stick to in and lb" (optional rules + units); dedup trim → "revert it, I got a bad result"; "use f-string… placeholder"; "precomputed_facts… isn't that always required?" → always on; "we should always do the validator check ourselves" → `SYSTEM_PROMPT` constant, all model cells via `plan_with_validation`.
- "remove the name in the stats… change ref_id to just id… weight_max… remove" then "the stat build shouldn't rename refId to id, its on the notebook. we should just use id. Remove any duplicate… Be consistent… id".
- "improve by giving more weight of maximizing the space… leaving space above for cutoff… center of gravity" → priorities/procedure; stats 1–3 added (ideal_fill_height, layers/height needed, floor %).
- "show the number of tokens… potential cost" → `report()` + PRICES.
- "extend the user prompt from a user input… policy or rules" → instructions; "add the additional prompt to the front" (front is prioritized); notes field "relayed"; visualizer shows notes.
- Floating items + padding refusal → "anything that we can do in the code stays in the code"; "so we need a clear settings/configuration that… the llm read and maps".
- Padding: "every side x y z" (dimension grows by 2×padding). Blocks: option 2 (solid block); "11… block is 4 → 4,4,3… balanced not greedy… 18 at 7 → 6,6,6"; "by default stacking it" + parse arrangement from instructions.
- "implement all for the item. Box stock… pass stock in the box details… apply all box rules. Implement order rules… create sample cases in the notebooks… remove old evaluation".
- "result doesn't maximize the space and doesn't leave space above" → packer ("go").
- "better to have the items flat or prioritize it" → flat pass + shipped volume.
- "when I say cut down by 0.5. The box dimension height should be the one divisible by 0.5".
- Sample orders: golf 9112069 ("use the same name items and boxes so I can run it later"), golf balls 9172381, "two more random sample sets", "a sample that we won't find any box".

## Task(s)

- Token reduction / flat output / renderers — completed.
- Offloading (precompute, validation, planner loop, prompt builder) — completed.
- Settings schema with item/box/order rules + extraction — completed (offline-tested).
- Code packer (target + fallback, flat preference, shipped volume) — completed (offline-tested).
- Notebook sample cases (evaluation) — completed; **not yet run with a real model after latest changes**.
- Sample orders golf / golf balls / A / B / C — completed.
- System prompt rethink — **deferred by user** ("We should also rethink this for the system prompt but we'll do that later").

## Current Work (Immediate State)

Just finished Sample order C (nothing fits) and two precompute fixes
(`labs/precompute.py`): a box with `max_by_weight == 0` is no longer a fit
(too-heavy items become unpackable with reason "one unit weighs … lb, more
than any box it fits can carry"), and `single_box_candidates` is empty when
nothing is packable. Docs updated (`labs/CLAUDE.md` "Unpackable items").
No pending edits. Next natural step: user runs the notebook (restart kernel).

## User Intent & Preferences

- Keep it simple; ask before adding subsystems. Saved as memory
  (`~/.claude/projects/-Users-jomarimacaraig-Desktop-projects-package-manager/memory/project-simple-packaging-manager.md`).
- Anything code can do stays in code; LLM only maps text → settings and plans placements.
- Never let the LLM bypass the validator.
- Units fixed: inches and pounds. Use `id` everywhere (never refId/ref_id in our code).
- Goals for plans: pack everything, fewest boxes, least **shipped** volume, maximize floor use (left-right, front-back), leave free space on top for cutting down, lay items flat, centered/low center of gravity.
- Cut height: resulting box height must be a multiple of cut_step (amount removed needn't be).
- Additional instructions go at the **front** of the user prompt.
- Sample order cells must define `items` and `boxes` (same names) so later cells run on whichever was run last.
- User's global rule: after implementing a feature update module `labs/CLAUDE.md` and root `CLAUDE.md` downlinks.
- Prompt wording changes can make results worse (a dedup trim did) — keep backups, compare.
- Cost-conscious: don't run model calls without being asked.

## Key Technical Concepts

- OpenAI Python SDK `chat.completions.parse(response_format=FlatResponse)`; strict JSON schema (defaults stripped recursively for `PackingSettings` in `settings._strict_schema`). Function tools + reasoning_effort rejected on `/v1/chat/completions` for gpt-5.4-mini.
- OpenRouter: base_url `https://openrouter.ai/api/v1`, `extra_body={"reasoning":{"effort":...},"provider":{"require_parameters":True}}`; `OPENROUTER_API_KEY` (not yet in .env).
- Coordinates: X = height (up), Y = depth (back→front), Z = span (left→right); origin back-left-bottom; Paccurate also uses X as up. Renderers use view coords (u,v,w)=(Z,Y,X); plan frame is left-handed → `mirror_depth` for VTK, reversed y in Plotly.
- Prepared items carry flags: `fixed`, `upright`, `no_stack`, `max_load`, `floor_only`, `source_id`; boxes carry `stock`. Validator reads flags; `settings` object passed for order/carrier rules (getattr, no import → avoids circular import settings↔validation).
- Packer: greedy extreme-point, rotation combos (≤64) or sequential, plus flat pass, two type orders; box choice by least shipped volume; `_rank` = (leftover units, boxes, shipped volume, summed fill).
- Prices (third-party, 2026-10-04): gpt-5.4-nano $0.20/$1.25 per M; gpt-5.4-mini $0.75/$4.50.

## Critical References

- `labs/CLAUDE.md` — full module documentation (workflow diagram, every rule, packer, sample cases, history of decisions).
- `labs/prompt_builder.py` — `SYSTEM_PROMPT` (single readable string) + `build_user_prompt`.
- `labs/settings.py` — `PackingSettings` and all prepare/rules/cut code.

## Recent changes

- `labs/precompute.py` — `item_rotations` (fixed/upright flags), stock-0 boxes skipped, rotation stats (layers_needed/height_needed/floor_cover; sorted lowest h), `ideal_fill_height`, too-heavy → unpackable, no candidates when nothing packable, stats tables print only `id` + new columns.
- `labs/validation.py` — `cut_height`, item-flag checks, stock, `_settings_errors` (max_boxes, keep_together/apart, carrier), quality hints (min_fill, balance; removed "smaller box"), `plan_score` uses shipped volume.
- `labs/settings.py` — new module: PackingSettings, BlockRule/LoadLimit/ItemPadding, extract_settings, apply_padding, split_balanced, block_shape, apply_blocks, prepare_items, prepare_boxes, rules_text, cut_notes, final_plan.
- `labs/packer.py` — new module: `pack()`.
- `labs/planner.py` — `settings=`, `baseline=`, `_rank`, `_baseline_hint`, `_prefer_baseline`, `PlanResult.source/prompt_tokens/total_tokens`, structured-output loop.
- `labs/prompt_builder.py` — fixed `SYSTEM_PROMPT` (priorities incl. shipped volume + lay flat, no-floating rules, packing rules/instructions note), `build_user_prompt(items, boxes, instructions, rules)`, `HIDDEN_ITEM_KEYS`.
- `labs/schemas.py` — everything `id`, `notes` field, units fixed in `unflatten`.
- `labs/render_common.py` (`footer_lines`, `add_footer`, `paint_order`, `mirror_depth`) and all `render_*.py`/`visualize.py` — notes footer.
- `labs/manager.ipynb` — order cells (dispenser sample, golf 9112069, golf balls 9172381, Sample A, B, C), prompts cell (extract settings → prepare → rules → prompt), report/PRICES cell, model cell (pack baseline → plan_with_validation → final_plan, raises if not ok), renderer cells, sample-cases section (CASES, run_case, run-all table, view one).
- `labs/evaluation/` moved to `labs/_archive/evaluation/` (git shows deletions; archive is untracked).

## Errors and Fixes

- `LengthFinishReasonError` (OpenAI/Ollama): output cut off; Ollama `nimble` num_ctx 8194 consumed by reasoning → tokens reduced; local model abandoned.
- matplotlib 3D draws blocks in wrong order → custom painter ordering (`render_common.paint_order`).
- Plotly/VTK mirrored scenes (left-handed plan frame) → `mirror_depth` / reversed y range.
- PyVista legend overflow → positioned text; PIL default font lacks "—"/"×" → DejaVu Sans via matplotlib.
- OpenAI 400 "Function tools with reasoning_effort are not supported for gpt-5.4-mini in /v1/chat/completions" → structured output loop, no tools.
- Plan with floating items rendered → it was `ok=False` (validator caught it); notebook now raises instead of drawing; prompt "No floating units" + centering limited to top layer (model centered the dispenser and stacked tapes half on it).
- Model refused "+0.125 padding" (dims must match rotations) → padding applied in code (settings).
- Model plan 12.75 high where 9.5 possible → code packer.
- Packer stood items on edge (golf: CASE filled to 10.63/11) → flat pass + shipped-volume box choice (golf → X4 flat, 7 in, 6.25 in to cut).
- Carrier weight not reflected in candidates → `prepare_boxes` lowers `weightMax`.
- `plan_score` ignored leftovers → `_rank` puts leftover units first.
- Cut height not a multiple when capped at box height → "no cut needed" note.
- Too-heavy item listed as fitting with max_by_weight 0 → unpackable.
- Old Paccurate eval files were mismatched/duplicated inputs/outputs (user fixed repeatedly; script flags DUPLICATE / OUTPUT DOES NOT MATCH INPUT) — archived.

## Learnings

- Small/cheap models are weak at spatial search; code (packer, precompute) is far more reliable. The model is mainly useful for soft instructions and text mapping.
- Prompt dedup trims degraded results — wording matters; compare before keeping.
- With the "no floating" rule, heterogeneous stacking is hard; standing items on edge often "wins" unless ranking prefers flat + shipped volume.
- `.env` is tracked in git (`git status` shows ` M .env`; it was committed in `a401f6d "Created POC"`) — contains `OPENAI_API_KEY`. Should be untracked and the key rotated (not yet raised with the user).

## Artifacts

- `labs/settings.py`, `labs/packer.py` (new)
- `labs/precompute.py`, `labs/validation.py`, `labs/planner.py`, `labs/prompt_builder.py`, `labs/schemas.py`, `labs/tabular.py`
- `labs/visualize.py`, `labs/render_common.py`, `labs/render_plotly.py`, `labs/render_svg.py`, `labs/render_pyvista.py`, `labs/render_vedo.py`, `labs/render_threejs.py`
- `labs/manager.ipynb`
- `labs/CLAUDE.md`, root `CLAUDE.md`
- `labs/_archive/evaluation/` (archived Paccurate comparison + `evaluate.py`)
- `labs/system_prompts.py` (old V1–V5 prompts, kept for comparison; cell 0 still imports V5 unused)
- Memory: `~/.claude/projects/-Users-jomarimacaraig-Desktop-projects-package-manager/memory/project-simple-packaging-manager.md`

## Open Questions & Blockers

- Soft instructions are lost when the packer's plan wins (stated in notes). Options discussed, undecided: skip the LLM when there are no soft instructions; or give the packer plan to the model as a starting point.
- System prompt rethink deferred by user.
- `OPENROUTER_API_KEY` not in `.env`; Qwen3.8 27B free may fail routing with `require_parameters` (drop it if so).
- Center-of-gravity is only in the prompt (no code check/score) — offered, not built.
- `.env` tracked in git with an API key — needs to be raised with the user.

## Action Items & Next Steps

1. [HIGH] Tell the user `.env` is tracked in git (committed in a401f6d) — suggest `git rm --cached .env`, add to `.gitignore`, rotate the OpenAI key. Ask before acting.
2. [HIGH] When the user runs the notebook sample cases, read the run-all table (cell outputs) and compare model vs packer (`from` column), fill heights, rounds, cost.
3. [MEDIUM] Decide with the user the soft-instruction vs packer policy (above).
4. [MEDIUM] System prompt rethink (user-deferred) — keep backups, compare on sample cases.
5. [LOW] Possible: center-of-gravity score/hint in code; `report()` doesn't count the extraction call's tokens; keep_together isn't steered by the packer (only checked).

## Other Notes

- Notebook order cells all set `items`/`boxes`; the last one run wins. Sample cases use shared `BOXES` (Small/Medium/Large) except `basic` (current items/boxes) and `stock`.
- Restart the kernel after module edits (modules are imported, not reloaded).
- Scratchpad backups of earlier versions exist under the session scratchpad (`before_rules/`, `before_flat/`, `prompt_builder_before_*`), but scratchpad is session-specific; don't rely on it.
- Offline testing pattern used throughout: load notebook cells via `json` + `exec`, fake OpenAI client with `SimpleNamespace` returning `parsed` FlatResponse and `usage`.
