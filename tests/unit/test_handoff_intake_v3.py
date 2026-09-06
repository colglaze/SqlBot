from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from release_sql_bot.application.handoff_intake_v3 import (
    FACT_BINDING_SCHEMA_ID_V3,
    FACT_BINDING_SCHEMA_SHA256_V3,
    FactBindingHandoffBatchInvalidErrorV3,
    FactBindingHandoffBatchNotFoundErrorV3,
    intake_fact_binding_handoffs_v3,
    load_fact_binding_schema_v3,
)
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffBatchDocumentInvalidV3Error,
    FactBindingHandoffBatchRepositoryV3UnavailableError,
)
from release_sql_bot.domain.fact_binding_handoffs_v3 import StoredFactBindingHandoffBatchV3
from tests.v3_handoff_support import (
    valid_batch_document,
    valid_v3_binding_payload,
    with_fact_code,
)


@dataclass
class FakeV3BatchRepository:
    batch: StoredFactBindingHandoffBatchV3 | None = None
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.calls: list[str] = []

    async def get_batch_by_rule_version(self, rule_version: str):
        self.calls.append(rule_version)
        if self.error is not None:
            raise self.error
        return self.batch


def _stored(document: dict | None = None) -> StoredFactBindingHandoffBatchV3:
    return StoredFactBindingHandoffBatchV3.model_validate(document or valid_batch_document())


def test_checked_in_v3_schema_hash_is_frozen() -> None:
    schema = load_fact_binding_schema_v3()

    assert schema["$id"] == FACT_BINDING_SCHEMA_ID_V3
    assert FACT_BINDING_SCHEMA_SHA256_V3 == (
        "2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566"
    )


def test_valid_ready_batch_is_intact_without_gap_semantics() -> None:
    stored = _stored()
    repository = FakeV3BatchRepository(stored)
    original = deepcopy(stored.model_dump(by_alias=True, mode="python"))

    batch = asyncio.run(intake_fact_binding_handoffs_v3(repository, stored.rule_version))

    assert repository.calls == [stored.rule_version]
    assert batch.status == "readyForMetadataResolution"
    assert batch.executable is False
    assert batch.contract_schema_id == FACT_BINDING_SCHEMA_ID_V3
    assert batch.contract_schema_sha256 == FACT_BINDING_SCHEMA_SHA256_V3
    assert batch.batch_sha256 == stored.batch_sha256
    assert batch.request_count == 1
    assert batch.requests[0].payload == stored.requests[0].payload
    assert batch.requests[0].payload.uncertainties[0].impact == "warning"
    assert stored.model_dump(by_alias=True, mode="python") == original


def test_batch_records_are_sorted_deterministically_by_request_identity() -> None:
    alpha = with_fact_code(valid_v3_binding_payload(), "report.alpha_amount")
    stored = _stored(valid_batch_document([valid_v3_binding_payload(), alpha]))

    batch = asyncio.run(
        intake_fact_binding_handoffs_v3(FakeV3BatchRepository(stored), stored.rule_version)
    )

    assert [record.fact_code for record in batch.requests] == [
        "report.alpha_amount",
        "report.synthetic_amount",
    ]
    assert batch.request_count == 2


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda batch: batch["requests"][0].update(payload_sha256="f" * 64), "hash"),
        (lambda batch: batch.update(batch_sha256="e" * 64), "hash"),
        (lambda batch: batch.update(request_ids=["zzz"]), "requestIds"),
        (lambda batch: batch.update(request_count=2), "request count"),
        (lambda batch: batch.update(_id="OTHER@VERSION"), "rule version"),
        (
            lambda batch: batch["requests"][0].update(fact_code="report.other_amount"),
            "identity",
        ),
        (
            lambda batch: batch["requests"][0].update(rule_version="OTHER@VERSION"),
            "foreign rule version",
        ),
    ],
)
def test_corrupt_batch_fails_closed_without_partial_intake(mutate, match: str) -> None:
    document = valid_batch_document()
    mutate(document)
    stored = _stored(document)

    with pytest.raises(FactBindingHandoffBatchInvalidErrorV3, match=match):
        asyncio.run(
            intake_fact_binding_handoffs_v3(FakeV3BatchRepository(stored), stored.rule_version)
        )


def test_duplicate_fact_identity_fails_the_whole_batch() -> None:
    payload = valid_v3_binding_payload()
    stored = _stored(valid_batch_document([payload, payload]))

    with pytest.raises(FactBindingHandoffBatchInvalidErrorV3, match="unique"):
        asyncio.run(
            intake_fact_binding_handoffs_v3(FakeV3BatchRepository(stored), stored.rule_version)
        )


def test_cross_version_batch_content_is_rejected() -> None:
    foreign_payload = with_fact_code(valid_v3_binding_payload(), "report.foreign_amount")
    foreign_payload["ruleRef"]["ruleVersion"] = "SYNTH_RULE_SET@OTHERVERSION"
    foreign_payload["requestId"] = (
        f"{foreign_payload['ruleRef']['ruleVersion']}#report.foreign_amount"
    )
    document = valid_batch_document([valid_v3_binding_payload(), foreign_payload])
    stored = _stored(document)

    with pytest.raises(FactBindingHandoffBatchInvalidErrorV3, match="foreign rule version"):
        asyncio.run(
            intake_fact_binding_handoffs_v3(FakeV3BatchRepository(stored), document["rule_version"])
        )


def test_wrapper_timestamps_are_excluded_from_hash_verification() -> None:
    document = valid_batch_document()
    later = datetime(2027, 1, 1, tzinfo=UTC)
    document["created_at"] = later
    document["requests"][0]["created_at"] = later
    stored = _stored(document)

    batch = asyncio.run(
        intake_fact_binding_handoffs_v3(FakeV3BatchRepository(stored), stored.rule_version)
    )

    assert batch.request_count == 1
    assert batch.requests[0].created_at == later


def test_dangling_payload_evidence_is_rejected_by_the_consumer_model() -> None:
    payload = valid_v3_binding_payload()
    payload["queryRequirements"]["entity"]["evidenceIds"] = ["ev-missing"]

    with pytest.raises(ValidationError, match="unknown evidence"):
        _stored(valid_batch_document(payload))


def test_not_found_document_invalid_and_unavailable_are_stable() -> None:
    rule_version = valid_v3_binding_payload()["ruleRef"]["ruleVersion"]
    with pytest.raises(FactBindingHandoffBatchNotFoundErrorV3):
        asyncio.run(intake_fact_binding_handoffs_v3(FakeV3BatchRepository(), rule_version))

    document_invalid = FactBindingHandoffBatchDocumentInvalidV3Error("must stay hidden")
    with pytest.raises(FactBindingHandoffBatchInvalidErrorV3, match="wrapper is invalid"):
        asyncio.run(
            intake_fact_binding_handoffs_v3(
                FakeV3BatchRepository(error=document_invalid), rule_version
            )
        )

    unavailable = FactBindingHandoffBatchRepositoryV3UnavailableError("mongo-secret.example")
    with pytest.raises(FactBindingHandoffBatchRepositoryV3UnavailableError):
        asyncio.run(
            intake_fact_binding_handoffs_v3(FakeV3BatchRepository(error=unavailable), rule_version)
        )
