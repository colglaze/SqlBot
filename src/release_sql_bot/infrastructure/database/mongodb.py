"""基于 PyMongo Async API 的最新规则只读适配器。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from pydantic import ValidationError
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient, ReadPreference
from pymongo.errors import PyMongoError

from release_sql_bot.application.ports.database import DatabaseStatus
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffDocumentInvalidError,
    FactBindingHandoffRepositoryUnavailableError,
)
from release_sql_bot.application.ports.rules import (
    RuleDocumentInvalidError,
    RuleRepositoryUnavailableError,
)
from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.fact_binding_handoffs_v2 import StoredFactBindingHandoffV2
from release_sql_bot.domain.rule_versions import StoredRuleVersion

logger = logging.getLogger(__name__)

MongoClientFactory = Callable[..., Any]


class MongoRuleStore:
    """共享只读客户端生命周期，查询最新规则或精确版本事实交接。"""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: MongoClientFactory = AsyncMongoClient,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._client: Any | None = None
        self._rule_collection: Any | None = None
        self._handoff_collection: Any | None = None
        self._status = DatabaseStatus.UNAVAILABLE

    @property
    def status(self) -> DatabaseStatus:
        return self._status

    async def initialize(self) -> DatabaseStatus:
        if self._status is DatabaseStatus.READY and self._client is not None:
            return self._status

        uri = self._settings.mongodb_uri
        if uri is None:
            self._status = DatabaseStatus.UNAVAILABLE
            return self._status

        client: Any | None = None
        try:
            client = self._client_factory(
                uri.get_secret_value(),
                appname="ReleaseSQLBot-RuleReader",
                connectTimeoutMS=self._milliseconds(self._settings.mongodb_connect_timeout_seconds),
                read_preference=ReadPreference.PRIMARY,
                serverSelectionTimeoutMS=self._milliseconds(
                    self._settings.mongodb_server_selection_timeout_seconds
                ),
                timeoutMS=self._milliseconds(self._settings.mongodb_operation_timeout_seconds),
                tls=self._settings.mongodb_tls,
                tz_aware=True,
                **self._tls_options(),
            )
            await client.admin.command("ping", comment="release-sql-bot-readiness")
            self._client = client
            database = client[self._settings.mongodb_database]
            self._rule_collection = database[self._settings.mongodb_rule_collection]
            self._handoff_collection = database[self._settings.mongodb_fact_binding_collection]
            self._status = DatabaseStatus.READY
        except (OSError, PyMongoError, ValueError):
            if client is not None:
                with suppress(OSError, PyMongoError):
                    await client.close()
            self._client = None
            self._rule_collection = None
            self._handoff_collection = None
            self._status = DatabaseStatus.UNAVAILABLE
            logger.warning("MongoDB 规则仓储初始化失败，连接信息已隐藏")
        return self._status

    async def close(self) -> None:
        client = self._client
        self._client = None
        self._rule_collection = None
        self._handoff_collection = None
        self._status = DatabaseStatus.UNAVAILABLE
        if client is not None:
            with suppress(OSError, PyMongoError):
                await client.close()

    async def get_latest_rule(
        self,
        *,
        rule_id: str,
    ) -> StoredRuleVersion | None:
        if self._status is not DatabaseStatus.READY or self._rule_collection is None:
            raise RuleRepositoryUnavailableError("规则仓储当前不可用")

        try:
            document = await self._rule_collection.find_one(
                {"rule_id": rule_id},
                projection=self._rule_projection(),
                sort=[("generated_at", DESCENDING), ("_id", DESCENDING)],
                comment="release-sql-bot-latest-rule",
            )
        except (OSError, PyMongoError):
            self._status = DatabaseStatus.UNAVAILABLE
            raise RuleRepositoryUnavailableError("最新规则查询失败") from None

        if document is None:
            return None

        try:
            rule = StoredRuleVersion.model_validate(document)
            rule.model_dump(mode="json", by_alias=True)
        except (ValidationError, ValueError):
            raise RuleDocumentInvalidError("最新规则文档不符合 RuleReader 版本契约") from None
        return rule

    async def list_by_rule_version(
        self,
        rule_version: str,
    ) -> tuple[StoredFactBindingHandoffV2, ...]:
        if self._status is not DatabaseStatus.READY or self._handoff_collection is None:
            raise FactBindingHandoffRepositoryUnavailableError("事实交接只读仓储当前不可用")

        try:
            cursor = self._handoff_collection.find(
                {"rule_version": rule_version},
                sort=[("fact_code", ASCENDING), ("_id", ASCENDING)],
                comment="release-sql-bot-fact-binding-handoffs",
            )
            documents = [document async for document in cursor]
        except (OSError, PyMongoError):
            self._status = DatabaseStatus.UNAVAILABLE
            raise FactBindingHandoffRepositoryUnavailableError("事实交接只读查询失败") from None

        try:
            return tuple(
                StoredFactBindingHandoffV2.model_validate(document) for document in documents
            )
        except (ValidationError, ValueError):
            raise FactBindingHandoffDocumentInvalidError(
                "事实交接包装不符合 RuleReader V2 存储契约"
            ) from None

    def _tls_options(self) -> dict[str, str]:
        if self._settings.mongodb_tls_ca_file is None:
            return {}
        return {"tlsCAFile": self._settings.mongodb_tls_ca_file}

    @staticmethod
    def _milliseconds(seconds: float) -> int:
        return int(seconds * 1_000)

    @staticmethod
    def _rule_projection() -> dict[str, int]:
        projection = {field_name: 1 for field_name in StoredRuleVersion.model_fields}
        projection["_id"] = 0
        return projection
