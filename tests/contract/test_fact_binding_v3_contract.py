from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from release_sql_bot.application.handoff_intake_v3 import (
    FACT_BINDING_SCHEMA_ID_V3,
    FACT_BINDING_SCHEMA_SHA256_V3,
    load_fact_binding_schema_v3,
)
from release_sql_bot.domain.fact_binding_handoffs_v3 import StoredFactBindingHandoffBatchV3
from release_sql_bot.domain.fact_bindings import FactBindingRequest
from release_sql_bot.domain.fact_bindings_v2 import FactBindingRequestV2
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3

ROOT = Path(__file__).resolve().parents[2]
V3_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fact-binding-request-3.0.0.synthetic-ready.json"
V3_BATCH_FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "fact-binding-handoff-batch-3.0.0.synthetic.json"
)
V2_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fact-binding-request-2.0.0.synthetic-blocked.json"
SOURCE_PATH = ROOT / "docs" / "specs" / "fact-binding-request-3.0.0-source.json"


def _fixture() -> dict[str, object]:
    return json.loads(V3_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_checked_in_v3_schema_has_frozen_identity_and_hash() -> None:
    schema = load_fact_binding_schema_v3()

    assert schema["$id"] == FACT_BINDING_SCHEMA_ID_V3
    assert FACT_BINDING_SCHEMA_SHA256_V3 == (
        "2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566"
    )


def test_synthetic_v3_fixture_is_losslessly_consumed_as_camel_case() -> None:
    payload = _fixture()

    request = FactBindingRequestV3.model_validate(payload)

    assert request.model_dump(by_alias=True, mode="json") == payload
    assert "queryRequirements" in payload
    assert "evidence" in payload
    assert "uncertainties" in payload


def test_v3_contract_rejects_extra_fields_at_every_object_boundary() -> None:
    root_extra = _fixture()
    root_extra["inventedPermission"] = True

    with pytest.raises(ValidationError, match="inventedPermission"):
        FactBindingRequestV3.model_validate(root_extra)

    nested_extra = _fixture()
    nested_extra["ruleRef"]["ruleId"] = "SYNTH_RULE_SET"

    with pytest.raises(ValidationError, match="ruleId"):
        FactBindingRequestV3.model_validate(nested_extra)


def test_v3_wire_contract_rejects_snake_case_fallback() -> None:
    payload = _fixture()
    payload["contract_version"] = payload.pop("contractVersion")

    with pytest.raises(ValidationError):
        FactBindingRequestV3.model_validate(payload)


def test_v3_wire_contract_rejects_json_type_coercion() -> None:
    payload = _fixture()
    payload["fact"]["nullable"] = 0

    with pytest.raises(ValidationError):
        FactBindingRequestV3.model_validate(payload)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda payload: payload["uncertainties"][0].update(impact="blocking"), "blocking"),
        (lambda payload: payload["fact"].update(factKind="derived"), "derived"),
        (
            lambda payload: payload["queryRequirements"]["filters"].update(
                completeness="unresolved"
            ),
            "complete",
        ),
        (
            lambda payload: payload["queryRequirements"]["entity"].update(
                evidenceIds=["ev-missing"]
            ),
            "unknown evidence",
        ),
        (
            lambda payload: payload["evidence"].append(
                {
                    "evidenceId": "ev-unreferenced",
                    "kind": "example",
                    "sourceDocument": "ruleResult",
                    "sourcePath": "/unused",
                }
            ),
            "unreferenced evidence",
        ),
    ],
)
def test_v3_consumer_rejects_upstream_forbidden_shapes(mutate, match: str) -> None:
    payload = _fixture()
    mutate(payload)

    with pytest.raises(ValidationError, match=match):
        FactBindingRequestV3.model_validate(payload)


def test_v2_and_v1_models_cannot_accept_v3_payload() -> None:
    with pytest.raises(ValidationError):
        FactBindingRequestV2.model_validate(_fixture())

    with pytest.raises(ValidationError):
        FactBindingRequest.model_validate(_fixture())


def test_v3_consumer_cannot_accept_v2_payload() -> None:
    v2_payload = json.loads(V2_FIXTURE_PATH.read_text(encoding="utf-8"))

    with pytest.raises(ValidationError):
        FactBindingRequestV3.model_validate(v2_payload)


