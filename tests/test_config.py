import pytest

from app.core.config import Settings, validate_production_secrets


@pytest.mark.parametrize("secret", ["", "short", "dev-only-change-me-use-32-bytes-minimum"])
def test_production_requires_a_strong_jwt_secret(secret: str) -> None:
    with pytest.raises(ValueError):
        validate_production_secrets(Settings(app_env="production", jwt_secret=secret))


def test_production_accepts_a_32_character_secret() -> None:
    settings = Settings(app_env="production", jwt_secret="x" * 32)
    validate_production_secrets(settings)
    assert settings.jwt_secret == "x" * 32
