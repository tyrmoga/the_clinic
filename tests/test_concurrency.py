import asyncio
import os
from datetime import UTC, date, datetime, time, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import Appointment, AppointmentEvent, AppointmentStatus, IdempotencyKey
from app.db.session import SessionLocal
from app.schemas.appointment import BookingRequest
from app.services.appointments import reschedule_appointment
from app.services.availability import clear_availability_cache
from app.services.booking import BookingOutcome, create_appointment
from app.services.idempotency import hash_request

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


def _utc(day: date, hour: int) -> datetime:
    return datetime.combine(day, time(hour, 0), tzinfo=Nairobi).astimezone(UTC)


async def _book(patient_id: int, start_at: datetime) -> BookingOutcome:
    async with SessionLocal() as session:
        return await create_appointment(
            session,
            patient_id=patient_id,
            doctor_id=1,
            start_at=start_at,
            body_patient_id=None,
        )


async def _book_idempotent(
    patient_id: int,
    start_at: datetime,
    key: str,
    request_hash: str,
) -> BookingOutcome:
    async with SessionLocal() as session:
        return await create_appointment(
            session,
            patient_id=patient_id,
            doctor_id=1,
            start_at=start_at,
            body_patient_id=None,
            idempotency_key=key,
            idempotency_hash=request_hash,
        )


async def _reschedule(appointment_id, start_at: datetime):
    async with SessionLocal() as session:
        return await reschedule_appointment(
            session,
            appointment_id=appointment_id,
            patient_id=1,
            start_at=start_at,
        )


def _booked_count(start_at: datetime) -> int:
    with Session(sync_engine) as session:
        return session.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.doctor_id == 1,
                Appointment.start_at == start_at,
                Appointment.status == AppointmentStatus.booked,
            )
        )


async def test_two_patients_racing_for_one_slot() -> None:
    day = _next_weekday()
    start_at = _utc(day, 9)

    results = await asyncio.gather(
        _book(1, start_at),
        _book(2, start_at),
        return_exceptions=True,
    )

    successes = [
        result
        for result in results
        if isinstance(result, BookingOutcome) and result.appointment is not None
    ]
    errors = [result for result in results if isinstance(result, AppError)]

    assert len(successes) == 1
    assert len(errors) == 1
    assert errors[0].status_code == 409
    assert errors[0].code == "slot_taken"
    assert _booked_count(start_at) == 1


async def test_two_reschedules_racing_for_one_slot() -> None:
    day = _next_weekday()
    first = await _book(1, _utc(day, 9))
    second = await _book(1, _utc(day, 10))
    assert first.appointment is not None
    assert second.appointment is not None

    target = _utc(day, 11)
    results = await asyncio.gather(
        _reschedule(first.appointment.id, target),
        _reschedule(second.appointment.id, target),
        return_exceptions=True,
    )

    successes = [result for result in results if isinstance(result, Appointment)]
    errors = [result for result in results if isinstance(result, AppError)]

    assert len(successes) == 1
    assert len(errors) == 1
    assert errors[0].status_code == 409
    assert errors[0].code == "slot_taken"
    assert _booked_count(target) == 1


async def test_two_requests_with_the_same_idempotency_key() -> None:
    day = _next_weekday()
    start_at = _utc(day, 9)
    key = uuid4().hex
    request_hash = hash_request(BookingRequest(doctor_id=1, start_at=start_at, patient_id=1))

    results = await asyncio.gather(
        _book_idempotent(1, start_at, key, request_hash),
        _book_idempotent(1, start_at, key, request_hash),
        return_exceptions=True,
    )

    assert not any(isinstance(result, Exception) for result in results)
    ids = {
        str(result.appointment.id)
        if result.appointment is not None
        else str(result.stored.body["id"])
        for result in results
        if isinstance(result, BookingOutcome)
    }
    assert len(ids) == 1
    assert _booked_count(start_at) == 1
