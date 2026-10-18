from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Appointment, AppointmentEvent, AppointmentStatus, Doctor
from app.services.availability import invalidate_availability
from app.services.booking import is_slot_conflict, normalize_start, slot_for_start


async def _db_now(session: AsyncSession) -> datetime:
    now = await session.scalar(text("SELECT now()"))
    if now is None:
        raise AppError(503, "database_unavailable", "Database time is unavailable.")
    return now


async def _lock_appointment(
    session: AsyncSession,
    appointment_id: UUID,
    patient_id: int,
) -> Appointment:
    appointment = await session.scalar(
        select(Appointment).where(Appointment.id == appointment_id).with_for_update()
    )
    if appointment is None or appointment.patient_id != patient_id:
        raise AppError(404, "not_found", "Resource not found.")
    return appointment


def _doctor_day(doctor: Doctor, start_at: datetime) -> date:
    return start_at.astimezone(ZoneInfo(doctor.timezone)).date()


async def cancel_appointment(
    session: AsyncSession,
    *,
    appointment_id: UUID,
    patient_id: int,
    reason: str,
) -> Appointment:
    appointment = await _lock_appointment(session, appointment_id, patient_id)
    if appointment.status != AppointmentStatus.booked:
        raise AppError(409, "already_cancelled", "This appointment is already cancelled.")

    doctor = await session.get(Doctor, appointment.doctor_id)
    if doctor is None:
        raise AppError(404, "doctor_not_found", "Doctor not found.")

    db_now = await _db_now(session)
    if appointment.start_at <= db_now:
        raise AppError(
            409,
            "appointment_started",
            "This appointment has already started.",
        )

    appointment.status = AppointmentStatus.cancelled
    appointment.cancellation_reason = reason
    appointment.cancelled_at = db_now
    appointment.updated_at = db_now
    session.add(
        AppointmentEvent(
            appointment_id=appointment.id,
            event_type="cancelled",
            reason=reason,
        )
    )
    await session.commit()
    await session.refresh(appointment)

    invalidate_availability(doctor.id, _doctor_day(doctor, appointment.start_at))
    return appointment


async def reschedule_appointment(
    session: AsyncSession,
    *,
    appointment_id: UUID,
    patient_id: int,
    start_at: datetime,
) -> Appointment:
    appointment = await _lock_appointment(session, appointment_id, patient_id)
    if appointment.status != AppointmentStatus.booked:
        raise AppError(409, "appointment_cancelled", "This appointment is cancelled.")

    doctor = await session.get(Doctor, appointment.doctor_id)
    if doctor is None:
        raise AppError(404, "doctor_not_found", "Doctor not found.")

    db_now = await _db_now(session)
    if appointment.start_at <= db_now:
        raise AppError(
            409,
            "appointment_started",
            "This appointment has already started.",
        )

    new_start = normalize_start(start_at, doctor)
    old_start = appointment.start_at
    if new_start == old_start:
        await session.commit()
        return appointment

    slot = await slot_for_start(session, doctor, new_start, db_now)
    appointment.start_at = slot.start_at
    appointment.end_at = slot.end_at
    appointment.updated_at = db_now
    session.add(
        AppointmentEvent(
            appointment_id=appointment.id,
            event_type="rescheduled",
            old_start_at=old_start,
            new_start_at=slot.start_at,
        )
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        if is_slot_conflict(exc):
            raise AppError(
                409,
                "slot_taken",
                "That slot has just been booked.",
                details={"start_at": slot.start_at.isoformat()},
            ) from exc
        raise

    await session.refresh(appointment)
    invalidate_availability(doctor.id, _doctor_day(doctor, old_start))
    invalidate_availability(doctor.id, _doctor_day(doctor, appointment.start_at))
    return appointment
