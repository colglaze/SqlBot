"""SqlBot-owned MongoDB persistence for generated V3 SQL template candidates.

Insert-only sink on a dedicated database/collection owned by ReleaseSQLBot.
The RuleReader databases and collections are never touched by this adapter.
All failures degrade to outcome values; nothing here can break the generation
flow, and no URI, credential, SQL text, or store location is ever logged.

This adapter is independent of the V2 adapter (``mongodb_candidates.py``).
It uses the same MongoDB URI/TLS/timeout configuration but targets a
separate collection (``sql_template_candidates_v3`` by default).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from logging import getLogger
from typing import Any

from pymongo import AsyncMongoClient, ReadPreference
from pymongo.errors import DuplicateKeyError, PyMongoError

from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.application.ports.candidate_store_v3 import (
    CandidateStoreV3Outcome,
    CandidateStoreV3Status,
)
from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3

logger = getLogger(__name__)

_DOCUMENT_SCHEMA_VERSION = "1.0.0"
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def _validate_v3_candidate(candidate: Any) -> SqlTemplateCandidateV3:
    """Re-validate the candidate as a self-consistent V3 payload.

    Serializes the input to an independent wire dict, then rebuilds a fresh
    ``SqlTemplateCandidateV3`` with ``warnings="error"`` so no illegal field
    values can slip through. The rebuilt object (not the original input) is
    returned for subsequent storage. Computes ``canonical_content_sha256``
    on the rebuilt object and compares it against the rebuilt object's own
    ``content_sha256``.

    Raises ``TypeError`` / ``ValueError`` on any structural, serialization,
    missing-required-field, or hash mismatch so the caller can map to a
    neutral ``failed`` outcome.
    """
    if not isinstance(candidate, SqlTemplateCandidateV3):
        raise TypeError("candidate must be a SqlTemplateCandidateV3")
    try:
        wire = candidate.model_dump(by_alias=True, mode="json", warnings="error")
    except (ValueError, TypeError):
        raise ValueError("candidate serialization failed") from None
    try:
        rebuilt = SqlTemplateCandidateV3.model_validate(wire)
    except ValueError as exc:
        raise ValueError(f"candidate revalidation failed: {type(exc).__name__}") from None
    expected_sha = canonical_content_sha256(rebuilt)
    if rebuilt.content_sha256 != expected_sha:
        raise ValueError("candidate contentSha256 mismatch")
    return rebuilt


class MongoCandidateStoreV3:
    """AsyncMongoClient-backed insert-only store for V3 candidate templates."""

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
        if not self._settings.candidate_store_v3_enabled:
            return
        uri = self._settings.mongodb_uri
        if uri is None:
            logger.warning("V3 候选模板存储未配置 MongoDB URI，持久化保持不可用")
            return
        client: Any | None = None
        try:
            client = self._client_factory(
                uri.get_secret_value(),
                appname="ReleaseSQLBot-CandidateStoreV3",
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
            await client.admin.command("ping", comment="release-sql-bot-candidate-store-v3")
            database = client[self._settings.candidate_store_v3_database]
            collection = database[self._settings.candidate_store_v3_collection]
            await collection.create_index("contentSha256", unique=True, name="ux_content_sha256")
            self._client = client
            self._collection = collection
            self._ready = True
            logger.info("V3 候选模板存储就绪，insert-only 持久化已启用")
        except (OSError, PyMongoError, ValueError):
            if client is not None:
                with suppress(OSError, PyMongoError):
                    await client.close()
            self._client = None
            self._collection = None
            self._ready = False
            logger.warning(
                "V3 候选模板存储初始化失败（账号可能缺少对自有库的建集合/写权限），持久化保持不可用"
            )

    async def save(self, candidate: SqlTemplateCandidateV3) -> CandidateStoreV3Outcome:
        if not self._ready or self._collection is None:
            return CandidateStoreV3Outcome(
                status=CandidateStoreV3Status.UNAVAILABLE,
                content_sha256=_safe_sha(candidate),
            )
        try:
            validated = _validate_v3_candidate(candidate)
        except (TypeError, ValueError):
            logger.warning("V3 候选模板写入被拒绝：候选结构或自哈希不一致")
            return CandidateStoreV3Outcome(
                status=CandidateStoreV3Status.FAILED,
                content_sha256="",
            )
        content_sha256 = validated.content_sha256
        document = {
            "schemaVersion": _DOCUMENT_SCHEMA_VERSION,
            "contentSha256": content_sha256,
            "storedAtUtc": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "candidate": validated.model_dump(by_alias=True, mode="json"),
        }
        try:
            await self._collection.insert_one(document)
        except DuplicateKeyError:
            logger.info(
                "V3 候选模板已存在，幂等跳过（contentSha256=%s…）",
                content_sha256[:12],
            )
            return CandidateStoreV3Outcome(
                status=CandidateStoreV3Status.DUPLICATE,
                content_sha256=content_sha256,
            )
        except (OSError, PyMongoError):
            logger.warning("V3 候选模板写入失败，生成结果不受影响")
            return CandidateStoreV3Outcome(
                status=CandidateStoreV3Status.FAILED,
                content_sha256=content_sha256,
            )
        logger.info(
            "V3 候选模板已写入（contentSha256=%s…）",
            content_sha256[:12],
        )
        return CandidateStoreV3Outcome(
            status=CandidateStoreV3Status.STORED,
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


def _safe_sha(candidate: Any) -> str:
    """Return a valid content hash reference or empty string.

    Only returns the value if it is a 64-char lowercase hex string. Any
    missing, wrong-type, or malformed value becomes an empty string so the
    failed/unavailable outcome paths never reflect an illegal string.
    """
    sha = getattr(candidate, "content_sha256", None)
    if isinstance(sha, str) and _SHA256_PATTERN.fullmatch(sha):
        return sha
    return ""
