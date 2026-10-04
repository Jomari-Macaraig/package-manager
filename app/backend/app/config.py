"""Static configuration: models, prices, caps, limits, paths."""

import os
from pathlib import Path

from dotenv import load_dotenv

# repo root = app/backend/app/config.py -> parents[3]
REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(REPO_ROOT / ".env", override=False)

Efforts = ("low", "medium", "high")

# id, label, reasoning, extra create kwargs
EXTRACTION_MODELS: list[dict] = [
    {"id": "gpt-5.4-nano", "label": "gpt-5.4-nano", "reasoning": True,
     "kwargs": {"reasoning_effort": "none"}},
    {"id": "gpt-4.1-nano", "label": "gpt-4.1-nano", "reasoning": False,
     "kwargs": {}},
    {"id": "gpt-4.1-mini", "label": "gpt-4.1-mini", "reasoning": False,
     "kwargs": {}},
    {"id": "gpt-4o-mini", "label": "gpt-4o-mini", "reasoning": False,
     "kwargs": {}},
]
DEFAULT_EXTRACTION_MODEL = "gpt-4.1-nano"

PLANNING_MODELS: list[dict] = [
    {"id": "gpt-5.4-nano", "label": "gpt-5.4-nano", "efforts": list(Efforts)},
    {"id": "gpt-5.4-mini", "label": "gpt-5.4-mini", "efforts": list(Efforts)},
]
DEFAULT_PLANNING_MODEL = "gpt-5.4-nano"
DEFAULT_REASONING_EFFORT = "low"

# USD per 1M tokens: (input, output). Reasoning tokens are already inside
# completion_tokens.
PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.4-nano": (0.20, 1.25),
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4o-mini": (0.15, 0.60),
}

LOOP = {"max_rounds": 3, "improve_rounds": 1}

CAPS = {"max_item_rows": 20, "max_box_rows": 10, "max_units": 150,
        "max_instruction_chars": 2000}

# Planning by reasoning effort: reasoning tokens count against the limit,
# and medium/high reasoning on a mixed order spent all of 16k (2026-10-05).
MAX_COMPLETION_TOKENS = {
    "extraction": 2000,
    "planning": {"low": 16000, "medium": 32000, "high": 48000},
}

# ~250 output tokens/s seen on gpt-5.4-mini: 48k tokens ≈ 3+ minutes
OPENAI_TIMEOUT_S = 300
OPENAI_MAX_RETRIES = 1

# Pipeline v2: per job, the allowed models, the default and the output limit
# (strategy: by reasoning effort, from MAX_COMPLETION_TOKENS["planning"]).
JOBS = {
    "extract": {"models": [m["id"] for m in EXTRACTION_MODELS],
                "default": {"model": "gpt-4.1-nano",
                            "reasoning_effort": None},
                "max_tokens": 2000},
    "strategy": {"models": [m["id"] for m in PLANNING_MODELS],
                 "default": {"model": "gpt-5.4-nano",
                             "reasoning_effort": "low"}},
    "check": {"models": [m["id"] for m in EXTRACTION_MODELS],
              "default": {"model": "gpt-4.1-mini", "reasoning_effort": None},
              "max_tokens": 1500},
    "notes": {"models": [m["id"] for m in EXTRACTION_MODELS],
              "default": {"model": "gpt-4.1-nano", "reasoning_effort": None},
              "max_tokens": 1500},
}
BUDGET = {"usd": 0.02, "seconds": 30}

# Bounds on settings returned by the extraction model
SETTINGS_BOUNDS = {
    "padding": 12.0,
    "wall_clearance": 12.0,
    "cut_step_min": 0.01,
    "block_size": 50,
    "max_boxes": 50,
}

DB_PATH = Path(os.getenv("PLAYGROUND_DB")
               or Path(__file__).resolve().parents[1] / "data" / "runs.db")

# Read at call time by callers that need to react to test overrides
FAKE_LLM = os.getenv("PLAYGROUND_FAKE_LLM") == "1"

ALLOWED_ORIGINS = {"http://localhost:5173", "http://127.0.0.1:5173"}
MAX_BODY_BYTES = 256 * 1024
