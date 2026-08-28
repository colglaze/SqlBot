from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass

import pytest

from release_sql_bot.application.handoff_intake_v2 import (
    FACT_BINDING_SCHEMA_ID_V2,
    FACT_BINDING_SCHEMA_SHA256_V2,
    FactBindingHandoffInvalidError,
    FactBindingHandoffNotFoundError,
    intake_fact_binding_handoffs_v2,
    load_fact_binding_schema_v2,
)
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffRepositoryUnavailableError,
)
from release_sql_bot.domain.fact_binding_handoffs_v2 import StoredFactBindingHandoffV2
from tests.handoff_support import valid_handoff_document, valid_v2_binding_payload


@dataclass
class FakeHandoffRepository:
    records: tuple[StoredFactBindingHandoffV2, ...] = ()
    error: Exception | None = None

    def __post_init__(self) -> None:
        self.calls: list[str] = []

    async def list_by_rule_version(
        self,
        rule_version: str,
    ) -> tuple[StoredFactBindingHandoffV2, ...]:
        self.calls.append(rule_version)
        if self.error is not None:
            raise self.error
        return self.records


def _stored(document: dict[str, object] | None = None) -> StoredFactBindingHandoffV2:
    return StoredFactBindingHandoffV2.model_validate(document or valid_handoff_document())


def test_checked_in_v2_schema_has_frozen_identity_and_hash() -> None:
    schema = load_fact_binding_schema_v2()

    assert schema["$id"] == FACT_BINDING_SCHEMA_ID_V2
    assert FACT_BINDING_SCHEMA_SHA256_V2 == (
        "38fec6b22511984983e7e7fbbdb40afd58aeffd51b2de8ab73fdfb187024026b"
    )


def test_valid_blocking_handoff_is_preserved_and_blocks_the_batch() -> None:
    stored = _stored()
    repository = FakeHandoffRepository((stored,))
    original = deepcopy(stored.model_dump(by_alias=True, mode="python"))

    batch = asyncio.run(intake_fact_binding_handoffs_v2(repository, stored.rule_version))

    assert repository.calls == [stored.rule_version]
    assert batch.status == "blocked"
    assert batch.executable is False
    assert batch.record_count == 1
    assert batch.blocking_request_count == 1
    assert batch.records[0].payload == stored.payload
    assert batch.records[0].payload_sha256 == stored.payload_sha256
    assert batch.records[0].gap_report.status == "blocked"
    assert stored.model_dump(by_alias=True, mode="python") == original


def test_batch_records_are_sorted_deterministically_by_fact_identity() -> None:
    rule_version = valid_v2_binding_payload()["ruleRef"]["ruleVersion"]
    records = []
    for fact_code in ("report.zeta_amount", "report.alpha_amount"):
        payload = valid_v2_binding_payload()
        payload["requestId"] = f"{rule_version}#{fact_code}"
        payload["fact"]["factCode"] = fact_code
        payload["mappingCandidate"]["factCode"] = fact_code
        records.append(_stored(valid_handoff_document(payload)))

    batch = asyncio.run(
        intake_fact_binding_handoffs_v2(FakeHandoffRepository(tuple(records)), rule_version)
    )

    assert [record.fact_code for record in batch.records] == [
        "report.alpha_amount",
        "report.zeta_amount",
    ]


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda document: document.update(payload_sha256="f" * 64), "hash"),
        (
            lambda document: document["payload"]["provenance"]["source"].update(sha256="b" * 64),
            "source",
        ),
        (
            lambda document: document["payload"].update(contractVersion="1.0.0"),
            "Schema",
        ),
        (lambda document: document.update(fact_code="report.other_amount"), "identity"),
    ],
)
def test_corrupt_handoff_fails_closed_without_partial_intake(mutate, match: str) -> None:
    document = valid_handoff_document()
    mutate(document)
    stored = _stored(document)

    with pytest.raises(FactBindingHandoffInvalidError, match=match):
        asyncio.run(
            intake_fact_binding_handoffs_v2(
                FakeHandoffRepository((stored,)),
                stored.rule_version,
            )
        )


def test_duplicate_fact_or_cross_version_record_fails_the_whole_batch() -> None:
    first = _stored()
    duplicate_document = valid_handoff_document()
    duplicate_document["_id"] = f"{first.rule_version}#report.other_amount"
    duplicate_document["request_id"] = duplicate_document["_id"]
    duplicate_document["payload"]["requestId"] = duplicate_document["_id"]
    duplicate_document["payload"]["fact"]["factCode"] = "report.other_amount"
    duplicate_document["payload"]["mappingCandidate"]["factCode"] = "report.other_amount"
    duplicate_document["payload_sha256"] = valid_handoff_document(duplicate_document["payload"])[
        "payload_sha256"
    ]
    duplicate = _stored(duplicate_document)
    duplicate = duplicate.model_copy(update={"fact_code": first.fact_code})

    with pytest.raises(FactBindingHandoffInvalidError, match="duplicate"):
        asyncio.run(
            intake_fact_binding_handoffs_v2(
                FakeHandoffRepository((first, duplicate)),
                first.rule_version,
            )
        )

    cross_version = first.model_copy(update={"rule_version": "OTHER@VERSION"})
    with pytest.raises(FactBindingHandoffInvalidError, match="rule version"):
        asyncio.run(
            intake_fact_binding_handoffs_v2(
                FakeHandoffRepository((cross_version,)),
                first.rule_version,
            )
        )


def test_not_found_and_repository_unavailable_are_stable() -> None:
    rule_version = valid_v2_binding_payload()["ruleRef"]["ruleVersion"]
    with pytest.raises(FactBindingHandoffNotFoundError):
        asyncio.run(intake_fact_binding_handoffs_v2(FakeHandoffRepository(), rule_version))

    unavailable = FactBindingHandoffRepositoryUnavailableError("must stay hidden")
    with pytest.raises(FactBindingHandoffRepositoryUnavailableError):
        asyncio.run(
            intake_fact_binding_handoffs_v2(
                FakeHandoffRepository(error=unavailable),
                rule_version,
            )
        )
