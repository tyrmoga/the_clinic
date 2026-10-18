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


def _iso(day: date, hour: int) -> str:
    return f"{day.isoformat()}T{hour:02d}:00:00+03:00"


def _login(client: TestClient, email: str = "demo1@example.com") -> str:
    response = client.post(
        "/auth/login",
        json={"email": email, "password": "DemoPass123!"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _book(client: TestClient, token: str, day: date, hour: int, patient_id: int = 1):
    return client.post(
        "/appointments",
        json={"doctor_id": 1, "start_at": _iso(day, hour), "patient_id": patient_id},
        headers={"Authorization": f"Bearer {token}"},
    )


def _cancel(client: TestClient, token: str, appointment_id: str):
    return client.patch(
        f"/appointments/{appointment_id}/cancel",
        json={"reason": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )


def _reschedule(client: TestClient, token: str, appointment_id: str, start_at: str):
    return client.patch(
        f"/appointments/{appointment_id}/reschedule",
        json={"start_at": start_at},
        headers={"Authorization": f"Bearer {token}"},
    )


def _event_count(appointment_id: str) -> int:
    with Session(sync_engine) as session:
        return session.scalar(
            select(func.count())
            .select_from(AppointmentEvent)
            .where(AppointmentEvent.appointment_id == UUID(appointment_id))
        )


def _insert_booked(start_at: datetime) -> UUID:
    with Session(sync_engine) as session:
        appointment = Appointment(
            doctor_id=1,
            patient_id=1,
            start_at=start_at,
            end_at=start_at + timedelta(minutes=30),
            status=AppointmentStatus.booked,
        )
        session.add(appointment)
        session.commit()
        return appointment.id


def _slot_starts(response) -> list[datetime]:
    return [datetime.fromisoformat(slot["start_at"]) for slot in response.json()["slots"]]


def test_cancel_frees_the_slot(client: TestClient) -> None:
    token = _login(client)
    day = _next_weekday()
    headers = {"Authorization": f"Bearer {token}"}

    booked = _book(client, token, day, 9)
    assert booked.status_code == 201
    appointment_id = booked.json()["id"]

    before = client.get(f"/doctors/1/availability?date={day.isoformat()}", headers=headers)
    assert len(before.json()["slots"]) == 15

    cancelled = _cancel(client, token, appointment_id)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["cancellation_reason"] == "test"
    assert cancelled.json()["cancelled_at"] is not None

    after = client.get(f"/doctors/1/availability?date={day.isoformat()}", headers=headers)
    assert len(after.json()["slots"]) == 16
    assert datetime.fromisoformat(_iso(day, 9)) in _slot_starts(after)
    assert _event_count(appointment_id) == 2


def test_cancel_twice_is_rejected(client: TestClient) -> None:
    token = _login(client)
    appointment_id = _book(client, token, _next_weekday(), 9).json()["id"]

    assert _cancel(client, token, appointment_id).status_code == 200

    second = _cancel(client, token, appointment_id)
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "already_cancelled"


def test_cancel_started_appointment_is_rejected(client: TestClient) -> None:
    token = _login(client)
    appointment_id = _insert_booked(datetime.now(UTC) - timedelta(hours=2))

    response = _cancel(client, token, str(appointment_id))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "appointment_started"


def test_reschedule_moves_the_slot_and_frees_the_old_one(client: TestClient) -> None:
    token = _login(client)
    day = _next_weekday()
    headers = {"Authorization": f"Bearer {token}"}

    booked = _book(client, token, day, 9)
    assert booked.status_code == 201
    appointment_id = booked.json()["id"]

    moved = _reschedule(client, token, appointment_id, _iso(day, 10))
    assert moved.status_code == 200
    assert datetime.fromisoformat(moved.json()["start_at"]) == datetime.combine(
        day, time(10, 0), tzinfo=Nairobi
    ).astimezone(UTC)

    availability = client.get(f"/doctors/1/availability?date={day.isoformat()}", headers=headers)
    starts = _slot_starts(availability)
    assert datetime.fromisoformat(_iso(day, 9)) in starts
    assert datetime.fromisoformat(_iso(day, 10)) not in starts
    assert _event_count(appointment_id) == 2


def test_reschedule_to_same_slot_is_a_noop(client: TestClient) -> None:
    token = _login(client)
    day = _next_weekday()
    booked = _book(client, token, day, 9)
    appointment_id = booked.json()["id"]

    response = _reschedule(client, token, appointment_id, _iso(day, 9))
    assert response.status_code == 200
    assert response.json()["id"] == appointment_id
    assert response.json()["start_at"] == booked.json()["start_at"]
    assert _event_count(appointment_id) == 1


def test_reschedule_same_slot_inside_one_hour_is_a_noop(client: TestClient) -> None:
    token = _login(client)
    start = (datetime.now(UTC) + timedelta(minutes=40)).replace(second=0, microsecond=0)
    appointment_id = _insert_booked(start)

    response = _reschedule(client, token, str(appointment_id), start.isoformat())
    assert response.status_code == 200


def test_reschedule_cancelled_appointment_is_rejected(client: TestClient) -> None:
    token = _login(client)
    day = _next_weekday()
    appointment_id = _book(client, token, day, 9).json()["id"]
    assert _cancel(client, token, appointment_id).status_code == 200

    response = _reschedule(client, token, appointment_id, _iso(day, 10))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "appointment_cancelled"


def test_reschedule_started_appointment_is_rejected(client: TestClient) -> None:
    token = _login(client)
    appointment_id = _insert_booked(datetime.now(UTC) - timedelta(hours=2))

    response = _reschedule(
        client,
        token,
        str(appointment_id),
        _iso(_next_weekday(), 10),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "appointment_started"


def test_reschedule_to_taken_slot_is_rejected(client: TestClient) -> None:
    token = _login(client)
    second_token = _login(client, "demo2@example.com")
    day = _next_weekday()

    first = _book(client, token, day, 9)
    assert first.status_code == 201
    second = _book(client, second_token, day, 10, patient_id=2)
    assert second.status_code == 201

    response = _reschedule(client, token, first.json()["id"], _iso(day, 10))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "slot_taken"

    with Session(sync_engine) as session:
        appointment = session.get(Appointment, UUID(first.json()["id"]))
        assert appointment is not None
        assert appointment.start_at == datetime.combine(day, time(9, 0), tzinfo=Nairobi).astimezone(
            UTC
        )


def test_non_owner_cannot_cancel_or_reschedule(client: TestClient) -> None:
    token = _login(client)
    other_token = _login(client, "demo2@example.com")
    day = _next_weekday()
    appointment_id = _book(client, token, day, 9).json()["id"]

    cancel = _cancel(client, other_token, appointment_id)
    assert cancel.status_code == 404

    reschedule = _reschedule(client, other_token, appointment_id, _iso(day, 10))
    assert reschedule.status_code == 404
