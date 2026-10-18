import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

import jwt
from cachetools import LRUCache
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse

from app.core.config import Settings
from app.core.errors import error_payload
from app.core.security import decode_access_token

_SKIP_PATHS = {"/", "/healthz", "/readyz", "/openapi.json"}
_SKIP_PREFIXES = ("/docs", "/redoc")


@dataclass
class TokenBucket:
    capacity: float
    refill_per_second: float
    tokens: float = field(init=False)
    updated_at: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = self.capacity
        self.updated_at = time.monotonic()

    def consume(self, now: float | None = None) -> tuple[bool, float]:
        now = time.monotonic() if now is None else now
        elapsed = max(0.0, now - self.updated_at)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        self.updated_at = now

        if self.tokens >= 1:
            self.tokens -= 1
            return True, 0.0

        retry_after = (1 - self.tokens) / self.refill_per_second
        return False, retry_after


class RateLimiter:
    def __init__(self, limits: Mapping[str, int]) -> None:
        self.limits = dict(limits)
        self.buckets: LRUCache = LRUCache(maxsize=10_000)
        self.last_cleanup = time.monotonic()

    def consume(self, scope: str, key: str, now: float | None = None) -> tuple[bool, float]:
        now = time.monotonic() if now is None else now
        limit = self.limits.get(scope, self.limits["default"])
        bucket_key = (scope, key)
        bucket = self.buckets.get(bucket_key)
        if bucket is None:
            bucket = TokenBucket(capacity=limit, refill_per_second=limit / 60)
            self.buckets[bucket_key] = bucket

        allowed, retry_after = bucket.consume(now)
        self._cleanup(now)
        return allowed, retry_after

    def _cleanup(self, now: float) -> None:
        if now - self.last_cleanup < 60:
            return
        self.last_cleanup = now
        stale = [
            key
            for key, bucket in self.buckets.items()
            if now - bucket.updated_at > 300 and bucket.tokens >= bucket.capacity
        ]
        for key in stale:
            del self.buckets[key]


def limit_scope(path: str) -> str:
    if path.startswith("/auth/"):
        return "auth"
    if path.startswith("/doctors/"):
        return "availability"
    if path.startswith("/appointments"):
        return "booking"
    return "default"


def rate_key(request: Request) -> str:
    if not request.url.path.startswith("/auth/"):
        authorization = request.headers.get("Authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() == "bearer" and token:
            try:
                return f"patient:{decode_access_token(token)}"
            except jwt.PyJWTError:
                pass

    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        host = forwarded_for.split(",")[-1].strip()
    else:
        host = request.client.host if request.client is not None else "unknown"
    return f"ip:{host}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.limiter = RateLimiter(
            {
                "auth": settings.rate_limit_auth_per_minute,
                "availability": settings.rate_limit_availability_per_minute,
                "booking": settings.rate_limit_booking_per_minute,
                "default": settings.rate_limit_default_per_minute,
            }
        )

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if (
            not self.settings.rate_limit_enabled
            or path in _SKIP_PATHS
            or any(path.startswith(prefix) for prefix in _SKIP_PREFIXES)
        ):
            return await call_next(request)

        scope = limit_scope(path)
        allowed, retry_after = self.limiter.consume(scope, rate_key(request))
        if allowed:
            return await call_next(request)

        request_id = getattr(request.state, "request_id", None)
        response = JSONResponse(
            status_code=429,
            content=error_payload(
                "rate_limited",
                "Too many requests. Try again shortly.",
                request_id=request_id,
            ),
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )
        return response
