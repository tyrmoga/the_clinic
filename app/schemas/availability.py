from datetime import date, datetime

from pydantic import BaseModel


class SlotResponse(BaseModel):
    start_at: datetime
    end_at: datetime


class AvailabilityResponse(BaseModel):
    doctor_id: int
    date: date
    timezone: str
    slot_minutes: int
    slots: list[SlotResponse]
