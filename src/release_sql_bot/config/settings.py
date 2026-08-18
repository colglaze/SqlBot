"""Environment-backed settings with safe defaults."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RSB_",
        env_file=None,
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = Field(default="ReleaseSQLBot", min_length=1, max_length=128)
    environment: Environment = "local"
    log_level: LogLevel = "INFO"
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8000, ge=1, le=65535)
    database_enabled: bool = False

    @model_validator(mode="after")
    def reject_unavailable_database_adapter(self) -> Self:
        if self.database_enabled:
            raise ValueError(
                "Database integration is not configured yet; keep RSB_DATABASE_ENABLED=false."
            )
        return self

    def safe_summary(self) -> dict[str, str | int | bool]:
        return {
            "service_name": self.service_name,
            "environment": self.environment,
            "log_level": self.log_level,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "database_enabled": self.database_enabled,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
