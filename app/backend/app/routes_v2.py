"""HTTP endpoints for pipeline v2 (prefix /api/v2)."""

import json
import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from packing.render_svg import render_svg
from packing.schemas import Response as Plan

from . import store_v2
from .routes import (SSE_HEADERS, SVG_HEADERS, _comparison, _load_row,
                     _not_found)
from .runs import NotActive, RunBusy
from .samples import SAMPLES, SAMPLES_V2
from .schemas import TERMINAL_STATUSES
from .schemas_v2 import (EvalGroup, Feedback, FeedbackIn, JobName,
                         RunRequestV2, RunSummaryV2, RunV2, SampleV2,
                         build_config_v2)
from .sse import event_stream

router = APIRouter()

PlanKey = Literal["final", "model", "packer"]
SINCE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?Z$")


def _row(run_id: uuid.UUID) -> dict[str, Any]:
    row = store_v2.get(str(run_id))
    if row is None:
        raise _not_found()
    return row


def _result(row: dict[str, Any]) -> dict[str, Any]:
    return json.loads(row["result_json"]) if row["result_json"] else {}


@router.get("/config")
def get_config() -> JSONResponse:
    return JSONResponse(build_config_v2().model_dump(mode="json"))


@router.get("/samples")
def get_samples() -> JSONResponse:
    return JSONResponse([SampleV2.model_validate(s).model_dump(mode="json")
                         for s in [*SAMPLES, *SAMPLES_V2]])


@router.post("/runs", status_code=202, response_model=None)
async def create_run(req: RunRequestV2, request: Request):
    meta = {
        "client_ip": request.client.host if request.client else "unknown",
        "user_agent": request.headers.get("user-agent", "")[:200],
        "request_id": request.state.request_id,
    }
    try:
        run_id = request.app.state.runs_v2.start(req, meta)
    except RunBusy as busy:
        return JSONResponse({"detail": "A run is already in progress",
                             "kind": busy.kind}, status_code=409)
    return {"run_id": str(run_id)}


@router.get("/runs")
def list_runs() -> JSONResponse:
    rows = store_v2.list(limit=100)
    return JSONResponse([
        RunSummaryV2.model_validate({
            **r, "over_budget": bool(r["over_budget"]),
            "fake": bool(r["fake"])}).model_dump(mode="json")
        for r in rows])


@router.get("/runs/active")
def active_run(request: Request) -> dict[str, str | None]:
    run_id = request.app.state.runs_v2.active_id()
    return {"run_id": str(run_id) if run_id else None}


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, request: Request) -> JSONResponse:
    row = _row(run_id)
    events = json.loads(row["events_json"] or "[]")
    terminal = row["status"] in TERMINAL_STATUSES
    if not terminal:
        manager = request.app.state.runs_v2
        active = manager.get_active(run_id)
        if active is not None:
            events = manager.events_from(active, 0)
    result = _result(row)
    body = {
        "run_id": row["run_id"], "status": row["status"],
        "error": row["error"], "created_at": row["created_at"],
        "finished_at": row["finished_at"], "fake": bool(row["fake"]),
        "request": json.loads(row["request_json"]),
        "events": events,
        **{key: result.get(key) for key in (
            "settings", "prepared_items", "prepared_boxes", "packer",
            "model", "source", "reason", "final", "totals")},
        "jobs": store_v2.job_views(row["run_id"]) if terminal else [],
        "preferences_model": result.get("preferences_model") or [],
        "preferences_packer": result.get("preferences_packer") or [],
        "verdicts": result.get("verdicts") or [],
        "comparison": _comparison(result),
    }
    return JSONResponse(RunV2.model_validate(body).model_dump(mode="json"))


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: uuid.UUID, request: Request,
    from_: Annotated[int | None, Query(alias="from", ge=0)] = None,
) -> StreamingResponse:
    if store_v2.events(str(run_id)) is None:
        raise _not_found()
    start = from_ or 0
    last_id = request.headers.get("last-event-id")
    if last_id is not None:
        try:
            start = int(last_id) + 1
        except ValueError:
            pass
    stream = event_stream(request.app.state.runs_v2, store_v2, run_id, start,
                          request.is_disconnected)
    return StreamingResponse(stream, headers=SSE_HEADERS)


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: uuid.UUID, request: Request) -> dict[str, str]:
    _row(run_id)
    try:
        request.app.state.runs_v2.cancel(run_id)
    except NotActive:
        raise HTTPException(status_code=409, detail="Run is not active")
    return {"status": "cancelling"}


