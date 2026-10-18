from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.core.errors import AppError
from app.db.models import Appointment
from app.services.patient_appointments import decode_cursor, encode_cursor


def test_cursor_round_trip() -> None:
    appointment = Appointment(
        id=uuid4(),
        doctor_id=1,
        patient_id=1,
        start_at=datetime(2026, 10, 19, 6, 0, tzinfo=UTC),
        end_at=datetime(2026, 10, 19, 6, 30, tzinfo=UTC),
    )

    cursor = encode_cursor(appointment)
    start_at, appointment_id = decode_cursor(cursor)

    assert start_at == appointment.start_at
    assert appointment_id == appointment.id


def test_invalid_cursor_is_rejected() -> None:
    with pytest.raises(AppError) as exc_info:
        decode_cursor("not-a-cursor")
    assert exc_info.value.code == "invalid_cursor"
