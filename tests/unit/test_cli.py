from __future__ import annotations

import json

from release_sql_bot.__main__ import main
from release_sql_bot.config.settings import get_settings


def test_check_config_prints_safe_summary(capsys) -> None:
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
    }
