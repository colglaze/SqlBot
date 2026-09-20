from __future__ import annotations

import asyncio

import pytest
from pydantic import SecretStr

from release_sql_bot.application.complete_delivery_intake_v31 import (
    CompleteDeliveryNotConsumableV31Error,
    CompleteDeliveryNotFoundV31Error,
    select_complete_delivery_v31,
)
from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.purpose_v31 import DeliveryPurposeV31
from release_sql_bot.infrastructure.complete_delivery_mongo_v31 import (
    MongodbCompleteDeliverySourceV31,
    MongodbCompleteDeliveryV31Error,
)
from tests.v31_delivery_support import build_synthetic_complete_delivery


class _FakeCollection:
    def __init__(self, documents: dict[str, dict | None]) -> None:
        self._documents = documents
        self.queries: list[object] = []

    async def find_one(self, query: dict[str, object], **_kwargs: object) -> dict | None:
        self.queries.append(query)
        return self._documents.get(query.get("_id"))  # type: ignore[return-value]


class _FakeDatabase:
    def __init__(self, collections: dict[str, _FakeCollection]) -> None:
        self._collections = collections

    def __getitem__(self, name: str) -> _FakeCollection:
        return self._collections[name]


class _FakeClient:
    def __init__(self, database: str, collections: dict[str, _FakeCollection]) -> None:
        self._database = database
        self._collections = collections

    def __getitem__(self, name: str) -> _FakeDatabase:
        if name != self._database:
            raise KeyError(name)
        return _FakeDatabase(self._collections)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        database_enabled=True,
        mongodb_uri=SecretStr("mongodb://localhost:27017"),
    )


def _agent1_rule_document(stored) -> dict[str, object]:
    return {
        "_id": stored.rule_version,
        "rule_version": stored.rule_version,
        "schema_version": stored.schema_version,
        "source_file_sha256": stored.source_file_sha256,
        "parse_input_sha256": stored.parse_input_sha256,
        "catalog_digest": stored.catalog_digest,
        "status": stored.status,
        "executable": stored.executable,
        "purpose": stored.purpose,
        "payload": stored.result_payload,
        "catalog_payload": stored.catalog_payload,
        "candidate_payload": stored.candidate_payload,
    }


def _agent1_batch_document(stored) -> dict[str, object]:
    batch = stored.batch
    assert batch is not None
    return {
        "_id": batch.mongo_id,
        "rule_version": batch.rule_version,
        "contract_version": batch.contract_version,
        "request_count": batch.request_count,
        "request_ids": list(batch.request_ids),
        "batch_sha256": batch.batch_sha256,
        "created_at": batch.created_at,
        "requests": [
            {
                "request_id": item.request_id,
                "rule_version": item.rule_version,
                "fact_code": item.fact_code,
                "contract_version": item.contract_version,
                "payload_sha256": item.payload_sha256,
                "created_at": item.created_at,
                "payload": item.payload.model_dump(by_alias=True, mode="json"),
            }
            for item in batch.requests
        ],
    }


def _source(rule_docs: dict[str, dict | None], batch_docs: dict[str, dict | None]):
    settings = _settings()
    client = _FakeClient(
        settings.mongodb_database,
        {
            settings.mongodb_rule_versions_v3_collection: _FakeCollection(rule_docs),
            settings.mongodb_fact_binding_batch_collection: _FakeCollection(batch_docs),
        },
    )
    return MongodbCompleteDeliverySourceV31(settings, client=client)


def test_fake_mongo_select_delivery_reads_schema_v6_documents() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    source = _source(
        {stored.rule_version: _agent1_rule_document(stored)},
        {stored.rule_version: _agent1_batch_document(stored)},
    )

    delivery = asyncio.run(
        select_complete_delivery_v31(
            source,
            purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
            rule_version=stored.rule_version,
        )
    )

    assert delivery.consumable is True
    assert delivery.schema_version == "3.1.0"
    assert delivery.executable is False
    assert delivery.request_count == 2
    assert all(item.payload.mapping_is_unresolved() for item in delivery.batch.requests)


def test_schema_v3_rule_document_is_not_a_complete_delivery() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    rule = _agent1_rule_document(stored)
    rule["schema_version"] = "3.0.0"
    source = _source(
        {stored.rule_version: rule},
        {stored.rule_version: _agent1_batch_document(stored)},
    )

    with pytest.raises(CompleteDeliveryNotFoundV31Error):
        asyncio.run(
            select_complete_delivery_v31(
                source,
                purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
                rule_version=stored.rule_version,
            )
        )


def test_v3_batch_is_not_consumable_as_complete_delivery() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    batch = _agent1_batch_document(stored)
    batch["contract_version"] = "3.0.0"
    source = _source(
        {stored.rule_version: _agent1_rule_document(stored)},
        {stored.rule_version: batch},
    )

    with pytest.raises(CompleteDeliveryNotConsumableV31Error) as error:
        asyncio.run(
            select_complete_delivery_v31(
                source,
                purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
                rule_version=stored.rule_version,
            )
        )
    assert error.value.missing == ("batch",)


def test_mongo_source_stays_disabled_without_database() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    source = MongodbCompleteDeliverySourceV31(Settings(_env_file=None))
    with pytest.raises(MongodbCompleteDeliveryV31Error):
        asyncio.run(source.get_delivery(stored.rule_version))
