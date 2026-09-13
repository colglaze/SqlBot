"""M5 first slice: V3 candidate store offline tests.

Uses a fake MongoDB client that distinguishes database/collection, simulates
unique index violations per collection, and exposes only insert_one (no
update/replace/delete/upsert/bulk_write/drop). All tests inject the fake
client.
"""

from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from release_sql_bot.application.ports.candidate_store_v3 import (
    CandidateStoreV3Status,
)
from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3
from release_sql_bot.domain.stored_candidate_v3 import StoredCandidateV3
from release_sql_bot.infrastructure.database.mongodb_candidates_v3 import (
    MongoCandidateStoreV3,
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
    """Only exposes create_index and insert_one — no modify/delete methods."""

    def __init__(
        self,
        *,
        insert_error: Exception | None = None,
        index_error: Exception | None = None,
    ) -> None:
        self.inserted: list[dict[str, Any]] = []
        self.index_calls: list[tuple[Any, dict[str, Any]]] = []
        self.insert_error = insert_error
        self.index_error = index_error
        self.insert_attempts: int = 0
        self._seen_sha: set[str] = set()

    def __getitem__(self, name: str) -> FakeCollection:
        return self

    async def create_index(self, keys: Any, **kwargs: Any) -> str:
        self.index_calls.append((keys, kwargs))
        if self.index_error is not None:
            raise self.index_error
        return "ux_content_sha256"

    async def insert_one(self, document: dict[str, Any]) -> Any:
        self.insert_attempts += 1
        if self.insert_error is not None:
            raise self.insert_error
        sha = document.get("contentSha256")
        if sha in self._seen_sha:
            from pymongo.errors import DuplicateKeyError

            raise DuplicateKeyError("duplicate")
        self._seen_sha.add(sha)
        self.inserted.append(document)

        class _Result:
            inserted_id = "synthetic-id"

        return _Result()


class FakeDatabase:
    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


class FakeClient:
    def __init__(self, *, ping_error: Exception | None = None) -> None:
        self.admin = FakeAdmin(ping_error=ping_error)
        self._databases: dict[str, FakeDatabase] = {}
        self.closed = False
        self.accessed_databases: list[str] = []
        self.accessed_collections: list[tuple[str, str]] = []

    def __getitem__(self, name: str) -> FakeDatabase:
        self.accessed_databases.append(name)
        if name not in self._databases:
            self._databases[name] = FakeDatabase()
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


def settings_v3(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "candidate_store_v3_enabled": True,
        "mongodb_uri": SecretStr(
            "mongodb://synthetic:synthetic@127.0.0.1:27017/default?authSource=admin"
        ),
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)  # type: ignore[call-arg]


def build_store(**kwargs: Any) -> tuple[MongoCandidateStoreV3, FakeClient]:
    client = FakeClient()
    store = MongoCandidateStoreV3(
        settings_v3(**kwargs),
        client_factory=lambda *args, **kw: client,
    )
    return store, client


def _synthetic_candidate_payload() -> dict[str, Any]:
    req_wire = valid_resolve_metadata_request_v3_wire()
    binding = req_wire["bindingRequest"]
    fact = binding["fact"]
    usages = binding["usages"]

    parameters = [
        {
            "name": p["name"],
            "dataType": p["dataType"],
            "required": p["required"],
            "source": f"fact.parameters.{p['name']}",
        }
        for p in fact["parameters"]
    ]

    declared_usage_coverage = [
        {
            "stage": u["stage"],
            "ruleCode": u["ruleCode"],
            "priority": u["priority"],
            "conditionId": u["conditionId"],
            "conditionPath": u["conditionPath"],
            "outcome": u["outcome"],
        }
        for u in usages
    ]

    return {
        "templateCode": "SYNTHETIC_V3_STORE",
        "sqlTemplate": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey"
        ),
        "parameters": parameters,
        "result": {
            "columnName": "fact_value",
            "dataType": fact["dataType"],
            "cardinality": "scalar",
            "nullable": fact["nullable"],
            "nullPolicy": fact["nullPolicy"],
            "unit": fact.get("unit"),
        },
        "declaredObjects": [{"schemaName": "dbo", "relationName": "synthetic_table"}],
        "declaredUsageCoverage": declared_usage_coverage,
        "assumptions": [],
        "warnings": [],
    }


