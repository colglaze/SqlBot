"""SqlBot-owned MongoDB persistence for generated V2 SQL template candidates.

Insert-only sink on a dedicated database/collection owned by ReleaseSQLBot.
The RuleReader databases and collections are never touched by this adapter.
All failures degrade to outcome values; nothing here can break the generation
flow, and no URI, credential, SQL text, or store location is ever logged.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from logging import getLogger
from typing import Any

from pymongo import AsyncMongoClient, ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from release_sql_bot.application.ports.candidate_store import (
    CandidateStoreOutcome,
    CandidateStoreStatus,
)
from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.sql_candidates_v2 import SqlTemplateCandidateV2

logger = getLogger(__name__)

_DOCUMENT_SCHEMA_VERSION = "1.0.0"


class MongoCandidateStore:
    """AsyncMongoClient-backed insert-only store for V2 candidate templates."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[..., Any] = AsyncMongoClient,
    ) -> None:
        self._settings = settings
        self._client_factory = client_factory
        self._client: Any | None = None
        self._collection: Any | None = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def initialize(self) -> None:
        if self._ready and self._client is not None:
            return
        uri = self._settings.mongodb_uri
        if uri is None:
            logger.warning("候选模板存储未配置 MongoDB URI，持久化保持不可用")
            return
        client: Any | None = None
        try:
            client = self._client_factory(
                uri.get_secret_value(),
                appname="ReleaseSQLBot-CandidateStore",
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
            await client.admin.command("ping", comment="release-sql-bot-candidate-store")
            database = client[self._settings.candidate_store_database]
            collection = database[self._settings.candidate_store_collection]
            await collection.create_index("contentSha256", unique=True, name="ux_content_sha256")
            self._client = client
            self._collection = collection
            self._ready = True
            logger.info("候选模板存储就绪，insert-only 持久化已启用")
        except (OSError, PyMongoError, ValueError):
            if client is not None:
                with suppress(OSError, PyMongoError):
                    await client.close()
            self._client = None
            self._collection = None
            self._ready = False
            logger.warning(
                "候选模板存储初始化失败（账号可能缺少对自有库的建集合/写权限），持久化保持不可用"
            )

    async def save(self, candidate: SqlTemplateCandidateV2) -> CandidateStoreOutcome:
        content_sha256 = candidate.content_sha256
        if not self._ready or self._collection is None:
            return CandidateStoreOutcome(
                status=CandidateStoreStatus.UNAVAILABLE,
                content_sha256=content_sha256,
            )
        document = {
            "schemaVersion": _DOCUMENT_SCHEMA_VERSION,
            "contentSha256": content_sha256,
            "storedAtUtc": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "candidate": candidate.model_dump(by_alias=True, mode="json"),
        }
        try:
            await self._collection.insert_one(document)
        except DuplicateKeyError:
            logger.info(
                "候选模板已存在，幂等跳过（contentSha256=%s…）",
                content_sha256[:12],
            )
            return CandidateStoreOutcome(
                status=CandidateStoreStatus.DUPLICATE,
                content_sha256=content_sha256,
            )
        except (OSError, PyMongoError):
            logger.warning("候选模板写入失败，生成结果不受影响")
            return CandidateStoreOutcome(
                status=CandidateStoreStatus.FAILED,
                content_sha256=content_sha256,
            )
        logger.info(
            "候选模板已写入（templateCode=%s, contentSha256=%s…）",
            candidate.template_code,
            content_sha256[:12],
        )
        return CandidateStoreOutcome(
            status=CandidateStoreStatus.STORED,
            content_sha256=content_sha256,
        )

    async def close(self) -> None:
        if self._client is not None:
            with suppress(OSError, PyMongoError):
                await self._client.close()
        self._client = None
        self._collection = None
        self._ready = False

    @staticmethod
    def _milliseconds(seconds: float) -> int:
        return int(seconds * 1000)

    def _tls_options(self) -> dict[str, Any]:
        if not self._settings.mongodb_tls:
            return {}
        options: dict[str, Any] = {}
        if self._settings.mongodb_tls_ca_file:
            options["tlsCAFile"] = self._settings.mongodb_tls_ca_file
        return options
