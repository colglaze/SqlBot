from __future__ import annotations

import asyncio

from release_sql_bot.application.ports.database import DatabaseStatus
from release_sql_bot.application.readiness import build_readiness_graph
from release_sql_bot.infrastructure.database.disabled import DisabledDatabaseInitializer


class UnavailableDatabaseInitializer:
    @property
    def status(self) -> DatabaseStatus:
        return DatabaseStatus.UNAVAILABLE

    async def initialize(self) -> DatabaseStatus:
        return self.status

    async def close(self) -> None:
        return None


def test_readiness_graph_accepts_explicitly_disabled_database() -> None:
    graph = build_readiness_graph(DisabledDatabaseInitializer())

    result = asyncio.run(graph.ainvoke({}))

    assert result["ready"] is True
    assert result["checks"] == {"config": "ok", "database": "disabled"}


def test_readiness_graph_rejects_unavailable_database() -> None:
    graph = build_readiness_graph(UnavailableDatabaseInitializer())

    result = asyncio.run(graph.ainvoke({}))

    assert result["ready"] is False
    assert result["checks"] == {"config": "ok", "database": "unavailable"}
