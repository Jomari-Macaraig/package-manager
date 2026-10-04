# package-manager

LLM-based box packing planner (Python 3.12, managed with `uv`).

## Subsystem Documentation

- [app/backend/packing/CLAUDE.md](app/backend/packing/CLAUDE.md) — the packing pipeline (single source of truth): schemas, tabular input, precompute, validation, planner loop, prompt builder, settings, code packer, SVG renderer
- [labs/CLAUDE.md](labs/CLAUDE.md) — experiment notebook (imports `packing`), old system prompts, matplotlib/Plotly/PyVista/vedo/three.js renderers, token/cost report, sample cases
- `labs/_archive/evaluation/` — archived Paccurate comparison (replaced by the sample cases in `labs/manager.ipynb`; see labs/CLAUDE.md)
- [app/CLAUDE.md](app/CLAUDE.md) — packing playground (FastAPI backend + React frontend)
