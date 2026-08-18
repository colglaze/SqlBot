from __future__ import annotations

import os
from collections.abc import Generator

import pytest

from release_sql_bot.config.settings import get_settings


@pytest.fixture(autouse=True)
def isolate_service_environment(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    for name in tuple(os.environ):
        if name.startswith("RSB_"):
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
