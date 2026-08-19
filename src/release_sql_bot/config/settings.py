"""Environment-backed settings with safe defaults."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
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
    api_port: int = Field(default=8010, ge=1, le=65535)
    database_enabled: bool = False
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: AnyHttpUrl | None = None
    deepseek_model: str | None = "deepseek-v4-flash"
    deepseek_timeout_seconds: float = Field(default=90.0, ge=1.0, le=600.0)
    deepseek_max_retries: int = Field(default=2, ge=0, le=5)
    sql_dialect: Literal["sqlserver"] = "sqlserver"
    temp_table_allowed: bool = False

    @model_validator(mode="after")
    def reject_unavailable_database_adapter(self) -> Self:
        if self.database_enabled:
            raise ValueError(
                "Database integration is not configured yet; keep RSB_DATABASE_ENABLED=false."
            )
        if self.temp_table_allowed:
            raise ValueError(
                "Session temporary tables are disabled for the first SQL Server slice."
            )
        return self

    def safe_summary(self) -> dict[str, str | int | float | bool | None]:
        return {
            "service_name": self.service_name,
            "environment": self.environment,
            "log_level": self.log_level,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "database_enabled": self.database_enabled,
            "deepseek_api_key_configured": self.deepseek_api_key is not None,
            "deepseek_base_url_configured": self.deepseek_base_url is not None,
            "deepseek_model": self.deepseek_model,
            "deepseek_timeout_seconds": self.deepseek_timeout_seconds,
            "deepseek_max_retries": self.deepseek_max_retries,
            "sql_dialect": self.sql_dialect,
            "temp_table_allowed": self.temp_table_allowed,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
