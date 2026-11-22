"""Application settings loaded from environment variables / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration for the API.

    Every value can be overridden with an environment variable of the same name.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str = "postgresql+psycopg://apph:apph@localhost:5432/apph"
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET: str = "dev-only-change-me-8f3b9c1d7e5a4b2f"
    JWT_TTL_SECONDS: int = 3600
    JWT_ISSUER: str = "apipartnerhub"
    JWT_AUDIENCE: str = "apipartnerhub-api"
    JWT_ALGORITHM: str = "HS256"

    RATE_LIMIT_DEFAULT_RPM: int = 60

    API_BASE_URL: str = "http://localhost:8000"
    DOC_BASE_URL: str = "https://docs.apipartnerhub.dev"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""

    return Settings()
