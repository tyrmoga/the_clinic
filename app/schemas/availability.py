from datetime import date, datetime

from pydantic import BaseModel, field_serializer

from app.schemas.common import to_utc_iso


class SlotResponse(BaseModel):
    start_at: datetime
    end_at: datetime

    @field_serializer("start_at", "end_at", when_used="json")
    def serialize_datetime(self, value: datetime) -> str:
        return to_utc_iso(value) or ""


class AvailabilityResponse(BaseModel):
    doctor_id: int
    date: date
    timezone: str
    slot_minutes: int
    slots: list[SlotResponse]
