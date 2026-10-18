import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("secret", ["", "short", "dev-only-change-me-use-32-bytes-minimum"])
def test_production_requires_a_strong_jwt_secret(secret: str) -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", jwt_secret=secret)


def test_production_accepts_a_32_character_secret() -> None:
    settings = Settings(app_env="production", jwt_secret="x" * 32)
    assert settings.jwt_secret == "x" * 32
