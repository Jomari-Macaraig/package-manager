# app — Packing Playground

Local web app: edit items/boxes (Samples A–E preloaded; D is the original
dispenser order from the notebook; E is a two-box order where the packer
ships 23% more than a one-layer Medium + Large plan), write packing
instructions, pick models, run the LLM packing pipeline, watch live progress,
compare the model's plan with the code packer's plan, browse past runs.

## Architecture

POST /api/runs -> `RunManager.start` (one run at a time, slot taken under a
lock) -> daemon worker thread -> `run_pipeline` stages (extract, prepare,
packer, plan, finalize) -> events appended in memory and persisted to SQLite
as they happen -> final state saved in one guarded transaction, then `end`
event, then the slot is released. SSE (`GET /runs/{id}/events`) polls the
active run's in-memory event list (or replays the stored list for finished
runs). The React app talks only to `/api`.

## Key files

Backend (`app/backend`, uv project; run from that directory):
- `app/main.py` lifespan, middleware stack; `app/middleware.py` request id,
  guard (413/415/Origin 403), 422 handler without `input` echo
- `app/routes.py` endpoints; `app/sse.py` `event_stream` async generator
- `app/runs.py` `RunManager` (`client_factory` hook, cooperative cancel)
- `app/pipeline.py` stages + trace; `app/store.py` SQLite; `app/tracing.py`
- `app/schemas.py` request/response models; `app/config.py`; `app/samples.py`
- `app/fake_llm.py` `FakeClient` (`gate`, `create_script`, `script`)
- `packing/` the packing pipeline — single source of truth, also imported by
  the labs notebook (editable path dependency of the root project). See
  [backend/packing/CLAUDE.md](backend/packing/CLAUDE.md). `app/__init__.py`
  sets matplotlib's Agg backend for the server.

Frontend (`app/frontend`, Vite + React + TS + Tailwind):
- `src/api/types.ts` contract types (from the plan), `src/api/client.ts`
  every network call + SSE `subscribe`
- `src/hooks/useRunFlow.ts` run/attach/cancel flow; `src/components/` UI
- `src/components/MetricsTable.tsx` "Metrics" section of every run: model
  vs code packer per metric (value, delta, better/worse/same) and a tally,
  from `run.comparison` (`packing.metrics.compare`, computed in
  `routes._comparison` on read, so older runs show it too).
- `src/components/PromptsPanel.tsx` "Prompts" section of a run: on "Show
  prompts" it loads `GET /runs/{id}/history` and lists each message (System
  prompt, User prompt incl. the Starting plan, Model reply N, Feedback N)
  collapsed, with a copy button. History is saved when a stage finishes,
  so a running run shows nothing until then.

## Contract summary

