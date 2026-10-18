from datetime import UTC, datetime

from app.db.models import Doctor
from app.services.booking import normalize_start


def test_normalize_start_treats_naive_values_as_doctor_local() -> None:
    doctor = Doctor(
        id=1,
        full_name="Dr. Wanjiku",
        timezone="Africa/Nairobi",
        slot_minutes=30,
    )

    naive = datetime(2026, 10, 19, 9, 0)
    aware = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)

    assert normalize_start(naive, doctor) == datetime(2026, 10, 19, 6, 0, tzinfo=UTC)
    assert normalize_start(aware, doctor) == aware
