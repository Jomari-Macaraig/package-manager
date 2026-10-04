"""HTTP endpoints (prefix /api), per the plan's API contract."""

import json
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from packing.render_svg import render_svg
from packing.schemas import Response as Plan

from . import config, store
from .runs import NotActive, RunBusy
from .samples import SAMPLES
from .schemas import Run, RunRequest, RunSummary, Sample, build_config
from .sse import event_stream

router = APIRouter()

SVG_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
}
SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _not_found(what: str = "Run") -> HTTPException:
    return HTTPException(status_code=404, detail=f"{what} not found")


def _load_row(run_id: uuid.UUID) -> dict[str, Any]:
    row = store.get(str(run_id))
    if row is None:
        raise _not_found()
    return row


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/config")
def get_config() -> JSONResponse:
    return JSONResponse(build_config().model_dump(mode="json"))


@router.get("/samples")
def get_samples() -> JSONResponse:
    return JSONResponse([Sample.model_validate(s).model_dump(mode="json")
                         for s in SAMPLES])


@router.post("/runs", status_code=202)
async def create_run(req: RunRequest, request: Request) -> dict[str, str]:
    meta = {
        "client_ip": request.client.host if request.client else "unknown",
        "user_agent": request.headers.get("user-agent", "")[:200],
        "request_id": request.state.request_id,
    }
    try:
        run_id = request.app.state.runs.start(req, meta)
    except RunBusy:
        raise HTTPException(status_code=409,
                            detail="A run is already in progress")
    return {"run_id": str(run_id)}


@router.get("/runs")
def list_runs() -> JSONResponse:
    rows = store.list(limit=100)
    return JSONResponse([RunSummary.model_validate(r).model_dump(mode="json")
                         for r in rows])


@router.get("/runs/active")
def active_run(request: Request) -> dict[str, str | None]:
    run_id = request.app.state.runs.active_id()
    return {"run_id": str(run_id) if run_id else None}


@router.get("/runs/{run_id}")
def get_run(run_id: uuid.UUID, request: Request) -> JSONResponse:
    row = _load_row(run_id)
    events = json.loads(row["events_json"] or "[]")
    if row["status"] == "running":
        manager = request.app.state.runs
        active = manager.get_active(run_id)
        if active is not None:
            events = manager.events_from(active, 0)
    result = json.loads(row["result_json"]) if row["result_json"] else {}
    body = {
        "run_id": row["run_id"], "trace_id": row["trace_id"],
        "request_id": row["request_id"], "status": row["status"],
        "error": row["error"], "created_at": row["created_at"],
        "finished_at": row["finished_at"], "client_ip": row["client_ip"],
        "user_agent": row["user_agent"],
        "request": json.loads(row["request_json"]),
        "loop": config.LOOP,
        **{key: result.get(key) for key in (
            "settings", "prepared_items", "prepared_boxes", "final",
            "model", "packer", "stats")},
        "events": events,
    }
    return JSONResponse(Run.model_validate(body).model_dump(mode="json"))


@router.get("/runs/{run_id}/history")
def get_history(run_id: uuid.UUID) -> dict[str, Any]:
    history = store.history(str(run_id))
    if history is None:
        raise _not_found()
    return {"extraction": history.get("extraction", []),
            "planning": history.get("planning", [])}


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: uuid.UUID, request: Request,
    from_: Annotated[int | None, Query(alias="from", ge=0)] = None,
) -> StreamingResponse:
    if store.events(str(run_id)) is None:
        raise _not_found()
    start = from_ or 0
    last_id = request.headers.get("last-event-id")
    if last_id is not None:
        try:
            start = int(last_id) + 1
        except ValueError:
            pass
    stream = event_stream(request.app.state.runs, store, run_id, start,
                          request.is_disconnected)
    return StreamingResponse(stream, headers=SSE_HEADERS)


@router.post("/runs/{run_id}/cancel", status_code=202)
def cancel_run(run_id: uuid.UUID, request: Request) -> dict[str, str]:
    _load_row(run_id)
    try:
        request.app.state.runs.cancel(run_id)
    except NotActive:
        raise HTTPException(status_code=409, detail="Run is not active")
    return {"status": "cancelling"}


@router.get("/runs/{run_id}/render")
def render(run_id: uuid.UUID, plan: Literal["model", "packer"], box: int,
           upto: Annotated[int | None, Query(ge=1)] = None) -> Response:
    row = _load_row(run_id)
    if row["status"] == "running":
        raise HTTPException(status_code=409, detail="Run is still running")
    result = json.loads(row["result_json"]) if row["result_json"] else {}
    side = result.get(plan)
    if not side or not side.get("plan"):
        raise _not_found("Plan")
    try:
        svg = render_svg(Plan.model_validate(side["plan"]), upto, box)
    except IndexError:
        raise _not_found("Box")
    return Response(svg, media_type="image/svg+xml",
                    headers={**SVG_HEADERS,
                             "Cache-Control": "max-age=3600"})


@router.get("/runs/{run_id}/svg")
def download_svg(run_id: uuid.UUID, plan: Literal["model", "packer"],
                 ) -> Response:
    _load_row(run_id)
    svg = store.svg(str(run_id), plan)
    if svg is None:
        raise _not_found("SVG")
    return Response(
        svg, media_type="image/svg+xml",
        headers={**SVG_HEADERS,
                 "Content-Disposition":
                 f'attachment; filename="run-{run_id}-{plan}.svg"'})
