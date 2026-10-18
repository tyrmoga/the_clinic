from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.core.config import Settings
from app.core.rate_limit import RateLimitMiddleware, TokenBucket, limit_scope, rate_key


def test_token_bucket_allows_burst_then_refills() -> None:
    bucket = TokenBucket(capacity=2, refill_per_second=1)

    assert bucket.consume(100.0) == (True, 0.0)
    assert bucket.consume(100.0) == (True, 0.0)

    allowed, retry_after = bucket.consume(100.0)
    assert allowed is False
    assert 0.9 <= retry_after <= 1.0

    allowed, _ = bucket.consume(101.1)
    assert allowed is True


def test_limit_scope_mapping() -> None:
    assert limit_scope("/auth/login") == "auth"
    assert limit_scope("/doctors/1/availability") == "availability"
    assert limit_scope("/appointments") == "booking"
    assert limit_scope("/patients/1/appointments") == "default"


def test_middleware_returns_429_with_retry_after() -> None:
    settings = Settings(
        app_env="test",
        rate_limit_enabled=True,
        rate_limit_auth_per_minute=1,
        rate_limit_availability_per_minute=1,
        rate_limit_booking_per_minute=1,
        rate_limit_default_per_minute=1,
    )
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, settings=settings)

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(app) as client:
        assert client.get("/ping").status_code == 200

        response = client.get("/ping")
        assert response.status_code == 429
        assert response.headers["retry-after"] == "60"
        assert response.json()["error"]["code"] == "rate_limited"


def test_rate_key_uses_the_last_forwarded_address() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/appointments",
        "scheme": "http",
        "query_string": b"",
        "headers": [(b"x-forwarded-for", b"1.2.3.4, 5.6.7.8")],
        "client": ("9.9.9.9", 1234),
        "server": ("testserver", 80),
    }

    assert rate_key(Request(scope)) == "ip:5.6.7.8"


def test_rate_key_falls_back_to_the_client_address() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/appointments",
        "scheme": "http",
        "query_string": b"",
        "headers": [],
        "client": ("9.9.9.9", 1234),
        "server": ("testserver", 80),
    }

    assert rate_key(Request(scope)) == "ip:9.9.9.9"
