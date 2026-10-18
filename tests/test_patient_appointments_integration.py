import os
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Appointment, AppointmentEvent, AppointmentStatus

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


def _book(client: TestClient, token: str, day: date, hour: int):
    return client.post(
        "/appointments",
        json={"doctor_id": 1, "start_at": _iso(day, hour), "patient_id": 1},
        headers={"Authorization": f"Bearer {token}"},
    )


def _insert_past_booked() -> None:
    start = datetime.now(UTC) - timedelta(days=1)
    with Session(sync_engine) as session:
        session.add(
            Appointment(
                doctor_id=1,
                patient_id=1,
                start_at=start,
                end_at=start + timedelta(minutes=30),
                status=AppointmentStatus.booked,
            )
        )
        session.commit()


def test_list_upcoming_booked_appointments(client: TestClient) -> None:
    token = _login(client)
    day = _next_weekday()
    headers = {"Authorization": f"Bearer {token}"}

    first = _book(client, token, day, 9)
    second = _book(client, token, day, 10)
    cancelled = _book(client, token, day, 11)
    assert first.status_code == second.status_code == cancelled.status_code == 201

    cancel = client.patch(
        f"/appointments/{cancelled.json()['id']}/cancel",
        json={"reason": "test"},
        headers=headers,
    )
    assert cancel.status_code == 200

    _insert_past_booked()

    response = client.get("/patients/1/appointments", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    starts = [datetime.fromisoformat(item["start_at"]) for item in body["data"]]
    assert starts == [
        datetime.combine(day, time(9, 0), tzinfo=Nairobi).astimezone(UTC),
        datetime.combine(day, time(10, 0), tzinfo=Nairobi).astimezone(UTC),
    ]


def test_keyset_pagination(client: TestClient) -> None:
    token = _login(client)
    day = _next_weekday()
    headers = {"Authorization": f"Bearer {token}"}
    _book(client, token, day, 9)
    _book(client, token, day, 10)

    first_page = client.get(
        "/patients/1/appointments",
        params={"limit": 1},
        headers=headers,
    )
    assert first_page.status_code == 200
    first_body = first_page.json()
    assert len(first_body["data"]) == 1
    assert first_body["has_more"] is True
    assert first_body["next_cursor"]

    second_page = client.get(
        "/patients/1/appointments",
        params={"limit": 1, "cursor": first_body["next_cursor"]},
        headers=headers,
    )
    assert second_page.status_code == 200
    second_body = second_page.json()
    assert len(second_body["data"]) == 1
    assert second_body["has_more"] is False
    assert second_body["next_cursor"] is None
    assert second_body["data"][0]["id"] != first_body["data"][0]["id"]


def test_list_validation_and_ownership(client: TestClient) -> None:
    token = _login(client)
    other_token = _login(client, "demo2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    invalid_cursor = client.get(
        "/patients/1/appointments",
        params={"cursor": "not-a-cursor"},
        headers=headers,
    )
    assert invalid_cursor.status_code == 400
    assert invalid_cursor.json()["error"]["code"] == "invalid_cursor"

    too_many = client.get(
        "/patients/1/appointments",
        params={"limit": 101},
        headers=headers,
    )
    assert too_many.status_code == 422

    non_owner = client.get(
        "/patients/1/appointments",
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert non_owner.status_code == 404

    unauthenticated = client.get("/patients/1/appointments")
    assert unauthenticated.status_code == 401