def _make_candidate(**overrides: Any) -> SqlTemplateCandidateV3:
    from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3
    from release_sql_bot.application.ports.approval_records_v3 import (
        InMemoryApprovalRecordPortV3,
    )
    from release_sql_bot.application.ports.candidates import CandidateModelResponse
    from release_sql_bot.domain.fact_binding_handoffs_v3 import (
        StoredFactBindingHandoffBatchV3,
    )
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
    from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3
    from release_sql_bot.domain.sql_candidates_v3 import GenerateSqlCandidateRequestV3
    from tests.fakes import FixedCandidateModelProvider

    req_wire = valid_resolve_metadata_request_v3_wire()
    binding_req = req_wire["bindingRequest"]

    from release_sql_bot.application.canonical import canonical_sha256

    binding_model = FactBindingRequestV3.model_validate(binding_req)
    payload_hash = canonical_sha256(binding_model)
    closure_wire = {
        "schemaVersion": "1.0.0",
        "ruleVersion": binding_req["ruleRef"]["ruleVersion"],
        "requestId": binding_req["requestId"],
        "factCode": binding_req["fact"]["factCode"],
        "payloadSha256": payload_hash,
        "batchSha256": canonical_sha256(
            [{"requestId": binding_req["requestId"], "payloadSha256": payload_hash}]
        ),
        "contractSchemaId": "urn:rulereader:fact-binding-request:3.0.0",
        "contractSchemaSha256": "2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566",
        "intakeStatus": "readyForMetadataResolution",
        "payload": binding_req,
    }
    req_wire["handoffClosure"] = closure_wire
    from tests.v3_metadata_support import _reclose_all_hashes_from_wire

    _reclose_all_hashes_from_wire(req_wire)

    from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3

    request = ResolveMetadataRequestV3.model_validate(req_wire)
    report = resolve_metadata_v3(request)
    assert report.status == "metadataResolved"

    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)
    request_dict = {
        "request_id": closure_wire["requestId"],
        "rule_version": closure_wire["ruleVersion"],
        "fact_code": closure_wire["factCode"],
        "contract_version": "3.0.0",
        "payload_sha256": closure_wire["payloadSha256"],
        "created_at": now,
        "payload": binding_model.model_dump(by_alias=True, mode="json"),
    }
    batch = StoredFactBindingHandoffBatchV3.model_validate(
        {
            "_id": closure_wire["ruleVersion"],
            "rule_version": closure_wire["ruleVersion"],
            "contract_version": "3.0.0",
            "request_count": 1,
            "request_ids": [closure_wire["requestId"]],
            "batch_sha256": closure_wire["batchSha256"],
            "created_at": now,
            "requests": [request_dict],
        }
    )

    class _FakeRepo:
        async def get_batch_by_rule_version(self, rv: str) -> Any:
            return batch

    approval_rec = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    approval_port = InMemoryApprovalRecordPortV3(records={approval_rec.approval_id: approval_rec})

    payload = dict(_synthetic_candidate_payload())
    payload.update(overrides)

    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-001",
                model="fixed-model-v3",
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        ]
    )
    gen_req = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": req_wire,
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=gen_req,
            handoff_repository=_FakeRepo(),
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )
    return candidate


# ---------------------------------------------------------------------------
# 1. Default disabled, client_factory zero calls
# ---------------------------------------------------------------------------


def test_v3_store_disabled_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.candidate_store_v3_enabled is False
    assert settings.candidate_store_v3_database == "release_sql_bot"
    assert settings.candidate_store_v3_collection == "sql_template_candidates_v3"


def test_v3_store_disabled_no_client_created() -> None:
    called = False

    def factory(*args: Any, **kwargs: Any) -> Any:
        nonlocal called
        called = True
        return FakeClient()

    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    store = MongoCandidateStoreV3(settings, client_factory=factory)
    asyncio.run(store.initialize())
    assert called is False
    assert store.ready is False


# ---------------------------------------------------------------------------
# 2. Initialization success, unique index
# ---------------------------------------------------------------------------


def test_v3_store_initialization_creates_unique_index() -> None:
    client = FakeClient()

    def factory(*a: Any, **kw: Any) -> Any:
        return client

    store = MongoCandidateStoreV3(settings_v3(), client_factory=factory)
    asyncio.run(store.initialize())
    assert store.ready is True
    # Verify the exact collection was indexed
    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None
    assert v3_collection.index_calls and v3_collection.index_calls[0][0] == "contentSha256"
    assert v3_collection.index_calls[0][1]["unique"] is True
    asyncio.run(store.close())
    assert client.closed is True


# ---------------------------------------------------------------------------
# 3. Ping failure, index failure → unavailable, resources closed immediately
# ---------------------------------------------------------------------------


