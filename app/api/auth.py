import asyncio

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_patient
from app.core.errors import AppError
from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    verify_password,
)
from app.db.models import Patient
from app.db.session import get_session
from app.schemas.auth import (
    LoginRequest,
    PatientResponse,
    RegisterRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _normalize_email(email: str) -> str:
    return email.strip().lower()


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a patient",
    description="Create a patient account and return a bearer token.",
)
async def register(
    payload: RegisterRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    email = _normalize_email(str(payload.email))
    password_hash = await asyncio.to_thread(hash_password, payload.password)

    patient = Patient(
        full_name=payload.full_name,
        email=email,
        password_hash=password_hash,
        phone=payload.phone,
    )
    session.add(patient)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            409,
            "email_taken",
            "An account with this email already exists.",
        ) from exc

    await session.refresh(patient)
    token, expires_in = create_access_token(patient.id)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        patient_id=patient.id,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in",
    description="Exchange email and password for a bearer token.",
)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    email = _normalize_email(str(payload.email))
    patient = await session.scalar(select(Patient).where(Patient.email == email))

    password_hash = patient.password_hash if patient is not None else DUMMY_PASSWORD_HASH
    password_ok = await asyncio.to_thread(verify_password, password_hash, payload.password)
    if patient is None or not password_ok:
        raise AppError(401, "invalid_credentials", "Invalid email or password.")

    token, expires_in = create_access_token(patient.id)
    return TokenResponse(
        access_token=token,
        expires_in=expires_in,
        patient_id=patient.id,
    )


@router.get(
    "/me",
    response_model=PatientResponse,
    summary="Current patient",
    description="Return the patient identified by the bearer token.",
)
async def me(patient: Patient = Depends(get_current_patient)) -> Patient:
    return patient
