from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings

# Tuned for a 0.1 CPU / 512 MB Render instance. Benchmark on the deployed
# instance before raising these values.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
)

# Used when the email is unknown, so login timing does not reveal whether an
# account exists.
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=19456,t=2,p=1$f/u/Fs68e6APuHhgbaieCw$"
    "wPQJNxayNb4WaC16yWKQio5c7lW42uG38rtW8pEsGNw"
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def create_access_token(patient_id: int) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.jwt_ttl_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": str(patient_id),
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return token, expires_in


def decode_access_token(token: str) -> int:
    settings = get_settings()
    payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.isdigit():
        raise jwt.InvalidTokenError("Invalid token subject")
    return int(subject)
