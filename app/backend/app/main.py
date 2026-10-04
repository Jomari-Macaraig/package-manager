"""FastAPI app: uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
--timeout-graceful-shutdown 5  (single process; one worker only)."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import config, store, store_v2, tracing
from .middleware import GuardMiddleware, RequestIdMiddleware
from .pipeline_v2 import V2_ADAPTER
from .routes import router
from .routes_v2 import router as router_v2
from .runs import RunManager, Slot

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init()
    store_v2.init()
    store.mark_interrupted()
    store_v2.mark_interrupted()
    tracing.setup()
    yield
    await app.state.runs.shutdown()  # the shared slot, either kind
    await asyncio.to_thread(store.mark_interrupted)
    await asyncio.to_thread(store_v2.mark_interrupted)
    await asyncio.to_thread(tracing.shutdown)


async def _validation_handler(request: Request, exc: RequestValidationError):
    # an unparseable run id in the path is "no such run"
    if any(err["loc"] and err["loc"][0] == "path" and err["loc"][-1] == "run_id"
           for err in exc.errors()):
        return JSONResponse({"detail": "Run not found"}, status_code=404)
    # FastAPI's default echoes `input`, which can be inf/NaN and then fails
    # JSON encoding; the contract only promises loc, msg and type.
    detail = [{"loc": list(err["loc"]), "msg": err["msg"],
               "type": err["type"]} for err in exc.errors()]
    return JSONResponse({"detail": detail}, status_code=422)


def create_app() -> FastAPI:
    app = FastAPI(title="Packing Playground", lifespan=lifespan)
    slot = Slot()
    app.state.runs = RunManager(slot=slot)
    app.state.runs_v2 = RunManager(V2_ADAPTER, slot, "v2")
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.include_router(router, prefix="/api")
    app.include_router(router_v2, prefix="/api/v2")

    # outermost first: RequestId, TrustedHost, CORS, Guard (added in reverse)
    app.add_middleware(GuardMiddleware)
    app.add_middleware(CORSMiddleware,
                       allow_origins=sorted(config.ALLOWED_ORIGINS),
                       allow_methods=["GET", "POST", "PUT"], allow_headers=["*"])
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=["localhost", "127.0.0.1"])
    app.add_middleware(RequestIdMiddleware)
    return app


app = create_app()
