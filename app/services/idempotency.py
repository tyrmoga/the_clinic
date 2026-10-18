import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.models import IdempotencyKey

ENDPOINT = "POST /appointments"
TTL = timedelta(hours=24)
_KEY_PATTERN = re.compile(r"^[\x21-\x7E]{8,128}$")


@dataclass(frozen=True)
class StoredResponse:
    status_code: int
    body: dict[str, object]


def validate_key(key: str) -> None:
    if not _KEY_PATTERN.fullmatch(key):
        raise AppError(
            400,
            "invalid_idempotency_key",
            "Idempotency-Key must be 8-128 visible ASCII characters (no spaces).",
        )


def hash_request(payload: BaseModel) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode()).hexdigest()


async def get_stored(
    session: AsyncSession,
    *,
    patient_id: int,
    key: str,
    request_hash: str,
) -> StoredResponse | None:
    cutoff = datetime.now(UTC) - TTL
    await session.execute(
        delete(IdempotencyKey).where(
            IdempotencyKey.patient_id == patient_id,
            IdempotencyKey.endpoint == ENDPOINT,
            IdempotencyKey.key == key,
            IdempotencyKey.created_at < cutoff,
        )
    )

    row = await session.scalar(
        select(IdempotencyKey).where(
            IdempotencyKey.patient_id == patient_id,
            IdempotencyKey.endpoint == ENDPOINT,
            IdempotencyKey.key == key,
        )
    )
    if row is None:
        return None
    if row.request_hash != request_hash:
        raise AppError(
            409,
            "idempotency_key_reused",
            "This Idempotency-Key was already used with a different request.",
        )
    return StoredResponse(status_code=row.response_status, body=row.response_body)


async def prune_expired(session: AsyncSession) -> int:
    cutoff = datetime.now(UTC) - TTL
    result = await session.execute(delete(IdempotencyKey).where(IdempotencyKey.created_at < cutoff))
    return result.rowcount or 0
