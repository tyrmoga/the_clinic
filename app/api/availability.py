from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_patient_id
from app.core.errors import AppError
from app.db.session import get_session
from app.schemas.availability import AvailabilityResponse, SlotResponse
from app.services import availability as availability_service

router = APIRouter(prefix="/doctors", tags=["availability"])


@router.get(
    "/{doctor_id}/availability",
    response_model=AvailabilityResponse,
    summary="List available slots",
    description=(
        "Return free 30-minute slots for a doctor on a date, excluding booked "
        "slots and slots less than 1 hour away."
    ),
)
async def get_availability(
    doctor_id: int,
    date_str: str | None = Query(default=None, alias="date"),
    session: AsyncSession = Depends(get_session),
    _: int = Depends(get_current_patient_id),
) -> AvailabilityResponse:
    if date_str is None:
        raise AppError(400, "invalid_date", "date is required (YYYY-MM-DD).")

    try:
        day = date.fromisoformat(date_str)
    except ValueError as exc:
        raise AppError(400, "invalid_date", "date must be YYYY-MM-DD.") from exc

    result = await availability_service.get_availability(session, doctor_id, day)
    return AvailabilityResponse(
        doctor_id=result.doctor_id,
        date=result.day,
        timezone=result.timezone,
        slot_minutes=result.slot_minutes,
        slots=[SlotResponse(start_at=slot.start_at, end_at=slot.end_at) for slot in result.slots],
    )
