"""数据库资源选择与装配。"""

from __future__ import annotations

from release_sql_bot.application.ports.database import DatabaseInitializer
from release_sql_bot.application.runtime import DatabaseResources
from release_sql_bot.config.settings import Settings
from release_sql_bot.infrastructure.database.disabled import DisabledDatabaseInitializer
from release_sql_bot.infrastructure.database.mongodb import MongoRuleStore
from release_sql_bot.infrastructure.database.mongodb_candidates import MongoCandidateStore


def build_database_resources(settings: Settings) -> DatabaseResources:
    candidate_store = MongoCandidateStore(settings) if settings.candidate_store_enabled else None
    if settings.database_enabled:
        store = MongoRuleStore(settings)
        return DatabaseResources(
            initializer=store,
            rule_repository=store,
            fact_binding_repository=store,
            candidate_store=candidate_store,
        )
    if candidate_store is not None:
        return DatabaseResources(
            initializer=DisabledDatabaseInitializer(),
            rule_repository=None,
            fact_binding_repository=None,
            candidate_store=candidate_store,
        )
    return DatabaseResources(
        initializer=DisabledDatabaseInitializer(),
        rule_repository=None,
        fact_binding_repository=None,
    )


def build_database_initializer(settings: Settings) -> DatabaseInitializer:
    """保留已有工厂入口，供只需要生命周期端口的调用方使用。"""

    return build_database_resources(settings).initializer
