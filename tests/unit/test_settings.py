from __future__ import annotations

import pytest
from pydantic import ValidationError

from release_sql_bot.config.settings import Settings


def test_settings_have_safe_local_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.service_name == "ReleaseSQLBot"
    assert settings.environment == "local"
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8010
    assert settings.database_enabled is False
    assert settings.mongodb_configured is False
    assert settings.mongodb_database == "rule_reader"
    assert settings.mongodb_fact_binding_collection == "fact_binding_handoffs"
    assert settings.mongodb_rule_collection == "rule_versions"
    assert settings.mongodb_read_only is True
    assert settings.mongodb_operation_timeout_seconds == 5
    assert settings.sqlserver_configured is False
    assert settings.sqlserver_port == 1433
    assert settings.sqlserver_auth_mode == "sql_login"
    assert settings.sqlserver_read_only is True
    assert settings.sqlserver_application_intent == "ReadOnly"
    assert settings.deepseek_model == "deepseek-v4-flash"
    assert settings.deepseek_configured is False
    assert settings.sql_dialect == "sqlserver"
    assert settings.temp_table_allowed is False


def test_settings_read_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RSB_ENVIRONMENT", "test")
    monkeypatch.setenv("RSB_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("RSB_API_PORT", "8100")

    settings = Settings(_env_file=None)

    assert settings.environment == "test"
    assert settings.log_level == "DEBUG"
    assert settings.api_port == 8100


def test_settings_read_local_env_file_and_environment_takes_precedence(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            (
                "RSB_MONGODB_URI=mongodb://reader:secret@mongo:27017",
                "RSB_SQLSERVER_HOST=sql.example.internal",
                "RSB_SQLSERVER_DATABASE=release_test",
                "RSB_SQLSERVER_AUTH_MODE=windows_integrated",
                "RSB_SQLSERVER_QUERY_TIMEOUT_SECONDS=20",
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RSB_SQLSERVER_QUERY_TIMEOUT_SECONDS", "45")

    settings = Settings(_env_file=env_file)

    assert settings.mongodb_configured is True
    assert settings.sqlserver_configured is True
    assert settings.sqlserver_query_timeout_seconds == 45


def test_sql_login_requires_username_and_password() -> None:
    incomplete = Settings(
        _env_file=None,
        sqlserver_host="sql.example.internal",
        sqlserver_database="release_test",
        sqlserver_auth_mode="sql_login",
    )
    complete = Settings(
        _env_file=None,
        sqlserver_host="sql.example.internal",
        sqlserver_database="release_test",
        sqlserver_auth_mode="sql_login",
        sqlserver_username="release_reader",
        sqlserver_password="not-a-real-password",
    )

    assert incomplete.sqlserver_configured is False
    assert complete.sqlserver_configured is True


def test_safe_summary_does_not_expose_connection_targets_or_secrets() -> None:
    settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://reader:mongo-secret@mongo.example.internal:27017",
        sqlserver_host="sql.example.internal",
        sqlserver_database="release_test",
        sqlserver_username="release_reader",
        sqlserver_password="sql-secret",
        deepseek_api_key="deepseek-secret",
    )

    summary_text = str(settings.safe_summary())

    assert settings.safe_summary()["mongodb_configured"] is True
    assert settings.safe_summary()["sqlserver_configured"] is True
    assert "mongo.example.internal" not in summary_text
    assert "sql.example.internal" not in summary_text
    assert "release_test" not in summary_text
    assert "release_reader" not in summary_text
    assert "mongo-secret" not in summary_text
    assert "sql-secret" not in summary_text
    assert "deepseek-secret" not in summary_text


def test_deepseek_requires_key_base_url_and_model_before_provider_is_enabled() -> None:
    partial = Settings(_env_file=None, deepseek_api_key="not-a-real-key")
    complete = Settings(
        _env_file=None,
        deepseek_api_key="not-a-real-key",
        deepseek_base_url="https://api.deepseek.example",
        deepseek_model="deepseek-v4-flash",
    )

    assert partial.deepseek_configured is False
    assert complete.deepseek_configured is True
    assert complete.safe_summary()["deepseek_configured"] is True


def test_database_can_only_be_enabled_with_complete_mongodb_configuration() -> None:
    with pytest.raises(ValidationError, match="MongoDB URI, database, and collection"):
        Settings(_env_file=None, database_enabled=True)

    settings = Settings(
        _env_file=None,
        database_enabled=True,
        mongodb_uri="mongodb://reader:not-real@mongo.example.invalid:27017",
    )

    assert settings.database_enabled is True
    assert settings.mongodb_configured is True


def test_database_access_modes_must_remain_read_only() -> None:
    with pytest.raises(ValidationError, match="MongoDB access must remain read-only"):
        Settings(_env_file=None, mongodb_read_only=False)

    with pytest.raises(ValidationError, match="SQL Server access must remain read-only"):
        Settings(_env_file=None, sqlserver_read_only=False)


def test_temp_tables_cannot_be_enabled_in_first_sqlserver_slice() -> None:
    with pytest.raises(ValidationError, match="temporary tables are disabled"):
        Settings(temp_table_allowed=True)
