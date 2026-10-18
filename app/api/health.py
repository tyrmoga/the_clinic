import logging

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.session import get_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(session: AsyncSession = Depends(get_session)) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
        migrated = await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
        if migrated.first() is None:
            raise RuntimeError("no migrations have been applied")
    except Exception as exc:
        logger.exception("Readiness check failed")
        raise AppError(
            503,
            "database_unavailable",
            "Database is not ready.",
            headers={"Retry-After": "5"},
        ) from exc
    return {"status": "ready"}
