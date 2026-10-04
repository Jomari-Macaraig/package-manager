"""Pydantic models for the pipeline v2 API contract (docs/plans/2026-10-05-
pipeline-v2-narrow-llm-jobs-with-evals.md, "Output - contract").

The TypeScript equivalents live in app/frontend/src/api/typesV2.ts. Plan,
RunStatus, MetricRow, PreparedItem, PreparedBox, PackingSettings, Caps and
Sample are reused from v1 (schemas.py).
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packing.schemas import Response as Plan
from packing.settings import PackingSettings
from packing.v2 import VOLUME_MARGIN

from . import config
from .schemas import (Caps, MetricRow, OrderInput, PreparedBox, PreparedItem,
                      RunStatus, Sample)

JobName = Literal["extract", "strategy", "check", "notes"]
JOB_NAMES: tuple[str, ...] = ("extract", "strategy", "check", "notes")
JobStatus = Literal["ok", "fallback", "skipped"]
Side = Literal["left", "right", "back", "front", "any"]
Level = Literal["bottom", "top", "any"]
Source = Literal["model", "packer", "none"]
Effort = Literal["low", "medium", "high"]


# --- Request ------------------------------------------------------------------

class JobChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    reasoning_effort: Effort | None = None


class RunRequestV2(OrderInput):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    jobs: dict[JobName, JobChoice] = {}

    @model_validator(mode="after")
    def _jobs(self) -> "RunRequestV2":
        """Each model must be on its job's allowed list; the effort one the
        model supports (strategy models all reason; the others take none)."""
        planning = {m["id"]: m for m in config.PLANNING_MODELS}
        for name, choice in self.jobs.items():
            if choice.model not in config.JOBS[name]["models"]:
                raise ValueError(
                    f"unknown model {choice.model!r} for job {name}")
            if name == "strategy":
                if choice.reasoning_effort not in planning[
                        choice.model]["efforts"]:
                    raise ValueError(
                        f"{choice.model} does not support effort "
                        f"{choice.reasoning_effort!r}")
            elif choice.reasoning_effort is not None:
                raise ValueError(f"{choice.model} takes no reasoning effort "
                                 f"for job {name}")
        return self


# --- Output -------------------------------------------------------------------

class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str


class Tokens(BaseModel):
    prompt: int
    completion: int
    reasoning: int


class Feedback(BaseModel):
    rating: Literal["up", "down"] | None
    comment: str
    wrong_verdicts: list[int]
    updated_at: str


class JobView(BaseModel):
    job: JobName
    status: JobStatus
    model: str | None
    reasoning_effort: str | None
    prompt_version: str
    calls: int
    checks: list[CheckResult]
    output: dict | list | None
    answer: dict | list | None
    error: str | None
    cost: float
    latency_s: float
    tokens: Tokens
    feedback: Feedback | None


class PlanSideV2(BaseModel):
    ok: bool
    plan: Plan | None
    box_count: int
    rank: list[float] | None
    errors: list[str]
    hints: list[str]


class PreferenceRow(BaseModel):
    id: int
    name: str
    side: Side
    level: Level
    honoured: int
    total: int
    detail: str


class Verdict(BaseModel):
    index: int
    instruction: str
    verdict: Literal["honoured", "not_honoured", "cannot_tell"]
    reason: str


class BoxNotes(BaseModel):
    box: int
    steps: list[str]


class FinalV2(BaseModel):
    source: Literal["model", "packer"]
    reason: str
    plan: Plan  # plan.notes = cut notes only
    notes: list[BoxNotes]
    cut_notes: list[str]


class Totals(BaseModel):
    cost: float
    seconds: float
    over_budget: bool
    budget: dict[str, float]  # {"usd", "seconds"}


class _Event(BaseModel):
    id: int
    t: float


class StartedV2(_Event):
    type: Literal["started"]


class JobEventV2(_Event):
    type: Literal["job"]
    job: JobName
    status: JobStatus
    passed: int
    total: int


class PackerV2(_Event):
    type: Literal["packer"]
    ok: bool
    boxes: int


class ChosenV2(_Event):
    type: Literal["chosen"]
    source: Source
    reason: str


class EndV2(_Event):
    type: Literal["end"]
    status: RunStatus
    error: str | None


RunEventV2 = Annotated[
    StartedV2 | JobEventV2 | PackerV2 | ChosenV2 | EndV2,
    Field(discriminator="type")]


class RunV2(BaseModel):
    run_id: str
    status: RunStatus
    error: str | None
    created_at: str
    finished_at: str | None
    fake: bool
    request: RunRequestV2
    events: list[RunEventV2]
    settings: PackingSettings | None
    prepared_items: list[PreparedItem] | None
    prepared_boxes: list[PreparedBox] | None
    jobs: list[JobView]  # 4 rows once terminal; [] while running, and [] for
    # the rare terminal run whose finish fell back to set_status
    packer: PlanSideV2 | None
    model: PlanSideV2 | None
    source: Source | None  # authoritative winner; None while running
    reason: str | None
    final: FinalV2 | None
    preferences_model: list[PreferenceRow]
    preferences_packer: list[PreferenceRow]
    verdicts: list[Verdict]
    comparison: list[MetricRow] | None
    totals: Totals | None


class RunSummaryV2(BaseModel):
    run_id: str
    created_at: str
    finished_at: str | None
    status: RunStatus
    title: str
    source: Source | None
    reason: str | None
    total_cost: float | None
    total_s: float | None
    over_budget: bool
    fake: bool


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["up", "down"] | None = None
    comment: str = Field("", max_length=2000)
    wrong_verdicts: list[int] = []  # check job only; indices must exist


class EvalGroup(BaseModel):
    job: JobName
    prompt_version: str
    model: str | None
    reasoning_effort: str | None
    calls: int  # non-skipped calls
    skipped: int
    ok_rate: float | None  # status ok / calls; None when calls == 0
    fallback_rate: float | None
    all_checks_rate: float | None  # share of calls where every check passed
    per_check: dict[str, float]  # check name -> pass share over calls that ran it
    thumbs_up: int
    thumbs_down: int
    wrong_verdict_rate: float | None  # check only
    model_won_rate: float | None  # strategy only, run-level
    avg_cost: float | None
    p50_latency_s: float | None
    p95_latency_s: float | None  # nearest-rank
    first_seen: str
    last_seen: str


class ModelOptionV2(BaseModel):
    id: str
    label: str
    efforts: list[Effort] | None  # None for non-reasoning / fixed-kwargs models
    price_in: float
    price_out: float


class JobConfigView(BaseModel):
    default: JobChoice
    models: list[ModelOptionV2]


class ConfigV2(BaseModel):
    jobs: dict[JobName, JobConfigView]
    budget: dict[str, float]
    caps: Caps
    volume_margin: float


class SampleV2(Sample):
    """key: A-H (A-E are the v1 samples)."""


def build_config_v2() -> ConfigV2:
    def option(model_id: str, label: str, efforts) -> ModelOptionV2:
        p_in, p_out = config.PRICES[model_id]
        return ModelOptionV2(id=model_id, label=label, efforts=efforts,
                             price_in=p_in, price_out=p_out)

    extraction = [option(m["id"], m["label"], None)
                  for m in config.EXTRACTION_MODELS]
    planning = [option(m["id"], m["label"], m["efforts"])
                for m in config.PLANNING_MODELS]
    models: dict[str, Any] = {"extract": extraction, "strategy": planning,
                              "check": extraction, "notes": extraction}
    return ConfigV2(
        jobs={name: JobConfigView(
            default=JobChoice(**config.JOBS[name]["default"]),
            models=models[name]) for name in JOB_NAMES},
        budget=dict(config.BUDGET), caps=Caps(**config.CAPS),
        volume_margin=VOLUME_MARGIN)
