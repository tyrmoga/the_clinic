from datetime import datetime
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.db.models import AppointmentStatus


class BookingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    doctor_id: int = Field(gt=0)
    start_at: datetime = Field(
        validation_alias=AliasChoices("start_at", "start_time"),
    )
    patient_id: int | None = Field(default=None, gt=0)


class AppointmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    doctor_id: int
    patient_id: int
    start_at: datetime
    end_at: datetime
    status: AppointmentStatus
    cancellation_reason: str | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime
