from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_patient_id
from app.core.errors import AppError
from app.db.session import get_session
from app.schemas.appointment import AppointmentListResponse, AppointmentResponse
from app.services.patient_appointments import list_patient_appointments

router = APIRouter(prefix="/patients", tags=["patients"])


@router.get(
    "/{patient_id}/appointments",
    response_model=AppointmentListResponse,
    summary="List a patient's upcoming appointments",
    description="Keyset-paginated list of booked appointments starting in the future.",
)
async def list_appointments(
    patient_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    current_patient_id: int = Depends(get_current_patient_id),
    session: AsyncSession = Depends(get_session),
) -> AppointmentListResponse:
    if patient_id != current_patient_id:
        raise AppError(404, "not_found", "Resource not found.")

    page, next_cursor, has_more = await list_patient_appointments(
        session,
        patient_id=patient_id,
        limit=limit,
        cursor=cursor,
    )
    return AppointmentListResponse(
        data=[AppointmentResponse.model_validate(appointment) for appointment in page],
        next_cursor=next_cursor,
        has_more=has_more,
    )
