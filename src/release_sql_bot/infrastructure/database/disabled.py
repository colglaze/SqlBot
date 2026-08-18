"""No-op database adapter used until MongoDB configuration is available."""

from __future__ import annotations

from release_sql_bot.application.ports.database import DatabaseStatus


class DisabledDatabaseInitializer:
    @property
    def status(self) -> DatabaseStatus:
        return DatabaseStatus.DISABLED

    async def initialize(self) -> DatabaseStatus:
        return self.status

    async def close(self) -> None:
        return None