def test_v3_store_ping_failure_degrades_to_unavailable() -> None:
    from pymongo.errors import PyMongoError

    client = FakeClient(ping_error=PyMongoError("connection refused"))
    store = MongoCandidateStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    assert store.ready is False
    # Client must be closed immediately on init failure, not waiting for close()
    assert client.closed is True
    outcome = asyncio.run(store.save(_make_candidate()))
    assert outcome.status is CandidateStoreV3Status.UNAVAILABLE


def test_v3_store_index_failure_degrades_to_unavailable() -> None:
    from pymongo.errors import PyMongoError

    # Need a custom factory that returns a client whose collection raises on index
    class IndexFailClient(FakeClient):
        def __getitem__(self, name: str) -> Any:
            self.accessed_databases.append(name)
            db = FakeDatabase()
            # Pre-populate the collection with one that fails on index
            db._collections["sql_template_candidates_v3"] = FakeCollection(
                index_error=PyMongoError("not authorized")
            )
            self._databases[name] = db
            return db

    client = IndexFailClient()
    store = MongoCandidateStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    assert store.ready is False
    assert client.closed is True
    outcome = asyncio.run(store.save(_make_candidate()))
    assert outcome.status is CandidateStoreV3Status.UNAVAILABLE


# ---------------------------------------------------------------------------
# 4. Valid V3 candidate saved, document round-trips through contract
# ---------------------------------------------------------------------------


def test_v3_store_save_persists_valid_candidate() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()
    outcome = asyncio.run(store.save(candidate))
    assert outcome.status is CandidateStoreV3Status.STORED

    # Verify exactly one document in the V3 collection
    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None
    assert len(v3_collection.inserted) == 1

    document = v3_collection.inserted[0]
    assert document["schemaVersion"] == "1.0.0"
    assert document["contentSha256"] == candidate.content_sha256
    assert document["candidate"]["contentSha256"] == candidate.content_sha256
    assert document["candidate"]["executable"] is False
    assert document["candidate"]["reviewStatus"] == "pending"
    # Round-trip through wrapper contract
    stored = StoredCandidateV3.model_validate(document)
    assert stored.content_sha256 == candidate.content_sha256
    assert stored.candidate.schema_version == "3.0.0"


# ---------------------------------------------------------------------------
# 5. Full payload round-trip: SQL, six-tuple, hash, traceability
# ---------------------------------------------------------------------------


def test_v3_store_save_preserves_full_payload() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()
    asyncio.run(store.save(candidate))

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None
    document = v3_collection.inserted[0]
    cand_doc = document["candidate"]

    # Full candidate wire comparison (excluding contentSha256 which is
    # computed deterministically)
    original_wire = candidate.model_dump(by_alias=True, mode="json")
    saved_wire = {k: v for k, v in cand_doc.items()}
    assert original_wire == saved_wire, "full candidate wire must round-trip exactly"


# ---------------------------------------------------------------------------
# 6. Duplicate: second save returns duplicate, only one doc, first time unchanged
# ---------------------------------------------------------------------------


