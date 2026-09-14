"""SqlBot-owned MongoDB read-only adapter for V3 approval records.

Read-only lookup on a dedicated database/collection that stores the
metadataReview approval records. The RuleReader intake databases and
collections are never touched by this adapter. All failures degrade to
``ApprovalRecordLookupError``; nothing here can return a fabricated record
or "always approve", and no URI, credential, or record content is ever logged.

This adapter is independent of the V2 adapter (``mongodb.py``) and of the V3
candidate store (``mongodb_candidates_v3.py``). It uses the same MongoDB
URI/TLS/timeout configuration but targets the immutable approval collection
and a separate active-pointer collection. The protocol exposes only
``get_by_approval_id``; there is no write path in this module.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from logging import getLogger
from typing import Any

from pymongo import AsyncMongoClient, ReadPreference
from pymongo.errors import PyMongoError

from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.application.ports.approval_records_v3 import (
    ApprovalRecordLookupError,
)
from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordActivePointerV3,
    ApprovalRecordV3,
)

logger = getLogger(__name__)


class MongoApprovalRecordStoreV3:
    """AsyncMongoClient-backed read-only store for V3 approval records."""

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
        self._active_pointer_collection: Any | None = None
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def initialize(self) -> None:
        if self._ready and self._client is not None:
            return
        if not self._settings.approval_store_v3_enabled:
            return
        uri = self._settings.mongodb_uri
        if uri is None:
            logger.warning("V3 批准记录存储未配置 MongoDB URI，查询保持不可用")
            return
        client: Any | None = None
        try:
            client = self._client_factory(
                uri.get_secret_value(),
                appname="ReleaseSQLBot-ApprovalStoreV3",
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
            await client.admin.command("ping", comment="release-sql-bot-approval-store-v3")
            database = client[self._settings.approval_store_v3_database]
            collection = database[self._settings.approval_store_v3_collection]
            active_pointer_collection = database[
                self._settings.approval_store_v3_active_pointer_collection
            ]
            self._client = client
            self._collection = collection
            self._active_pointer_collection = active_pointer_collection
            self._ready = True
            logger.info("V3 批准记录存储就绪，只读查询已启用")
        except (OSError, PyMongoError, ValueError):
            if client is not None:
                with suppress(OSError, PyMongoError):
                    await client.close()
            self._client = None
            self._collection = None
            self._active_pointer_collection = None
            self._ready = False
            logger.warning("V3 批准记录存储初始化失败，查询保持不可用")

    async def get_by_approval_id(
        self,
        approval_id: str,
    ) -> ApprovalRecordV3 | None:
        if not self._ready or self._collection is None or self._active_pointer_collection is None:
            raise ApprovalRecordLookupError(
                f"V3 批准记录存储不可用（approvalId={approval_id[:12]}…）"
            )
        document: dict[str, Any] | None = None
        try:
            document = await self._collection.find_one(
                {"approvalId": approval_id},
                comment="release-sql-bot-approval-records-v3",
                projection={"_id": 0},
            )
        except (OSError, PyMongoError):
            logger.warning(
                "V3 批准记录查询异常（approvalId=%s…），降级为不可用",
                approval_id[:12],
            )
            await self._degrade()
            raise ApprovalRecordLookupError(
                f"V3 批准记录查询异常（approvalId={approval_id[:12]}…）"
            ) from None
        if document is None:
            return None
        document.pop("_id", None)
        try:
            record = ApprovalRecordV3.model_validate(document)
        except (ValueError, TypeError):
            logger.warning(
                "V3 批准记录结构非法（approvalId=%s…）",
                approval_id[:12],
            )
            raise ApprovalRecordLookupError(
                f"V3 批准记录结构非法（approvalId={approval_id[:12]}…）"
            ) from None
        expected_sha = canonical_content_sha256(record)
        if record.content_sha256 != expected_sha:
            logger.warning(
                "V3 批准记录 contentSha256 不一致（approvalId=%s…）",
                approval_id[:12],
            )
            raise ApprovalRecordLookupError(
                f"V3 批准记录 contentSha256 不一致（approvalId={approval_id[:12]}…）"
            ) from None
        pointer_document: dict[str, Any] | None = None
        try:
            pointer_document = await self._active_pointer_collection.find_one(
                {"approvalId": approval_id},
                comment="release-sql-bot-approval-active-pointer-v3",
                projection={"_id": 0},
            )
        except (OSError, PyMongoError):
            logger.warning(
                "V3 批准生命周期查询异常（approvalId=%s…），降级为不可用",
                approval_id[:12],
            )
            await self._degrade()
            raise ApprovalRecordLookupError(
                f"V3 批准生命周期查询异常（approvalId={approval_id[:12]}…）"
            ) from None
        if pointer_document is None:
            return None
        pointer_document.pop("_id", None)
        try:
            pointer = ApprovalRecordActivePointerV3.model_validate(pointer_document)
        except (ValueError, TypeError):
            logger.warning(
                "V3 批准生命周期结构非法（approvalId=%s…）",
                approval_id[:12],
            )
            raise ApprovalRecordLookupError(
                f"V3 批准生命周期结构非法（approvalId={approval_id[:12]}…）"
            ) from None
        if pointer.content_sha256 != canonical_content_sha256(pointer):
            logger.warning(
                "V3 批准生命周期 contentSha256 不一致（approvalId=%s…）",
                approval_id[:12],
            )
            raise ApprovalRecordLookupError(
                f"V3 批准生命周期 contentSha256 不一致（approvalId={approval_id[:12]}…）"
            ) from None
        if (
            pointer.approval_id != record.approval_id
            or pointer.approval_content_sha256 != record.content_sha256
        ):
            logger.warning(
                "V3 批准生命周期绑定不一致（approvalId=%s…）",
                approval_id[:12],
            )
            raise ApprovalRecordLookupError(
                f"V3 批准生命周期绑定不一致（approvalId={approval_id[:12]}…）"
            ) from None
        if pointer.state != "active":
            return None
        return record

    async def close(self) -> None:
        if self._client is not None:
            with suppress(OSError, PyMongoError):
                await self._client.close()
        self._client = None
        self._collection = None
        self._active_pointer_collection = None
        self._ready = False

    async def _degrade(self) -> None:
        if self._client is not None:
            with suppress(OSError, PyMongoError):
                await self._client.close()
        self._client = None
        self._collection = None
        self._active_pointer_collection = None
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
