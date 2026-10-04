# package-manager

LLM-based box packing planner (Python 3.12, managed with `uv`).

## Subsystem Documentation

- [app/backend/packing/CLAUDE.md](app/backend/packing/CLAUDE.md) — the packing pipeline (single source of truth): schemas, tabular input, precompute, validation, planner loop, prompt builder, settings, code packer, SVG renderer
- [labs/CLAUDE.md](labs/CLAUDE.md) — experiment notebook (imports `packing`), old system prompts, matplotlib/Plotly/PyVista/vedo/three.js renderers, token/cost report, sample cases
- `labs/_archive/evaluation/` — archived Paccurate comparison (replaced by the sample cases in `labs/manager.ipynb`; see labs/CLAUDE.md)
- [app/CLAUDE.md](app/CLAUDE.md) — packing playground (FastAPI backend + React frontend); includes the v2 API, storage, Evaluation page, shared run slot, fake runs and the smoke script
- Pipeline v2 (four narrow LLM jobs with per-job evaluation, the default in the playground): jobs, checks, choose rule, preferences, replay in [app/backend/packing/CLAUDE.md](app/backend/packing/CLAUDE.md) ("Pipeline v2"); plan in `docs/plans/2026-10-05-pipeline-v2-narrow-llm-jobs-with-evals.md`