def test_v3_store_save_duplicate_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Idempotency with deterministic, distinct timestamps via monkeypatch."""
    import datetime as dt_module

    import release_sql_bot.infrastructure.database.mongodb_candidates_v3 as mod

    # Two distinct UTC timestamps
    t1 = dt_module.datetime(2026, 9, 9, 0, 0, 1, 0, tzinfo=dt_module.UTC)
    t2 = dt_module.datetime(2026, 9, 9, 0, 0, 2, 0, tzinfo=dt_module.UTC)
    time_iter = iter([t1, t2])

    class _FakeDatetimeMeta(type):
        def __getattr__(cls, name: str) -> Any:
            return getattr(dt_module.datetime, name)

    class _FakeDatetime(metaclass=_FakeDatetimeMeta):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            result = next(time_iter)
            if tz is not None:
                return result.astimezone(tz)
            return result

    monkeypatch.setattr(mod, "datetime", _FakeDatetime)

    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()

    outcome1 = asyncio.run(store.save(candidate))
    assert outcome1.status is CandidateStoreV3Status.STORED

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None
    assert len(v3_collection.inserted) == 1

    # Deep copy of the first document
    first_document = deepcopy(v3_collection.inserted[0])
    assert first_document["storedAtUtc"] == "2026-09-09T00:00:01.000Z"

    # Second save with the same candidate (same hash)
    outcome2 = asyncio.run(store.save(candidate))
    assert outcome2.status is CandidateStoreV3Status.DUPLICATE

    # Only one document
    assert len(v3_collection.inserted) == 1

    # First document completely unchanged: same storedAtUtc and full payload
    assert v3_collection.inserted[0]["storedAtUtc"] == "2026-09-09T00:00:01.000Z"
    assert v3_collection.inserted[0] == first_document


# ---------------------------------------------------------------------------
# 7. Different content → independent record
# ---------------------------------------------------------------------------


def test_v3_store_different_content_creates_independent_record() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())

    candidate1 = _make_candidate()
    outcome1 = asyncio.run(store.save(candidate1))
    assert outcome1.status is CandidateStoreV3Status.STORED

    candidate2 = _make_candidate(templateCode="SYNTHETIC_V3_ALT")
    assert candidate2.content_sha256 != candidate1.content_sha256
    outcome2 = asyncio.run(store.save(candidate2))
    assert outcome2.status is CandidateStoreV3Status.STORED

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None
    assert len(v3_collection.inserted) == 2


# ---------------------------------------------------------------------------
# 8. Insert exception → failed
# ---------------------------------------------------------------------------


def test_v3_store_insert_error_folds_to_failed() -> None:
    from pymongo.errors import PyMongoError

    class InsertFailClient(FakeClient):
        def __getitem__(self, name: str) -> Any:
            self.accessed_databases.append(name)
            db = FakeDatabase()
            db._collections["sql_template_candidates_v3"] = FakeCollection(
                insert_error=PyMongoError("write failed")
            )
            self._databases[name] = db
            return db

    client = InsertFailClient()
    store = MongoCandidateStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    outcome = asyncio.run(store.save(_make_candidate()))
    assert outcome.status is CandidateStoreV3Status.FAILED


# ---------------------------------------------------------------------------
# 9. Not initialized / init failed / closed → unavailable
# ---------------------------------------------------------------------------


def test_v3_store_save_before_init_unavailable() -> None:
    store, _client = build_store()
    outcome = asyncio.run(store.save(_make_candidate()))
    assert outcome.status is CandidateStoreV3Status.UNAVAILABLE


def test_v3_store_save_after_close_unavailable() -> None:
    store, _client = build_store()
    asyncio.run(store.initialize())
    asyncio.run(store.close())
    outcome = asyncio.run(store.save(_make_candidate()))
    assert outcome.status is CandidateStoreV3Status.UNAVAILABLE


def test_v3_store_close_is_idempotent() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    asyncio.run(store.close())
    assert client.closed is True
    # Second close should not raise
    asyncio.run(store.close())
    assert store.ready is False


# ---------------------------------------------------------------------------
# 10. Structure re-validation: lifecycle violations → failed, zero insert
# ---------------------------------------------------------------------------


def _mutate_and_rehash(candidate: SqlTemplateCandidateV3, **changes: Any) -> SqlTemplateCandidateV3:
    """Apply changes to a candidate via model_copy and recompute contentSha256.

    Uses ``model_copy`` to bypass validation (simulating an in-memory tampering
    attack), then recomputes the hash so the candidate appears self-consistent.
    The returned object will fail re-validation in the adapter because the
    rebuilt model rejects illegal lifecycle values.
    """
    from release_sql_bot.application.canonical import canonical_content_sha256

    tampered = candidate.model_copy(update=changes, deep=True)
    new_sha = canonical_content_sha256(tampered)
    # Use model_copy again to set the hash (bypassing frozen validation)
    return tampered.model_copy(update={"content_sha256": new_sha}, deep=True)


def test_v3_store_rejects_executable_true() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()
    tampered = _mutate_and_rehash(candidate, executable=True)
    assert tampered.executable is True

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None
    assert len(v3_collection.inserted) == 0  # pre-condition

    outcome = asyncio.run(store.save(tampered))
    assert outcome.status is CandidateStoreV3Status.FAILED
    assert v3_collection.insert_attempts == 0


def test_v3_store_rejects_review_status_approved() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()
    tampered = _mutate_and_rehash(candidate, review_status="approved")
    assert tampered.review_status == "approved"

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None

    outcome = asyncio.run(store.save(tampered))
    assert outcome.status is CandidateStoreV3Status.FAILED
    assert v3_collection.insert_attempts == 0


def test_v3_store_rejects_status_not_candidate() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()
    tampered = _mutate_and_rehash(candidate, status="superseded")
    assert tampered.status == "superseded"

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None

    outcome = asyncio.run(store.save(tampered))
    assert outcome.status is CandidateStoreV3Status.FAILED
    assert v3_collection.insert_attempts == 0


def test_v3_store_rejects_nested_invalid_structure() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()

    # Deep copy and tamper a nested field directly (bypassing validation)
    tampered = candidate.model_copy(deep=True)
    object.__setattr__(tampered.rule_ref, "rule_set_id", "Q" * 200)
    from release_sql_bot.application.canonical import canonical_content_sha256

    new_sha = canonical_content_sha256(tampered)
    final = tampered.model_copy(update={"content_sha256": new_sha}, deep=True)

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None

    outcome = asyncio.run(store.save(final))
    assert outcome.status is CandidateStoreV3Status.FAILED
    assert v3_collection.insert_attempts == 0


def test_v3_store_rejects_missing_content_sha256() -> None:
    """Real V3 model with content_sha256 deleted → failed, insert zero calls."""
    from release_sql_bot.application.canonical import canonical_content_sha256

    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()

    # Deep copy then delete content_sha256 from __dict__
    incomplete = candidate.model_copy(deep=True)
    del incomplete.__dict__["content_sha256"]

    # Verify it's still a V3 instance but missing the field
    assert isinstance(incomplete, SqlTemplateCandidateV3)
    assert "content_sha256" not in incomplete.__dict__

    # Nested field types must match original (not raw dicts from model_construct)
    assert type(incomplete.rule_ref) is type(candidate.rule_ref)
    assert type(incomplete.request_ref) is type(candidate.request_ref)

    # Serialization succeeds without contentSha256
    wire = incomplete.model_dump(by_alias=True, mode="json", warnings="error")
    assert "contentSha256" not in wire

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None

    outcome = asyncio.run(store.save(incomplete))
    assert outcome.status is CandidateStoreV3Status.FAILED
    assert outcome.content_sha256 == ""
    assert v3_collection.insert_attempts == 0

    # Original candidate unchanged
    assert candidate.content_sha256 == canonical_content_sha256(candidate)


def test_v3_store_missing_sha_old_logic_attribute_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Historical regression: old validation (isinstance + hash compare) raises
    AttributeError on missing content_sha256. Current implementation returns
    neutral failed. This test uses monkeypatch to restore the old logic and
    confirms it would have crashed — proving the new validation is necessary.
    """
    import release_sql_bot.infrastructure.database.mongodb_candidates_v3 as mod
    from release_sql_bot.application.canonical import canonical_content_sha256

    def old_validate(candidate: Any) -> Any:
        """Original validation: isinstance + direct hash comparison."""
        if not isinstance(candidate, SqlTemplateCandidateV3):
            raise TypeError("not a V3 candidate")
        if candidate.content_sha256 != canonical_content_sha256(candidate):
            raise ValueError("hash mismatch")
        return candidate

    monkeypatch.setattr(mod, "_validate_v3_candidate", old_validate)

    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()

    # Same "only missing hash" input as the main test
    incomplete = candidate.model_copy(deep=True)
    del incomplete.__dict__["content_sha256"]

    with pytest.raises(AttributeError):
        asyncio.run(store.save(incomplete))


