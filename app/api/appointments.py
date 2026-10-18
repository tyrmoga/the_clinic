from uuid import UUID

from fastapi import APIRouter, Depends, Header, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_patient_id
from app.db.models import Appointment
from app.db.session import get_session
from app.schemas.appointment import (
    AppointmentResponse,
    BookingRequest,
    CancelRequest,
    RescheduleRequest,
)
from app.services.appointments import cancel_appointment, reschedule_appointment
from app.services.booking import create_appointment
from app.services.idempotency import hash_request, validate_key

router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def book(
    payload: BookingRequest,
    patient_id: int = Depends(get_current_patient_id),
    session: AsyncSession = Depends(get_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Appointment | JSONResponse:
    hash_value = None
    if idempotency_key is not None:
        validate_key(idempotency_key)
        hash_value = hash_request(payload)

    outcome = await create_appointment(
        session,
        patient_id=patient_id,
        doctor_id=payload.doctor_id,
        start_at=payload.start_at,
        body_patient_id=payload.patient_id,
        idempotency_key=idempotency_key,
        idempotency_hash=hash_value,
    )
    if outcome.stored is not None:
        return JSONResponse(
            status_code=outcome.stored.status_code,
            content=outcome.stored.body,
        )

    assert outcome.appointment is not None
    return outcome.appointment


@router.patch("/{appointment_id}/cancel", response_model=AppointmentResponse)
async def cancel(
    appointment_id: UUID,
    payload: CancelRequest,
    patient_id: int = Depends(get_current_patient_id),
    session: AsyncSession = Depends(get_session),
) -> Appointment:
    return await cancel_appointment(
        session,
        appointment_id=appointment_id,
        patient_id=patient_id,
        reason=payload.reason,
    )


@router.patch("/{appointment_id}/reschedule", response_model=AppointmentResponse)
async def reschedule(
    appointment_id: UUID,
    payload: RescheduleRequest,
    patient_id: int = Depends(get_current_patient_id),
    session: AsyncSession = Depends(get_session),
) -> Appointment:
    return await reschedule_appointment(
        session,
        appointment_id=appointment_id,
        patient_id=patient_id,
        start_at=payload.start_at,
    )
