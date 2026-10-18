import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "unprocessable_entity",
    429: "too_many_requests",
    500: "internal_error",
    503: "service_unavailable",
}

_DEFAULT_MESSAGES = {
    400: "Malformed request.",
    401: "Authentication is required.",
    403: "You are not allowed to perform this action.",
    404: "Resource not found.",
    405: "Method not allowed.",
    409: "The request conflicts with the current state.",
    413: "Request body is too large.",
    415: "Content-Type must be application/json.",
    422: "Request validation failed.",
    429: "Too many requests.",
    500: "Internal server error.",
    503: "Service temporarily unavailable.",
}


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


def error_payload(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or {},
            "request_id": request_id,
        }
    }


def request_id_for(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def json_error(
    status_code: int,
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    request: Request | None = None,
) -> JSONResponse:
    request_id = request_id_for(request) if request is not None else None
    response_headers = dict(headers or {})
    if request_id:
        response_headers.setdefault("X-Request-ID", request_id)
    return JSONResponse(
        status_code=status_code,
        content=error_payload(code, message, details, request_id),
        headers=response_headers,
    )


def _http_error(exc: StarletteHTTPException, request: Request) -> JSONResponse:
    code = _STATUS_CODES.get(exc.status_code, "http_error")
    message = _DEFAULT_MESSAGES.get(exc.status_code, str(exc.detail))

    if isinstance(exc.detail, dict):
        code = str(exc.detail.get("code", code))
        message = str(exc.detail.get("message", message))
        details = exc.detail.get("details", {})
    else:
        details = {}

    return json_error(
        exc.status_code,
        code,
        message,
        details=details,
        headers=exc.headers,
        request=request,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return json_error(
            exc.status_code,
            exc.code,
            exc.message,
            details=exc.details,
            headers=exc.headers,
            request=request,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _http_error(exc, request)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        fields = []
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"] if part != "body")
            fields.append(
                {
                    "field": location or "body",
                    "message": error["msg"],
                    "type": error["type"],
                }
            )
        return json_error(
            422,
            "validation_error",
            _DEFAULT_MESSAGES[422],
            details={"fields": fields},
            request=request,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception", extra={"request_id": request_id_for(request)})
        return json_error(500, "internal_error", _DEFAULT_MESSAGES[500], request=request)


__all__ = [
    "AppError",
    "error_payload",
    "json_error",
    "register_exception_handlers",
    "request_id_for",
]
