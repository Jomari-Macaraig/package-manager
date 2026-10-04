# package-manager

LLM-based box packing planner. `labs/` holds the notebook and pipeline
modules; `app/` holds the Packing Playground (FastAPI backend + React
frontend). See `app/CLAUDE.md` for architecture.

## Run the playground

Backend (port 8000, single worker):

    cd app/backend
    uv sync
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --timeout-graceful-shutdown 5

Frontend (port 5173, `/api` proxied to the backend):

    cd app/frontend
    npm install
    npm run dev

Open http://localhost:5173. The OpenAI key is read from the root `.env`
(`OPENAI_API_KEY`). To try it without calling OpenAI, start the backend with
`PLAYGROUND_FAKE_LLM=1`. If the backend is not on port 8000, start the
frontend with `API_TARGET=http://127.0.0.1:<port> npm run dev`.

## Tests

    cd app/backend && uv run pytest
    cd app/frontend && npm run typecheck && npm run build && npm test
