from __future__ import annotations

import json

from release_sql_bot.__main__ import main
from release_sql_bot.config.settings import get_settings


def test_check_config_prints_safe_summary(capsys, tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()

    main(["check-config"])

    output = json.loads(capsys.readouterr().out)
    assert output["service_name"] == "ReleaseSQLBot"
    assert output["database_enabled"] is False
    assert set(output) == {
        "service_name",
        "environment",
        "log_level",
        "api_host",
        "api_port",
        "database_enabled",
        "mongodb_configured",
        "mongodb_read_only",
        "mongodb_tls",
        "mongodb_operation_timeout_seconds",
        "sqlserver_configured",
        "sqlserver_auth_mode",
        "sqlserver_odbc_driver",
        "sqlserver_encrypt",
        "sqlserver_trust_server_certificate",
        "sqlserver_read_only",
        "sqlserver_application_intent",
        "sqlserver_login_timeout_seconds",
        "sqlserver_query_timeout_seconds",
        "sqlserver_max_rows",
        "sqlserver_max_result_bytes",
        "sqlserver_schema_allowlist_count",
        "sqlserver_metadata_workbook_configured",
        "sqlserver_validation_enabled",
        "sqlserver_validation_profile_id",
        "sqlserver_validation_environment_class",
        "sqlserver_validation_allowed_modes",
        "sqlserver_validation_max_concurrent_runs",
        "sqlserver_lock_timeout_milliseconds",
        "sqlserver_validation_max_describe_rows",
        "sqlserver_validation_max_describe_bytes",
        "sqlserver_validation_parameter_hmac_key_configured",
        "sqlserver_supported_major_versions",
        "deepseek_configured",
        "deepseek_api_key_configured",
        "deepseek_base_url_configured",
        "deepseek_model",
        "deepseek_timeout_seconds",
        "deepseek_max_retries",
        "candidate_store_enabled",
        "candidate_store_configured",
        "sql_dialect",
        "temp_table_allowed",
    }
