import asyncio
from datetime import time

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.security import hash_password
from app.db.models import Doctor, DoctorWorkingHour, Patient
from app.db.session import SessionLocal, engine

DEMO_PASSWORD = "DemoPass123!"

DOCTORS = [
    {
        "id": 1,
        "full_name": "Dr. Wanjiku",
        "timezone": "Africa/Nairobi",
        "slot_minutes": 30,
    },
    {
        "id": 2,
        "full_name": "Dr. Otieno",
        "timezone": "Africa/Nairobi",
        "slot_minutes": 30,
    },
    {
        "id": 3,
        "full_name": "Dr. Mwangi",
        "timezone": "Africa/Nairobi",
        "slot_minutes": 30,
    },
    {
        "id": 4,
        "full_name": "Dr. Achieng",
        "timezone": "Africa/Nairobi",
        "slot_minutes": 30,
    },
    {
        "id": 5,
        "full_name": "Dr. Kamau",
        "timezone": "Africa/Nairobi",
        "slot_minutes": 30,
    },
]

WORKING_HOURS = [
    {
        "doctor_id": doctor["id"],
        "weekday": weekday,
        "start_time": start_time,
        "end_time": end_time,
    }
    for doctor in DOCTORS
    for weekday in range(5)
    for start_time, end_time in (
        (time(8, 0), time(12, 0)),
        (time(13, 0), time(17, 0)),
    )
]

PATIENTS = [
    {
        "id": 1,
        "full_name": "Demo Patient One",
        "email": "demo1@example.com",
        "phone": "+254700000001",
    },
    {
        "id": 2,
        "full_name": "Demo Patient Two",
        "email": "demo2@example.com",
        "phone": "+254700000002",
    },
    {
        "id": 3,
        "full_name": "Demo Patient Three",
        "email": "demo3@example.com",
        "phone": "+254700000003",
    },
    {
        "id": 4,
        "full_name": "Demo Patient Four",
        "email": "demo4@example.com",
        "phone": "+254700000004",
    },
    {
        "id": 5,
        "full_name": "Demo Patient Five",
        "email": "demo5@example.com",
        "phone": "+254700000005",
    },
]


async def seed() -> None:
    async with SessionLocal() as session:
        existing_emails = set(await session.scalars(select(Patient.email)))

        for doctor in DOCTORS:
            await session.execute(pg_insert(Doctor).values(**doctor).on_conflict_do_nothing())

        for working_hours in WORKING_HOURS:
            await session.execute(
                pg_insert(DoctorWorkingHour).values(**working_hours).on_conflict_do_nothing()
            )

        for patient in PATIENTS:
            if patient["email"] in existing_emails:
                continue
            await session.execute(
                pg_insert(Patient)
                .values(**patient, password_hash=hash_password(DEMO_PASSWORD))
                .on_conflict_do_nothing()
            )

        await session.commit()

        for table in ("doctors", "patients"):
            await session.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM {table}), 1), true)"
                )
            )
        await session.commit()

        doctor_count = await session.scalar(select(func.count()).select_from(Doctor))
        patient_count = await session.scalar(select(func.count()).select_from(Patient))
        print(f"Seed complete: {doctor_count} doctors, {patient_count} patients.")


async def _run() -> None:
    try:
        await seed()
    finally:
        await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
