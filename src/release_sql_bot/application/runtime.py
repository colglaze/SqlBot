"""Application runtime container."""

from __future__ import annotations

from dataclasses import dataclass

from release_sql_bot.application.ports.candidates import CandidateModelProvider
from release_sql_bot.application.ports.database import DatabaseInitializer
from release_sql_bot.application.ports.handoffs import FactBindingHandoffRepository
from release_sql_bot.application.ports.rules import RuleRepository
from release_sql_bot.application.readiness import ReadinessGraph
from release_sql_bot.config.settings import Settings


@dataclass(frozen=True, slots=True)
class DatabaseResources:
    initializer: DatabaseInitializer
    rule_repository: RuleRepository | None
    fact_binding_repository: FactBindingHandoffRepository | None = None


@dataclass(frozen=True, slots=True)
class RuntimeContainer:
    settings: Settings
    database: DatabaseInitializer
    rule_repository: RuleRepository | None
    fact_binding_repository: FactBindingHandoffRepository | None
    candidate_provider: CandidateModelProvider | None
    readiness_graph: ReadinessGraph
