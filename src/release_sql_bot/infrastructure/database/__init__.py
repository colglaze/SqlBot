"""Database initializer selection."""

from __future__ import annotations

from release_sql_bot.application.ports.database import DatabaseInitializer
from release_sql_bot.config.settings import Settings
from release_sql_bot.infrastructure.database.disabled import DisabledDatabaseInitializer


def build_database_initializer(settings: Settings) -> DatabaseInitializer:
    if settings.database_enabled:
        raise RuntimeError("Database adapter is not implemented.")
    return DisabledDatabaseInitializer()
