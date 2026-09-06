from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError, PyMongoError

from release_sql_bot.application.candidates_v2 import generate_and_store_sql_candidate_v2
from release_sql_bot.application.ports.candidate_store import (
    CandidateStoreStatus,
)
from release_sql_bot.application.ports.candidates import CandidateModelResponse
from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.sql_candidates_v2 import GenerateSqlCandidateRequestV2
from release_sql_bot.infrastructure.database.mongodb_candidates import MongoCandidateStore
from tests.fakes import FixedCandidateModelProvider
from tests.phase2g_support import (
    generate_candidate_request_payload,
    valid_generated_candidate_v2_content,
)


def settings_with(**overrides: Any) -> Settings:
    from pydantic import SecretStr

    values: dict[str, Any] = {
        "candidate_store_enabled": True,
        "mongodb_uri": SecretStr(
            "mongodb://synthetic:synthetic@127.0.0.1:27017/default?authSource=admin"
        ),
    }
    values.update(overrides)
    return Settings(**values, _env_file=None)  # type: ignore[call-arg]


class FakeAdmin:
    async def command(self, name: str, **kwargs: Any) -> dict[str, int]:
        return {"ok": 1}


class FakeCollection:
    def __init__(self) -> None:
        self.inserted: list[dict[str, Any]] = []
        self.index_calls: list[tuple[Any, dict[str, Any]]] = []
        self.insert_error: Exception | None = None
        self.index_error: Exception | None = None

    def __getitem__(self, name: str) -> FakeCollection:
        return self

    async def create_index(self, keys: Any, **kwargs: Any) -> str:
        self.index_calls.append((keys, kwargs))
        if self.index_error is not None:
            raise self.index_error
        return "ux_content_sha256"

    async def insert_one(self, document: dict[str, Any]) -> Any:
        if self.insert_error is not None:
            raise self.insert_error
        self.inserted.append(document)

        class _Result:
            inserted_id = "synthetic-id"

        return _Result()


class FakeDatabase:
    def __init__(self, collection: FakeCollection) -> None:
        self._collection = collection

    def __getitem__(self, name: str) -> FakeCollection:
        return self._collection


class FakeClient:
    def __init__(self, collection: FakeCollection) -> None:
        self.admin = FakeAdmin()
        self._database = FakeDatabase(collection)
        self.closed = False

    def __getitem__(self, name: str) -> FakeDatabase:
        return self._database

    async def close(self) -> None:
        self.closed = True


class FakeStore:
    def __init__(self) -> None:
        self.saved: list[Any] = []
        self.outcome_status: CandidateStoreStatus = CandidateStoreStatus.STORED

    async def initialize(self) -> None:
        return None

    async def save(self, candidate: Any) -> Any:
        self.saved.append(candidate)
        from release_sql_bot.application.ports.candidate_store import CandidateStoreOutcome

        return CandidateStoreOutcome(
            status=self.outcome_status,
            content_sha256=candidate.content_sha256,
        )

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_candidate_store_disabled_by_default() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.candidate_store_enabled is False
    assert settings.candidate_store_database == "release_sql_bot"
    assert settings.candidate_store_collection == "sql_template_candidates"


def test_candidate_store_requires_mongodb_uri() -> None:
    with pytest.raises(ValidationError):
        settings_with(mongodb_uri=None)


def test_candidate_store_rejects_invalid_collection_name() -> None:
    with pytest.raises(ValidationError):
        settings_with(candidate_store_collection="bad.name")


# ---------------------------------------------------------------------------
# Mongo adapter with fake client
# ---------------------------------------------------------------------------


def build_store(
    collection: FakeCollection, **kwargs: Any
) -> tuple[MongoCandidateStore, FakeClient]:
    client = FakeClient(collection)
    store = MongoCandidateStore(
        settings_with(**kwargs),
        client_factory=lambda *args, **kw: client,
    )
    return store, client


def _candidate() -> Any:
    from release_sql_bot.application.candidates_v2 import (
        _assemble_candidate_v2,
        _validated_resolution,
    )
    from release_sql_bot.application.ports.candidates import CandidateModelRequest
    from release_sql_bot.application.prompts_v2 import build_sqlserver_candidate_prompt_v2

    payload = GenerateSqlCandidateRequestV2.model_validate(generate_candidate_request_payload())
    report = _validated_resolution(payload)
    prompt = build_sqlserver_candidate_prompt_v2(payload)
    request = CandidateModelRequest(
        model="offline",
        prompt_version=prompt.version,
        system_prompt=prompt.system,
        user_prompt=prompt.user,
        response_format="json_object",
        max_tokens=1024,
    )
    response = CandidateModelResponse(
        provider="offline",
        request_id="store-test-001",
        model="offline",
        content=valid_generated_candidate_v2_content(),
        system_fingerprint=None,
    )
    generated = __import__(
        "release_sql_bot.application.candidates_v2", fromlist=["_parse_generated_payload_v2"]
    )._parse_generated_payload_v2(response.content)
    return _assemble_candidate_v2(generated, payload, report, request, response, 1)


def test_store_initialization_creates_unique_index() -> None:
    collection = FakeCollection()
    store, client = build_store(collection)
    asyncio.run(store.initialize())
    assert store.ready is True
    assert collection.index_calls and collection.index_calls[0][0] == "contentSha256"
    assert collection.index_calls[0][1]["unique"] is True
    asyncio.run(store.close())
    assert client.closed is True