# ---------------------------------------------------------------------------
# 10b. Existing negative cases: V2 input and hash mismatch
# ---------------------------------------------------------------------------


def test_v3_store_rejects_v2_candidate() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())

    from release_sql_bot.application.candidates_v2 import (
        _assemble_candidate_v2,
        _validated_resolution,
    )
    from release_sql_bot.application.ports.candidates import (
        CandidateModelRequest,
        CandidateModelResponse,
    )
    from release_sql_bot.application.prompts_v2 import build_sqlserver_candidate_prompt_v2
    from release_sql_bot.domain.sql_candidates_v2 import GenerateSqlCandidateRequestV2
    from tests.phase2g_support import (
        generate_candidate_request_payload,
        valid_generated_candidate_v2_content,
    )

    payload = generate_candidate_request_payload()
    req = GenerateSqlCandidateRequestV2.model_validate(payload)
    report = _validated_resolution(req)
    prompt = build_sqlserver_candidate_prompt_v2(req)
    req_obj = CandidateModelRequest(
        model="offline",
        prompt_version=prompt.version,
        system_prompt=prompt.system,
        user_prompt=prompt.user,
        response_format="json_object",
        max_tokens=1024,
    )
    resp = CandidateModelResponse(
        provider="offline",
        request_id="v2-store-test",
        model="offline",
        content=valid_generated_candidate_v2_content(),
        system_fingerprint=None,
    )
    gen = __import__(
        "release_sql_bot.application.candidates_v2", fromlist=["_parse_generated_payload_v2"]
    )._parse_generated_payload_v2(resp.content)
    v2 = _assemble_candidate_v2(gen, req, report, req_obj, resp, 1)

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None

    outcome = asyncio.run(store.save(v2))  # type: ignore[arg-type]
    assert outcome.status is CandidateStoreV3Status.FAILED
    assert v3_collection.insert_attempts == 0


