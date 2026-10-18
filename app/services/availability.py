from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from cachetools import TTLCache
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.db.models import Appointment, AppointmentStatus, Doctor, DoctorWorkingHour

settings = get_settings()

_cache: TTLCache = TTLCache(
    maxsize=2048,
    ttl=settings.availability_cache_ttl_seconds,
)


@dataclass(frozen=True)
class Slot:
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class Availability:
    doctor_id: int
    day: date
    timezone: str
    slot_minutes: int
    slots: tuple[Slot, ...]


def generate_slots(
    day: date,
    timezone: str,
    slot_minutes: int,
    intervals: list[tuple[time, time]],
) -> list[Slot]:
    tz = ZoneInfo(timezone)
    step = timedelta(minutes=slot_minutes)
    slots: dict[datetime, Slot] = {}

    for start_time, end_time in intervals:
        current = datetime.combine(day, start_time, tzinfo=tz)
        end = datetime.combine(day, end_time, tzinfo=tz)
        while current + step <= end:
            start_utc = current.astimezone(UTC)
            slots[start_utc] = Slot(
                start_at=start_utc,
                end_at=(current + step).astimezone(UTC),
            )
            current += step

    return sorted(slots.values(), key=lambda slot: slot.start_at)


async def _db_now(session: AsyncSession) -> datetime:
    now = await session.scalar(text("SELECT now()"))
    if now is None:
        raise AppError(503, "database_unavailable", "Database time is unavailable.")
    return now


async def _load_availability(
    session: AsyncSession,
    doctor: Doctor,
    day: date,
    db_now: datetime,
) -> Availability:
    tz = ZoneInfo(doctor.timezone)

    hours = (
        await session.scalars(
            select(DoctorWorkingHour)
            .where(
                DoctorWorkingHour.doctor_id == doctor.id,
                DoctorWorkingHour.weekday == day.weekday(),
            )
            .order_by(DoctorWorkingHour.start_time)
        )
    ).all()
    intervals = [(hour.start_time, hour.end_time) for hour in hours]
    slots = generate_slots(day, doctor.timezone, doctor.slot_minutes, intervals)

    day_start_local = datetime.combine(day, time.min, tzinfo=tz)
    day_start = day_start_local.astimezone(UTC)
    day_end = (day_start_local + timedelta(days=1)).astimezone(UTC)

    booked = (
        await session.scalars(
            select(Appointment).where(
                Appointment.doctor_id == doctor.id,
                Appointment.status == AppointmentStatus.booked,
                Appointment.start_at < day_end,
                Appointment.end_at > day_start,
            )
        )
    ).all()

    cutoff = db_now + timedelta(hours=1)
    available = [
        slot
        for slot in slots
        if slot.start_at >= cutoff
        and not any(
            slot.start_at < appointment.end_at and slot.end_at > appointment.start_at
            for appointment in booked
        )
    ]

    return Availability(
        doctor_id=doctor.id,
        day=day,
        timezone=doctor.timezone,
        slot_minutes=doctor.slot_minutes,
        slots=tuple(available),
    )


def _cache_key(doctor_id: int, day: date) -> tuple[int, str]:
    return doctor_id, day.isoformat()


async def get_availability(
    session: AsyncSession,
    doctor_id: int,
    day: date,
) -> Availability:
    key = _cache_key(doctor_id, day)
    cached = _cache.get(key)
    if cached is not None:
        return cached

    doctor = await session.get(Doctor, doctor_id)
    if doctor is None:
        raise AppError(404, "doctor_not_found", "Doctor not found.")

    result = await _load_availability(session, doctor, day, await _db_now(session))
    _cache[key] = result
    return result


def invalidate_availability(doctor_id: int, day: date) -> None:
    _cache.pop(_cache_key(doctor_id, day), None)


def clear_availability_cache() -> None:
    _cache.clear()