def _plan_of(result: dict[str, Any], plan: str) -> dict[str, Any] | None:
    side = result.get(plan)
    return (side or {}).get("plan")


@router.get("/runs/{run_id}/render")
def render(run_id: uuid.UUID, plan: PlanKey, box: int,
           upto: Annotated[int | None, Query(ge=1)] = None) -> Response:
    row = _row(run_id)
    if row["status"] == "running":
        raise HTTPException(status_code=409, detail="Run is still running")
    data = _plan_of(_result(row), plan)
    if not data:
        raise _not_found("Plan")
    try:
        svg = render_svg(Plan.model_validate(data), upto, box)
    except IndexError:
        raise _not_found("Box")
    return Response(svg, media_type="image/svg+xml",
                    headers={**SVG_HEADERS, "Cache-Control": "max-age=3600"})


@router.get("/runs/{run_id}/svg")
def download_svg(run_id: uuid.UUID, plan: PlanKey) -> Response:
    _row(run_id)
    svg = store_v2.svg(str(run_id), plan)
    if svg is None:
        raise _not_found("SVG")
    return Response(
        svg, media_type="image/svg+xml",
        headers={**SVG_HEADERS, "Content-Disposition":
                 f'attachment; filename="run-{run_id}-{plan}.svg"'})


@router.get("/runs/{run_id}/jobs/{job}/messages")
def job_messages(run_id: uuid.UUID, job: JobName) -> dict[str, Any]:
    _row(run_id)
    messages = store_v2.job_messages(str(run_id), job)
    if messages is None:
        raise _not_found("Job")
    return {"messages": messages}


@router.put("/runs/{run_id}/feedback/{job}")
def put_feedback(run_id: uuid.UUID, job: JobName,
                 body: FeedbackIn) -> JSONResponse:
    row = _row(run_id)
    if row["status"] not in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="Run is not finished")
    job_row = store_v2.job_row(str(run_id), job)
    if job_row is None:
        raise _not_found("Job")
    if job_row["status"] == "skipped":
        raise HTTPException(status_code=422,
                            detail="A skipped job cannot take feedback")
    if body.wrong_verdicts:
        if job != "check":
            raise HTTPException(
                status_code=422,
                detail="wrong_verdicts is only for the check job")
        count = len(job_row["output"] or [])
        if any(not 0 <= i < count for i in body.wrong_verdicts):
            raise HTTPException(status_code=422,
                                detail="wrong_verdicts index out of range")
    wrong = sorted(set(body.wrong_verdicts))
    feedback = store_v2.upsert_feedback(str(run_id), job, body.rating,
                                        body.comment, wrong)
    if feedback is None:
        raise _not_found("Job")
    return JSONResponse(Feedback.model_validate(feedback).model_dump(
        mode="json"))


@router.get("/eval/summary")
def eval_summary(job: JobName | None = None, since: str | None = None,
                 include_fake: bool = False) -> JSONResponse:
    if since is not None and not SINCE.match(since):
        raise HTTPException(status_code=422,
                            detail="since must be an ISO UTC time ending in Z")
    groups = store_v2.eval_rows(job, since, include_fake)
    return JSONResponse({"groups": [
        EvalGroup.model_validate(g).model_dump(mode="json") for g in groups]})
