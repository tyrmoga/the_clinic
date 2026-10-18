import os
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/clinic_test",
)
_database_name = urlparse(TEST_DATABASE_URL).path.strip("/")
if not _database_name.endswith("_test"):
    raise RuntimeError("TEST_DATABASE_URL must point to a database whose name ends with '_test'")

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("JWT_SECRET", "test-secret-use-32-bytes-minimum")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")

from app.main import create_app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as test_client:
        yield test_client
