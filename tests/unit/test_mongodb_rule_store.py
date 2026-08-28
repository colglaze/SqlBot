from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest
from pymongo import ASCENDING, DESCENDING, ReadPreference
from pymongo.errors import ConnectionFailure

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
from release_sql_bot.infrastructure.database.mongodb import MongoRuleStore
from tests.handoff_support import valid_handoff_document
from tests.support import valid_stored_rule_version


class FakeAdmin:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def command(self, name: str, **kwargs: Any) -> dict[str, int]:
        self.calls.append((name, kwargs))
        if self.error is not None:
            raise self.error
        return {"ok": 1}


class FakeCollection:
    def __init__(
        self,
        responses: list[dict[str, Any] | None | Exception],
        find_responses: list[list[dict[str, Any]] | Exception] | None = None,
    ) -> None:
        self._responses = iter(responses)
        self._find_responses = iter(find_responses or [])
        self.calls: list[tuple[dict[str, Any], dict[str, Any]]] = []
        self.find_calls: list[tuple[dict[str, Any], dict[str, Any]]] = []

    async def find_one(self, query: dict[str, Any], **kwargs: Any) -> dict[str, Any] | None:
        self.calls.append((query, kwargs))
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    def find(self, query: dict[str, Any], **kwargs: Any):
        self.find_calls.append((query, kwargs))
        response = next(self._find_responses)

        async def iterate():
            if isinstance(response, Exception):
                raise response
            for document in response:
                yield deepcopy(document)

        return iterate()


class FakeDatabase:
    def __init__(self, collection: FakeCollection) -> None:
        self.collection = collection
        self.requested_collections: list[str] = []

    def __getitem__(self, name: str) -> FakeCollection:
        self.requested_collections.append(name)
        return self.collection


class FakeClient:
    def __init__(
        self,
        collection: FakeCollection,
        *,
        admin_error: Exception | None = None,
    ) -> None:
        self.admin = FakeAdmin(admin_error)
        self.database = FakeDatabase(collection)
        self.requested_databases: list[str] = []
        self.close_calls = 0

    def __getitem__(self, name: str) -> FakeDatabase:
        self.requested_databases.append(name)
        return self.database

    async def close(self) -> None:
        self.close_calls += 1


class FakeClientFactory:
    def __init__(self, client: FakeClient) -> None:
        self.client = client
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, uri: str, **kwargs: Any) -> FakeClient:
        self.calls.append((uri, kwargs))
        return self.client


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_enabled=True,
        mongodb_uri="mongodb://reader:not-real@mongo.example.invalid:27017",
    )


def _rule_document(version: int) -> dict[str, Any]:
    return valid_stored_rule_version(
        rule_version=f"REPORT_RELEASE_ALL_001@V{version}",
    )


def _ready_store(
    responses: list[dict[str, Any] | None | Exception],
    find_responses: list[list[dict[str, Any]] | Exception] | None = None,
) -> tuple[MongoRuleStore, FakeClient, FakeCollection, FakeClientFactory]:
    collection = FakeCollection(responses, find_responses)
    client = FakeClient(collection)
    factory = FakeClientFactory(client)
    store = MongoRuleStore(_settings(), client_factory=factory)
    assert asyncio.run(store.initialize()) is DatabaseStatus.READY
    return store, client, collection, factory


def test_mongodb_store_manages_lifecycle_and_queries_latest_without_cache() -> None:
    store, client, collection, factory = _ready_store([_rule_document(3), _rule_document(4)])

    first = asyncio.run(store.get_latest_rule(rule_id="REPORT_RELEASE_ALL_001"))
    second = asyncio.run(store.get_latest_rule(rule_id="REPORT_RELEASE_ALL_001"))

    assert first is not None and first.rule_version.endswith("@V3")
    assert second is not None and second.rule_version.endswith("@V4")
    assert len(collection.calls) == 2
    for query, options in collection.calls:
        assert query == {"rule_id": "REPORT_RELEASE_ALL_001"}
        assert options["sort"] == [("generated_at", DESCENDING), ("_id", DESCENDING)]
        assert options["projection"] == {
            **{field_name: 1 for field_name in StoredRuleVersion.model_fields},
            "_id": 0,
        }
        assert options["comment"] == "release-sql-bot-latest-rule"

    assert client.admin.calls == [("ping", {"comment": "release-sql-bot-readiness"})]
    assert client.requested_databases == ["rule_reader"]
    assert client.database.requested_collections == [
        "rule_versions",
        "fact_binding_handoffs",
    ]
    _, client_options = factory.calls[0]
    assert client_options["read_preference"] is ReadPreference.PRIMARY
    assert client_options["serverSelectionTimeoutMS"] == 5_000
    assert client_options["connectTimeoutMS"] == 5_000
    assert client_options["timeoutMS"] == 5_000
    assert client_options["tz_aware"] is True

    asyncio.run(store.close())
    asyncio.run(store.close())

    assert store.status is DatabaseStatus.UNAVAILABLE
    assert client.close_calls == 1


