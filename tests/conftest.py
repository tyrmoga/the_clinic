import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/clinic_test",
)
os.environ.setdefault("JWT_SECRET", "test-secret-use-32-bytes-minimum")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

from app.main import create_app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client
