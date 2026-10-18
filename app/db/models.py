from datetime import datetime, time
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AppointmentStatus(StrEnum):
    booked = "booked"
    cancelled = "cancelled"


class Doctor(Base):
    __tablename__ = "doctors"
    __table_args__ = (CheckConstraint("slot_minutes = 30", name="slot_minutes_is_30"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    timezone: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'Africa/Nairobi'")
    )
    slot_minutes: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("30")
    )

    working_hours: Mapped[list["DoctorWorkingHour"]] = relationship(
        back_populates="doctor", cascade="all, delete-orphan"
    )


class DoctorWorkingHour(Base):
    __tablename__ = "doctor_working_hours"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        CheckConstraint("end_time > start_time", name="working_hours_end_after_start"),
        UniqueConstraint(
            "doctor_id",
            "weekday",
            "start_time",
            name="doctor_working_hours_slot_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    doctor_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)

    doctor: Mapped[Doctor] = relationship(back_populates="working_hours")


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (UniqueConstraint("email", name="patients_email_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("end_at = start_at + interval '30 minutes'", name="end_at_is_30_min"),
        CheckConstraint(
            "status <> 'cancelled' OR "
            "(cancellation_reason IS NOT NULL AND cancelled_at IS NOT NULL)",
            name="cancelled_fields",
        ),
        CheckConstraint(
            "cancellation_reason IS NULL OR char_length(cancellation_reason) BETWEEN 1 AND 500",
            name="cancellation_reason_length",
        ),
        Index(
            "one_active_booking_per_slot",
            "doctor_id",
            "start_at",
            unique=True,
            postgresql_where=text("status = 'booked'"),
        ),
        Index(
            "appointments_patient_upcoming",
            "patient_id",
            "start_at",
            postgresql_where=text("status = 'booked'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    doctor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("doctors.id"), nullable=False)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("patients.id"), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        SAEnum(AppointmentStatus, name="appointment_status", native_enum=True),
        nullable=False,
        default=AppointmentStatus.booked,
        server_default=text("'booked'::appointment_status"),
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    doctor: Mapped[Doctor] = relationship()
    patient: Mapped[Patient] = relationship()
    events: Mapped[list["AppointmentEvent"]] = relationship(
        back_populates="appointment", cascade="all, delete-orphan"
    )


class AppointmentEvent(Base):
    __tablename__ = "appointment_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('created', 'cancelled', 'rescheduled')",
            name="appointment_event_type",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    appointment_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("appointments.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    old_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    new_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    appointment: Mapped[Appointment] = relationship(back_populates="events")


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "patient_id",
            "endpoint",
            "key",
            name="idempotency_keys_patient_endpoint_key_key",
        ),
        Index("idempotency_keys_created", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    patient_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("patients.id"), nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    response_status: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    response_body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