def test_store_save_persists_canonical_document() -> None:
    collection = FakeCollection()
    store, _client = build_store(collection)
    asyncio.run(store.initialize())
    candidate = _candidate()
    outcome = asyncio.run(store.save(candidate))
    assert outcome.status is CandidateStoreStatus.STORED
    assert len(collection.inserted) == 1
    document = collection.inserted[0]
    assert document["schemaVersion"] == "1.0.0"
    assert document["contentSha256"] == candidate.content_sha256
    assert document["candidate"]["contentSha256"] == candidate.content_sha256
    assert document["candidate"]["executable"] is False
    assert document["candidate"]["sqlTemplate"] == candidate.sql_template


def test_store_save_duplicate_is_idempotent() -> None:
    collection = FakeCollection()
    collection.insert_error = DuplicateKeyError("duplicate")
    store, _client = build_store(collection)
    asyncio.run(store.initialize())
    candidate = _candidate()
    outcome = asyncio.run(store.save(candidate))
    assert outcome.status is CandidateStoreStatus.DUPLICATE


def test_store_save_error_folds_to_failed() -> None:
    collection = FakeCollection()
    collection.insert_error = PyMongoError("write failed")
    store, _client = build_store(collection)
    asyncio.run(store.initialize())
    outcome = asyncio.run(store.save(_candidate()))
    assert outcome.status is CandidateStoreStatus.FAILED


def test_store_index_failure_degrades_to_unavailable() -> None:
    collection = FakeCollection()
    collection.index_error = PyMongoError("not authorized")
    store, client = build_store(collection)
    asyncio.run(store.initialize())
    assert store.ready is False
    outcome = asyncio.run(store.save(_candidate()))
    assert outcome.status is CandidateStoreStatus.UNAVAILABLE
    assert collection.inserted == []
    asyncio.run(store.close())
    assert client.closed is True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def _provider() -> FixedCandidateModelProvider:
    return FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="offline",
                request_id="store-orchestration-001",
                model="offline",
                content=valid_generated_candidate_v2_content(),
                system_fingerprint=None,
            )
        ]
    )


def _payload() -> GenerateSqlCandidateRequestV2:
    return GenerateSqlCandidateRequestV2.model_validate(generate_candidate_request_payload())


def test_generate_and_store_persists_generated_candidate() -> None:
    store = FakeStore()
    candidate = asyncio.run(
        generate_and_store_sql_candidate_v2(
            _provider(),
            _payload(),
            store,
            model="offline",
            max_retries=0,
        )
    )
    assert candidate.executable is False
    assert [saved.content_sha256 for saved in store.saved] == [candidate.content_sha256]


def test_generate_and_store_without_success_keeps_store_unused() -> None:
    from release_sql_bot.application.candidates_v2 import (
        CandidateGenerationProviderUnavailableV2Error,
    )
    from release_sql_bot.application.ports.candidates import CandidateProviderTransientError

    store = FakeStore()
    provider = FixedCandidateModelProvider(
        [CandidateProviderTransientError("transient")],
    )
    with pytest.raises(CandidateGenerationProviderUnavailableV2Error):
        asyncio.run(
            generate_and_store_sql_candidate_v2(
                provider,
                _payload(),
                store,
                model="offline",
                max_retries=0,
            )
        )
    assert store.saved == []


def test_generate_and_store_swallows_store_failures_of_outcome_kind() -> None:
    store = FakeStore()
    store.outcome_status = CandidateStoreStatus.FAILED
    candidate = asyncio.run(
        generate_and_store_sql_candidate_v2(
            _provider(),
            _payload(),
            store,
            model="offline",
            max_retries=0,
        )
    )
    assert candidate.executable is False
    assert len(store.saved) == 1


# ---------------------------------------------------------------------------
# API wiring
# ---------------------------------------------------------------------------


def test_generate_endpoint_persists_candidate_when_store_present() -> None:
    from typing import Any as _Any

    from fastapi.testclient import TestClient

    from release_sql_bot.api.app import create_app
    from release_sql_bot.application.ports.database import DatabaseStatus
    from release_sql_bot.application.runtime import DatabaseResources

    class ReadyInitializer:
        @property
        def status(self) -> DatabaseStatus:
            return DatabaseStatus.READY

        async def initialize(self) -> DatabaseStatus:
            return self.status

        async def close(self) -> None:
            return None

    class DisabledStore:
        def __init__(self) -> None:
            self.saved: list[_Any] = []

        async def initialize(self) -> None:
            return None

        async def save(self, candidate: _Any) -> Any:
            from release_sql_bot.application.ports.candidate_store import CandidateStoreOutcome

            self.saved.append(candidate)
            return CandidateStoreOutcome(
                status=CandidateStoreStatus.STORED,
                content_sha256=candidate.content_sha256,
            )

        async def close(self) -> None:
            return None

    store = DisabledStore()
    app = create_app(
        Settings(_env_file=None, environment="test"),
        DatabaseResources(
            initializer=ReadyInitializer(),
            rule_repository=None,
            candidate_store=store,
        ),
        candidate_provider=_provider(),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sql-candidates/v2/generate",
            json=generate_candidate_request_payload(),
        )
    assert response.status_code == 200
    assert response.json()["executable"] is False
    assert len(store.saved) == 1
