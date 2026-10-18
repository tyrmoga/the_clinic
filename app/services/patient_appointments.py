import base64
import binascii
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import Appointment, AppointmentStatus


def encode_cursor(appointment: Appointment) -> str:
    payload = {
        "start_at": appointment.start_at.astimezone(UTC).isoformat(),
        "id": str(appointment.id),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        payload = json.loads(raw)
        start_at = datetime.fromisoformat(payload["start_at"])
        appointment_id = UUID(payload["id"])
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise AppError(400, "invalid_cursor", "The cursor is invalid.") from exc

    if start_at.tzinfo is None:
        raise AppError(400, "invalid_cursor", "The cursor is invalid.")
    return start_at.astimezone(UTC), appointment_id


async def list_patient_appointments(
    session: AsyncSession,
    *,
    patient_id: int,
    limit: int,
    cursor: str | None,
) -> tuple[list[Appointment], str | None, bool]:
    db_now = await session.scalar(text("SELECT now()"))
    if db_now is None:
        raise AppError(503, "database_unavailable", "Database time is unavailable.")

    statement = select(Appointment).where(
        Appointment.patient_id == patient_id,
        Appointment.status == AppointmentStatus.booked,
        Appointment.start_at >= db_now,
    )

    if cursor is not None:
        cursor_start, cursor_id = decode_cursor(cursor)
        statement = statement.where(
            or_(
                Appointment.start_at > cursor_start,
                and_(
                    Appointment.start_at == cursor_start,
                    Appointment.id > cursor_id,
                ),
            )
        )

    statement = statement.order_by(Appointment.start_at, Appointment.id).limit(limit + 1)
    appointments = list((await session.scalars(statement)).all())

    has_more = len(appointments) > limit
    page = appointments[:limit]
    next_cursor = encode_cursor(page[-1]) if has_more else None
    return page, next_cursor, has_more
