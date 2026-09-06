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

    assert source == {
        "schemaId": "urn:rulereader:fact-binding-request:3.0.0",
        "contractVersion": "3.0.0",
        "sourceRepository": "RuleReader",
        "sourcePath": "contracts/fact-binding-request-3.0.0.schema.json",
        "sha256": "2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566",
        "observedAt": "2026-09-06",
        "consumerFixture": "tests/fixtures/fact-binding-request-3.0.0.synthetic-ready.json",
        "fixtureDataClassification": "synthetic-redacted",
        "sourceHeadAtObservation": "65a96846b3ae991b42a8159dbbee43a9bbe15846",
        "upstreamCommittedAtObservation": False,
    }
    assert source["sha256"] == FACT_BINDING_SCHEMA_SHA256_V3


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
