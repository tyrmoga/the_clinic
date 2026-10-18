from sqlalchemy import CheckConstraint, UniqueConstraint

from app.db.base import Base
from app.db.models import Appointment, AppointmentStatus, Doctor, IdempotencyKey


def test_all_tables_are_present() -> None:
    assert set(Base.metadata.tables) == {
        "doctors",
        "doctor_working_hours",
        "patients",
        "appointments",
        "appointment_events",
        "idempotency_keys",
    }


def test_appointment_status_enum() -> None:
    assert {status.value for status in AppointmentStatus} == {"booked", "cancelled"}


def test_partial_unique_index_is_present() -> None:
    index = next(
        index
        for index in Appointment.__table__.indexes
        if index.name == "one_active_booking_per_slot"
    )
    assert index.unique is True
    predicate = index.dialect_options["postgresql"]["where"]
    assert "status = 'booked'" in str(predicate)


def test_appointment_check_constraints_are_named() -> None:
    names = {
        constraint.name
        for constraint in Appointment.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {"end_at_is_30_min", "cancelled_fields", "cancellation_reason_length"} <= names


def test_doctor_slot_minutes_is_fixed_at_thirty() -> None:
    names = {
        constraint.name
        for constraint in Doctor.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "slot_minutes_is_30" in names


def test_idempotency_key_is_unique_per_patient_endpoint_and_key() -> None:
    constraint = next(
        constraint
        for constraint in IdempotencyKey.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
        and constraint.name == "idempotency_keys_patient_endpoint_key_key"
    )
    assert {column.name for column in constraint.columns} == {
        "patient_id",
        "endpoint",
        "key",
    }
