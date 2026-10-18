from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_patient_id
from app.db.models import Appointment
from app.db.session import get_session
from app.schemas.appointment import AppointmentResponse, BookingRequest
from app.services.booking import create_appointment

router = APIRouter(prefix="/appointments", tags=["appointments"])


@router.post("", response_model=AppointmentResponse, status_code=status.HTTP_201_CREATED)
async def book(
    payload: BookingRequest,
    patient_id: int = Depends(get_current_patient_id),
    session: AsyncSession = Depends(get_session),
) -> Appointment:
    return await create_appointment(
        session,
        patient_id=patient_id,
        doctor_id=payload.doctor_id,
        start_at=payload.start_at,
        body_patient_id=payload.patient_id,
    )
