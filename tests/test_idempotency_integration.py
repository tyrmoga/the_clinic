import os
from datetime import date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Appointment, AppointmentEvent, IdempotencyKey
from app.services.availability import clear_availability_cache

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 and point DATABASE_URL at a migrated, seeded test database",
)

Nairobi = ZoneInfo("Africa/Nairobi")
sync_engine = create_engine(get_settings().database_url, pool_pre_ping=True)


@pytest.fixture(autouse=True)
def clean_appointments() -> None:
    def cleanup() -> None:
        with Session(sync_engine) as session:
            session.execute(delete(AppointmentEvent))
            session.execute(delete(Appointment))
            session.execute(delete(IdempotencyKey))
            session.commit()
        clear_availability_cache()

    cleanup()
    yield
    cleanup()


def _next_weekday() -> date:
    today = datetime.now(Nairobi).date()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def _iso(day: date, hour: int) -> str:
    return f"{day.isoformat()}T{hour:02d}:00:00+03:00"


def _token(client: TestClient) -> str:
    response = client.post(
        "/auth/login",
        json={"email": "demo1@example.com", "password": "DemoPass123!"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _post(client: TestClient, token: str, body: dict, key: str | None = None):
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post("/appointments", json=body, headers=headers)


def test_replay_returns_the_stored_response(client: TestClient) -> None:
    token = _token(client)
    day = _next_weekday()
    key = uuid4().hex
    body = {"doctor_id": 1, "start_at": _iso(day, 9), "patient_id": 1}

    first = _post(client, token, body, key)
    assert first.status_code == 201

    replay = _post(client, token, body, key)
    assert replay.status_code == 201
    assert replay.json() == first.json()


def test_same_key_with_a_different_body_is_rejected(client: TestClient) -> None:
    token = _token(client)
    day = _next_weekday()
    key = uuid4().hex

    first = _post(
        client,
        token,
        {"doctor_id": 1, "start_at": _iso(day, 9), "patient_id": 1},
        key,
    )
    assert first.status_code == 201

    second = _post(
        client,
        token,
        {"doctor_id": 1, "start_at": _iso(day, 10), "patient_id": 1},
        key,
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "idempotency_key_reused"


def test_replay_without_a_key_is_not_stored(client: TestClient) -> None:
    token = _token(client)
    day = _next_weekday()

    response = _post(
        client,
        token,
        {"doctor_id": 1, "start_at": _iso(day, 11), "patient_id": 1},
    )
    assert response.status_code == 201

    with Session(sync_engine) as session:
        keys = session.scalar(select(func.count()).select_from(IdempotencyKey))
    assert keys == 0


def test_short_idempotency_key_is_rejected(client: TestClient) -> None:
    token = _token(client)
    day = _next_weekday()

    response = _post(
        client,
        token,
        {"doctor_id": 1, "start_at": _iso(day, 12), "patient_id": 1},
        "short",
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_idempotency_key"
