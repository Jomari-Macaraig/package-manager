# app — Packing Playground

Local web app: edit items/boxes (Samples A, B, C preloaded), write packing
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
- `packing/` one-time fork of 10 `labs/` modules with relative imports; no
  sync with `labs/` afterwards. Does not import `agents` or FastAPI.

Frontend (`app/frontend`, Vite + React + TS + Tailwind):
- `src/api/types.ts` contract types (from the plan), `src/api/client.ts`
  every network call + SSE `subscribe`
- `src/hooks/useRunFlow.ts` run/attach/cancel flow; `src/components/` UI

## Contract summary

Normative spec: `docs/plans/2026-10-04-packing-playground.md`. Prefix `/api`:
`GET /health, /config, /samples, /runs, /runs/active, /runs/{id},
/runs/{id}/history, /runs/{id}/events (SSE), /runs/{id}/render?plan=&box=&upto=,
/runs/{id}/svg?plan=`; `POST /runs` (202 `{run_id}`, 409 busy, 415 not JSON,
422 invalid), `POST /runs/{id}/cancel` (202, 409 if not active; body `{}` with
JSON content type). SSE frames: `id: n`, `event: <type>`, `data: <RunEvent
JSON with id>`; resume via `Last-Event-ID` or `?from=`; a terminal run always
ends with `end`. Errors are `{"detail": ...}`.

## Env vars

- `OPENAI_API_KEY` from the repo-root `.env` (tracked in git, left as-is)
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
- Run with ONE worker and `--timeout-graceful-shutdown 5` (open SSE
  connections otherwise delay shutdown). `--reload` abandons an active run;
  it is marked `interrupted` on the next start.
- `client_ip` is always 127.0.0.1 behind the Vite proxy.
- Only Origins http://localhost:5173 / http://127.0.0.1:5173 and hosts
  localhost / 127.0.0.1 are accepted; the Vite dev port is strict.
- Planned follow-up: port extract/plan to the OpenAI Agents SDK
  (`Agent`/`Runner`); not part of this app yet.

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