def test_v3_store_rejects_tampered_candidate() -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["sqlTemplate"] = "SELECT * FROM marker_secret_table"  # tamper
    tampered = SqlTemplateCandidateV3.model_validate(wire)

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None

    outcome = asyncio.run(store.save(tampered))
    assert outcome.status is CandidateStoreV3Status.FAILED
    assert v3_collection.insert_attempts == 0


# ---------------------------------------------------------------------------
# 11. V2/V3 collection isolation; config collision rejected
# ---------------------------------------------------------------------------


def test_v3_store_rejects_same_db_and_collection_as_v2() -> None:
    with pytest.raises(ValidationError):
        Settings(
            candidate_store_enabled=True,
            candidate_store_database="release_sql_bot",
            candidate_store_collection="sql_template_candidates",
            candidate_store_v3_enabled=True,
            candidate_store_v3_database="release_sql_bot",
            candidate_store_v3_collection="sql_template_candidates",
            mongodb_uri=SecretStr("mongodb://x:y@127.0.0.1:27017/db?authSource=admin"),
            _env_file=None,
        )  # type: ignore[call-arg]


def test_v3_store_rejects_collision_when_v2_disabled() -> None:
    """Collision must be rejected even when V2 is disabled."""
    with pytest.raises(ValidationError):
        Settings(
            candidate_store_enabled=False,
            candidate_store_database="shared_db",
            candidate_store_collection="shared_coll",
            candidate_store_v3_enabled=True,
            candidate_store_v3_database="shared_db",
            candidate_store_v3_collection="shared_coll",
            mongodb_uri=SecretStr("mongodb://x:y@127.0.0.1:27017/db?authSource=admin"),
            _env_file=None,
        )  # type: ignore[call-arg]


def test_v3_store_rejects_collision_when_v3_disabled() -> None:
    """Collision must be rejected even when V3 is disabled."""
    with pytest.raises(ValidationError):
        Settings(
            candidate_store_enabled=True,
            candidate_store_database="shared_db",
            candidate_store_collection="shared_coll",
            candidate_store_v3_enabled=False,
            candidate_store_v3_database="shared_db",
            candidate_store_v3_collection="shared_coll",
            mongodb_uri=SecretStr("mongodb://x:y@127.0.0.1:27017/db?authSource=admin"),
            _env_file=None,
        )  # type: ignore[call-arg]


def test_v3_store_rejects_collision_when_both_disabled() -> None:
    """Collision must be rejected even when both are disabled."""
    with pytest.raises(ValidationError):
        Settings(
            candidate_store_enabled=False,
            candidate_store_database="shared_db",
            candidate_store_collection="shared_coll",
            candidate_store_v3_enabled=False,
            candidate_store_v3_database="shared_db",
            candidate_store_v3_collection="shared_coll",
            _env_file=None,
        )  # type: ignore[call-arg]


def test_v3_store_allows_different_collection() -> None:
    """Same database, different collection is valid."""
    settings = Settings(
        candidate_store_enabled=True,
        candidate_store_database="release_sql_bot",
        candidate_store_collection="sql_template_candidates",
        candidate_store_v3_enabled=True,
        candidate_store_v3_database="release_sql_bot",
        candidate_store_v3_collection="sql_template_candidates_v3",
        mongodb_uri=SecretStr("mongodb://x:y@127.0.0.1:27017/db?authSource=admin"),
        _env_file=None,
    )  # type: ignore[call-arg]
    assert settings.candidate_store_collection != settings.candidate_store_v3_collection


def test_v3_store_allows_different_database() -> None:
    """Different database, same collection name is valid."""
    settings = Settings(
        candidate_store_enabled=True,
        candidate_store_database="v2_db",
        candidate_store_collection="candidates",
        candidate_store_v3_enabled=True,
        candidate_store_v3_database="v3_db",
        candidate_store_v3_collection="candidates",
        mongodb_uri=SecretStr("mongodb://x:y@127.0.0.1:27017/db?authSource=admin"),
        _env_file=None,
    )  # type: ignore[call-arg]
    assert settings.candidate_store_database != settings.candidate_store_v3_database


def test_v3_store_default_config_is_valid() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.candidate_store_enabled is False
    assert settings.candidate_store_v3_enabled is False


