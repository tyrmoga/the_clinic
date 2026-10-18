import os
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Appointment, AppointmentStatus
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
            session.execute(delete(Appointment))
            session.commit()
        clear_availability_cache()

    cleanup()
    yield
    cleanup()


def _login(client: TestClient) -> str:
    response = client.post(
        "/auth/login",
        json={"email": "demo1@example.com", "password": "DemoPass123!"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _next_weekday() -> date:
    today = datetime.now(Nairobi).date()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def _book(day: date, hour: int, minute: int = 0) -> UUID:
    start = datetime.combine(day, time(hour, minute), tzinfo=Nairobi).astimezone(UTC)
    end = start + timedelta(minutes=30)
    with Session(sync_engine) as session:
        appointment = Appointment(
            doctor_id=1,
            patient_id=1,
            start_at=start,
            end_at=end,
            status=AppointmentStatus.booked,
        )
        session.add(appointment)
        session.commit()
        return appointment.id


def _slot_starts(response) -> list[datetime]:
    return [datetime.fromisoformat(slot["start_at"]) for slot in response.json()["slots"]]


def test_availability_returns_the_weekly_grid(client: TestClient) -> None:
    token = _login(client)
    day = _next_weekday()

    response = client.get(
        f"/doctors/1/availability?date={day.isoformat()}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["doctor_id"] == 1
    assert body["date"] == day.isoformat()
    assert body["timezone"] == "Africa/Nairobi"
    assert body["slot_minutes"] == 30
    starts = _slot_starts(response)
    assert len(starts) == 16
    assert starts[0] == datetime.combine(day, time(8, 0), tzinfo=Nairobi).astimezone(UTC)
    assert starts[8] == datetime.combine(day, time(13, 0), tzinfo=Nairobi).astimezone(UTC)


def test_booked_slot_is_hidden_and_cancelled_slot_returns(client: TestClient) -> None:
    token = _login(client)
    day = _next_weekday()
    headers = {"Authorization": f"Bearer {token}"}

    appointment_id = _book(day, 9)
    clear_availability_cache()

    booked = client.get(
        f"/doctors/1/availability?date={day.isoformat()}",
        headers=headers,
    )
    assert booked.status_code == 200
    assert len(booked.json()["slots"]) == 15
    booked_start = datetime.combine(day, time(9, 0), tzinfo=Nairobi).astimezone(UTC)
    assert booked_start not in _slot_starts(booked)

    with Session(sync_engine) as session:
        appointment = session.get(Appointment, appointment_id)
        assert appointment is not None
        appointment.status = AppointmentStatus.cancelled
        appointment.cancellation_reason = "test"
        appointment.cancelled_at = datetime.now(UTC)
        session.commit()
    clear_availability_cache()

    cancelled = client.get(
        f"/doctors/1/availability?date={day.isoformat()}",
        headers=headers,
    )
    assert cancelled.status_code == 200
    assert len(cancelled.json()["slots"]) == 16
    assert booked_start in _slot_starts(cancelled)


def test_availability_validation_and_not_found(client: TestClient) -> None:
    token = _login(client)
    day = _next_weekday()
    headers = {"Authorization": f"Bearer {token}"}

    missing_date = client.get("/doctors/1/availability", headers=headers)
    assert missing_date.status_code == 400
    assert missing_date.json()["error"]["code"] == "invalid_date"

    bad_date = client.get("/doctors/1/availability?date=nope", headers=headers)
    assert bad_date.status_code == 400

    unknown_doctor = client.get(
        f"/doctors/9999/availability?date={day.isoformat()}",
        headers=headers,
    )
    assert unknown_doctor.status_code == 404
    assert unknown_doctor.json()["error"]["code"] == "doctor_not_found"

    unauthenticated = client.get(f"/doctors/1/availability?date={day.isoformat()}")
    assert unauthenticated.status_code == 401
