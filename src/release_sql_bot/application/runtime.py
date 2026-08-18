"""Application runtime container."""

from __future__ import annotations

from dataclasses import dataclass

from release_sql_bot.application.ports.database import DatabaseInitializer
from release_sql_bot.application.readiness import ReadinessGraph
from release_sql_bot.config.settings import Settings


@dataclass(frozen=True, slots=True)
class RuntimeContainer:
    settings: Settings
    database: DatabaseInitializer
    readiness_graph: ReadinessGraph
