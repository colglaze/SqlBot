"""数据库资源选择与装配。"""

from __future__ import annotations

from release_sql_bot.application.ports.database import DatabaseInitializer
from release_sql_bot.application.runtime import DatabaseResources
from release_sql_bot.config.settings import Settings
from release_sql_bot.infrastructure.database.disabled import DisabledDatabaseInitializer
from release_sql_bot.infrastructure.database.mongodb import MongoRuleStore
from release_sql_bot.infrastructure.database.mongodb_approvals_v3 import MongoApprovalRecordStoreV3
from release_sql_bot.infrastructure.database.mongodb_candidates import MongoCandidateStore
from release_sql_bot.infrastructure.database.mongodb_candidates_v3 import MongoCandidateStoreV3


def build_database_resources(settings: Settings) -> DatabaseResources:
    candidate_store = MongoCandidateStore(settings) if settings.candidate_store_enabled else None
    approval_port_v3 = (
        MongoApprovalRecordStoreV3(settings) if settings.approval_store_v3_enabled else None
    )
    candidate_store_v3 = (
        MongoCandidateStoreV3(settings) if settings.candidate_store_v3_enabled else None
    )
    if settings.database_enabled:
        store = MongoRuleStore(settings)
        return DatabaseResources(
            initializer=store,
            rule_repository=store,
            fact_binding_repository=store,
            fact_binding_batch_repository_v3=store,
            candidate_store=candidate_store,
            approval_port_v3=approval_port_v3,
            candidate_store_v3=candidate_store_v3,
        )
    if candidate_store is not None:
        return DatabaseResources(
            initializer=DisabledDatabaseInitializer(),
            rule_repository=None,
            fact_binding_repository=None,
            fact_binding_batch_repository_v3=None,
            candidate_store=candidate_store,
            approval_port_v3=approval_port_v3,
            candidate_store_v3=candidate_store_v3,
        )
    return DatabaseResources(
        initializer=DisabledDatabaseInitializer(),
        rule_repository=None,
        fact_binding_repository=None,
        approval_port_v3=approval_port_v3,
        candidate_store_v3=candidate_store_v3,
    )


def build_database_initializer(settings: Settings) -> DatabaseInitializer:
    """保留已有工厂入口，供只需要生命周期端口的调用方使用。"""

    return build_database_resources(settings).initializer
