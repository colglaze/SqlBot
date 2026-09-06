from __future__ import annotations

import json

import pytest

from release_sql_bot.__main__ import main
from release_sql_bot.config.settings import get_settings
from tests.fakes import FixedSqlServerValidator
from tests.phase5_support import (
    HMAC_KEY,
    VALIDATION_DATABASE,
    VALIDATION_HOST,
    VALIDATION_USERNAME,
    default_session,
    sqlserver_validation_payload,
)

CLI_ENV = {
    "RSB_SQLSERVER_VALIDATION_ENABLED": "true",
    "RSB_SQLSERVER_VALIDATION_PROFILE_ID": "validation-profile-synthetic-01",
    "RSB_SQLSERVER_HOST": VALIDATION_HOST,
    "RSB_SQLSERVER_DATABASE": VALIDATION_DATABASE,
    "RSB_SQLSERVER_USERNAME": VALIDATION_USERNAME,
    "RSB_SQLSERVER_PASSWORD": "synthetic-password",
    "RSB_SQLSERVER_VALIDATION_PARAMETER_HMAC_KEY": HMAC_KEY,
    "RSB_DATABASE_ENABLED": "false",
}


@pytest.fixture()
def cli_setup(tmp_path, monkeypatch):
    for key, value in CLI_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    validator = FixedSqlServerValidator(default_session())
    monkeypatch.setattr(
        "release_sql_bot.infrastructure.sqlserver.build_validator_from_settings",
        lambda settings: validator,
    )
    input_path = tmp_path / "validation-request.json"
    input_path.write_text(
        json.dumps(sqlserver_validation_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    output_path = tmp_path / "validation-report.json"
    return input_path, output_path, validator


def test_cli_happy_path_writes_report_and_summary(cli_setup, capsys) -> None:
    input_path, output_path, validator = cli_setup

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "validate-sqlserver",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ]
        )

    assert excinfo.value.code == 0
    assert validator.open_count == 1
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert report["executable"] is False

    summary = json.loads(capsys.readouterr().out)
    assert set(summary) == {
        "validationRunId",
        "mode",
        "status",
        "issueCodes",
        "durationMs",
        "reportPath",
    }
    text = json.dumps(summary)
    assert VALIDATION_HOST not in text
    assert VALIDATION_DATABASE not in text
    assert VALIDATION_USERNAME not in text
    assert "synthetic-password" not in text


def test_cli_report_does_not_leak_sql_targets_or_values(cli_setup) -> None:
    input_path, output_path, _validator = cli_setup
    with pytest.raises(SystemExit):
        main(["validate-sqlserver", "--input", str(input_path), "--output", str(output_path)])
    report_text = output_path.read_text(encoding="utf-8")
    assert "SELECT" not in report_text
    assert "synthetic_report_amounts" not in report_text
    assert "total_amount" not in report_text
    assert VALIDATION_HOST not in report_text
    assert VALIDATION_DATABASE not in report_text
    assert VALIDATION_USERNAME not in report_text
    assert "synthetic-password" not in report_text
    assert '"value"' not in report_text


def test_cli_refuses_to_silently_overwrite_report(cli_setup) -> None:
    input_path, output_path, _validator = cli_setup
    with pytest.raises(SystemExit) as first:
        main(["validate-sqlserver", "--input", str(input_path), "--output", str(output_path)])
    assert first.value.code == 0
    with pytest.raises(SystemExit) as second:
        main(["validate-sqlserver", "--input", str(input_path), "--output", str(output_path)])
    assert second.value.code == 4
    with pytest.raises(SystemExit) as third:
        main(
            [
                "validate-sqlserver",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--overwrite",
            ]
        )
    assert third.value.code == 0


def test_cli_blocked_run_exits_with_code_2(tmp_path, monkeypatch) -> None:
    for key, value in CLI_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    validator = FixedSqlServerValidator(default_session())
    monkeypatch.setattr(
        "release_sql_bot.infrastructure.sqlserver.build_validator_from_settings",
        lambda settings: validator,
    )
    payload = sqlserver_validation_payload()
    payload["validationProfileId"] = "validation-profile-other"
    input_path = tmp_path / "blocked-request.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    output_path = tmp_path / "blocked-report.json"

    with pytest.raises(SystemExit) as excinfo:
        main(["validate-sqlserver", "--input", str(input_path), "--output", str(output_path)])

    assert excinfo.value.code == 2
    assert validator.open_count == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["status"] == "blocked"
    assert any(issue["code"] == "PROFILE_NOT_FOUND" for issue in report["issues"])


def test_cli_disabled_switch_is_config_error(tmp_path, monkeypatch, capsys) -> None:
    for key, value in CLI_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("RSB_SQLSERVER_VALIDATION_ENABLED", "false")
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    input_path = tmp_path / "validation-request.json"
    input_path.write_text(
        json.dumps(sqlserver_validation_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "validate-sqlserver",
                "--input",
                str(input_path),
                "--output",
                str(tmp_path / "report.json"),
            ]
        )

    assert excinfo.value.code == 4
    assert not (tmp_path / "report.json").exists()


def test_cli_invalid_json_is_wire_error(tmp_path, monkeypatch) -> None:
    for key, value in CLI_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    input_path = tmp_path / "bad.json"
    input_path.write_text("{not json", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "validate-sqlserver",
                "--input",
                str(input_path),
                "--output",
                str(tmp_path / "report.json"),
            ]
        )

    assert excinfo.value.code == 4


def test_cli_missing_input_file_is_wire_error(tmp_path, monkeypatch) -> None:
    for key, value in CLI_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "validate-sqlserver",
                "--input",
                str(tmp_path / "missing.json"),
                "--output",
                str(tmp_path / "report.json"),
            ]
        )

    assert excinfo.value.code == 4


def test_cli_never_starts_server_or_calls_provider(tmp_path, monkeypatch) -> None:
    for key, value in CLI_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    validator = FixedSqlServerValidator(default_session())
    monkeypatch.setattr(
        "release_sql_bot.infrastructure.sqlserver.build_validator_from_settings",
        lambda settings: validator,
    )
    input_path = tmp_path / "validation-request.json"
    input_path.write_text(
        json.dumps(sqlserver_validation_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as excinfo:
        main(
            [
                "validate-sqlserver",
                "--input",
                str(input_path),
                "--output",
                str(tmp_path / "report.json"),
            ]
        )
    assert excinfo.value.code == 0
    assert validator.open_count == 1
