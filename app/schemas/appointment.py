from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
)

from app.db.models import AppointmentStatus
from app.schemas.common import to_utc_iso


class BookingRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={
            "examples": [
                {
                    "doctor_id": 1,
                    "start_at": "2026-10-20T09:00:00+03:00",
                    "patient_id": 1,
                }
            ]
        },
    )

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

    @field_serializer(
        "start_at",
        "end_at",
        "cancelled_at",
        "created_at",
        "updated_at",
        when_used="json",
    )
    def serialize_datetime(self, value: datetime | None) -> str | None:
        return to_utc_iso(value)


class CancelRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"reason": "Patient unwell"}]},
    )

    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]


class RescheduleRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        json_schema_extra={"examples": [{"start_at": "2026-10-21T10:30:00+03:00"}]},
    )

    start_at: datetime = Field(
        validation_alias=AliasChoices("start_at", "start_time"),
    )


class AppointmentListResponse(BaseModel):
    data: list[AppointmentResponse]
    next_cursor: str | None = None
    has_more: bool
