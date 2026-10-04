"""Pydantic models for the API contract (docs/plans/2026-10-04-packing-playground.md).

Input models validate requests; output models make sure what the API sends
has exactly the contract's shape.
"""

import unicodedata
from typing import Annotated, Any, ClassVar, Literal

from pydantic import (BaseModel, ConfigDict, Field, StringConstraints,
                      field_validator, model_serializer, model_validator)

from packing.schemas import Response as Plan
from packing.settings import PackingSettings

from . import config

Effort = Literal["low", "medium", "high"]
# improve: the model edits the code packer's plan (Starting plan);
# scratch: it plans from the input alone; guide: it picks box types, item
# order and rotations, and code places the units
Start = Literal["improve", "scratch", "guide"]
RunStatus = Literal["running", "succeeded", "failed", "cancelled",
                    "interrupted"]
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "interrupted")

_STRICT = ConfigDict(allow_inf_nan=False)


def _no_control_chars(value: str, allowed: str = "") -> str:
    for ch in value:
        if unicodedata.category(ch) == "Cc" and ch not in allowed:
            raise ValueError("control characters are not allowed")
    return value


def _check_name(value: str) -> str:
    value = value.strip()
    if not 1 <= len(value) <= 60:
        raise ValueError("name must be 1-60 characters")
    return _no_control_chars(value)


# --- Input ------------------------------------------------------------------

Dim = Annotated[float, Field(gt=0, le=1000)]
MaxWeight = Annotated[float, Field(gt=0, le=10000)]


