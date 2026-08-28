from __future__ import annotations

import asyncio

from release_sql_bot.application.ports.database import DatabaseStatus
from release_sql_bot.config.settings import Settings
from release_sql_bot.infrastructure.database import build_database_resources
from release_sql_bot.infrastructure.database.disabled import DisabledDatabaseInitializer
from release_sql_bot.infrastructure.database.mongodb import MongoRuleStore


def test_disabled_database_lifecycle_is_explicit_and_idempotent() -> None:
    database = DisabledDatabaseInitializer()

    assert asyncio.run(database.initialize()) is DatabaseStatus.DISABLED
    assert database.status is DatabaseStatus.DISABLED
    asyncio.run(database.close())
    asyncio.run(database.close())


def test_database_resource_factory_keeps_disabled_mode_and_selects_mongodb_when_enabled() -> None:
    disabled = build_database_resources(Settings(_env_file=None))
    enabled = build_database_resources(
        Settings(
            _env_file=None,
            database_enabled=True,
            mongodb_uri="mongodb://reader:not-real@mongo.example.invalid:27017",
        )
    )

    assert isinstance(disabled.initializer, DisabledDatabaseInitializer)
    assert disabled.rule_repository is None
    assert disabled.fact_binding_repository is None
    assert isinstance(enabled.initializer, MongoRuleStore)
    assert enabled.rule_repository is enabled.initializer
    assert enabled.fact_binding_repository is enabled.initializer
