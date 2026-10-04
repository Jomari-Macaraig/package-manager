"""OpenAI Agents SDK tracing hooks.

Only agents.trace and spans are used (no Agent/Runner). packing/ stays free
of `agents` imports; it reaches this module through the on_call hook.
"""

import logging
import os
import threading
from collections.abc import Callable, Mapping
from typing import Any

from agents import (flush_traces, generation_span,  # type: ignore[attr-defined]
                    set_tracing_disabled)

from . import config

log = logging.getLogger(__name__)

ALLOWED_MODEL_CONFIG = ("reasoning_effort",)


def setup() -> None:
    """The only place that enables/disables tracing: on by default, off with
    OPENAI_AGENTS_DISABLE_TRACING=1 or the fake LLM."""
    disabled = (os.getenv("OPENAI_AGENTS_DISABLE_TRACING") == "1"
                or config.FAKE_LLM)
    set_tracing_disabled(disabled)


def record_generation(model: str, model_config: Mapping[str, Any] | None,
                      usage: Mapping[str, Any]) -> None:
    """Record one model call as a generation span (usage only, no
    input/output). The span opens after the call returns, so latency goes
    into the stage's custom_span data instead."""
    allowed = {k: v for k, v in (model_config or {}).items()
               if k in ALLOWED_MODEL_CONFIG}
    with generation_span(model=model, model_config=allowed,
                         usage=dict(usage)):
        pass


def on_call_hook(
    tally: Callable[[str, dict[str, Any], dict[str, int]], None] | None = None,
) -> Callable[[str, dict[str, Any], dict[str, int]], None]:
    """Factory for planner's on_call; `tally` sees the same arguments."""
    def on_call(model, model_config, usage) -> None:
        if tally is not None:
            tally(model, model_config, usage)
        record_generation(model, model_config, usage)
    return on_call


def shutdown() -> None:
    """Flush pending traces, waiting at most 5 s."""
    thread = threading.Thread(target=flush_traces, daemon=True)
    thread.start()
    thread.join(5)
    if thread.is_alive():
        log.warning("trace flush timed out")
