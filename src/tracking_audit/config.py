"""All configuration comes from the environment. The API token has no default: a missing token stops
the process at start-up rather than running open."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .report import DEFAULT_PREPARED_BY


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://audit:audit@localhost:5432/audit"
    api_token: SecretStr = Field(min_length=24)

    rate_limit_per_minute: int = Field(default=30, ge=1)
    max_batch: int = Field(default=50, ge=1, le=500)
    prepared_by: str = DEFAULT_PREPARED_BY

    worker_poll_s: float = 1.0
    worker_lease_s: int = 300
    worker_concurrency: int = Field(default=4, ge=1, le=16)
    worker_max_attempts: int = Field(default=2, ge=1)

    otel_exporter_otlp_endpoint: str | None = None
    service_name: str = "tracking-audit"