def test_v3_store_requires_mongodb_uri() -> None:
    with pytest.raises(ValidationError):
        Settings(
            candidate_store_v3_enabled=True,
            mongodb_uri=None,
            _env_file=None,
        )  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 12. Desensitization: failed result and logs don't leak
# ---------------------------------------------------------------------------

_MARKER = "MARKER_LEAK_DETECT_7f3a"


def _assert_no_marker(text: str) -> None:
    assert _MARKER not in text, f"marker leaked into: {text!r}"


def test_v3_store_failed_result_no_leak(caplog: pytest.LogCaptureFixture) -> None:
    store, client = build_store()
    asyncio.run(store.initialize())

    candidate = _make_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["sqlTemplate"] = f"SELECT {_MARKER}_col FROM {_MARKER}_table"
    tampered = SqlTemplateCandidateV3.model_validate(wire)

    with caplog.at_level(logging.WARNING):
        outcome = asyncio.run(store.save(tampered))

    assert outcome.status is CandidateStoreV3Status.FAILED
    # Outcome must not contain the marker
    _assert_no_marker(repr(outcome))
    # Logs must not contain the marker
    _assert_no_marker(caplog.text)
    for record in caplog.records:
        _assert_no_marker(record.getMessage())
        _assert_no_marker(str(record.args))


def test_v3_store_stored_result_no_leak(caplog: pytest.LogCaptureFixture) -> None:
    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()

    with caplog.at_level(logging.INFO):
        outcome = asyncio.run(store.save(candidate))

    assert outcome.status is CandidateStoreV3Status.STORED
    assert outcome.content_sha256 == candidate.content_sha256
    # Logs must not contain SQL or template code
    for record in caplog.records:
        _assert_no_marker(record.getMessage())
        _assert_no_marker(str(record.args))


def test_v3_store_unavailable_result_no_leak(caplog: pytest.LogCaptureFixture) -> None:
    store, client = build_store()
    # Don't initialize — triggers unavailable path

    # Use a non-V3 object with an illegal hash containing the marker
    class NonV3Candidate:
        content_sha256 = _MARKER + "_INVALID_HASH"

    with caplog.at_level(logging.DEBUG):
        outcome = asyncio.run(store.save(NonV3Candidate()))  # type: ignore[arg-type]

    assert outcome.status is CandidateStoreV3Status.UNAVAILABLE
    _assert_no_marker(outcome.content_sha256)
    _assert_no_marker(caplog.text)
    for record in caplog.records:
        _assert_no_marker(record.getMessage())
        _assert_no_marker(str(record.args))


# ---------------------------------------------------------------------------
# 12b. Exception path desensitization — serialization / rebuild / db failure
# ---------------------------------------------------------------------------


