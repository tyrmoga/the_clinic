from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "dev-only-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Clinic Appointment Booking API"
    app_env: str = "development"
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/clinic"
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_ttl_minutes: int = 60
    availability_cache_ttl_seconds: int = 10
    rate_limit_auth_per_minute: int = 5
    rate_limit_booking_per_minute: int = 30
    trusted_hosts: str = "*"
    cors_origins: str = ""
    max_body_bytes: int = 1_000_000
    log_level: str = "INFO"

    @model_validator(mode="after")
    def validate_production_settings(self) -> "Settings":
        if self.app_env.lower() == "production" and self.jwt_secret == DEFAULT_JWT_SECRET:
            raise ValueError("JWT_SECRET must be set in production")
        return self

    @property
    def trusted_hosts_list(self) -> list[str]:
        hosts = [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]
        return hosts or ["*"]

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
