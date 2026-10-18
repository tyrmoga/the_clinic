from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# Tuned for a 0.1 CPU / 512 MB Render instance. Benchmark on the deployed
# instance before raising these values.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
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
