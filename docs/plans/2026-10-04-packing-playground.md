# Packing Playground (FastAPI + React) Implementation Plan

> Revision 2 (2026-10-04): updated after the dry-run review (backend,
> frontend, real-time, security, architecture). Changes: box numbering,
> full contract types, SSE ids/reconnect, terminal ordering, color default,
> run-lifecycle hardening, request hardening, tracing hook, FakeClient
> design, packer timing at the caps (accepted by user).
>
> Revision 3 (2026-10-04): second dry-run review. Changes: SSE always
> ends a terminal run with `end` (reconnect-loop fix), events persisted as
> they happen, guarded `finish`, partial results on cancel, `started`
> emitter, OpenAI error mapping/order/retries, extract-stage error mapping,
> box-dimension check after wall clearance, inf/NaN rejection and caps,
> middleware order/details, TestClient host, direct SSE generator tests,
> non-interactive scaffold, client attach/dedupe rules, fake EventSource.

## Overview

A local web playground where a user edits items and boxes (Sample orders
A, B, C preloaded), writes free-text packing instructions, picks models, and
runs the full LLM packing pipeline from `labs/`. Runs execute as background
jobs with live progress over SSE, are traced with the OpenAI Agents SDK
`trace()`, and are saved to SQLite so past runs can be viewed. The model's
plan and the code packer's plan are shown side by side as server-rendered
SVGs with a per-box loading-step slider.

## Current State Analysis

- All pipeline code lives in `labs/` as flat modules imported by
  `labs/manager.ipynb` (`from schemas import ...`). No app, package, API, or
  persistence.
- Pipeline as run in the notebook (`labs/manager.ipynb` cells 9 and 12,
  `run_case` in cell 23):
  `extract_settings` → `prepare_items` / `prepare_boxes` → `rules_text` →
  `build_user_prompt` → `pack` (baseline) → `plan_with_validation` →
  `final_plan`.
- `labs/settings.py:182` `extract_settings` discards token usage.
- `labs/planner.py:193-211` `_prefer_baseline` overwrites `result.flat`
  with the packer's plan (and returns early when `baseline is None`), so
  the model's own plan is lost when the packer wins.
- `labs/planner.py` `_ask` catches only `pydantic.ValidationError`;
  `openai.LengthFinishReasonError` / API errors propagate.
- `labs/render_svg.py:138` `render_svg(plan, upto)` draws every box; `upto`
  applies to every box at once (`labs/render_common.py:60-76`). `_panel`
  is self-contained per box.
- `labs/schemas.py:189` `_item` reads `source["color"]` and `Item.color` is
  `str` (`schemas.py:63`): a missing or `None` color crashes `unflatten`.
- Nested `Response.boxes[]` (`Boxes`) has only the box **type** `id`;
  `unflatten` drops the physical box number (`BoxRow.box`), and the
  validator accepts gaps (boxes 2 and 5).
- Root `.env` (tracked in git, contains `OPENAI_API_KEY`) is the key source.
  Left as-is by user decision.
- Toolchain: Node 22.13, npm 11.6, uv 0.12.22, Python 3.12. Current npm
  versions: vite 8.3, @vitejs/plugin-react 6.1, @tailwindcss/vite 4.3,
  vitest 5.0, react 19.3.

## Desired End State

- `app/backend/`: uv project, FastAPI app
  (`uv run uvicorn app.main:app --host 127.0.0.1 --port 8000`).
- `app/frontend/`: Vite + React + TypeScript + Tailwind (`npm run dev`,
  port 5173, `/api` proxied to 127.0.0.1:8000).
- A user can:
  1. Load Sample A, B, or C (or edit tables from scratch), type instructions,
     choose extraction and planning models, and click **Pack**.
  2. Watch progress live and cancel (cooperative: takes effect before the
     next model call).
  3. See run stats, extracted settings, prepared items, the model's and the
     packer's plans side by side (rank, winner marked; invalid plan =
     errors, no image), each with a box picker + loading-step slider, notes,
     leftovers.
  4. Open a read-only history list of past runs and view any of them.
- Every run has a uuid4 `run_id`; its OpenAI trace id is
  `trace_<run_id.hex>`; client IP and User-Agent are stored with the run and
  attached to the trace metadata. Every HTTP response carries
  `X-Request-ID`; the run stores the request id that created it.
- `labs/` is unchanged.

### Key Discoveries

- Planner: `plan_with_validation` `labs/planner.py:131-191`, `_rank`
  `:104` = (leftover units, boxes, shipped volume, summed fill height),
  `_prefer_baseline` `:193-211`; `labs/validation.py:395` `plan_score`.
- Capturing `model_flat` / `model_errors` at the **top** of
  `_prefer_baseline` (before the `baseline is None` early return) is correct
  on every path: loop exhausted → errors non-empty; failed improve round →
  errors reset, first valid plan kept.
- Copy closure (verified): the 10 modules below cover all imports;
  `system_prompts.py` is not needed. Relative-import changes: planner
  (schemas, validation), settings (schemas, validation), packer (precompute,
  schemas, validation), precompute (tabular), prompt_builder (precompute,
  tabular), render_common (schemas), render_svg (render_common, schemas),
  validation (precompute, schemas). `settings.py:212` imports
  `openai.lib._pydantic` (private, fine). `render_common.add_footer` lazily
  imports PIL but `render_svg` never calls it.
- `extract_settings` skips the LLM for empty text (`labs/settings.py:193`)
  and passes `**create_kwargs` through.
- Colors: `render_common.item_colors` (`:37-48`) keys by item **name**,
  checks `is_color_like`, falls back to tab10, emits `to_hex` → only
  `#rrggbb` reaches the SVG. `is_color_like("")` is False → tab10.
- SVG text (box titles, legend names, footer notes/reasons) is
  `html.escape`d (`render_svg.py:99, 154, 160`).
- Measured on the samples: `pack()` A 1 box 0.06 s, B 2 boxes 0.23 s, C 0
  boxes, 4 leftover rows (5 units), valid; `final_plan` + `render_svg`
  handle C.
- Measured at the caps (random orders, 10 box types): 140–151 units take
  **29–43 s** in `pack()`, 60 units 12 s. Pure Python, holds the GIL,
  not cancellable. **Accepted by user**: caps stay; documented.
- `Path(__file__).resolve().parents[3]` from `app/backend/app/config.py` =
  repo root (verified).
- OpenAI Agents SDK tracing (`openai-agents`, `from agents import trace,
  custom_span, generation_span, set_tracing_disabled, add_trace_processor,
  set_trace_processors, flush_traces` — verify `flush_traces` export at
  install; fall back to `agents.tracing`): `trace(workflow_name,
  trace_id=None, group_id=None, metadata=None, disabled=False)`; trace id
  must be `trace_<32 alphanumeric>` (`uuid4().hex` is 32); metadata values
  must be strings; `generation_span(input=None, output=None, model=None,
  model_config=None, usage=None)`; `custom_span(name, data=None)`; current
  trace is a `contextvar` (opened inside the worker thread → fine); spans
  attach only when a trace is current; exports with `OPENAI_API_KEY`;
  disabled via `OPENAI_AGENTS_DISABLE_TRACING=1` / `set_tracing_disabled`.
  Docs: https://openai.github.io/openai-agents-python/tracing/
