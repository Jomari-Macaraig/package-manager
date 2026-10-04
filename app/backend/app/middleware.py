"""Pure ASGI middleware (BaseHTTPMiddleware would break SSE disconnects)."""

import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import config


class RequestIdMiddleware:
    """X-Request-ID on every response; an incoming one is reused when it is
    a UUID. Outermost, so TrustedHost and guard responses carry it too."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = Headers(scope=scope).get("x-request-id")
        try:
            request_id = str(uuid.UUID(incoming)) if incoming else ""
        except ValueError:
            request_id = ""
        request_id = request_id or str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_id)


class GuardMiddleware:
    """Origin allowlist (403), body size from Content-Length (413), and
    JSON-only POSTs (415)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        error: tuple[int, str] | None = None

        origin = headers.get("origin")
        length = headers.get("content-length")
        if origin is not None and origin not in config.ALLOWED_ORIGINS:
            error = (403, "Origin not allowed")
        elif length is not None and length.isdigit() and (
                int(length) > config.MAX_BODY_BYTES):
            error = (413, "Request body too large")
        elif scope["method"] == "POST":
            content_type = headers.get("content-type", "")
            if content_type.split(";")[0].strip().lower() != "application/json":
                error = (415, "Content-Type must be application/json")

        if error is not None:
            response = JSONResponse({"detail": error[1]}, status_code=error[0])
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
