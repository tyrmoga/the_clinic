import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Patient

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 and point DATABASE_URL at a migrated test database",
)

PASSWORD = "Password123!"


sync_engine = create_engine(get_settings().database_url, pool_pre_ping=True)


@pytest.fixture(autouse=True)
def clean_auth_test_patients() -> None:
    def cleanup() -> None:
        with Session(sync_engine) as session:
            session.execute(delete(Patient).where(Patient.email.like("authtest-%")))
            session.commit()

    cleanup()
    yield
    cleanup()


def _register_payload(email: str) -> dict[str, str]:
    return {
        "email": email,
        "password": PASSWORD,
        "full_name": "Auth Test Patient",
        "phone": "+254700000099",
    }


def _unique_email() -> str:
    return f"authtest-{uuid4().hex}@example.com"


def test_register_returns_token(client: TestClient) -> None:
    email = _unique_email()
    response = client.post("/auth/register", json=_register_payload(email))

    assert response.status_code == 201
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert body["patient_id"] > 0


def test_duplicate_email_is_rejected(client: TestClient) -> None:
    email = _unique_email()
    first = client.post("/auth/register", json=_register_payload(email))
    assert first.status_code == 201

    second = client.post("/auth/register", json=_register_payload(email))
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "email_taken"


def test_weak_password_is_rejected(client: TestClient) -> None:
    payload = _register_payload(_unique_email())
    payload["password"] = "short"

    response = client.post("/auth/register", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_invalid_email_is_rejected(client: TestClient) -> None:
    payload = _register_payload("not-an-email")

    response = client.post("/auth/register", json=payload)
    assert response.status_code == 422


def test_seeded_demo_login(client: TestClient) -> None:
    response = client.post(
        "/auth/login",
        json={"email": "demo1@example.com", "password": "DemoPass123!"},
    )
    assert response.status_code == 200
    assert response.json()["patient_id"] == 1


def test_login_and_me(client: TestClient) -> None:
    email = _unique_email()
    registered = client.post("/auth/register", json=_register_payload(email))
    assert registered.status_code == 201
    token = registered.json()["access_token"]

    login = client.post(
        "/auth/login",
        json={"email": email, "password": PASSWORD},
    )
    assert login.status_code == 200
    assert login.json()["patient_id"] == registered.json()["patient_id"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == email


def test_login_with_wrong_password_is_rejected(client: TestClient) -> None:
    email = _unique_email()
    client.post("/auth/register", json=_register_payload(email))

    response = client.post(
        "/auth/login",
        json={"email": email, "password": "WrongPassword123!"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credentials"


def test_missing_or_invalid_token_is_rejected(client: TestClient) -> None:
    missing = client.get("/auth/me")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"

    invalid = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer not-a-token"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "invalid_token"