- Prices (USD per 1M tokens, input / output, looked up 2026-10-04):
  gpt-5.4-nano 0.20 / 1.25, gpt-5.4-mini 0.75 / 4.50, gpt-4.1-nano 0.10 /
  0.40, gpt-4.1-mini 0.40 / 1.60, gpt-4o-mini 0.15 / 0.60. Reasoning
  tokens are already inside `completion_tokens` (don't add twice).

## What We're NOT Doing

- **Porting to Agents SDK `Agent` / `Runner` / `function_tool` /
  `SQLiteSession`.** Deferred to a follow-up plan. This plan uses only
  `agents.trace` and spans. Notes for the port: function tools +
  `reasoning_effort` were rejected on `/v1/chat/completions`; `Runner` uses
  the Responses API — re-check then. `SQLiteSession` should use its own
  file (`data/sessions.db`); `/history` is defined as opaque lists of
  message objects so its content can switch to Responses input items.
- Changing anything under `labs/` (one-time fork; no sync).
- Auth, users, rate limiting beyond the caps, request hardening below, and
  one-run-at-a-time.
- Editing, deleting, re-running, or loading past runs into the editor.
- Packer-only mode, OpenRouter, Ollama.
- Speeding up the packer or a packer time budget (user accepted 29–43 s at
  the caps).
- Interactive 3D (three.js) in the app; other renderers.
- Deployment, Docker, production server config.
- Fixing the tracked root `.env` / key rotation (user decision: as-is).
- Full prompts/completions in traces (metadata only; history in SQLite).
- Resuming interrupted runs.

## Implementation Approach

1. Copy the needed `labs/` modules into `app/backend/packing/` as a package
   (relative imports) and make the few listed changes there. `packing/`
   stays free of `agents` and FastAPI imports (hooks instead).
2. Wrap the notebook pipeline in `run_pipeline` with stages (extract,
   prepare, packer, plan, finalize), a progress callback, a cancel check,
   and an Agents SDK trace. Only extract and plan call a model (the stages
   the later port replaces).
3. A `RunManager` runs one pipeline at a time in a daemon worker thread,
   keeps an in-memory event list for the active run, persists the final
   state to SQLite in one transaction, and only then releases the slot.
   SSE polls the event list with event ids.
4. The React app talks only to the contract below.

---

## API contract (shared by backend and frontend agents)

Units are inches and pounds. JSON keys are snake_case except `weightMax`
(kept from the notebook dicts). TypeScript types below are normative; the
backend Pydantic models must produce exactly these shapes.

### Input

```ts
type InputItem = {
  name: string;          // 1–60 chars, trimmed, unique case-insensitive, no control chars
  color: string | null;  // ≤ 32 chars, no control chars; null/"" = auto (tab10)
  weight: number;        // ≥ 0 and ≤ 10000, lb
  x: number; y: number; z: number;  // > 0 and ≤ 1000, in
  quantity: number;      // integer ≥ 1
};
type InputBox = {
  name: string;          // same rules as item name
  weightMax: number;     // > 0 and ≤ 10000
  x: number; y: number; z: number;  // > 0 and ≤ 1000
  stock: number | null;  // integer 0..1000; null = unlimited
};
type RunRequest = {
  items: InputItem[];    // 1..20 rows; Σ quantity ≤ 150
  boxes: InputBox[];     // 1..10 rows
  instructions: string;  // ≤ 2000 chars, no control chars except \n \t
  extraction: { model: string };                       // must be in config
  planning: { model: string; reasoning_effort: "low" | "medium" | "high" };
};
```
Ids are not sent; the backend assigns item ids 1..n and box ids 1..n by row
order. All input models use `ConfigDict(allow_inf_nan=False)` (JSON
`Infinity`/`NaN` → 422). Request bodies over 256 KB → 413.

### Output

```ts
type Dimensions = { x: number; y: number; z: number };
type Item = { id: number; color: string; name: string; weight: number;
              dimensions: Dimensions; quantity: number };
type PackedItem = Item & {           // one unit; quantity = 1
  packed_index: number;              // loading step within its box (1..n)
  placement_position: { x: number; y: number; z: number };  // min corner
};                                   // dimensions = rotated extents
type PlanBox = { id: number;         // box TYPE id
                 name: string; dimensions: Dimensions;
                 max_contents_weight: number; items: PackedItem[] };
type LeftoverGroup = { items: Item[]; reason: string };
type Plan = {                        // labs schemas.Response.model_dump()
  length_unit: "in"; weight_unit: "lb";
  coordinate_system: Record<string, string>;
  boxes: PlanBox[];                  // physical box N = boxes[N-1]
  leftover_items: LeftoverGroup[];
  notes: string[];                   // cut notes (code) first, then model notes
};
// Field names verified against labs/schemas.py (Boxes, PackedItem,
// LeftoverItems, Item) on 2026-10-04.

type Rank = [leftover_units: number, boxes: number,
             shipped_volume_in3: number, summed_fill_height_in: number];

type PackingSettings = {
  padding: number; padding_by_item: { item_id: number; padding: number }[];
  blocks: { item_id: number; size: number; arrangement: "stack" | "compact";
            grid: number[] | null }[];
  upright: number[]; fixed_orientation: number[]; no_stack_on: number[];
  max_load_on_top: { item_id: number; max_weight: number }[];
  floor_only_over_weight: number | null;
  wall_clearance: number; cut_step: number | null; min_cut_height: number | null;
  carrier_max_weight: number | null; carrier_max_length: number | null;
  carrier_max_girth: number | null; min_fill: number | null;
  max_boxes: number | null; keep_together: number[][]; keep_apart: number[][];
  balance_boxes: boolean; soft_instructions: string[];
};
// Verified against labs/settings.py PackingSettings / BlockRule /
// LoadLimit / ItemPadding on 2026-10-04.

type PreparedItem = {
  id: number; source_id: number; name: string; color: string;
  weight: number; dimensions: Dimensions; quantity: number;
  fixed?: boolean; upright?: boolean; no_stack?: boolean;
  max_load?: number; floor_only?: boolean;
};
type PreparedBox = { id: number; name: string; weightMax: number;
                     dimensions: Dimensions; stock?: number };

type PlanSide = {
  ok: boolean; rank: Rank | null; errors: string[]; hints: string[];
  plan: Plan | null;          // null when invalid
  box_count: number;          // = plan?.boxes.length ?? 0
};
type ModelSide = PlanSide & { rounds: number; improved: boolean };
// ModelSide.hints/errors are the model's own (captured before the packer
// replaces them); errors include "model call failed: …" for API errors.

type CallStats = { model: string; reasoning_effort: string | null;
                   prompt_tokens: number; completion_tokens: number;
                   reasoning_tokens: number; cost: number };
type RunStatus = "running" | "succeeded" | "failed" | "cancelled" | "interrupted";

type RunEvent =
  | { id: number; type: "started"; t: number }
  | { id: number; type: "extracted"; t: number; tokens: number; skipped: boolean }
  | { id: number; type: "prepared"; t: number; item_types: number; units: number }
  | { id: number; type: "packer"; t: number; ok: boolean; boxes: number; errors_count: number }
  | { id: number; type: "round"; t: number; n: number; kind: "fix" | "improve";
      valid: boolean; errors_count: number }
  | { id: number; type: "chosen"; t: number; source: "model" | "packer" | null }
  | { id: number; type: "end"; t: number; status: RunStatus; error: string | null };
// id = 0-based index in the run's event list; t = seconds since start.

type Run = {
  run_id: string; trace_id: string; request_id: string;
  status: RunStatus; error: string | null;
  created_at: string; finished_at: string | null;   // ISO 8601 UTC
  client_ip: string; user_agent: string;            // UA truncated to 200
  request: RunRequest;
  loop: { max_rounds: 3; improve_rounds: 1 };
  // Null while status = "running", and when the run failed before the stage:
  settings: PackingSettings | null;
  prepared_items: PreparedItem[] | null;
  prepared_boxes: PreparedBox[] | null;
  final: { source: "model" | "packer"; plan: Plan } | null;  // null = neither side ok
  model: ModelSide | null;
  packer: PlanSide | null;
  stats: { extraction: CallStats | null; planning: CallStats | null;
           total_tokens: number; total_cost: number;
           timings: { extract_s: number | null; packer_s: number | null;
                      plan_s: number | null; total_s: number | null } } | null;
  events: RunEvent[];   // persisted as they happen; complete after end
};

type RunSummary = { run_id: string; created_at: string; finished_at: string | null;
  status: RunStatus; title: string;            // first 3 item names
  extraction_model: string; planning_model: string;
  source: "model" | "packer" | null; total_cost: number | null; client_ip: string };

type ModelOption = { id: string; label: string; price_in: number; price_out: number };
type Config = {
  extraction_models: (ModelOption & { reasoning: boolean })[];
  planning_models: (ModelOption & { efforts: ("low" | "medium" | "high")[] })[];
  defaults: { extraction_model: string; planning_model: string;
              reasoning_effort: "low" | "medium" | "high" };
  loop: { max_rounds: 3; improve_rounds: 1 };
  caps: { max_item_rows: 20; max_box_rows: 10; max_units: 150;
          max_instruction_chars: 2000 };
};
type Sample = { key: "A" | "B" | "C"; title: string; description: string;
                items: InputItem[]; boxes: InputBox[]; instructions: string };
```

Box numbering: in finalize the backend renumbers each plan's
`flat.boxes` to 1..n (sorted by original number, placements remapped)
**before** `final_plan`, so cut notes ("Cut box 2 …") match. Physical box
N everywhere (notes, `/render?box=N`, UI picker) = `plan.boxes[N-1]`.
Slider max for box N = `plan.boxes[N-1].items.length`. Known limitation:
free-text notes written by the model may still cite its original box
numbers; code cut notes always use the renumbered ones.

Errors: 403/404/409/413/415 `{"detail": string}`; TrustedHost 400 is plain text; 422 FastAPI default
`{"detail": [{loc, msg, type}]}` (custom validators raise `ValueError`, so
`msg` starts with "Value error, ").

### Endpoints (prefix `/api`)

| Method | Path | Response |
|---|---|---|
| GET | `/health` | `{"status": "ok"}` |
| GET | `/config` | `Config` |
| GET | `/samples` | `Sample[]` |
| POST | `/runs` | `202 {run_id}`; 409 if a run is active; 415 if not `application/json`; 422 invalid |
| GET | `/runs` | `RunSummary[]` newest first, ≤ 100 |
| GET | `/runs/active` | `{run_id: string \| null}` |
| GET | `/runs/{id}` | `Run`; 404 unknown or not a UUID |
| GET | `/runs/{id}/history` | `{extraction: object[], planning: object[]}` (opaque message lists) |
| GET | `/runs/{id}/events` | SSE, see below |
| POST | `/runs/{id}/cancel` | 202; 409 if not the active run |
| GET | `/runs/{id}/render?plan=model\|packer&box=N&upto=K` (`plan` typed `Literal["model","packer"]`) | `image/svg+xml` of box N (1-based); `upto` optional int ≥ 1 (clamped to the box's unit count); 404 if run/plan/box missing; 409 while running; headers `Cache-Control: max-age=3600`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'` |
| GET | `/runs/{id}/svg?plan=model\|packer` | stored full SVG (all boxes), `Content-Type: image/svg+xml`, `Content-Disposition: attachment; filename="run-<run_id>-<plan>.svg"` (never built from item names), same security headers; 404 if that side has no SVG |

### SSE (`GET /runs/{id}/events`)

- Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`,
  `Connection: keep-alive`, `X-Accel-Buffering: no`. No gzip middleware.
- Each event: `id: <n>\nevent: <type>\ndata: <json RunEvent>\n\n`.
- Start index = `int(Last-Event-ID) + 1` if the header is present, else
  `int(?from=)` if given, else 0. (Browsers send Last-Event-ID only on their
  own auto-reconnect; the client uses `?from=` when it attaches.)
- Source: if the DB row status is `running` and the run is the active one,
  hold that `ActiveRun` reference and read its in-memory list (copy a
  slice under the lock), polling every 0.25 s; otherwise replay the stored
  `events_json`. If the row says `running` but the active slot is gone,
  re-read the row for up to 2 s; if still `running`, send a synthetic
  `end {status: "failed", error: "Run state could not be saved"}` and close.
- **Terminal runs always end with `end`:** for a terminal run, send the
  stored events from the start index, and if the start index is past the
  stored `end` (e.g. the client saw ids 0–5 and the interrupted run's
  stored `end` is id 3), still send the stored `end` event. A terminal run's
  stream never closes without an `end` frame.
- Stop after sending `end`; also stop when `request.is_disconnected()` or
  the row becomes terminal (then send the stored remainder incl. `end`).
  Heartbeat comment `: ping` every 15 s. Generator wrapped in try/finally.
- Events are persisted as they happen (`store.append_event` on every
  emit), so interrupted runs keep their progress. On startup
  `mark_interrupted` appends `end {status: "interrupted"}` with
  `id = len(stored events)` (NULL `events_json` treated as `[]`).
- The stream is an async generator `event_stream(manager, store, run_id,
  start, is_disconnected, poll_s=0.25, heartbeat_s=15)` in
  `app/backend/app/sse.py`; the route only wraps it in a
  `StreamingResponse` (so tests can drive it directly).
- Client: `subscribe(runId, onEvent, onClosed, fromId?)`.
  `addEventListener` for each of the 7 types (named events never reach
  `onmessage`); dedupe by `id`; `close()` in the `end` listener and on
  unmount; on **any** `error` event, call `getRun` (at most once per 2 s):
  if its status is terminal, `close()`, deliver its missing events, then
  `onClosed(run)`. `onClosed` fires exactly once, after `end` or that
  fallback. After `end`, refetch `getRun` and the history list.
- Attach (page load with an active run, "Show it" after 409, opening a
  running run from history): `getRun(id)` first, seed the log and the
  seen-id set from `run.events`, then `subscribe(id, …, fromId =
  lastId + 1)`.

---

## Agent Teams

### Implementation Agents

#### Backend Engineer (Wave 1)
- **Agent type**: `voltagent-lang:python-pro`
- **Branch**: `plan/packing-playground-backend`
- **Phases**: 1, 2
- **Directories**: `app/backend/`, `.gitignore`
- **Dependencies**: none
- **Context files**: this plan; `labs/CLAUDE.md`; `labs/planner.py`,
  `labs/settings.py`, `labs/render_svg.py`, `labs/render_common.py`,
  `labs/schemas.py`, `labs/packer.py`; notebook cells 5–7, 9, 10, 12, 23 of
  `labs/manager.ipynb`.
- **Prompt**: Create the `app/backend` uv project, copy the listed labs
  modules into `packing/` with relative imports and the specified changes,
  then build the pipeline, tracing, SQLite store, run manager and FastAPI
  endpoints exactly per the API contract (including the SSE and terminal
  ordering rules). Do not modify `labs/`.
- **Acceptance criteria**:
  - [ ] `cd app/backend && uv run pytest` passes (Phase 1–2 tests).
  - [ ] `uv run python -c "import app.main"` succeeds without
        `OPENAI_API_KEY`.
  - [ ] Response shapes match the TS types in the contract (a test
        validates `GET /runs/{id}` for Sample A against them field by field).
  - [ ] `git diff --stat labs/` is empty.

#### Frontend Engineer (Wave 1, parallel)
- **Agent type**: `voltagent-lang:react-specialist`
- **Branch**: `plan/packing-playground-frontend`
- **Phases**: 3, 4
- **Directories**: `app/frontend/`
- **Dependencies**: none (mocked `client.ts` in tests)
- **Context files**: this plan (API contract and SSE sections especially).
- **Prompt**: Scaffold the Vite + React + TS + Tailwind app and build the
  editor, run flow (SSE per the client rules, cancel), results view (side
  by side plans with box picker + step slider via `/render`), and read-only
  history, typed exactly against the contract.
- **Acceptance criteria**:
  - [ ] `npm run typecheck`, `npm run build`, `npm test` pass.
  - [ ] Contract types copied verbatim into `src/api/types.ts`; all
        network calls in `src/api/client.ts`.

#### Integration Engineer (Wave 2)
- **Agent type**: `voltagent-core-dev:fullstack-developer`
- **Branch**: `plan/packing-playground-integration`
- **Phases**: 5
- **Directories**: `app/`, `CLAUDE.md`, `app/CLAUDE.md`, `README.md`
- **Dependencies**: Backend Engineer, Frontend Engineer merged
- **Context files**: this plan; both branches' code.
- **Prompt**: Run both servers with `PLAYGROUND_FAKE_LLM=1`, fix any
  contract mismatches (paths, fields, SSE parsing, status codes), verify
  the three samples end to end, and write the docs.
- **Acceptance criteria**:
  - [ ] With fake LLM, Samples A, B, C complete from the UI (A/B succeed
        with plans; C succeeds with 0 boxes, 4 leftover rows, 5 units).
  - [ ] `app/CLAUDE.md` exists; root `CLAUDE.md` links it.

#### Test Engineer (Wave 3)
- **Agent type**: `voltagent-qa-sec:test-automator`
- **Branch**: `plan/packing-playground-tests`
- **Phases**: tests in "Testing Strategy" not already written
- **Directories**: `app/backend/tests/`, `app/frontend/src/**/*.test.tsx`
- **Dependencies**: Integration Engineer merged
- **Prompt**: Fill test gaps per the Testing Strategy (SSE ids/reconnect
  and terminal race, cancel, interrupted, caps, hardening, trace metadata,
  render, UI states). Use small orders (≤ 20 units) — cap-size orders make
  `pack()` take 30–45 s. Mid-stream SSE behavior is tested by driving
  `event_stream` directly (TestClient buffers streaming responses).
- **Acceptance criteria**:
  - [ ] Every bullet in "Testing Strategy" has a test.
  - [ ] Suites pass with no real network calls (tracing disabled except the
        recording-processor test, which uses `set_trace_processors`).

### Execution Order

| Wave | Agents | Mode | Depends On |
|------|--------|------|------------|
| 1 | Backend Engineer, Frontend Engineer | own branches (parallel) | — |
| 2 | Integration Engineer | own branch | Wave 1 merged |
| 3 | Test Engineer | own branch | Wave 2 merged |
| 4 | Bug, Security, Accessibility Reviewers | read-only (parallel) | Wave 3 merged |
| 5 | Meta-Reviewer | read-only | Wave 4 complete |
| 6 | Fix agents (if blockers) | own branch | Wave 5 verdict |

No Architecture Engineer: the API contract is the shared interface.

### Review Agents

#### Bug Reviewer
- **Agent type**: `voltagent-qa-sec:code-reviewer`
- **Scope**: `app/backend/app/`, `app/backend/packing/planner.py`,
  `app/backend/packing/render_svg.py`, `app/frontend/src/`
- **Checklist**:
  - [ ] Slot check-and-set under one lock; slot released on every path
        (success, failure, any exception incl. `BaseException`, cancel).
  - [ ] Terminal ordering: DB write (one transaction incl. `end`, guarded
        `WHERE status='running'`) → `end` in memory → release slot.
  - [ ] A terminal run's stream always sends `end`, even when the start
        index is past it.
  - [ ] SSE: ids, Last-Event-ID resume, ends on every terminal status and
        on disconnect, no leaked generators.
  - [ ] Cancel checked before extraction, each planning call, the packer,
        and finalize; status `cancelled` saved with partial stats.
  - [ ] `model.plan` is the model's own plan even when the packer wins;
        `final.source` matches the better `_rank`; packer-wins `final.plan`
        carries the "Code packer's plan used…" note.
  - [ ] Box renumbering applied before `final_plan`; `/render?box=N` and
        UI use the same index.
  - [ ] Prepared items/boxes used for validation, `final_plan`, rendering.
  - [ ] Frontend: dedupe by event id; EventSource closed on `end`/unmount;
        slider clamped on box change; reconnect to active run on load.
- **Severity guide**: blocker = wrong plan shown, stuck slot, lost run,
  crash on Sample A/B/C; warning = poor error message; suggestion = style.

#### Security Reviewer
- **Agent type**: `voltagent-qa-sec:security-auditor`
- **Scope**: `app/backend/app/`, `app/frontend/src/`
- **Checklist**:
  - [ ] Caps and string limits enforced server-side; prepared-unit re-check
        after extraction; duplicate block rules rejected; settings bounds.
  - [ ] POST requires `application/json` (415); Origin check; TrustedHost.
  - [ ] SQL parameterized; run ids parsed as UUID.
  - [ ] SVG names escaped; colors hex-only; `/render` and `/svg` send
        nosniff + CSP; UI uses `<img src>` only.
  - [ ] Notes, reasons, soft instructions, errors rendered as plain React
        text (no `dangerouslySetInnerHTML`, no markdown→HTML).
  - [ ] API key never logged/returned/traced; trace `model_config` is an
        allowlist; 401 errors return a generic message.
- **Severity guide**: blocker = injection or key exposure; accepted: no
  auth, tracked root `.env`, IP/UA sent to OpenAI traces.

#### Accessibility Reviewer
- **Agent type**: `voltagent-qa-sec:accessibility-tester`
- **Scope**: `app/frontend/src/`
- **Checklist**:
  - [ ] Table inputs labelled (row + column).
  - [ ] Slider and box picker keyboard operable and labelled.
  - [ ] Progress log `aria-live="polite"`.
  - [ ] SVG `<img>` has meaningful `alt`.
  - [ ] Winner/invalid not conveyed by color alone.
- **Severity guide**: blocker = control unusable by keyboard; warning =
  missing label; suggestion = contrast.

### Meta-Reviewer (Pragmatism Filter)
- **Agent type**: `voltagent-qa-sec:architect-reviewer`
- **Input**: All review agent findings
- **Filters**:
  - KEEP as blocker: production bugs, real security vulns, broken
    functionality, violated project conventions
  - DOWNGRADE to suggestion: best practices not already followed,
    future-proofing, style preferences
  - REJECT: scope creep, contradicts "What We're NOT Doing" (auth, Agents
    port, `.env` fix, packer speed, history editing), rewrites outside plan
- **Output**: Final Review Verdict with GO/NO-GO decision

---

## Phase 1: Backend core (copied pipeline + tracing + storage)

### Overview
Self-contained backend package that runs Samples A/B/C through the full
pipeline with a fake or real OpenAI client, traced and saved.

### Changes Required

#### 1. Project
**File**: `app/backend/pyproject.toml` — deps `fastapi`,
`uvicorn[standard]`, `openai`, `openai-agents`, `pydantic`, `matplotlib`,
`python-dotenv`; dev group `pytest`, `httpx`. `requires-python =
">=3.12"`. Commit `uv.lock`.

**File**: `.gitignore` (root) — add `app/backend/data/`,
`app/frontend/node_modules/`, `app/frontend/dist/`.

#### 2. Copied modules
**Directory**: `app/backend/packing/` — `__init__.py` + copies of
`schemas.py`, `tabular.py`, `precompute.py`, `validation.py`,
`settings.py`, `packer.py`, `planner.py`, `prompt_builder.py`,
`render_common.py`, `render_svg.py`. Changes (nothing else):
- Relative imports per Key Discoveries.
- `render_common.py`: `matplotlib.use("Agg")` before importing pyplot.
- `settings.extract_settings`: returns `(settings, usage, messages)`;
  `usage` is `response.usage` or `None` when skipped; `messages` the list
  sent (`[]` when skipped).
- `render_svg.render_svg(plan, upto=None, box=None)`: `box` is 1-based
  physical number; when given, draw only `plan.boxes[box-1]` at x_offset 0.
  Colors still from the whole plan (`item_colors(plan)`), legend lists all
  items, footer kept. `IndexError` → caller returns 404.
- `planner.plan_with_validation` new keyword args (defaults keep current
  behavior):
  - `on_event(dict)`: called after every call in the fix loop and the
    improve loop with `{"type": "round", "n", "kind", "valid",
    "errors_count"}`. In the improve loop compute
    `errs = validate_plan(...)` once into a local; when `_ask` returns
    `None`, `valid=False, errors_count=len(result.errors)`.
  - `should_stop()`: checked before every `_ask`; raises
    `RunCancelled(result)` (new exception in planner; `.result` is the
    partial `PlanResult` with history and token counts).
  - `on_call(model, model_config, usage)`: called after every `_ask` with
    an allowlisted `model_config` (`reasoning_effort` only) and
    `{"input_tokens", "output_tokens", "reasoning_tokens"}`. The app turns
    this into a trace span. `packing/` does not import `agents`.
  - `_ask` error handling, in this order (subclasses first —
    `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`,
    `BadRequestError` are `APIStatusError` → `APIError`):
    1. `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`,
       `BadRequestError` → re-raise (config problems: wrong key, model,
       or parameter; the run fails with that message).
    2. `LengthFinishReasonError` (not an `APIError`): add
       `exc.completion.usage` to the token counts and call `on_call`, then
       treat as no plan.
    3. `ContentFilterFinishReasonError`, other `APIError`
       (`APIConnectionError`/`APITimeoutError`, `RateLimitError`,
       `InternalServerError` …) → no plan.
    "No plan" = `result.errors = ["model call failed: <class name>:
    <message ≤ 300 chars>"]`, return `None`, so the packer can still win;
    the message stays visible in `model.errors`.
  - `PlanResult` gains `model_flat: FlatResponse | None`,
    `model_errors: list[str]` and `model_hints: list[str]`, set at the
    **top** of `_prefer_baseline` (before the `baseline is None` return)
    from `result.flat` / `result.errors` / `result.hints`.

#### 3. Config and samples
**File**: `app/backend/app/config.py`
- Load repo-root `.env` (`Path(__file__).resolve().parents[3] / ".env"`),
  `load_dotenv(override=False)`.
- `EXTRACTION_MODELS`: gpt-5.4-nano (reasoning True, kwargs
  `{"reasoning_effort": "none"}`), gpt-4.1-nano, gpt-4.1-mini,
  gpt-4o-mini (reasoning False, no kwargs). Default `gpt-4.1-nano`. If the
  real API rejects `"none"` for gpt-5.4-nano during Phase 5 manual testing,
  change that entry to `"minimal"`/`"low"` and note it in `app/CLAUDE.md`.
- `PLANNING_MODELS`: gpt-5.4-nano (default), gpt-5.4-mini; efforts
  low/medium/high, default low.
- `PRICES` per Key Discoveries; `LOOP = {"max_rounds": 3,
  "improve_rounds": 1}`; `CAPS` per contract.
- `MAX_COMPLETION_TOKENS = {"extraction": 2000, "planning": 16000}` passed
  as `max_completion_tokens` on every call (cost ceiling). If the
  gpt-5.4-nano extraction effort has to change from `"none"` to a
  reasoning level, raise `extraction` to 8000.
- `OPENAI_TIMEOUT_S = 120`, `OPENAI_MAX_RETRIES = 1` (client
  `OpenAI(timeout=..., max_retries=...)`).
- `SETTINGS_BOUNDS`: padding, padding_by_item, wall_clearance ≤ 12 in;
  `cut_step` ≥ 0.01 when set; block `size` ≤ 50; `max_boxes` ≤ 50.
- `DB_PATH = app/backend/data/runs.db` (override `PLAYGROUND_DB`).
- `FAKE_LLM = os.getenv("PLAYGROUND_FAKE_LLM") == "1"` (no side effects
  on import; `tracing.setup()` reads it).
- `ALLOWED_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}`;
  `MAX_BODY_BYTES = 256 * 1024`.

**File**: `app/backend/app/samples.py` — Samples A, B, C from notebook cells
5, 6, 7 in `InputItem`/`InputBox` shape (no ids; `dimensions` flattened;
`stock: null`), titles "A: Home goods", "B: Office / electronics", "C:
Nothing fits". Fixture facts for tests: A 5 items/5 boxes, B 6 items/5
boxes, C 4 items/4 boxes.

#### 4. API schemas
**File**: `app/backend/app/schemas.py` — Pydantic models for the contract
(`InputItem`, `InputBox`, `RunRequest`, `Run`, `RunSummary`, `Config`,
`Sample`, events) with the field rules: trimmed names, case-insensitive
uniqueness, control-char rejection, `max_length` limits, numeric bounds,
caps, model/effort membership. `to_pipeline_inputs(req) -> (items, boxes)`
assigns ids 1..n and builds notebook-shaped dicts (`dimensions: {x, y,
z}`; `stock` only when not null; **color `None` → `""`** so `unflatten`
works and tab10 is used).

#### 5. Pipeline
**File**: `app/backend/app/pipeline.py`
```python
def run_pipeline(req, client, *, run_id, meta, emit, should_stop,
                 partial: dict) -> dict:
    """Notebook pipeline; returns the Run result fields plus history and
    svgs. Raises RunCancelled, PipelineError (user-facing message).
    `partial` is filled as stages finish (settings, prepared, packer side,
    stats, extraction history; planning history/tokens from
    RunCancelled.result) so cancelled/failed runs still save them."""
    with trace("packing-playground run", trace_id=f"trace_{run_id.hex}",
               group_id=meta["client_ip"], metadata={   # all str values
                   "run_id": str(run_id), "request_id": meta["request_id"],
                   "client_ip": meta["client_ip"],
                   "user_agent": meta["user_agent"][:200],
                   "extraction_model": ..., "planning_model": ...,
                   "reasoning_effort": ... or ""}):
        ...  # stages, each in custom_span(name, data={counts/timings only})
```
Stages (separate functions; extract and plan are what the Agents port
replaces):
1. **extract**: `should_stop()`; `extract_settings(...,
   max_completion_tokens=...)`; validate against `SETTINGS_BOUNDS` and
   reject duplicate `blocks[].item_id` → `PipelineError`; emit
   `extracted`. Wrap the call in `generation_span` (usage only).
   `pydantic.ValidationError`, `TypeError`, `ValueError` from parsing the
   reply (truncation, refusal with `content=None`, malformed JSON, values
   violating existing `ge`/`gt` constraints) → `PipelineError("Could not
   interpret the instructions: <short reason>")`.
2. **prepare**: `prepare_items`, `prepare_boxes`, `rules_text`,
   `build_user_prompt`; messages with `SYSTEM_PROMPT`. Guard: Σ prepared
   `quantity` (placement units; a block counts as 1) ≤ `max_units` (cannot
   normally fire — blocks and padding never add units — kept as a cheap
   invariant). After `prepare_boxes`, any prepared box with y or z ≤ 0
   (wall clearance too large) → `PipelineError("Wall clearance leaves no
   room in box <name>")`. `ValueError` (unknown settings ids) →
   `PipelineError`. Emit `prepared`.
3. **packer**: `should_stop()`; `pack(...)` (not cancellable; 30–45 s at
   the caps — accepted); if `client` has a `context` attribute (FakeClient),
   set `client.context = (items_p, boxes_p, settings, baseline)`; emit
   `packer`.
4. **plan**: `plan_with_validation(..., settings=, baseline=,
   max_rounds=3, improve_rounds=1, on_event=emit, should_stop=,
   on_call=<opens a generation_span with model, model_config, usage>,
   reasoning_effort=..., max_completion_tokens=...)`.
5. **finalize**: `should_stop()` at its start only; model side =
   `result.model_flat` if `not result.model_errors`, else none (errors
   and hints from `model_errors` / `model_hints`); packer side = baseline if valid.
   Renumber boxes 1..n (remap placements) on each flat, then nested plans
   via `final_plan(flat, prepared items, prepared boxes, settings)`;
   `final` = `result.flat` renumbered (keeps the packer note when the
   packer won) or `None` if neither side is ok (`result.ok` false and no
   valid baseline); ranks via
   `planner._rank`; full SVGs via `render_svg` for each valid side; costs
   from `PRICES`; emit `chosen`.
Errors: `openai.AuthenticationError` (extract or plan) →
`PipelineError("OpenAI authentication failed — check OPENAI_API_KEY")`;
`PermissionDeniedError` / `NotFoundError` / `BadRequestError` →
`PipelineError("<class name>: <message ≤ 300 chars>")`; other
`openai.APIError` during extract → same form. Extraction history and
tokens are written to `partial` before re-raising.
Never put tracebacks or `repr(exc)` in user-facing errors (log them).

**File**: `app/backend/app/fake_llm.py` — `FakeClient` with
`chat.completions.create` and `.parse`, plus attributes `context` (set by
the pipeline) and `script: list[FlatResponse | Exception | None]`
(consumed per parse call; empty script → return the baseline from
`context`, i.e. the packer's plan; no second `pack()` call). Response shape:
`SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
parsed=flat, content=flat.model_dump_json(), refusal=None))],
usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=200,
completion_tokens_details=SimpleNamespace(reasoning_tokens=0)))`;
`create()` returns the same shape with `content=PackingSettings()
.model_dump_json()` (or a scripted settings JSON). Records received kwargs
for assertions.

#### 6. Store
**File**: `app/backend/app/store.py` — stdlib `sqlite3`, a connection per
call with `timeout=5`, `PRAGMA journal_mode=WAL` in `init()`.
```sql
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, trace_id TEXT, request_id TEXT,
  status TEXT NOT NULL, error TEXT,
  created_at TEXT NOT NULL, finished_at TEXT,
  client_ip TEXT, user_agent TEXT,
  title TEXT, extraction_model TEXT, planning_model TEXT,
  source TEXT, total_cost REAL,
  request_json TEXT NOT NULL, result_json TEXT,
  svgs_json TEXT, history_json TEXT, events_json TEXT
);
```
Functions: `init()`, `create(...)` (`events_json = '[]'`),
`append_event(run_id, events)` (writes the full list, outside the
manager lock), `finish(run_id, status, error, result, svgs, history,
events) -> bool` (single transaction, `UPDATE … WHERE run_id=? AND
status='running'`; returns False if the row was no longer running),
`set_status(run_id, status, error)` (minimal fallback, same guard),
`get`, `list(limit=100)` (summary columns only), `history`,
`svg(run_id, which)`, `mark_interrupted()` (status running →
interrupted, finished_at now, append `end` with `id = len(events)`;
NULL events → `[]`). All SQL parameterized.

#### 7. Tracing
**File**: `app/backend/app/tracing.py` — `setup()` (the only place that
calls `set_tracing_disabled`: on by default; off when
`OPENAI_AGENTS_DISABLE_TRACING=1` or `FAKE_LLM`), `on_call` hook
factory (opens `generation_span(model=..., model_config=allowlisted,
usage=...)`; the span opens after the call returns, so call latency is
recorded in the stage's `custom_span` data instead), `shutdown()` calling
`flush_traces()` in a thread with a 5 s timeout.

### Success Criteria

#### Automated Verification:
- [ ] `cd app/backend && uv sync && uv run pytest tests/test_pipeline.py`:
  - Samples A and B with `FakeClient` → `final` present, both sides ok,
    ranks set, SVGs start with `<svg`, boxes numbered 1..n.
  - Sample C → `final` present, 0 boxes, 4 leftover rows / 5 units.
  - Item with `color: null` → plan builds, tab10 color used.
  - Empty instructions → no extraction call, 0 extraction tokens.
  - Non-reasoning extraction model gets no `reasoning_effort` kwarg.
  - `render_svg(plan, box=2)` renders only box 2; `box=99` → IndexError.
  - Scripted invalid first reply → a `round` event with `valid: False`,
    then a valid fix round.
  - `should_stop` true before round 2 (scripted invalid first reply) →
    `RunCancelled`.
  - Scripted `openai.APIConnectionError` in planning → packer plan wins,
    `model.errors` has "model call failed: APIConnectionError…";
    `AuthenticationError` and `NotFoundError` → `PipelineError`;
    `LengthFinishReasonError` with usage → tokens counted, no model plan.
  - Extraction reply with malformed JSON / `content=None` →
    `PipelineError("Could not interpret the instructions…")`.
  - `wall_clearance` that zeroes a box's y → `PipelineError`.
  - Cancel during planning → `RunCancelled.result` carries history and
    tokens; `partial` holds settings, prepared, packer side.
  - Duplicate block rules or padding 1e9 from scripted extraction →
    `PipelineError`.
  - `model_flat` and `model_hints` kept when the packer wins (scripted
    worse model plan).
  - Model plan with box numbers 2 and 5 → renumbered 1, 2; cut notes say
    "box 1"/"box 2".
  - Recording processor (fixture saves the current processors, calls
    `set_trace_processors([recorder])` and `set_tracing_disabled(False)`,
    and restores both afterwards): trace id
    `trace_<hex>`, string metadata incl. client_ip/user_agent, generation
    spans with usage and no input/output.
- [ ] `git diff --stat labs/` empty.

#### Manual Verification:
- [ ] One real run of Sample A from a Python shell appears in the OpenAI
      traces dashboard with the expected id and metadata.

**Implementation Note**: pause after this phase for manual confirmation.

---

## Phase 2: Runs API (FastAPI, background job, SSE)

### Changes Required

#### 1. Run manager
**File**: `app/backend/app/runs.py`
- `RunManager` on `app.state`: `lock: threading.Lock`, `active: ActiveRun
  | None`. `ActiveRun`: `run_id`, `events: list[dict]`, `cancel:
  threading.Event`, `thread: threading.Thread`.
- `start(req, meta) -> run_id` (sync, no `await`): under the lock, if
  `active` → `RunBusy` (409); else set `active = ActiveRun(...)`. Then
  `store.create(...)` and start a
  `threading.Thread(target=self._work, daemon=True)` (kept on
  `ActiveRun`) inside one try: if either raises, clear `active` under the
  lock and re-raise.
- `_work`: whole body in `try/except BaseException/finally`. First
  `emit({"type": "started"})`. Build the client (`FakeClient` if `FAKE_LLM`, else `OpenAI(timeout=120)`; missing
  `OPENAI_API_KEY` → `failed` "OPENAI_API_KEY is not set"). `emit` appends
  `{id: len(events), t, ...}` under the lock, then (outside the lock)
  `store.append_event(run_id, events_copy)`. Outcome → status:
  result with `final` → `succeeded`; no valid plan → `failed` ("No valid
  plan from the model or the packer"); `RunCancelled` → `cancelled` (a
  cancel that arrives after finalize started does not change the
  outcome); `PipelineError` → `failed` with its
  message; any other exception → `failed` "Internal error" (logged with
  traceback). Cancelled/failed runs save what `partial` (and
  `RunCancelled.result`) collected.
- **Terminal ordering** (in `finally`): (1) build the `end` event once
  (`id = len(events)`, one `t`; the same dict is stored and appended);
  (2) `store.finish(...)` with status, result, svgs, history, events
  **including `end`**, in one guarded transaction (if it raises: log,
  retry once, then try `store.set_status`; if `finish` returns False the
  row was already marked interrupted — skip step 3); (3) append `end` to
  the in-memory list; (4) under the lock set `active = None`.
- `cancel(run_id)`: 409 unless it is the active run; set the event.
- `shutdown()` (async, called from lifespan): if active, set cancel,
  `await asyncio.to_thread(thread.join, 5)`, then
  `store.mark_interrupted()` for anything still running. Single-process
  only (`--workers 1`); documented.

#### 2. App and routes
**File**: `app/backend/app/main.py`
- Lifespan: `store.init()`, `store.mark_interrupted()`, `tracing.setup()`;
  shutdown: `runs.shutdown()`, `tracing.shutdown()`.
- Middleware stack, outermost first (added in reverse with
  `app.add_middleware`):
  1. `RequestIdMiddleware` (pure ASGI, not `BaseHTTPMiddleware`, which
     breaks SSE disconnect detection): `scope["type"] == "http"` only
     (lifespan/websocket pass through); incoming `X-Request-ID` if it
     parses as a UUID (normalized `str(uuid.UUID(v))`), else uuid4; stored
     via `scope.setdefault("state", {})["request_id"]`; header added on
     `http.response.start` without buffering. Wraps everything below, so
     TrustedHost 400s and guard responses carry it (unhandled-exception
     500s from `ServerErrorMiddleware` may not — accepted).
  2. `TrustedHostMiddleware(allowed_hosts=["localhost", "127.0.0.1"])`
     (port is ignored; the Vite proxy forwards `Host: localhost:5173`).
  3. `CORSMiddleware` with `ALLOWED_ORIGINS`.
  4. `GuardMiddleware` (pure ASGI, http only): `Content-Length` >
     `MAX_BODY_BYTES` → 413; `POST` whose
     `content_type.split(";")[0].strip().lower() != "application/json"` →
     415 (the cancel POST included; frontend sends `{}`); `Origin` present
     and not in `ALLOWED_ORIGINS` → 403. Responses are sent through the
     wrapped `send`.
- No gzip middleware.
- Run with `uvicorn app.main:app --host 127.0.0.1 --port 8000
  --timeout-graceful-shutdown 5` (open SSE connections otherwise delay
  shutdown until the run ends).

**File**: `app/backend/app/routes.py` — endpoints per contract. Requester
meta: `request.client.host`, `user-agent` header truncated to 200 (also in
SQLite), request id from state. `run_id` path params typed `uuid.UUID`
(404 for invalid via a custom handler). `box`/`upto` typed `int`. SSE per
the SSE section. `/render`: 409 while running; load stored plan, validate
as `Response`, `render_svg(plan, upto, box)`; `IndexError` → 404.
`GET /runs/{id}` while running: `events` from memory, other result fields
null.

### Success Criteria

#### Automated Verification:
- [ ] `uv run pytest` passes, incl. `tests/test_api.py` (TestClient
      created as `TestClient(app, base_url="http://localhost")` in
      `conftest.py` — the default `testserver` host fails TrustedHost;
      `PLAYGROUND_FAKE_LLM=1`, temp DB, small orders only) and
      `tests/test_sse.py` (drives `event_stream` directly with asyncio,
      short poll/heartbeat, FakeClient gated on a `threading.Event`):
  - `GET /config`, `/samples` (3 samples, no ids, fixture counts).
  - `POST /runs` Sample A → 202; SSE yields `id:` lines 0..n and ends with
    `end {status: succeeded}`; `GET /runs/{id}` validates against the
    contract types.
  - SSE resume: `Last-Event-ID: 2` starts at id 3; replay of a finished
    run ends with `end`; start index past the stored `end` still yields
    `end` (direct generator test).
  - Terminal race (direct generator test, store wrapper flips status
    between reads): still receives `end`; `GET /runs/{id}` right after
    `end` is terminal.
  - Events are persisted during the run (row's `events_json` grows before
    `end`).
  - `finish` after `mark_interrupted` returns False and does not overwrite
    `interrupted`.
  - Second `POST /runs` while active → 409.
  - Caps / limits: 21 item rows, 151 units, 2001 chars, duplicate names
    (case-insensitive), name with `\x00`, color > 32 chars, unknown model →
    422 with readable `msg`.
  - Hardening: POST without JSON content type → 415;
    `application/json; charset=utf-8` accepted; body > 256 KB → 413;
    foreign `Origin` → 403; bad `Host` → 400 (with `X-Request-ID`);
    invalid run id → 404; `plan=foo` → 422; `weight: Infinity` → 422.
  - Cancel → `cancelled` with partial stats; slot released; next run
    accepted.
  - Startup `mark_interrupted` flips a `running` row and appends `end`.
  - `/render?plan=model&box=1&upto=2` → `image/svg+xml` with nosniff + CSP;
    `box=99` → 404; `/svg?plan=packer` → attachment.
  - A name `<script>"'&` comes out escaped in the SVG.
  - Every response has `X-Request-ID`; run row stores it plus IP/UA.
- [ ] `uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
      --timeout-graceful-shutdown 5` starts;
      `curl 127.0.0.1:8000/api/health` → ok.

#### Manual Verification:
- [ ] `curl -N 127.0.0.1:8000/api/runs/<id>/events` shows live events for
      a real run.

**Implementation Note**: pause for manual confirmation.

---

## Phase 3: Frontend — editor and run flow

### Changes Required

**Scaffold**: `cd app && npm create vite@latest frontend -- --template
react-ts --no-interactive --no-immediate --no-eslint`, then `cd frontend &&
npm install` (no linter required); Tailwind via `@tailwindcss/vite`; Vitest + `@testing-library/
react` + `@testing-library/jest-dom` + `@testing-library/user-event` +
`jsdom`. `vite.config.ts` uses `defineConfig` from `vitest/config` with
`test: {environment: "jsdom", setupFiles: ["src/test/setup.ts"]}`,
`server: {port: 5173, strictPort: true}` (the backend only allows Origin
:5173) and proxy `/api` → `http://127.0.0.1:8000`. `src/test/setup.ts`
imports `@testing-library/jest-dom/vitest` and installs `FakeEventSource`
(`src/test/fakeEventSource.ts`) with `vi.stubGlobal("EventSource", …)`:
static `CONNECTING=0/OPEN=1/CLOSED=2`, `readyState`, `addEventListener`,
`close`, `onerror`, and a test helper `emit(type, data, id)` /
`fail()`; `afterEach(vi.unstubAllGlobals)` then re-stub. Scripts: `dev`, `build` (template),
`typecheck` = `tsc -b` (template tsconfigs already set `noEmit`; if the
scaffold differs use `tsc -p tsconfig.app.json --noEmit`), `test` =
`vitest run`. Add `vitest/globals` types to `tsconfig.app.json` if globals
are used.

**Files**:
- `src/api/types.ts` — contract types verbatim, with a comment on `Rank`.
- `src/api/client.ts` — `getConfig`, `getSamples`, `createRun`,
  `listRuns`, `getActiveRun`, `getRun`, `cancelRun` (POST `{}` JSON),
  `renderUrl(runId, plan, box, upto?)`, `svgDownloadUrl(runId, plan)`,
  `subscribe(runId, onEvent, onClosed, fromId?): () => void` per the SSE
  client rules (incl. the attach rule). `ApiError` with `status: number`
  and `messages: string[]`: normalize `detail` (string →
  `[detail]`; array → `loc.slice(1).join(".") + ": " + msg` with the
  "Value error, " prefix removed; non-JSON body → `"<status> <statusText>"`).
- `src/components/SamplePicker.tsx` — A/B/C select; loading replaces
  tables and instructions (`window.confirm` if edited).
- `src/components/ItemsTable.tsx` — columns name, color (text + swatch;
  swatch uses `CSS?.supports?.("color", v)` and shows "auto" when empty or
  unsupported; helper text "CSS name or hex; unrecognized colors are
  auto-assigned"), weight (lb), x, y, z (in), qty; add/remove rows; inline
  validation from `config.caps` (rows, Σ qty, unique trimmed names
  case-insensitive, positive numbers, empty cell invalid); live unit total
  vs cap.
- `src/components/BoxesTable.tsx` — name, weightMax, x, y, z, stock
  (blank → null).
- `src/components/InstructionsBox.tsx` — textarea + counter vs cap.
- `src/components/ModelSettings.tsx` — extraction model; planning model +
  effort; defaults from `config.defaults`; read-only "max rounds 3 ·
  improve rounds 1".
- `src/components/RunControls.tsx` — Pack (disabled until config loaded,
  while a run is active, or inputs invalid), Cancel (shows "Cancelling… —
  takes effect before the next model call"), progress log
  (`aria-live="polite"`) with one line per event ("Round 2 (fix):
  invalid, 5 errors"; "Code packer: 2 boxes — may take up to ~45 s on
  large orders" shown while waiting after `prepared`).
- `src/App.tsx` — layout (left editor, right result, history drawer);
  config/samples load with error banner + retry; on load `getActiveRun` and
  attach (SSE attach rule); 409 on Pack (`ApiError.status === 409`) →
  message "A run is already in progress" with "Show it" (attach to the
  active run). Effort select hidden for models without `efforts`.

### Success Criteria

#### Automated Verification:
- [ ] `npm run typecheck`, `npm run build`, `npm test` pass.
- [ ] Tests — component tests `vi.mock("../api/client")`;
      `client.test.ts` uses the real client with `vi.stubGlobal("fetch")`
      and the fake EventSource. Covered: loading
      Sample B (fixture: 6 items, 5 boxes) fills the tables; duplicate names
      disable Pack; request body matches `RunRequest` (no ids, blank stock
      → null); `detail` normalization for string, array, and non-JSON;
      `subscribe` registers all 7 listeners, dedupes ids, closes on `end`,
      and on `error` refetches the run and closes if terminal; attach
      seeds from `run.events` and passes `?from=`; progress log renders
      pushed events. Typed fixtures (`as const` / annotated) for `Config`.

#### Manual Verification:
- [ ] Tables are comfortable to edit with keyboard only.

---

## Phase 4: Frontend — results and history

### Changes Required

- `src/components/RunView.tsx` — status badge (text + color), run id
  (copyable), created time, stats per call + total, timings, error message;
  tolerates every nullable field (failed/cancelled/interrupted runs).
- `src/components/SettingsPanel.tsx` — non-default settings fields as a
  list; `soft_instructions` separately; "defaults" if none.
- `src/components/PreparedItemsTable.tsx` — id, name, x/y/z, weight, qty,
  flags.
- `src/components/PlansSideBySide.tsx` — two `PlanPanel`s (Model, Code
  packer); winner (`run.final?.source`) marked with the text "Chosen" plus
  a border.
- `src/components/PlanPanel.tsx` — rank labelled (leftover units, boxes,
  shipped volume in³, fill height in), box picker 1..`plan.boxes.length`
  (label "Box N — <type name>"), slider 1..`plan.boxes[N-1].items.length`
  (default max; clamped when the box changes), `<img
  src={renderUrl(...)} alt="Box N of the model plan, steps 1–K">` (or
  "code packer plan") with `onError` fallback text; "Download SVG" link
  (`svgDownloadUrl`); invalid side → error list, no image; 0 boxes → "No
  boxes used". Images only when the run is terminal.
- `src/components/NotesLeftovers.tsx` — `final.plan` notes (numbered) and
  leftovers with reasons, as plain text.
- `src/components/HistoryList.tsx` — `listRuns`: time, title, models,
  status, source, cost (nullable); click → `getRun` → `RunView`
  (read-only). Refreshes after `end`.

### Success Criteria

#### Automated Verification:
- [ ] `npm test`: invalid model side shows errors and no `<img>`; box
      picker + slider produce the right `renderUrl` and clamp on box
      change; history click loads a run; "Chosen" label present; failed run
      with null `final`/`model`/`packer` renders without crashing.

#### Manual Verification:
- [ ] Sample B shows two boxes on at least one side; switching box/step
      updates the image without lag.
- [ ] Past runs open correctly after a server restart.

---

## Phase 5: Integration and docs

### Changes Required
- Run both servers with `PLAYGROUND_FAKE_LLM=1`; fix any mismatches.
- `app/CLAUDE.md`: architecture (request → RunManager → daemon thread →
  pipeline stages → SQLite; SSE), key files, contract summary, env vars
  (`OPENAI_API_KEY` from root `.env`, `PLAYGROUND_FAKE_LLM`,
  `PLAYGROUND_DB`, `OPENAI_AGENTS_DISABLE_TRACING`), tracing (trace id =
  `trace_<run_id hex>`, metadata incl. IP/UA **sent to OpenAI**), box
  numbering rule (model-written notes may cite old numbers), packer
  timing at the caps (accepted), cancel is cooperative, run with
  `--timeout-graceful-shutdown 5` and one worker, `--reload` abandons an
  active run (marked interrupted on restart), `client_ip` is always
  127.0.0.1 behind the Vite proxy, "copy of labs is a one-time fork",
  planned Agents SDK port.
- Root `CLAUDE.md`: add `- [app/CLAUDE.md](app/CLAUDE.md) — packing playground (FastAPI backend + React frontend)` under Subsystem Documentation.
- `README.md`: run instructions (`cd app/backend && uv run uvicorn
  app.main:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5`;
  `cd app/frontend && npm install && npm run dev`).

### Success Criteria

#### Automated Verification:
- [ ] Backend and frontend suites pass.
- [ ] With fake LLM: a scripted run via `curl` for each sample ends
      `succeeded`.

#### Manual Verification:
- [ ] One real run per sample (gpt-4.1-nano extraction, gpt-5.4-nano low
      planning): A and B show plans; C shows 0 boxes, 4 leftover rows;
      cost matches tokens × prices.
- [ ] A real run with gpt-5.4-nano extraction confirms
      `reasoning_effort="none"` is accepted (else adjust config, see
      Phase 1 §3).
- [ ] Each run's trace is visible in the OpenAI dashboard under
      `trace_<run_id hex>` with IP/UA metadata.

---

## Testing Strategy

### Unit Tests (backend)
- Input → pipeline dicts (ids 1..n, stock omitted when null, color
  `None` → `""`).
- Validators: caps, unique names, control chars, string limits, bounds,
  model/effort membership.
- `extract_settings` triple return; kwargs per model type;
  `max_completion_tokens` passed.
- Settings bounds, duplicate block rules, extraction parse failures,
  zero/negative prepared box dims → `PipelineError`.
- Planner copy: `model_flat`/`model_errors`/`model_hints` on all paths;
  `on_event` per fix and improve call; `should_stop` → `RunCancelled(result)`;
  `on_call` allowlist; transient API errors → no model plan; auth/
  permission/not-found/bad-request re-raised (except order);
  `LengthFinishReasonError` usage counted.
- Box renumbering (gapped numbers 2, 5 → 1, 2; notes match).
- `render_svg(box=N)`; cost math (reasoning not double-counted).

### Integration Tests (backend; TestClient + FakeClient + temp DB; ≤ 20 units)
- Full run per sample; SSE ids, resume, replay, end-past-start-index,
  terminal race (direct generator), incremental event persistence,
  guarded `finish`.
- 409 busy, cancel, interrupted on startup, OpenAI errors, missing key.
- Hardening: 415 (+ charset accepted), 413, Origin 403, Host 400,
  invalid UUID 404, `plan` literal 422, inf/NaN 422, SVG escaping,
  security headers, download filename.
- Trace via recording processor (tracing otherwise disabled in
  `conftest.py`).

### Frontend Tests (Vitest + RTL; mocked `client.ts`; fake EventSource)
- Editor: sample load, add/remove rows, validation, request body.
- Client: `detail` normalization + `status`; `subscribe`
  listeners/dedupe/close/error-refetch; attach with `?from=`.
- Run flow: log lines; Pack disabled during a run; Cancel state; 409
  attach.
- Results: side by side, invalid side, slider/box URL and clamping,
  nullable runs, history view.

### Manual Testing Steps
1. Start both servers; load Sample A; Pack with defaults; watch progress;
   check stats and both plans.
2. Sample A with "keep mugs upright, 0.125 in padding": settings panel
   shows upright + padding; prepared dims grew by 0.25.
3. Sample B: step through each box with the slider.
4. Sample C: 0 boxes, 4 leftover rows with reasons.
5. Start a run, close the tab, reopen: progress reconnects without
   duplicate lines; history shows the run when done.
6. Start a run and Cancel: "Cancelling…", then `cancelled`.
7. Kill the server mid-run, restart: run shows `interrupted`.

## Performance Considerations
- Runs: 20–90 s of model calls; one at a time.
- `pack()` at the caps: 29–43 s, uncancellable, holds the GIL (SSE polls
  and API responses slow during it). Accepted by user; the UI warns.
- `/render` re-renders per slider move (ms-scale) with
  `Cache-Control: max-age=3600`.
- SQLite WAL; listing selects summary columns only.

## Migration Notes
New code only. `labs/` untouched. SQLite file created on startup.

## References
- Handoff: `ai-tools/thoughts/shared/handoffs/general/2026-10-04_06-54-04_llm-packing-planner.md`
- Pipeline docs: `labs/CLAUDE.md`
- Notebook pipeline: `labs/manager.ipynb` cells 9, 12, 23; samples 5–7
- Planner: `labs/planner.py:104`, `:131-211`; settings
  `labs/settings.py:182`, `:515`; schemas `labs/schemas.py:63`, `:189`
- Agents SDK tracing: https://openai.github.io/openai-agents-python/tracing/
