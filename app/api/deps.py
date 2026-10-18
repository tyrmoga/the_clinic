import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.security import decode_access_token
from app.db.models import Patient
from app.db.session import get_session

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="JWT access token returned by /auth/login",
)

_UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}


async def get_current_patient(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_session),
) -> Patient:
    if credentials is None:
        raise AppError(
            401,
            "unauthorized",
            "Authentication is required.",
            headers=_UNAUTHORIZED_HEADERS,
        )

    try:
        patient_id = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise AppError(
            401,
            "invalid_token",
            "The access token is invalid or expired.",
            headers=_UNAUTHORIZED_HEADERS,
        ) from exc

    patient = await session.get(Patient, patient_id)
    if patient is None:
        raise AppError(
            401,
            "invalid_token",
            "The access token is invalid or expired.",
            headers=_UNAUTHORIZED_HEADERS,
        )
    return patient


async def get_current_patient_id(
    patient: Patient = Depends(get_current_patient),
) -> int:
    return patient.id
