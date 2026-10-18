"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-18

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

appointment_status = postgresql.ENUM(
    "booked",
    "cancelled",
    name="appointment_status",
    create_type=False,
)


def upgrade() -> None:
    appointment_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "doctors",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column(
            "timezone",
            sa.Text(),
            server_default=sa.text("'Africa/Nairobi'"),
            nullable=False,
        ),
        sa.Column(
            "slot_minutes",
            sa.SmallInteger(),
            server_default=sa.text("30"),
            nullable=False,
        ),
        sa.CheckConstraint("slot_minutes = 30", name="slot_minutes_is_30"),
        sa.PrimaryKeyConstraint("id", name="doctors_pkey"),
    )

    op.create_table(
        "patients",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="patients_pkey"),
        sa.UniqueConstraint("email", name="patients_email_key"),
    )

    op.create_table(
        "doctor_working_hours",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("doctor_id", sa.BigInteger(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_range"),
        sa.CheckConstraint("end_time > start_time", name="working_hours_end_after_start"),
        sa.ForeignKeyConstraint(
            ["doctor_id"],
            ["doctors.id"],
            name="doctor_working_hours_doctor_id_fkey",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="doctor_working_hours_pkey"),
        sa.UniqueConstraint(
            "doctor_id",
            "weekday",
            "start_time",
            name="doctor_working_hours_slot_key",
        ),
    )

    op.create_table(
        "appointments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("doctor_id", sa.BigInteger(), nullable=False),
        sa.Column("patient_id", sa.BigInteger(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            appointment_status,
            server_default=sa.text("'booked'::appointment_status"),
            nullable=False,
        ),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "end_at = start_at + interval '30 minutes'",
            name="end_at_is_30_min",
        ),
        sa.CheckConstraint(
            "status <> 'cancelled' OR "
            "(cancellation_reason IS NOT NULL AND cancelled_at IS NOT NULL)",
            name="cancelled_fields",
        ),
        sa.CheckConstraint(
            "cancellation_reason IS NULL OR char_length(cancellation_reason) BETWEEN 1 AND 500",
            name="cancellation_reason_length",
        ),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], name="appointments_doctor_id_fkey"),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"], name="appointments_patient_id_fkey"
        ),
        sa.PrimaryKeyConstraint("id", name="appointments_pkey"),
    )
    op.create_index(
        "one_active_booking_per_slot",
        "appointments",
        ["doctor_id", "start_at"],
        unique=True,
        postgresql_where=sa.text("status = 'booked'"),
    )
    op.create_index(
        "appointments_patient_upcoming",
        "appointments",
        ["patient_id", "start_at"],
        postgresql_where=sa.text("status = 'booked'"),
    )

    op.create_table(
        "appointment_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("appointment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("old_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("new_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('created', 'cancelled', 'rescheduled')",
            name="appointment_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name="appointment_events_appointment_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="appointment_events_pkey"),
    )

    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("patient_id", sa.BigInteger(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_status", sa.SmallInteger(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.id"], name="idempotency_keys_patient_id_fkey"
        ),
        sa.PrimaryKeyConstraint("id", name="idempotency_keys_pkey"),
        sa.UniqueConstraint(
            "patient_id",
            "endpoint",
            "key",
            name="idempotency_keys_patient_endpoint_key_key",
        ),
    )
    op.create_index("idempotency_keys_created", "idempotency_keys", ["created_at"])


def downgrade() -> None:
    op.drop_index("idempotency_keys_created", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")

    op.drop_table("appointment_events")

    op.drop_index("appointments_patient_upcoming", table_name="appointments")
    op.drop_index("one_active_booking_per_slot", table_name="appointments")
    op.drop_table("appointments")

    op.drop_table("doctor_working_hours")
    op.drop_table("patients")
    op.drop_table("doctors")

    appointment_status.drop(op.get_bind(), checkfirst=True)
