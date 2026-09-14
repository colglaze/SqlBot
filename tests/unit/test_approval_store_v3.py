"""M3 真实装配切片 1：V3 批准记录 MongoDB 只读适配器离线测试。

Uses a fake MongoDB client that distinguishes database/collection and exposes
only find_one (no insert/update/replace/delete/upsert/bulk_write/drop). All
tests inject the fake client. No real MongoDB, no .env, no online model.
"""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.application.ports.approval_records_v3 import (
    ApprovalRecordLookupError,
)
from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordActivePointerV3,
    ApprovalRecordV3,
)
from release_sql_bot.infrastructure.database.mongodb_approvals_v3 import (
    MongoApprovalRecordStoreV3,
)
from tests.v3_metadata_support import valid_resolve_metadata_request_v3_wire

# ---------------------------------------------------------------------------
# Fake MongoDB client — tracks (database, collection) → FakeCollection
# ---------------------------------------------------------------------------


class FakeAdmin:
    def __init__(self, *, ping_error: Exception | None = None) -> None:
        self._ping_error = ping_error

    async def command(self, name: str, **kwargs: Any) -> dict[str, int]:
        if self._ping_error is not None:
            raise self._ping_error
        return {"ok": 1}


class FakeCollection:
    """Only exposes create_index and find_one — no write/modify methods."""

    def __init__(
        self,
        *,
        documents: list[dict[str, Any]] | None = None,
        find_error: Exception | None = None,
        index_error: Exception | None = None,
    ) -> None:
        self._documents: list[dict[str, Any]] = list(documents) if documents else []
        self.find_error = find_error
        self.index_error = index_error
        self.find_calls: list[dict[str, Any]] = []
        self.index_calls: list[tuple[Any, dict[str, Any]]] = []

    def __getitem__(self, name: str) -> FakeCollection:
        return self

    async def create_index(self, keys: Any, **kwargs: Any) -> str:
        self.index_calls.append((keys, kwargs))
        if self.index_error is not None:
            raise self.index_error
        return "ux_approval_id"

    async def find_one(
        self,
        query: dict[str, Any],
        *,
        comment: str | None = None,
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self.find_calls.append(
            {
                "filter": query,
                "comment": comment,
                "projection": projection,
            }
        )
        if self.find_error is not None:
            raise self.find_error
        key = query.get("approvalId")
        for doc in self._documents:
            if doc.get("approvalId") == key:
                return dict(doc)
        return None


class FakeDatabase:
    def __init__(
        self,
        *,
        documents: list[dict[str, Any]],
        pointer_documents: list[dict[str, Any]] | None = None,
    ) -> None:
        self._collections: dict[str, FakeCollection] = {}
        self._documents = documents
        self._pointer_documents = pointer_documents or []

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            documents = (
                self._pointer_documents
                if name == "approval_record_active_pointers_v3"
                else self._documents
            )
            self._collections[name] = FakeCollection(documents=documents)
        return self._collections[name]


class FakeClient:
    def __init__(
        self,
        *,
        ping_error: Exception | None = None,
        documents: list[dict[str, Any]] | None = None,
        pointer_documents: list[dict[str, Any]] | None = None,
    ) -> None:
        self.admin = FakeAdmin(ping_error=ping_error)
        self._databases: dict[str, FakeDatabase] = {}
        self.closed = False
        self.accessed_databases: list[str] = []
        self._documents: list[dict[str, Any]] = list(documents) if documents else []
        self._pointer_documents: list[dict[str, Any]] = (
            list(pointer_documents) if pointer_documents else []
        )

    def __getitem__(self, name: str) -> FakeDatabase:
        self.accessed_databases.append(name)
        if name not in self._databases:
            self._databases[name] = FakeDatabase(
                documents=self._documents,
                pointer_documents=self._pointer_documents,
            )
        return self._databases[name]

    def get_collection(self, database: str, collection: str) -> FakeCollection | None:
        db = self._databases.get(database)
        if db is None:
            return None
        return db._collections.get(collection)

    async def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _approval_wire() -> dict[str, Any]:
    """Return a valid, self-consistent approvalRecord wire with synthetic _id."""
    wire = valid_resolve_metadata_request_v3_wire()
    document = dict(wire["approvalRecord"])
    document["_id"] = "synthetic-object-id"
    return document


def _pointer_wire(
    approval: dict[str, Any] | None = None,
    *,
    state: str = "active",
) -> dict[str, Any]:
    record = approval or _approval_wire()
    wire: dict[str, Any] = {
        "schemaVersion": "1.0.0",
        "approvalId": record["approvalId"],
        "approvalContentSha256": record["contentSha256"],
        "state": state,
        "revision": 1,
        "changedAt": "2026-09-14T00:00:00Z",
        "actorRef": "metadata-review-synthetic",
        "previousPointerSha256": None,
        "contentSha256": "0" * 64,
    }
    pointer = ApprovalRecordActivePointerV3.model_validate(wire)
    wire["contentSha256"] = canonical_content_sha256(pointer)
    return wire


_MARKER_RECORD = "MARKER_RECORD_CONTENT_7f3a"
_MARKER_URI = "MARKER_URI_9b2e"


def _assert_no_leak(text: str) -> None:
    assert _MARKER_RECORD not in text, f"record content leaked into: {text!r}"
    assert _MARKER_URI not in text, f"URI leaked into: {text!r}"


def settings_v3(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "approval_store_v3_enabled": True,
        "mongodb_uri": SecretStr(
            f"mongodb://{_MARKER_URI}:{_MARKER_URI}@127.0.0.1:27017/db?authSource=admin"
        ),
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)  # type: ignore[call-arg]


def build_store(
    **kwargs: Any,
) -> tuple[MongoApprovalRecordStoreV3, FakeClient]:
    document = _approval_wire()
    client = FakeClient(documents=[document], pointer_documents=[_pointer_wire(document)])
    store = MongoApprovalRecordStoreV3(
        settings_v3(**kwargs),
        client_factory=lambda *args, **kw: client,
    )
    return store, client


# ---------------------------------------------------------------------------
# 1. Default disabled, client_factory zero calls
# ---------------------------------------------------------------------------


def test_v3_approval_store_disabled_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.approval_store_v3_enabled is False
    assert settings.approval_store_v3_database == "release_sql_bot"
    assert settings.approval_store_v3_collection == "approval_records_v3"
    assert (
        settings.approval_store_v3_active_pointer_collection == "approval_record_active_pointers_v3"
    )


def test_v3_approval_store_disabled_no_client_created() -> None:
    called = False

    def factory(*args: Any, **kwargs: Any) -> Any:
        nonlocal called
        called = True
        return FakeClient()

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    store = MongoApprovalRecordStoreV3(settings, client_factory=factory)
    asyncio.run(store.initialize())
    assert called is False
    assert store.ready is False


# ---------------------------------------------------------------------------
# 2. Ping failure → unavailable, get raises
# ---------------------------------------------------------------------------


def test_v3_approval_store_ping_failure_degrades_to_unavailable() -> None:
    from pymongo.errors import PyMongoError

    client = FakeClient(ping_error=PyMongoError("connection refused"))
    store = MongoApprovalRecordStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    assert store.ready is False
    assert client.closed is True
    with pytest.raises(ApprovalRecordLookupError):
        asyncio.run(store.get_by_approval_id("approval-1"))


# ---------------------------------------------------------------------------
# 3. Hit valid record → returns ApprovalRecordV3 matching fixture
# ---------------------------------------------------------------------------


def test_v3_approval_store_hit_returns_record(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, client = build_store()
    with caplog.at_level(logging.DEBUG):
        asyncio.run(store.initialize())
        assert store.ready is True
        record = asyncio.run(store.get_by_approval_id("approval-1"))
    assert record is not None
    assert isinstance(record, ApprovalRecordV3)
    assert record.approval_id == "approval-1"
    assert record.policy_version == "policy-v1"
    assert record.schema_version == "1.0.0"
    assert record.content_sha256 == _approval_wire()["contentSha256"]

    # Verify the exact collection was queried with audit comment and projection
    collection = client.get_collection("release_sql_bot", "approval_records_v3")
    assert collection is not None
    assert len(collection.find_calls) == 1
    assert collection.find_calls[0] == {
        "filter": {"approvalId": "approval-1"},
        "comment": "release-sql-bot-approval-records-v3",
        "projection": {"_id": 0},
    }
    pointer_collection = client.get_collection(
        "release_sql_bot", "approval_record_active_pointers_v3"
    )
    assert pointer_collection is not None
    assert pointer_collection.find_calls == [
        {
            "filter": {"approvalId": "approval-1"},
            "comment": "release-sql-bot-approval-active-pointer-v3",
            "projection": {"_id": 0},
        }
    ]
    warning_or_above = [item for item in caplog.records if item.levelno >= logging.WARNING]
    assert warning_or_above == []


# ---------------------------------------------------------------------------
# 4. Not found → returns None
# ---------------------------------------------------------------------------


def test_v3_approval_store_miss_returns_none() -> None:
    store, _client = build_store()
    asyncio.run(store.initialize())

    result = asyncio.run(store.get_by_approval_id("approval-does-not-exist"))
    assert result is None


def test_v3_approval_store_missing_pointer_returns_none() -> None:
    document = _approval_wire()
    client = FakeClient(documents=[document])
    store = MongoApprovalRecordStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    assert asyncio.run(store.get_by_approval_id("approval-1")) is None


@pytest.mark.parametrize("state", ["revoked", "superseded"])
def test_v3_approval_store_inactive_pointer_returns_none(state: str) -> None:
    document = _approval_wire()
    client = FakeClient(
        documents=[document],
        pointer_documents=[_pointer_wire(document, state=state)],
    )
    store = MongoApprovalRecordStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    assert asyncio.run(store.get_by_approval_id("approval-1")) is None


def test_v3_approval_store_pointer_binding_mismatch_raises() -> None:
    document = _approval_wire()
    pointer = _pointer_wire(document)
    pointer["approvalContentSha256"] = "1" * 64
    pointer["contentSha256"] = canonical_content_sha256(
        ApprovalRecordActivePointerV3.model_validate(pointer)
    )
    client = FakeClient(documents=[document], pointer_documents=[pointer])
    store = MongoApprovalRecordStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    with pytest.raises(ApprovalRecordLookupError):
        asyncio.run(store.get_by_approval_id("approval-1"))


def test_v3_approval_store_pointer_hash_tampering_raises() -> None:
    document = _approval_wire()
    pointer = _pointer_wire(document)
    pointer["contentSha256"] = "1" * 64
    client = FakeClient(documents=[document], pointer_documents=[pointer])
    store = MongoApprovalRecordStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    with pytest.raises(ApprovalRecordLookupError):
        asyncio.run(store.get_by_approval_id("approval-1"))


# ---------------------------------------------------------------------------
# 5. Invalid document structure → raises
# ---------------------------------------------------------------------------


def test_v3_approval_store_invalid_structure_raises() -> None:
    # Missing required fields (no schemaVersion, no contentSha256, etc.)
    bad_doc = {"_id": "bad-id", "approvalId": "approval-bad"}
    client = FakeClient(documents=[bad_doc])
    store = MongoApprovalRecordStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    assert store.ready is True

    with pytest.raises(ApprovalRecordLookupError):
        asyncio.run(store.get_by_approval_id("approval-bad"))


# ---------------------------------------------------------------------------
# 6. contentSha256 tampered → raises
# ---------------------------------------------------------------------------


def test_v3_approval_store_content_sha256_tampered_raises() -> None:
    document = _approval_wire()
    document["contentSha256"] = "0" * 64  # tamper with the hash
    client = FakeClient(documents=[document])
    store = MongoApprovalRecordStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    assert store.ready is True

    with pytest.raises(ApprovalRecordLookupError):
        asyncio.run(store.get_by_approval_id("approval-1"))


# ---------------------------------------------------------------------------
# 7. Query raises PyMongoError → degrade + raise
# ---------------------------------------------------------------------------


def test_v3_approval_store_query_exception_degrades_and_raises() -> None:
    from pymongo.errors import PyMongoError

    class FindFailClient(FakeClient):
        def __getitem__(self, name: str) -> Any:
            self.accessed_databases.append(name)
            db = FakeDatabase(documents=[])
            db._collections["approval_records_v3"] = FakeCollection(
                find_error=PyMongoError("connection reset")
            )
            self._databases[name] = db
            return db

    client = FindFailClient()
    store = MongoApprovalRecordStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    assert store.ready is True

    with pytest.raises(ApprovalRecordLookupError):
        asyncio.run(store.get_by_approval_id("approval-1"))

    # Degraded to unavailable after the exception
    assert store.ready is False
    assert client.closed is True


# ---------------------------------------------------------------------------
# 8. Before init / after close → raises
# ---------------------------------------------------------------------------


def test_v3_approval_store_get_before_init_raises() -> None:
    store, _client = build_store()
    with pytest.raises(ApprovalRecordLookupError):
        asyncio.run(store.get_by_approval_id("approval-1"))


def test_v3_approval_store_get_after_close_raises() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    asyncio.run(store.close())
    assert client.closed is True
    with pytest.raises(ApprovalRecordLookupError):
        asyncio.run(store.get_by_approval_id("approval-1"))


def test_v3_approval_store_close_is_idempotent() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    asyncio.run(store.close())
    assert client.closed is True
    asyncio.run(store.close())
    assert store.ready is False


# ---------------------------------------------------------------------------
# 9. Composition with generate_sql_candidate_v3
# ---------------------------------------------------------------------------


def test_v3_approval_store_composition_with_generate() -> None:
    """Mongo approval store (fake client) serves as approval_port in generation."""
    from tests.unit.test_candidates_v3 import (
        _build_generation_request,
        _build_synthetic_handoff_repository,
        _valid_provider,
    )

    document = _approval_wire()
    client = FakeClient(documents=[document], pointer_documents=[_pointer_wire(document)])
    store = MongoApprovalRecordStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    assert store.ready is True

    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    payload = _build_generation_request()

    from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=store,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert candidate is not None
    assert candidate.schema_version == "3.0.0"
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# 10. Desensitization: failure-path logs and errors don't leak markers
# ---------------------------------------------------------------------------


def test_v3_approval_store_hash_mismatch_no_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tampered = deepcopy(_approval_wire())
    original_sha = tampered["contentSha256"]
    tampered["actorRef"] = _MARKER_RECORD
    assert tampered["contentSha256"] == original_sha
    settings = settings_v3()
    configured_uri = settings.mongodb_uri
    assert configured_uri is not None
    assert _MARKER_URI in configured_uri.get_secret_value()
    client = FakeClient(documents=[tampered])
    store = MongoApprovalRecordStoreV3(
        settings,
        client_factory=lambda *a, **kw: client,
    )

    with caplog.at_level(logging.DEBUG):
        asyncio.run(store.initialize())
        with pytest.raises(ApprovalRecordLookupError) as exc_info:
            asyncio.run(store.get_by_approval_id("approval-1"))

    _assert_no_leak(caplog.text)
    for log_record in caplog.records:
        _assert_no_leak(log_record.getMessage())
        _assert_no_leak(str(log_record.args))
    _assert_no_leak(str(exc_info.value))


def test_v3_approval_store_invalid_structure_no_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad_doc = {
        "approvalId": "approval-1",
        "leakedPayload": _MARKER_RECORD,
    }
    settings = settings_v3()
    configured_uri = settings.mongodb_uri
    assert configured_uri is not None
    assert _MARKER_URI in configured_uri.get_secret_value()
    client = FakeClient(documents=[bad_doc])
    store = MongoApprovalRecordStoreV3(
        settings,
        client_factory=lambda *a, **kw: client,
    )

    with caplog.at_level(logging.DEBUG):
        asyncio.run(store.initialize())
        with pytest.raises(ApprovalRecordLookupError) as exc_info:
            asyncio.run(store.get_by_approval_id("approval-1"))

    _assert_no_leak(caplog.text)
    for log_record in caplog.records:
        _assert_no_leak(log_record.getMessage())
        _assert_no_leak(str(log_record.args))
    _assert_no_leak(str(exc_info.value))


def test_v3_approval_store_query_exception_no_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pymongo.errors import PyMongoError

    settings = settings_v3()
    configured_uri = settings.mongodb_uri
    assert configured_uri is not None
    assert _MARKER_URI in configured_uri.get_secret_value()
    document = _approval_wire()
    client = FakeClient(documents=[document])
    store = MongoApprovalRecordStoreV3(
        settings,
        client_factory=lambda *a, **kw: client,
    )

    with caplog.at_level(logging.DEBUG):
        asyncio.run(store.initialize())
        collection = client.get_collection("release_sql_bot", "approval_records_v3")
        assert collection is not None
        find_error = PyMongoError(f"{_MARKER_RECORD}: connection reset")
        assert _MARKER_RECORD in str(find_error)
        collection.find_error = find_error
        with pytest.raises(ApprovalRecordLookupError) as exc_info:
            asyncio.run(store.get_by_approval_id("approval-1"))

    _assert_no_leak(caplog.text)
    for log_record in caplog.records:
        _assert_no_leak(log_record.getMessage())
        _assert_no_leak(str(log_record.args))
    _assert_no_leak(str(exc_info.value))


def test_v3_approval_store_init_failure_no_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from pymongo.errors import PyMongoError

    settings = settings_v3()
    configured_uri = settings.mongodb_uri
    assert configured_uri is not None
    assert _MARKER_URI in configured_uri.get_secret_value()
    ping_error = PyMongoError(f"{_MARKER_RECORD}: auth failed")
    assert _MARKER_RECORD in str(ping_error)
    client = FakeClient(ping_error=ping_error)
    store = MongoApprovalRecordStoreV3(
        settings,
        client_factory=lambda *a, **kw: client,
    )

    with caplog.at_level(logging.DEBUG):
        asyncio.run(store.initialize())
        assert store.ready is False
        with pytest.raises(ApprovalRecordLookupError) as exc_info:
            asyncio.run(store.get_by_approval_id("approval-1"))

    _assert_no_leak(caplog.text)
    for log_record in caplog.records:
        _assert_no_leak(log_record.getMessage())
        _assert_no_leak(str(log_record.args))
    _assert_no_leak(str(exc_info.value))


# ---------------------------------------------------------------------------
# 11. Read-only scan: adapter source has no write method calls
# ---------------------------------------------------------------------------


def test_v3_approval_store_source_is_readonly() -> None:
    import inspect

    import release_sql_bot.infrastructure.database.mongodb_approvals_v3 as mod

    source = inspect.getsource(mod)
    for forbidden in (
        "insert_one",
        "update_one",
        "update_many",
        "replace_one",
        "delete_one",
        "delete_many",
        "bulk_write",
        "find_one_and_update",
        "find_one_and_replace",
        "find_one_and_delete",
    ):
        assert forbidden not in source, f"readonly adapter must not call {forbidden}"


# ---------------------------------------------------------------------------
# Config validation: enabled requires mongodb_uri
# ---------------------------------------------------------------------------


def test_v3_approval_store_requires_mongodb_uri() -> None:
    with pytest.raises(ValidationError):
        Settings(
            approval_store_v3_enabled=True,
            mongodb_uri=None,
            _env_file=None,
        )  # type: ignore[call-arg]


def test_v3_approval_store_default_config_is_valid() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.approval_store_v3_enabled is False
    assert settings.candidate_store_v3_enabled is False
    same_target = (
        settings.approval_store_v3_database == settings.candidate_store_v3_database
        and settings.approval_store_v3_collection == settings.candidate_store_v3_collection
    )
    assert same_target is False


def test_v3_approval_store_rejects_same_db_and_collection_as_candidate_v3() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            approval_store_v3_database="release_sql_bot",
            approval_store_v3_collection="sql_template_candidates_v3",
            candidate_store_v3_database="release_sql_bot",
            candidate_store_v3_collection="sql_template_candidates_v3",
            _env_file=None,
        )  # type: ignore[call-arg]
    assert (
        "V3 approval record store and V3 candidate store must not target the same "
        "database and collection."
    ) in str(exc_info.value)


def test_v3_approval_store_rejects_pointer_collection_collision() -> None:
    with pytest.raises(ValidationError, match="active pointer collection must be separate"):
        Settings(
            approval_store_v3_active_pointer_collection="approval_records_v3",
            _env_file=None,
        )  # type: ignore[call-arg]