class InputItem(BaseModel):
    model_config = _STRICT

    name: str
    color: str | None = Field(None, max_length=32)
    weight: float = Field(ge=0, le=10000)
    x: Dim
    y: Dim
    z: Dim
    quantity: int = Field(ge=1, le=config.CAPS["max_units"])

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _check_name(v)

    @field_validator("color")
    @classmethod
    def _color(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return _no_control_chars(v.strip())


class InputBox(BaseModel):
    model_config = _STRICT

    name: str
    weightMax: MaxWeight
    x: Dim
    y: Dim
    z: Dim
    stock: int | None = Field(None, ge=0, le=1000)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return _check_name(v)


class Extraction(BaseModel):
    model: str

    @field_validator("model")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in {m["id"] for m in config.EXTRACTION_MODELS}:
            raise ValueError(f"unknown extraction model {v!r}")
        return v


class Planning(BaseModel):
    model: str
    reasoning_effort: Effort
    start: Start = "improve"

    @model_validator(mode="after")
    def _known(self) -> "Planning":
        known = {m["id"]: m for m in config.PLANNING_MODELS}
        if self.model not in known:
            raise ValueError(f"unknown planning model {self.model!r}")
        if self.reasoning_effort not in known[self.model]["efforts"]:
            raise ValueError(
                f"{self.model} does not support effort "
                f"{self.reasoning_effort!r}")
        return self


class RunRequest(BaseModel):
    model_config = _STRICT

    items: list[InputItem] = Field(min_length=1,
                                   max_length=config.CAPS["max_item_rows"])
    boxes: list[InputBox] = Field(min_length=1,
                                  max_length=config.CAPS["max_box_rows"])
    instructions: str = Field(
        max_length=config.CAPS["max_instruction_chars"])
    extraction: Extraction
    planning: Planning

    @field_validator("instructions")
    @classmethod
    def _instructions(cls, v: str) -> str:
        return _no_control_chars(v, allowed="\n\t")

    @model_validator(mode="after")
    def _caps(self) -> "RunRequest":
        units = sum(i.quantity for i in self.items)
        if units > config.CAPS["max_units"]:
            raise ValueError(
                f"total quantity {units} exceeds the cap of "
                f"{config.CAPS['max_units']} units")
        for kind, rows in (("item", self.items), ("box", self.boxes)):
            seen: set[str] = set()
            for row in rows:
                key = row.name.casefold()
                if key in seen:
                    raise ValueError(f"duplicate {kind} name {row.name!r}")
                seen.add(key)
        return self


def to_pipeline_inputs(
    req: RunRequest,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Notebook-shaped dicts with ids 1..n by row order. Color None -> ""
    (tab10 is used); stock only when not null."""
    items = [{"id": n, "name": i.name, "color": i.color or "",
              "weight": i.weight,
              "dimensions": {"x": i.x, "y": i.y, "z": i.z},
              "quantity": i.quantity}
             for n, i in enumerate(req.items, start=1)]
    boxes = []
    for n, b in enumerate(req.boxes, start=1):
        box: dict[str, Any] = {
            "id": n, "name": b.name, "weightMax": b.weightMax,
            "dimensions": {"x": b.x, "y": b.y, "z": b.z}}
        if b.stock is not None:
            box["stock"] = b.stock
        boxes.append(box)
    return items, boxes


# --- Output -----------------------------------------------------------------

Rank = tuple[int, int, float, float]


class Dimensions(BaseModel):
    x: float
    y: float
    z: float


class _OptionalKeys(BaseModel):
    """Drops listed optional keys when they are None (TS `key?: T`)."""

    _optional: ClassVar[tuple[str, ...]] = ()

    @model_serializer(mode="wrap")
    def _drop_none(self, handler):
        data = handler(self)
        for key in self._optional:
            if data.get(key, 0) is None:
                data.pop(key)
        return data


class PreparedItem(_OptionalKeys):
    _optional: ClassVar[tuple[str, ...]] = ("fixed", "upright", "no_stack", "max_load", "floor_only")
    id: int
    source_id: int
    name: str
    color: str
    weight: float
    dimensions: Dimensions
    quantity: int
    fixed: bool | None = None
    upright: bool | None = None
    no_stack: bool | None = None
    max_load: float | None = None
    floor_only: bool | None = None


class PreparedBox(_OptionalKeys):
    _optional: ClassVar[tuple[str, ...]] = ("stock",)
    id: int
    name: str
    weightMax: float
    dimensions: Dimensions
    stock: int | None = None


class PlanSide(BaseModel):
    ok: bool
    rank: Rank | None
    errors: list[str]
    hints: list[str]
    plan: Plan | None
    box_count: int


class ModelSide(PlanSide):
    rounds: int
    improved: bool


class FinalPlan(BaseModel):
    source: Literal["model", "packer"]
    plan: Plan


class CallStats(BaseModel):
    model: str
    reasoning_effort: str | None
    prompt_tokens: int
    completion_tokens: int
    reasoning_tokens: int
    cost: float


class Timings(BaseModel):
    extract_s: float | None
    packer_s: float | None
    plan_s: float | None
    total_s: float | None


class Stats(BaseModel):
    extraction: CallStats | None
    planning: CallStats | None
    total_tokens: int
    total_cost: float
    timings: Timings


class _Event(BaseModel):
    id: int
    t: float


class StartedEvent(_Event):
    type: Literal["started"]


class ExtractedEvent(_Event):
    type: Literal["extracted"]
    tokens: int
    skipped: bool


class PreparedEvent(_Event):
    type: Literal["prepared"]
    item_types: int
    units: int


class PackerEvent(_Event):
    type: Literal["packer"]
    ok: bool
    boxes: int
    errors_count: int


class RoundEvent(_Event):
    type: Literal["round"]
    n: int
    kind: Literal["fix", "improve"]
    valid: bool
    errors_count: int


class ChosenEvent(_Event):
    type: Literal["chosen"]
    source: Literal["model", "packer"] | None


class EndEvent(_Event):
    type: Literal["end"]
    status: RunStatus
    error: str | None


RunEvent = Annotated[
    StartedEvent | ExtractedEvent | PreparedEvent | PackerEvent | RoundEvent
    | ChosenEvent | EndEvent,
    Field(discriminator="type"),
]


class Loop(BaseModel):
    max_rounds: Literal[3]
    improve_rounds: Literal[1]


class MetricRow(BaseModel):
    """packing.metrics.compare row: model vs packer for one metric."""
    metric: str
    label: str
    unit: str
    direction: Literal["lower", "higher", "info"]
    model: float | None
    packer: float | None
    delta: float | None
    verdict: Literal["better", "worse", "same"] | None


class Run(BaseModel):
    run_id: str
    trace_id: str
    request_id: str
    status: RunStatus
    error: str | None
    created_at: str
    finished_at: str | None
    client_ip: str
    user_agent: str
    request: RunRequest
    loop: Loop
    settings: PackingSettings | None
    prepared_items: list[PreparedItem] | None
    prepared_boxes: list[PreparedBox] | None
    final: FinalPlan | None
    model: ModelSide | None
    packer: PlanSide | None
    stats: Stats | None
    comparison: list[MetricRow] | None = None
    events: list[RunEvent]


class RunSummary(BaseModel):
    run_id: str
    created_at: str
    finished_at: str | None
    status: RunStatus
    title: str
    extraction_model: str
    planning_model: str
    source: Literal["model", "packer"] | None
    total_cost: float | None
    client_ip: str


class ModelOption(BaseModel):
    id: str
    label: str
    price_in: float
    price_out: float


class ExtractionOption(ModelOption):
    reasoning: bool


class PlanningOption(ModelOption):
    efforts: list[Effort]


class Defaults(BaseModel):
    extraction_model: str
    planning_model: str
    reasoning_effort: Effort


class Caps(BaseModel):
    max_item_rows: Literal[20]
    max_box_rows: Literal[10]
    max_units: Literal[150]
    max_instruction_chars: Literal[2000]


class Config(BaseModel):
    extraction_models: list[ExtractionOption]
    planning_models: list[PlanningOption]
    defaults: Defaults
    loop: Loop
    caps: Caps


class Sample(BaseModel):
    key: Literal["A", "B", "C", "D", "E"]
    title: str
    description: str
    items: list[InputItem]
    boxes: list[InputBox]
    instructions: str


def build_config() -> Config:
    def price(model_id: str) -> dict[str, float]:
        p_in, p_out = config.PRICES[model_id]
        return {"price_in": p_in, "price_out": p_out}

    return Config(
        extraction_models=[
            ExtractionOption(id=m["id"], label=m["label"],
                             reasoning=m["reasoning"], **price(m["id"]))
            for m in config.EXTRACTION_MODELS],
        planning_models=[
            PlanningOption(id=m["id"], label=m["label"],
                           efforts=m["efforts"], **price(m["id"]))
            for m in config.PLANNING_MODELS],
        defaults=Defaults(
            extraction_model=config.DEFAULT_EXTRACTION_MODEL,
            planning_model=config.DEFAULT_PLANNING_MODEL,
            reasoning_effort=config.DEFAULT_REASONING_EFFORT),
        loop=Loop(**config.LOOP),
        caps=Caps(**config.CAPS),
    )