def test_mongodb_store_returns_none_when_scope_has_no_rule() -> None:
    store, _, _, _ = _ready_store([None])

    result = asyncio.run(store.get_latest_rule(rule_id="REPORT_RELEASE_ALL_001"))

    assert result is None


def test_mongodb_store_reads_exact_version_handoffs_with_no_write_surface() -> None:
    document = valid_handoff_document()
    store, _, collection, _ = _ready_store([], [[document]])

    records = asyncio.run(store.list_by_rule_version(document["rule_version"]))

    assert records == (StoredFactBindingHandoffV2.model_validate(document),)
    assert collection.find_calls == [
        (
            {"rule_version": document["rule_version"]},
            {
                "sort": [("fact_code", ASCENDING), ("_id", ASCENDING)],
                "comment": "release-sql-bot-fact-binding-handoffs",
            },
        )
    ]
    assert not hasattr(store, "save_many")
    assert not hasattr(store, "update_handoff")
    assert not hasattr(store, "delete_handoff")


@pytest.mark.parametrize("mutation", ["missing", "extra"])
def test_mongodb_store_rejects_invalid_handoff_wrapper_and_hides_query_failure(
    mutation: str,
) -> None:
    invalid = valid_handoff_document()
    if mutation == "missing":
        invalid.pop("payload_sha256")
    else:
        invalid["unexpected_wrapper_field"] = "must-not-be-projected-away"
    store, _, _, _ = _ready_store([], [[invalid]])
    with pytest.raises(FactBindingHandoffDocumentInvalidError):
        asyncio.run(store.list_by_rule_version(invalid["rule_version"]))

    failed, _, _, _ = _ready_store(
        [],
        [ConnectionFailure("mongo-secret.example")],
    )
    with pytest.raises(FactBindingHandoffRepositoryUnavailableError):
        asyncio.run(failed.list_by_rule_version("SYNTHETIC@VERSION"))
    assert failed.status is DatabaseStatus.UNAVAILABLE


@pytest.mark.parametrize("invalid_document", [{"rule_id": "BROKEN"}, _rule_document(4)])
def test_mongodb_store_rejects_invalid_rule_documents(
    invalid_document: dict[str, Any],
) -> None:
    document = deepcopy(invalid_document)
    if "document" in document:
        document["source_sha256"] = "b" * 64
    store, _, _, _ = _ready_store([document])

    with pytest.raises(RuleDocumentInvalidError):
        asyncio.run(store.get_latest_rule(rule_id="REPORT_RELEASE_ALL_001"))


def test_mongodb_store_marks_query_failure_unavailable() -> None:
    store, _, _, _ = _ready_store([ConnectionFailure("sensitive host details")])

    with pytest.raises(RuleRepositoryUnavailableError, match="最新规则查询失败"):
        asyncio.run(store.get_latest_rule(rule_id="REPORT_RELEASE_ALL_001"))

    assert store.status is DatabaseStatus.UNAVAILABLE


def test_mongodb_store_hides_initialization_details_and_closes_failed_client(caplog) -> None:
    collection = FakeCollection([])
    client = FakeClient(collection, admin_error=ConnectionFailure("mongo-secret.example"))
    store = MongoRuleStore(_settings(), client_factory=FakeClientFactory(client))

    with caplog.at_level("WARNING"):
        status = asyncio.run(store.initialize())

    assert status is DatabaseStatus.UNAVAILABLE
    assert client.close_calls == 1
    assert "mongo-secret.example" not in caplog.text
    assert "not-real" not in caplog.text
    with pytest.raises(RuleRepositoryUnavailableError):
        asyncio.run(store.get_latest_rule(rule_id="REPORT_RELEASE_ALL_001"))