Normative spec: `docs/plans/2026-10-04-packing-playground.md`. Prefix `/api`:
`GET /health, /config, /samples, /runs, /runs/active, /runs/{id},
/runs/{id}/history, /runs/{id}/events (SSE), /runs/{id}/render?plan=&box=&upto=,
/runs/{id}/svg?plan=`; `POST /runs` (202 `{run_id}`, 409 busy, 415 not JSON,
422 invalid), `POST /runs/{id}/cancel` (202, 409 if not active; body `{}` with
JSON content type). SSE frames: `id: n`, `event: <type>`, `data: <RunEvent
JSON with id>`; resume via `Last-Event-ID` or `?from=`; a terminal run always
ends with `end`. Errors are `{"detail": ...}`.
Added after the plan: `planning.start` in the run request, `"improve"`
(default; the model edits the packer's plan), `"scratch"`, or `"guide"`
(the model picks box types, item order and rotations; code places, with
`GUIDE_SYSTEM_PROMPT`); the UI has a "Planning start" select and the run
view shows it. The prompt is built after the packer so it can include
`box_estimates` (`pipeline._messages`). Runs stored before the
field existed read back as `"scratch"` (`routes._request`).

## Env vars

- `OPENAI_API_KEY` from the repo-root `.env` (gitignored, not tracked)
- `PLAYGROUND_FAKE_LLM=1` use `FakeClient` (never calls OpenAI; tracing off)
- `PLAYGROUND_DB` SQLite path (default `app/backend/data/runs.db`)
- `OPENAI_AGENTS_DISABLE_TRACING=1` disable trace export
- `API_TARGET` (frontend dev server) Vite proxy target, default
  `http://127.0.0.1:8000`

## Operational notes

- Tracing: trace id `trace_<run_id hex>`; metadata includes run id, request
  id, models, **client IP and User-Agent, which are sent to OpenAI**. Spans
  carry usage only, no prompts/completions.
- Box numbering: finalize renumbers each plan's boxes 1..n before
  `final_plan`; physical box N = `plan.boxes[N-1]` everywhere (notes,
  `/render?box=N`, UI picker). Model-written notes may still cite old numbers.
- `pack()` is pure Python, holds the GIL, is not cancellable: 29-43 s at the
  caps (150 units, 10 box types); accepted.
- Cancel is cooperative: checked before extraction, each planning call, the
  packer and finalize.
- Giving up is an error: a model plan that leaves over units the packer
  placed fails validation (see `backend/packing/CLAUDE.md`).
- Run with ONE worker and `--timeout-graceful-shutdown 5` (open SSE
  connections otherwise delay shutdown). `--reload` abandons an active run;
  it is marked `interrupted` on the next start.
- Planning output limit follows reasoning effort
  (`config.MAX_COMPLETION_TOKENS["planning"]`: low 16k, medium 32k, high
  48k; reasoning tokens count against it) and the OpenAI timeout is 300 s.
  A call that hits the limit (`LengthFinishReasonError`) ends the planning
  loop with no retry (`PlanResult.cut_off`); the packer's plan is used.
  Before: gpt-5.4-mini medium spent 3 × 16k tokens, $0.23, for nothing.
- `client_ip` is always 127.0.0.1 behind the Vite proxy.
- Only Origins http://localhost:5173 / http://127.0.0.1:5173 and hosts
  localhost / 127.0.0.1 are accepted; the Vite dev port is strict.
- Planned follow-up: port extract/plan to the OpenAI Agents SDK
  (`Agent`/`Runner`); not part of this app yet.

## Pipeline v2 (default) and the Evaluation page

Plan: `docs/plans/2026-10-05-pipeline-v2-narrow-llm-jobs-with-evals.md`. The
pipeline itself is in [backend/packing/CLAUDE.md](backend/packing/CLAUDE.md)
("Pipeline v2"). v2 is the default in the UI; the header toggle switches to v1
(saved in `localStorage["packing-playground.pipeline"]`, a saved `"v1"` is
kept). v1 is unchanged.

Backend (`app/backend/app`):
- `routes_v2.py` (prefix `/api/v2`): `GET config | samples (A-H) | runs |
  runs/active | runs/{id} | runs/{id}/events (SSE) | runs/{id}/render?plan=
  final|model|packer&box&upto | runs/{id}/svg?plan= |
  runs/{id}/jobs/{job}/messages | eval/summary?job&since&include_fake`;
  `POST runs` (202; 409 `{"detail": "A run is already in progress", "kind":
  "v1"|"v2"}`), `POST runs/{id}/cancel`, `PUT runs/{id}/feedback/{job}`
  (`FeedbackIn`: rating up/down/null, comment <= 2000, `wrong_verdicts` for
  the check job only; 404 unknown run/job row, 409 run not finished, 422 a
  skipped job or a bad index).
- `schemas_v2.py` is the contract (`RunV2`, `JobView`, `EvalGroup`, ...);
  `src/api/typesV2.ts` mirrors it 1:1 (`output`/`answer` are `unknown`;
  `PlanSideV2.rank` is `number[] | null`, the same 4 numbers as v1's `Rank`).
- `pipeline_v2.py` `V2_ADAPTER` + `run_pipeline_v2`: builds `JobConfig`s from
  the request and `config.JOBS`, costs per job (`config.PRICES`), totals vs
  `config.BUDGET` ($0.02 / 30 s, shown as "over budget", not enforced),
  `PlanSideV2`s, final plan, SVGs.
- `runs.py`: `RunManager(adapter, slot, kind)` is generic; v1 and v2 managers
  share one `Slot`, so only one run of either kind at a time. `/runs/active`
  only reports a run of its own kind. v2 `outcome` is always `succeeded` when
  the pipeline returns (a missing plan is `source: "none"`).
- `store_v2.py` (same SQLite file): `runs_v2` (request, result, svgs, events),
  `job_calls` (one row per run and job, written in the same transaction as the
  guarded `finish`, so interrupted runs and the `set_status` fallback have
  none), `feedback` (per run and job). `finish(..., jobs)` also takes `svgs`;
  `set_fake` marks a run. `eval_rows` + Python grouping by (job,
  prompt_version, model, effort) give `EvalGroup`s: rates exclude skipped
  rows and fake runs unless `include_fake=true`; `model_won_rate` is run-level
  (strategy only); `wrong_verdict_rate` is check only.
- Fake runs: `PLAYGROUND_FAKE_LLM=1` -> `FakeClient`; v2 runs are flagged
  `fake` and hidden from the Evaluation page by default.

Frontend (`app/frontend/src`): `api/v2.ts` (calls, `subscribeV2`, `v2Api` for
`hooks/useRunFlow.ts`), `components/RunViewV2.tsx` (run, final plan with
per-box notes, Jobs, Plans side by side, Metrics, Preferences and verdicts
with "Mark wrong", extracted settings), `JobsPanel`/`JobCard` (checks, 4
cards, thumbs + comment feedback, messages), `JobSettings` (model/effort per
job), `EvaluationPage` (tab; one table per job; "Include fake runs" checkbox).

Fixture sync: `app/backend/tests/fixtures/run_v2.json` (deterministic fake
Sample G run) is copied to `app/frontend/src/test/run_v2.json`, used as
`runV2` in `src/test/fixturesV2.ts`; a backend test fails when the copies
differ. Regenerate with `UPDATE_FIXTURES=1 uv run pytest
tests/test_v2_api.py -k shared_fixture`, then copy the file.

Smoke test (fake LLM, temp DB, no OpenAI): `cd app/backend && uv run python
scripts/smoke_v2.py` posts A-H to v2 and A-E to v1 and checks status, 4 job
rows with `fake`, SVG renders, feedback and the eval summary.

## Wave 1 deviations from the plan

- Custom 422 handler that does not echo `input`; `store.events()` helper;
  SSE sends a synthetic failed `end` after 2 s when a running row has no
  active slot; `RunManager.client_factory`; FakeClient `gate` /
  `create_script` for tests.
- Integration: Vite proxy target configurable via `API_TARGET`.

## Verified end to end (fake LLM, through the Vite proxy)

Samples A (1 box), B (2 boxes), C (0 boxes, 4 leftover rows, 5 units) all end
`succeeded`; render for every box/step, svg download, history, 409 busy,
cancel (202 then `cancelled`, 409 when not active) behave per contract.
