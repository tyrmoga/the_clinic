from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import (
    Appointment,
    AppointmentEvent,
    AppointmentStatus,
    Doctor,
    IdempotencyKey,
)
from app.schemas.appointment import AppointmentResponse
from app.services.availability import Slot, invalidate_availability, slots_for_doctor
from app.services.idempotency import ENDPOINT, StoredResponse, get_stored


@dataclass
class BookingOutcome:
    appointment: Appointment | None = None
    stored: StoredResponse | None = None


def normalize_start(start_at: datetime, doctor: Doctor) -> datetime:
    if start_at.tzinfo is None:
        start_at = start_at.replace(tzinfo=ZoneInfo(doctor.timezone))
    return start_at.astimezone(UTC)


def is_slot_conflict(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == "one_active_booking_per_slot"


def _is_idempotency_conflict(exc: IntegrityError) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == "idempotency_keys_patient_endpoint_key_key"


async def slot_for_start(
    session: AsyncSession,
    doctor: Doctor,
    start_at: datetime,
    db_now: datetime,
) -> Slot:
    local_day = start_at.astimezone(ZoneInfo(doctor.timezone)).date()
    slots = await slots_for_doctor(session, doctor, local_day)
    slot = next((candidate for candidate in slots if candidate.start_at == start_at), None)
    if slot is None:
        raise AppError(
            422,
            "invalid_slot",
            "The requested time is not a valid slot for this doctor.",
        )

    if start_at < db_now + timedelta(hours=1):
        raise AppError(
            422,
            "slot_too_soon",
            "Appointments must be booked at least 1 hour in advance.",
        )
    return slot


async def create_appointment(
    session: AsyncSession,
    *,
    patient_id: int,
    doctor_id: int,
    start_at: datetime,
    body_patient_id: int | None,
    idempotency_key: str | None = None,
    idempotency_hash: str | None = None,
) -> BookingOutcome:
    if body_patient_id is not None and body_patient_id != patient_id:
        raise AppError(404, "not_found", "Resource not found.")

    if idempotency_key and idempotency_hash:
        stored = await get_stored(
            session,
            patient_id=patient_id,
            key=idempotency_key,
            request_hash=idempotency_hash,
        )
        if stored is not None:
            return BookingOutcome(stored=stored)

    doctor = await session.get(Doctor, doctor_id)
    if doctor is None:
        raise AppError(404, "doctor_not_found", "Doctor not found.")

    start_at = normalize_start(start_at, doctor)
    db_now = await session.scalar(text("SELECT now()"))
    if db_now is None:
        raise AppError(503, "database_unavailable", "Database time is unavailable.")

    slot = await slot_for_start(session, doctor, start_at, db_now)

    appointment = Appointment(
        doctor_id=doctor.id,
        patient_id=patient_id,
        start_at=slot.start_at,
        end_at=slot.end_at,
        status=AppointmentStatus.booked,
    )
    session.add(appointment)
    try:
        await session.flush()
        await session.refresh(appointment)

        if idempotency_key and idempotency_hash:
            response_body = AppointmentResponse.model_validate(appointment).model_dump(mode="json")
            session.add(
                IdempotencyKey(
                    patient_id=patient_id,
                    endpoint=ENDPOINT,
                    key=idempotency_key,
                    request_hash=idempotency_hash,
                    response_status=201,
                    response_body=response_body,
                )
            )
            await session.flush()
    except IntegrityError as exc:
        await session.rollback()

        if idempotency_key and idempotency_hash:
            stored = await get_stored(
                session,
                patient_id=patient_id,
                key=idempotency_key,
                request_hash=idempotency_hash,
            )
            if stored is not None:
                return BookingOutcome(stored=stored)

        if is_slot_conflict(exc):
            raise AppError(
                409,
                "slot_taken",
                "That slot has just been booked.",
                details={"start_at": slot.start_at.isoformat()},
            ) from exc
        if _is_idempotency_conflict(exc):
            raise AppError(
                409,
                "idempotency_key_reused",
                "This Idempotency-Key was already used with a different request.",
            ) from exc
        raise

    session.add(
        AppointmentEvent(
            appointment_id=appointment.id,
            event_type="created",
            new_start_at=appointment.start_at,
        )
    )
    await session.commit()
    await session.refresh(appointment)

    local_day = slot.start_at.astimezone(ZoneInfo(doctor.timezone)).date()
    invalidate_availability(doctor.id, local_day)
    return BookingOutcome(appointment=appointment)
