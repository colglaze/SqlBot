"""Environment-backed settings with safe database connection defaults."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal, Self

from pydantic import AnyHttpUrl, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "test", "staging", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
SqlServerAuthMode = Literal["sql_login", "windows_integrated"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RSB_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    service_name: str = Field(default="ReleaseSQLBot", min_length=1, max_length=128)
    environment: Environment = "local"
    log_level: LogLevel = "INFO"
    api_host: str = Field(default="127.0.0.1", min_length=1)
    api_port: int = Field(default=8010, ge=1, le=65535)
    database_enabled: bool = False

    mongodb_uri: SecretStr | None = None
    mongodb_database: str = Field(default="rule_reader", min_length=1, max_length=128)
    mongodb_fact_binding_collection: str = Field(
        default="fact_binding_handoffs",
        min_length=1,
        max_length=128,
    )
    mongodb_rule_collection: str = Field(default="rule_versions", min_length=1, max_length=128)
    mongodb_read_only: bool = True
    mongodb_tls: bool = False
    mongodb_tls_ca_file: str | None = None
    mongodb_server_selection_timeout_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    mongodb_connect_timeout_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    mongodb_operation_timeout_seconds: float = Field(default=5.0, ge=1.0, le=60.0)

    sqlserver_host: str | None = Field(default=None, min_length=1, max_length=255)
    sqlserver_port: int = Field(default=1433, ge=1, le=65535)
    sqlserver_database: str | None = Field(default=None, min_length=1, max_length=128)
    sqlserver_auth_mode: SqlServerAuthMode = "sql_login"
    sqlserver_username: str | None = Field(default=None, min_length=1, max_length=128)
    sqlserver_password: SecretStr | None = None
    sqlserver_odbc_driver: str = Field(
        default="ODBC Driver 18 for SQL Server",
        min_length=1,
        max_length=128,
    )
    sqlserver_encrypt: bool = True
    sqlserver_trust_server_certificate: bool = False
    sqlserver_read_only: bool = True
    sqlserver_application_intent: Literal["ReadOnly"] = "ReadOnly"
    sqlserver_login_timeout_seconds: int = Field(default=10, ge=1, le=60)
    sqlserver_query_timeout_seconds: int = Field(default=30, ge=1, le=300)
    sqlserver_max_rows: int = Field(default=1000, ge=1, le=10_000)
    sqlserver_max_result_bytes: int = Field(default=5_000_000, ge=1024, le=50_000_000)
    sqlserver_schema_allowlist: list[str] = Field(default_factory=list)
    sqlserver_metadata_workbook_path: str | None = None

    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: AnyHttpUrl | None = None
    deepseek_model: str = Field(default="deepseek-v4-flash", min_length=1, max_length=160)
    deepseek_timeout_seconds: float = Field(default=90.0, ge=1.0, le=600.0)
    deepseek_max_retries: int = Field(default=2, ge=0, le=5)
    sql_dialect: Literal["sqlserver"] = "sqlserver"
    temp_table_allowed: bool = False

    @model_validator(mode="after")
    def validate_database_safety(self) -> Self:
        if not self.mongodb_read_only:
            raise ValueError("MongoDB access must remain read-only for the intake adapter.")
        if not self.sqlserver_read_only:
            raise ValueError("SQL Server access must remain read-only.")
        if self.temp_table_allowed:
            raise ValueError(
                "Session temporary tables are disabled for the first SQL Server slice."
            )
        if self.database_enabled and not self.mongodb_configured:
            raise ValueError(
                "MongoDB URI, database, and collection settings are required when database "
                "integration is enabled."
            )
        return self

    @staticmethod
    def _has_text(value: str | None) -> bool:
        return value is not None and bool(value.strip())

    @staticmethod
    def _has_secret(value: SecretStr | None) -> bool:
        return value is not None and bool(value.get_secret_value())

    @property
    def mongodb_configured(self) -> bool:
        return (
            self._has_secret(self.mongodb_uri)
            and self._has_text(self.mongodb_database)
            and self._has_text(self.mongodb_fact_binding_collection)
            and self._has_text(self.mongodb_rule_collection)
        )

    @property
    def sqlserver_configured(self) -> bool:
        target_configured = self._has_text(self.sqlserver_host) and self._has_text(
            self.sqlserver_database
        )
        if not target_configured:
            return False
        if self.sqlserver_auth_mode == "windows_integrated":
            return True
        return self._has_text(self.sqlserver_username) and self._has_secret(self.sqlserver_password)

    @property
    def deepseek_configured(self) -> bool:
        return (
            self._has_secret(self.deepseek_api_key)
            and self.deepseek_base_url is not None
            and self._has_text(self.deepseek_model)
        )

    def safe_summary(self) -> dict[str, str | int | float | bool | None]:
        return {
            "service_name": self.service_name,
            "environment": self.environment,
            "log_level": self.log_level,
            "api_host": self.api_host,
            "api_port": self.api_port,
            "database_enabled": self.database_enabled,
            "mongodb_configured": self.mongodb_configured,
            "mongodb_read_only": self.mongodb_read_only,
            "mongodb_tls": self.mongodb_tls,
            "mongodb_operation_timeout_seconds": self.mongodb_operation_timeout_seconds,
            "sqlserver_configured": self.sqlserver_configured,
            "sqlserver_auth_mode": self.sqlserver_auth_mode,
            "sqlserver_odbc_driver": self.sqlserver_odbc_driver,
            "sqlserver_encrypt": self.sqlserver_encrypt,
            "sqlserver_trust_server_certificate": self.sqlserver_trust_server_certificate,
            "sqlserver_read_only": self.sqlserver_read_only,
            "sqlserver_application_intent": self.sqlserver_application_intent,
            "sqlserver_login_timeout_seconds": self.sqlserver_login_timeout_seconds,
            "sqlserver_query_timeout_seconds": self.sqlserver_query_timeout_seconds,
            "sqlserver_max_rows": self.sqlserver_max_rows,
            "sqlserver_max_result_bytes": self.sqlserver_max_result_bytes,
            "sqlserver_schema_allowlist_count": len(self.sqlserver_schema_allowlist),
            "sqlserver_metadata_workbook_configured": self._has_text(
                self.sqlserver_metadata_workbook_path
            ),
            "deepseek_configured": self.deepseek_configured,
            "deepseek_api_key_configured": self._has_secret(self.deepseek_api_key),
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
