import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import Appointment, AppointmentStatus, Doctor, Patient
from app.db.session import SessionLocal
from app.seed import seed

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_TESTS") != "1",
    reason="set RUN_DB_TESTS=1 and point DATABASE_URL at a migrated test database",
)


@pytest.fixture(autouse=True)
async def clean_appointments() -> None:
    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM appointment_events"))
        await session.execute(text("DELETE FROM appointments"))
        await session.commit()

    yield

    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM appointment_events"))
        await session.execute(text("DELETE FROM appointments"))
        await session.commit()


async def test_seed_is_idempotent() -> None:
    await seed()
    await seed()

    async with SessionLocal() as session:
        doctors = await session.scalar(select(func.count()).select_from(Doctor))
        patients = await session.scalar(select(func.count()).select_from(Patient))

    assert doctors == 5
    assert patients == 5


async def test_only_one_active_booking_per_doctor_slot() -> None:
    await seed()

    start_at = (datetime.now(UTC) + timedelta(days=2)).replace(second=0, microsecond=0)
    end_at = start_at + timedelta(minutes=30)

    async with SessionLocal() as session:
        first = Appointment(
            doctor_id=1,
            patient_id=1,
            start_at=start_at,
            end_at=end_at,
            status=AppointmentStatus.booked,
        )
        session.add(first)
        await session.commit()
        first_id = first.id

        second = Appointment(
            doctor_id=1,
            patient_id=2,
            start_at=start_at,
            end_at=end_at,
            status=AppointmentStatus.booked,
        )
        session.add(second)
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

        first = await session.get(Appointment, first_id)
        assert first is not None
        first.status = AppointmentStatus.cancelled
        first.cancellation_reason = "test"
        first.cancelled_at = datetime.now(UTC)
        await session.commit()

        replacement = Appointment(
            doctor_id=1,
            patient_id=2,
            start_at=start_at,
            end_at=end_at,
            status=AppointmentStatus.booked,
        )
        session.add(replacement)
        await session.commit()

        count = await session.scalar(select(func.count()).select_from(Appointment))
        assert count == 2
