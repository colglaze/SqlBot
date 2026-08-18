from __future__ import annotations

import asyncio

from release_sql_bot.application.ports.database import DatabaseStatus
from release_sql_bot.infrastructure.database.disabled import DisabledDatabaseInitializer


def test_disabled_database_lifecycle_is_explicit_and_idempotent() -> None:
    database = DisabledDatabaseInitializer()

    assert asyncio.run(database.initialize()) is DatabaseStatus.DISABLED
    assert database.status is DatabaseStatus.DISABLED
    asyncio.run(database.close())
    asyncio.run(database.close())
