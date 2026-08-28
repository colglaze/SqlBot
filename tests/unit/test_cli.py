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
        "deepseek_configured",
        "deepseek_api_key_configured",
        "deepseek_base_url_configured",
        "deepseek_model",
        "deepseek_timeout_seconds",
        "deepseek_max_retries",
        "sql_dialect",
        "temp_table_allowed",
    }
