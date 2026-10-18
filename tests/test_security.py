import jwt
import pytest

from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    needs_rehash,
    verify_password,
)


def test_password_hash_round_trip() -> None:
    password = "CorrectHorseBatteryStaple"
    password_hash = hash_password(password)

    assert password_hash != password
    assert verify_password(password_hash, password) is True
    assert verify_password(password_hash, "wrong-password") is False
    assert needs_rehash(password_hash) is False


def test_access_token_round_trip() -> None:
    token, expires_in = create_access_token(42)

    assert expires_in > 0
    assert decode_access_token(token) == 42


def test_invalid_access_token_is_rejected() -> None:
    with pytest.raises(jwt.PyJWTError):
        decode_access_token("not-a-token")