def test_upstream_schema_source_record_is_frozen_and_machine_readable() -> None:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))

    # Precise top-level key set: no more, no less.
    assert set(source.keys()) == {
        "schemaId",
        "contractVersion",
        "sourceRepository",
        "sourcePath",
        "sha256",
        "sha256Note",
        "observedAt",
        "consumerFixture",
        "fixtureDataClassification",
        "sourceHeadAtObservation",
        "upstreamCommittedAtObservation",
        "verifiedCommitTree",
        "runtime",
    }

    # Historical observation fields preserved unchanged.
    assert source["schemaId"] == "urn:rulereader:fact-binding-request:3.0.0"
    assert source["contractVersion"] == "3.0.0"
    assert source["sourceRepository"] == "RuleReader"
    assert source["sourcePath"] == "contracts/fact-binding-request-3.0.0.schema.json"
    assert source["observedAt"] == "2026-09-06"
    assert source["consumerFixture"] == (
        "tests/fixtures/fact-binding-request-3.0.0.synthetic-ready.json"
    )
    assert source["fixtureDataClassification"] == "synthetic-redacted"
    assert source["sourceHeadAtObservation"] == ("65a96846b3ae991b42a8159dbbee43a9bbe15846")
    assert source["upstreamCommittedAtObservation"] is False

    # Top-level sha256 is the historical/runtime-normalized hash.
    assert source["sha256"] == FACT_BINDING_SCHEMA_SHA256_V3
    assert source["sha256Note"] == (
        "Historical observation hash (2026-09-06). Matches runtime CRLF-normalized "
        "hash and FACT_BINDING_SCHEMA_SHA256_V3; NOT the upstream commit-tree raw bytes hash."
    )

    # Verified commit-tree evidence (2026-09-09 read-only verification).
    verified = source["verifiedCommitTree"]
    assert set(verified.keys()) == {
        "verifiedAt",
        "commit",
        "repositoryHeadAtVerification",
        "sourcePath",
        "byteLength",
        "lineEnding",
        "sha256",
        "jsonStructureIdenticalToPackagedSchema",
    }
    assert verified["verifiedAt"] == "2026-09-09"
    assert verified["commit"] == "bad6fd349a6ecbff190b9bd0ac1bc34a48588325"
    assert verified["repositoryHeadAtVerification"] == ("01ddae0979e8adb4fcb41e68e3f33deb850b1443")
    assert verified["sourcePath"] == "contracts/fact-binding-request-3.0.0.schema.json"
    assert verified["byteLength"] == 29870
    assert verified["lineEnding"] == "LF"
    assert verified["sha256"] == (
        "0e39c7acd96b22fc91c3a6a228561d3db9b6368db9f062c95f970b19ec164903"
    )
    assert verified["jsonStructureIdenticalToPackagedSchema"] is True

    # Runtime normalization evidence.
    runtime = source["runtime"]
    assert set(runtime.keys()) == {
        "normalization",
        "normalizedLineEnding",
        "sha256",
        "matchesFactBindingSchemaSha256V3",
    }
    assert runtime["normalization"] == (
        'CRLF-normalized before hashing (raw.replace(b"\\r\\n", b"\\n").replace(b"\\n", b"\\r\\n"))'
    )
    assert runtime["normalizedLineEnding"] == "CRLF"
    assert runtime["sha256"] == FACT_BINDING_SCHEMA_SHA256_V3
    assert runtime["matchesFactBindingSchemaSha256V3"] is True


def test_synthetic_v3_fixture_passes_the_frozen_upstream_json_schema() -> None:
    schema = load_fact_binding_schema_v3()

    Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    ).validate(_fixture())


def test_synthetic_v3_batch_fixture_json_is_parseable_and_consistent() -> None:
    raw = V3_BATCH_FIXTURE_PATH.read_text(encoding="utf-8")

    batch = StoredFactBindingHandoffBatchV3.model_validate_json(raw)

    assert batch.request_count == 1
    assert batch.mongo_id == batch.rule_version
    assert batch.requests[0].payload.request_id == batch.requests[0].request_id
