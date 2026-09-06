from __future__ import annotations

import pytest
from pydantic import ValidationError

from release_sql_bot.config.settings import Settings

ENABLED_ENV = {
    "RSB_SQLSERVER_VALIDATION_ENABLED": "true",
    "RSB_SQLSERVER_VALIDATION_PROFILE_ID": "validation-profile-synthetic-01",
    "RSB_SQLSERVER_HOST": "synthetic-host",
    "RSB_SQLSERVER_DATABASE": "synthetic_reporting",
    "RSB_SQLSERVER_USERNAME": "synthetic_validation_user",
    "RSB_SQLSERVER_PASSWORD": "synthetic-password",
    "RSB_SQLSERVER_VALIDATION_PARAMETER_HMAC_KEY": "0123456789abcdef-synthetic",
}


def settings_with(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    for key, value in {**ENABLED_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_validation_disabled_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.sqlserver_validation_enabled is False
    assert settings.sqlserver_validation_environment_class == "development"
    assert settings.sqlserver_validation_allowed_modes == ["describeOnly"]


def test_enabled_validation_requires_complete_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        settings_with(monkeypatch, RSB_SQLSERVER_HOST="")


def test_enabled_validation_requires_profile_id(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        settings_with(monkeypatch, RSB_SQLSERVER_VALIDATION_PROFILE_ID="")


def test_enabled_validation_rejects_missing_hmac_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValidationError):
        settings_with(monkeypatch, RSB_SQLSERVER_VALIDATION_PARAMETER_HMAC_KEY="")


def test_enabled_validation_rejects_short_hmac_key(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        settings_with(monkeypatch, RSB_SQLSERVER_VALIDATION_PARAMETER_HMAC_KEY="short")


def test_production_environment_class_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        settings_with(monkeypatch, RSB_SQLSERVER_VALIDATION_ENVIRONMENT_CLASS="production")


def test_production_service_environment_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        settings_with(monkeypatch, RSB_ENVIRONMENT="production")


def test_trusting_server_certificate_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        settings_with(monkeypatch, RSB_SQLSERVER_TRUST_SERVER_CERTIFICATE="true")


def test_disabling_encryption_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        settings_with(monkeypatch, RSB_SQLSERVER_ENCRYPT="false")


def test_unexpected_modes_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValidationError):
        settings_with(
            monkeypatch,
            RSB_SQLSERVER_VALIDATION_ALLOWED_MODES='["describeOnly","estimatedPlan"]',
        )


def test_safe_config_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch)
    assert settings.sqlserver_validation_enabled is True
    assert settings.sqlserver_configured is True


def test_safe_summary_never_contains_target_or_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    summary = settings_with(monkeypatch).safe_summary()
    text = repr(summary)
    assert "synthetic-host" not in text
    assert "synthetic_reporting" not in text
    assert "synthetic_validation_user" not in text
    assert "synthetic-password" not in text
    assert "0123456789abcdef" not in text
    assert "sqlserver_validation_parameter_hmac_key_configured" in summary
    assert summary["sqlserver_validation_parameter_hmac_key_configured"] is True


def test_validation_switch_is_independent_from_mongodb_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_with(monkeypatch, RSB_DATABASE_ENABLED="false")
    assert settings.sqlserver_validation_enabled is True
    assert settings.database_enabled is False