def test_v3_store_serialization_failure_no_leak(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force model_dump(warnings="error") to raise; verify no leak.

    First proves the candidate can normally serialize (pre-condition),
    then injects a broken dump to test the failure path.
    """
    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()

    # Pre-condition: this candidate serializes fine normally
    normal_wire = candidate.model_dump(by_alias=True, mode="json", warnings="error")
    assert "contentSha256" in normal_wire

    # Monkeypatch at class level since instance is frozen
    def broken_dump(self: Any, **kwargs: Any) -> Any:
        del kwargs
        raise ValueError(f"{_MARKER}: serialization failed")

    monkeypatch.setattr(SqlTemplateCandidateV3, "model_dump", broken_dump)

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None

    with caplog.at_level(logging.WARNING):
        outcome = asyncio.run(store.save(candidate))

    _assert_no_marker(repr(outcome))
    assert outcome.status is CandidateStoreV3Status.FAILED
    assert outcome.content_sha256 == ""
    assert v3_collection.insert_attempts == 0  # insert never reached
    _assert_no_marker(caplog.text)
    for record in caplog.records:
        _assert_no_marker(record.getMessage())
        _assert_no_marker(str(record.args))


def test_v3_store_rebuild_failure_no_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Illegal field with marker triggers model rebuild failure.

    Proves the tampered input CAN serialize (warnings="error" succeeds),
    so the failure must come from the rebuild step, not serialization.
    """
    from release_sql_bot.application.canonical import canonical_content_sha256

    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()

    # Tamper nested field with marker, recompute hash
    tampered = candidate.model_copy(deep=True)
    object.__setattr__(tampered.rule_ref, "rule_set_id", _MARKER * 20)

    # Pre-condition: serialization succeeds (failure is in rebuild, not dump)
    wire = tampered.model_dump(by_alias=True, mode="json", warnings="error")
    assert _MARKER in str(wire["ruleRef"]["ruleSetId"])

    new_sha = canonical_content_sha256(tampered)
    final = tampered.model_copy(update={"content_sha256": new_sha}, deep=True)

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None

    with caplog.at_level(logging.WARNING):
        outcome = asyncio.run(store.save(final))

    _assert_no_marker(repr(outcome))
    assert outcome.status is CandidateStoreV3Status.FAILED
    assert v3_collection.insert_attempts == 0  # insert never reached
    _assert_no_marker(caplog.text)
    for record in caplog.records:
        _assert_no_marker(record.getMessage())
        _assert_no_marker(str(record.args))


def test_v3_store_insert_exception_no_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Database insert raises a marker-containing exception."""
    from pymongo.errors import PyMongoError

    class InsertFailClient(FakeClient):
        def __getitem__(self, name: str) -> Any:
            self.accessed_databases.append(name)
            db = FakeDatabase()
            db._collections["sql_template_candidates_v3"] = FakeCollection(
                insert_error=PyMongoError(f"{_MARKER}: write concern failed")
            )
            self._databases[name] = db
            return db

    client = InsertFailClient()
    store = MongoCandidateStoreV3(
        settings_v3(),
        client_factory=lambda *a, **kw: client,
    )
    asyncio.run(store.initialize())
    assert store.ready is True  # pre-condition: store ready

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None

    candidate = _make_candidate()

    with caplog.at_level(logging.WARNING):
        outcome = asyncio.run(store.save(candidate))

    _assert_no_marker(repr(outcome))
    assert outcome.status is CandidateStoreV3Status.FAILED
    # The insert was actually attempted (not short-circuited by validation)
    assert v3_collection.insert_attempts == 1
    assert len(v3_collection.inserted) == 0
    _assert_no_marker(caplog.text)
    for record in caplog.records:
        _assert_no_marker(record.getMessage())
        _assert_no_marker(str(record.args))
    # Outcome still has the valid hash
    assert outcome.content_sha256 == candidate.content_sha256


def test_v3_store_illegal_hash_initialized_no_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Real V3 model with illegal content_sha256, store initialized."""
    store, client = build_store()
    asyncio.run(store.initialize())
    candidate = _make_candidate()

    # Tamper the hash to an illegal value with marker
    tampered = candidate.model_copy(update={"content_sha256": _MARKER + "_BAD"}, deep=True)
    assert isinstance(tampered, SqlTemplateCandidateV3)
    assert _MARKER in tampered.content_sha256

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None

    with caplog.at_level(logging.DEBUG):
        outcome = asyncio.run(store.save(tampered))

    _assert_no_marker(repr(outcome))
    assert outcome.status is CandidateStoreV3Status.FAILED
    assert v3_collection.insert_attempts == 0  # rejected before insert
    _assert_no_marker(outcome.content_sha256)
    _assert_no_marker(caplog.text)
    for record in caplog.records:
        _assert_no_marker(record.getMessage())
        _assert_no_marker(str(record.args))


def test_v3_store_illegal_hash_uninitialized_no_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Real V3 model with illegal content_sha256, store NOT initialized."""
    store, client = build_store()
    # Don't initialize → unavailable path
    candidate = _make_candidate()

    # Tamper the hash to an illegal value with marker
    tampered = candidate.model_copy(update={"content_sha256": _MARKER + "_BAD"}, deep=True)
    assert _MARKER in tampered.content_sha256

    with caplog.at_level(logging.DEBUG):
        outcome = asyncio.run(store.save(tampered))

    _assert_no_marker(repr(outcome))
    assert outcome.status is CandidateStoreV3Status.UNAVAILABLE
    _assert_no_marker(outcome.content_sha256)
    _assert_no_marker(caplog.text)


# ---------------------------------------------------------------------------
# 13. Fake doesn't provide modify/delete methods
# ---------------------------------------------------------------------------


def test_v3_store_fake_collection_only_allows_insert() -> None:
    collection = FakeCollection()
    assert not hasattr(collection, "update_one")
    assert not hasattr(collection, "update_many")
    assert not hasattr(collection, "replace_one")
    assert not hasattr(collection, "delete_one")
    assert not hasattr(collection, "delete_many")
    assert not hasattr(collection, "bulk_write")
    assert not hasattr(collection, "drop")
    assert not hasattr(collection, "find_one_and_update")
    assert hasattr(collection, "insert_one")
    assert hasattr(collection, "create_index")
