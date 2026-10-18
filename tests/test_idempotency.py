import pytest

from app.core.errors import AppError
from app.schemas.appointment import BookingRequest
from app.services.idempotency import hash_request, validate_key


def test_idempotency_key_validation() -> None:
    validate_key("valid-key-123")

    with pytest.raises(AppError) as exc_info:
        validate_key("short")
    assert exc_info.value.code == "invalid_idempotency_key"

    with pytest.raises(AppError):
        validate_key("bad key with spaces")


def test_start_at_and_start_time_hash_the_same() -> None:
    canonical = BookingRequest(
        doctor_id=1,
        start_at="2026-10-19T09:00:00+03:00",
        patient_id=1,
    )
    alias = BookingRequest(
        doctor_id=1,
        start_time="2026-10-19T09:00:00+03:00",
        patient_id=1,
    )
    different = BookingRequest(
        doctor_id=1,
        start_at="2026-10-19T10:00:00+03:00",
        patient_id=1,
    )

    assert hash_request(canonical) == hash_request(alias)
    assert hash_request(canonical) != hash_request(different)
