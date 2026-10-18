import re
import uuid

from fastapi import Request, Response
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.context import request_id_var
from app.core.errors import error_payload

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming if _REQUEST_ID_RE.fullmatch(incoming) else uuid.uuid4().hex

        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)

        response.headers.setdefault("X-Request-ID", request_id)
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        headers = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
            "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        }
        # Swagger/ReDoc load their assets from a CDN; a strict CSP would break them.
        if not (request.url.path.startswith("/docs") or request.url.path.startswith("/redoc")):
            headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        for name, value in headers.items():
            response.headers.setdefault(name, value)
        return response


class _PayloadTooLarge(Exception):
    pass


def _request_id(scope: Scope) -> str | None:
    state = scope.get("state")
    if isinstance(state, dict):
        value = state.get("request_id")
        return value if isinstance(value, str) else None
    return None


class BodySizeLimitMiddleware:
    """Reject requests whose body exceeds the configured size.

    Content-Length is checked up front; the receive wrapper also counts bytes so a
    lying or chunked Content-Length cannot bypass the limit.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                await self._send_error(
                    scope,
                    receive,
                    send,
                    400,
                    "bad_request",
                    "Invalid Content-Length.",
                )
                return
            if length > self.max_bytes:
                await self._send_error(
                    scope, receive, send, 413, "payload_too_large", "Request body is too large."
                )
                return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise _PayloadTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _PayloadTooLarge:
            if not response_started:
                await self._send_error(
                    scope, receive, send, 413, "payload_too_large", "Request body is too large."
                )

    async def _send_error(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        code: str,
        message: str,
    ) -> None:
        request_id = _request_id(scope)
        response = JSONResponse(
            status_code=status_code,
            content=error_payload(code, message, request_id=request_id),
        )
        if request_id:
            response.headers["X-Request-ID"] = request_id
        await response(scope, receive, send)
