from __future__ import annotations

import pytest
from pydantic import ValidationError

from release_sql_bot.config.settings import Settings


def test_settings_have_safe_local_defaults() -> None:
    settings = Settings()

    assert settings.service_name == "ReleaseSQLBot"
    assert settings.environment == "local"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8010
    assert settings.database_enabled is False
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.sql_dialect == "sqlserver"
    assert settings.temp_table_allowed is False


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSB_ENVIRONMENT", "test")
    monkeypatch.setenv("RSB_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("RSB_API_PORT", "8100")

    settings = Settings()

    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
    assert settings.api_port == 8100


def test_database_cannot_be_enabled_before_adapter_exists() -> None:
    with pytest.raises(ValidationError, match="Database integration is not configured"):
        Settings(database_enabled=True)


def test_temp_tables_cannot_be_enabled_in_first_sqlserver_slice() -> None:
    with pytest.raises(ValidationError, match="temporary tables are disabled"):
        Settings(temp_table_allowed=True)
