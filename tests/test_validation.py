import pytest
from pydantic import ValidationError

from app.schemas.appointment import CancelRequest
from app.schemas.auth import RegisterRequest


@pytest.mark.parametrize("field", ["full_name", "phone"])
def test_nul_bytes_are_rejected_in_patient_fields(field: str) -> None:
    payload = {
        "email": "valid@example.com",
        "password": "Password123!",
        "full_name": "Valid Name",
    }
    payload[field] = "A\x00B"

    with pytest.raises(ValidationError):
        RegisterRequest(**payload)


def test_nul_bytes_are_rejected_in_cancel_reason() -> None:
    with pytest.raises(ValidationError):
        CancelRequest(reason="A\x00B")
