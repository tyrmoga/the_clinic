import os
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Appointment, AppointmentEvent, AppointmentStatus
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
            session.commit()
        clear_availability_cache()

    cleanup()
    yield
    cleanup()


def _next_weekday() -> date:
    today = datetime.now(Nairobi).date()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def _login(client: TestClient, email: str = "demo1@example.com") -> str:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": "DemoPass123!"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _iso(day: date, hour: int, minute: int = 0, naive: bool = False) -> str:
    value = f"{day.isoformat()}T{hour:02d}:{minute:02d}:00"
    return value if naive else f"{value}+03:00"


def _book(
    client: TestClient,
    token: str,
    start_at: str,
    *,
    doctor_id: int = 1,
    patient_id: int | None = None,
    field: str = "start_at",
):
    payload: dict[str, object] = {"doctor_id": doctor_id}
    payload[field] = start_at
    if patient_id is not None:
        payload["patient_id"] = patient_id
    return client.post(
        "/appointments",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )


def _insert_cancelled(day: date, hour: int) -> None:
    start = datetime.combine(day, time(hour, 0), tzinfo=Nairobi).astimezone(UTC)
    with Session(sync_engine) as session:
        session.add(
            Appointment(
                doctor_id=1,
                patient_id=1,
                start_at=start,
                end_at=start + timedelta(minutes=30),
                status=AppointmentStatus.cancelled,
                cancellation_reason="test",
                cancelled_at=datetime.now(UTC),
            )
        )
        session.commit()


def _slot_starts(response) -> list[datetime]:
    return [datetime.fromisoformat(slot["start_at"]) for slot in response.json()["slots"]]


def test_book_creates_appointment_and_event(client: TestClient) -> None:
    token = _login(client)
    day = _next_weekday()

    response = _book(client, token, _iso(day, 9), patient_id=1)

    assert response.status_code == 201
    body = response.json()
    assert body["doctor_id"] == 1
    assert body["patient_id"] == 1
    assert body["status"] == "booked"
    assert datetime.fromisoformat(body["start_at"]) == datetime(
        day.year, day.month, day.day, 6, 0, tzinfo=UTC
    )
    assert datetime.fromisoformat(body["end_at"]) == datetime(
        day.year, day.month, day.day, 6, 30, tzinfo=UTC
    )

    with Session(sync_engine) as session:
        events = session.scalar(
            select(func.count())
            .select_from(AppointmentEvent)
            .where(AppointmentEvent.appointment_id == UUID(body["id"]))
        )
    assert events == 1


def test_double_booking_returns_409(client: TestClient) -> None:
    day = _next_weekday()
    first = _book(client, _login(client), _iso(day, 9), patient_id=1)
    assert first.status_code == 201

    second = _book(
        client,
        _login(client, "demo2@example.com"),
        _iso(day, 9),
        patient_id=2,
    )
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "slot_taken"


def test_start_time_alias_is_accepted(client: TestClient) -> None:
    response = _book(
        client,
        _login(client),
        _iso(_next_weekday(), 10),
        patient_id=1,
        field="start_time",
    )
    assert response.status_code == 201


def test_body_patient_id_must_match_token(client: TestClient) -> None:
    response = _book(
        client,
        _login(client),
        _iso(_next_weekday(), 11),
        patient_id=2,
    )
    assert response.status_code == 404


def test_outside_working_hours_is_rejected(client: TestClient) -> None:
    response = _book(
        client,
        _login(client),
        _iso(_next_weekday(), 7),
        patient_id=1,
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_slot"


def test_past_slot_is_rejected(client: TestClient) -> None:
    day = _next_weekday() - timedelta(days=7)
    response = _book(client, _login(client), _iso(day, 9), patient_id=1)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "slot_too_soon"


def test_naive_timestamp_uses_doctor_timezone(client: TestClient) -> None:
    response = _book(
        client,
        _login(client),
        _iso(_next_weekday(), 11, naive=True),
        patient_id=1,
    )
    assert response.status_code == 201
    body = response.json()
    assert datetime.fromisoformat(body["start_at"]).astimezone(UTC).hour == 8


def test_cancelled_slot_can_be_rebooked(client: TestClient) -> None:
    day = _next_weekday()
    _insert_cancelled(day, 14)

    response = _book(
        client,
        _login(client, "demo2@example.com"),
        _iso(day, 14),
        patient_id=2,
    )
    assert response.status_code == 201


def test_booking_invalidates_availability_cache(client: TestClient) -> None:
    day = _next_weekday()
    token = _login(client)
    headers = {"Authorization": f"Bearer {token}"}

    before = client.get(
        f"/doctors/1/availability?date={day.isoformat()}",
        headers=headers,
    )
    assert before.status_code == 200
    assert len(before.json()["slots"]) == 16

    assert _book(client, token, _iso(day, 9), patient_id=1).status_code == 201

    after = client.get(
        f"/doctors/1/availability?date={day.isoformat()}",
        headers=headers,
    )
    assert after.status_code == 200
    assert len(after.json()["slots"]) == 15
    assert datetime.fromisoformat(_iso(day, 9)) not in _slot_starts(after)
