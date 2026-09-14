"""Application runtime container."""

from __future__ import annotations

from dataclasses import dataclass

from release_sql_bot.application.ports.approval_records_v3 import ApprovalRecordPortV3
from release_sql_bot.application.ports.candidate_store import CandidateTemplateStore
from release_sql_bot.application.ports.candidate_store_v3 import CandidateTemplateStoreV3
from release_sql_bot.application.ports.candidates import CandidateModelProvider
from release_sql_bot.application.ports.database import DatabaseInitializer
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffBatchRepositoryV3,
    FactBindingHandoffRepository,
)
from release_sql_bot.application.ports.rules import RuleRepository
from release_sql_bot.application.readiness import ReadinessGraph
from release_sql_bot.config.settings import Settings


@dataclass(frozen=True, slots=True)
class DatabaseResources:
    initializer: DatabaseInitializer
    rule_repository: RuleRepository | None
    fact_binding_repository: FactBindingHandoffRepository | None = None
    fact_binding_batch_repository_v3: FactBindingHandoffBatchRepositoryV3 | None = None
    candidate_store: CandidateTemplateStore | None = None
    approval_port_v3: ApprovalRecordPortV3 | None = None
    candidate_store_v3: CandidateTemplateStoreV3 | None = None


@dataclass(frozen=True, slots=True)
class RuntimeContainer:
    settings: Settings
    database: DatabaseInitializer
    rule_repository: RuleRepository | None
    fact_binding_repository: FactBindingHandoffRepository | None
    fact_binding_batch_repository_v3: FactBindingHandoffBatchRepositoryV3 | None
    candidate_provider: CandidateModelProvider | None
    candidate_store: CandidateTemplateStore | None
    readiness_graph: ReadinessGraph
    approval_port_v3: ApprovalRecordPortV3 | None = None
    candidate_store_v3: CandidateTemplateStoreV3 | None = None
