"""Settings & configuration — pydantic-settings, env-driven, fail-loud.

docs/architecture.md §D: configuration comes from environment with validated
defaults; secrets are never logged.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # App
    app_name: str = "boq-v2"
    env: str = "dev"
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+asyncpg://boq:boq@localhost:5432/boq"

    # Auth
    jwt_secret: str = Field(default="CHANGE-ME-dev-only", min_length=8)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 14

    # Storage
    storage_backend: str = "local"  # local | s3
    storage_local_dir: str = "./data/uploads"
    s3_endpoint: str = ""
    s3_bucket: str = "boq-v2"
    s3_access_key: str = ""
    s3_secret_key: str = ""

    # Uploads
    max_upload_mb: int = 200

    # Jobs
    job_poll_seconds: float = 2.0
    job_max_attempts: int = 3

    # AI provider (T060). The provider layer is advisory-only: it writes
    # suggestions + prompt logs, never a quantity (docs/domain-model.md
    # invariant 3). An empty api_key keeps every AI path honestly disabled.
    ai_provider: str = "stub"  # stub | http
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = "advisory-default"
    ai_timeout_seconds: float = 30.0

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
