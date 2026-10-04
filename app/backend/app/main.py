"""FastAPI app: uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
--timeout-graceful-shutdown 5  (single process; one worker only)."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config, store, tracing
from .middleware import GuardMiddleware, RequestIdMiddleware
from .routes import router
from .runs import RunManager

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    store.mark_interrupted()
    tracing.setup()
    yield
    await app.state.runs.shutdown()
    await asyncio.to_thread(tracing.shutdown)


async def _validation_handler(request: Request, exc: RequestValidationError):
    # an unparseable run id in the path is "no such run"
    if any(err["loc"] and err["loc"][0] == "path" and err["loc"][-1] == "run_id"
           for err in exc.errors()):
        return JSONResponse({"detail": "Run not found"}, status_code=404)
    return await request_validation_exception_handler(request, exc)


def create_app() -> FastAPI:
    app = FastAPI(title="Packing Playground", lifespan=lifespan)
    app.state.runs = RunManager()
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.include_router(router, prefix="/api")

    # outermost first: RequestId, TrustedHost, CORS, Guard (added in reverse)
    app.add_middleware(GuardMiddleware)
    app.add_middleware(CORSMiddleware,
                       allow_origins=sorted(config.ALLOWED_ORIGINS),
                       allow_methods=["GET", "POST"], allow_headers=["*"])
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=["localhost", "127.0.0.1"])
    app.add_middleware(RequestIdMiddleware)
    return app


app = create_app()
