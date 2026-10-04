"""Shared pieces of the jobs: results, checks and the two call helpers.

Both helpers copy planner._ask's error handling: config errors (wrong key,
model or parameter) are re-raised and fail the run; everything else comes
back as an error string, with the usage of the call kept. Neither retries.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import openai
from pydantic import ValidationError

from ..planner import _CONFIG_ERRORS

VOLUME_MARGIN = 1.15  # a model plan may ship this much more than the packer's


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class JobConfig:
    model: str
    reasoning_effort: str | None = None  # sent as reasoning_effort when set
    max_completion_tokens: int = 2000
    # e.g. {"reasoning_effort": "none"} for gpt-5.4-nano on extract/check/notes
    extra_kwargs: dict = field(default_factory=dict)


@dataclass
class CallOutcome:
    parsed: Any = None
    error: str | None = None
    usage: dict[str, int] = field(default_factory=lambda: {
        "input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0})
    latency_s: float = 0.0
    reply: str | None = None  # the assistant message, for the history
    cut_off: bool = False  # hit max_completion_tokens (a retry would too)


@dataclass
class JobResult:
    job: str  # "extract" | "strategy" | "check" | "notes"
    status: str  # "ok" | "fallback" | "skipped"
    output: Any  # what the pipeline uses (answer or fallback), JSON-able
    answer: Any = None  # parsed model answer, if any
    checks: list[Check] = field(default_factory=list)
    model: str | None = None
    reasoning_effort: str | None = None
    prompt_version: str = ""
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    latency_s: float = 0.0
    error: str | None = None
    messages: list[dict] = field(default_factory=list)

    def add(self, outcome: CallOutcome) -> None:
        """Count one call's usage, latency and (if any) its reply."""
        self.calls += 1
        self.prompt_tokens += outcome.usage["input_tokens"]
        self.completion_tokens += outcome.usage["output_tokens"]
        self.reasoning_tokens += outcome.usage["reasoning_tokens"]
        self.latency_s = round(self.latency_s + outcome.latency_s, 3)
        if outcome.reply is not None:
            self.messages.append({"role": "assistant",
                                  "content": outcome.reply})


def skipped_result(job: str, cfg: JobConfig, prompt_version: str,
                   output: Any = None, error: str | None = None) -> JobResult:
    """A job that did not run; it still records the configured model and
    effort so evaluation groups it correctly."""
    return JobResult(job=job, status="skipped", output=output,
                     model=cfg.model, reasoning_effort=cfg.reasoning_effort,
                     prompt_version=prompt_version, error=error)


def failed_checks(checks: list[Check]) -> str:
    return ", ".join(c.name for c in checks if not c.passed)


def _kwargs(cfg: JobConfig) -> dict[str, Any]:
    kwargs = dict(cfg.extra_kwargs)
    if cfg.reasoning_effort is not None:
        kwargs["reasoning_effort"] = cfg.reasoning_effort
    return {**kwargs, "max_completion_tokens": cfg.max_completion_tokens}


def _usage(usage) -> dict[str, int]:
    counts = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    if usage:
        counts["input_tokens"] = usage.prompt_tokens or 0
        counts["output_tokens"] = usage.completion_tokens or 0
        details = getattr(usage, "completion_tokens_details", None)
        if details and details.reasoning_tokens:
            counts["reasoning_tokens"] = details.reasoning_tokens
    return counts


def _guarded(call: Callable[[CallOutcome], None]) -> CallOutcome:
    """Run `call` (fills the outcome), mapping errors as planner._ask does."""
    outcome = CallOutcome()
    start = time.monotonic()
    try:
        call(outcome)
    except _CONFIG_ERRORS:
        raise  # wrong key, model or parameter: the run fails with it
    except (openai.LengthFinishReasonError,
            openai.ContentFilterFinishReasonError) as exc:
        completion = getattr(exc, "completion", None)
        outcome.usage = _usage(getattr(completion, "usage", None))
        outcome.cut_off = isinstance(exc, openai.LengthFinishReasonError)
        outcome.error = f"{type(exc).__name__}: {str(exc)[:300]}"
    except ValidationError as exc:
        outcome.error = f"answer does not match the schema: {exc}"[:400]
    except openai.APIError as exc:
        outcome.error = f"{type(exc).__name__}: {str(exc)[:300]}"
    outcome.latency_s = round(time.monotonic() - start, 3)
    return outcome


def call_parsed(client, cfg: JobConfig, messages, schema) -> CallOutcome:
    """chat.completions.parse(response_format=<pydantic model>)."""
    def call(outcome: CallOutcome) -> None:
        response = client.chat.completions.parse(
            model=cfg.model, messages=list(messages),
            response_format=schema, **_kwargs(cfg))
        outcome.usage = _usage(response.usage)
        message = response.choices[0].message
        outcome.reply = message.content
        if message.parsed is None:
            outcome.error = f"no answer returned: {message.refusal or 'empty'}"
        else:
            outcome.parsed = message.parsed
    return _guarded(call)


def call_json(client, cfg: JobConfig, messages, json_schema: dict,
              validate: Callable[[str], Any],
              name: str = "answer") -> CallOutcome:
    """chat.completions.create(response_format={"type": "json_schema", ...}),
    then validate(content). Used by extract (PackingSettings has defaults,
    so strict parse is not possible)."""
    def call(outcome: CallOutcome) -> None:
        response = client.chat.completions.create(
            model=cfg.model, messages=list(messages),
            response_format={"type": "json_schema", "json_schema": {
                "name": name, "strict": True, "schema": json_schema}},
            **_kwargs(cfg))
        outcome.usage = _usage(response.usage)
        message = response.choices[0].message
        outcome.reply = message.content
        if not message.content:
            outcome.error = f"no answer returned: {message.refusal or 'empty'}"
            return
        try:
            outcome.parsed = validate(message.content)
        except (ValidationError, ValueError, TypeError) as exc:
            outcome.error = f"answer rejected: {str(exc)[:300]}"
    return _guarded(call)
