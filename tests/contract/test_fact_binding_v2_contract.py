from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from release_sql_bot.application.handoff_intake_v2 import (
    FACT_BINDING_SCHEMA_SHA256_V2,
    load_fact_binding_schema_v2,
)
from release_sql_bot.domain.fact_bindings import FactBindingRequest
from release_sql_bot.domain.fact_bindings_v2 import FactBindingRequestV2

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fact-binding-request-2.0.0.synthetic-blocked.json"
SOURCE_PATH = ROOT / "docs" / "specs" / "fact-binding-request-2.0.0-source.json"


def _fixture() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_synthetic_v2_fixture_is_losslessly_consumed_as_camel_case() -> None:
    payload = _fixture()

    request = FactBindingRequestV2.model_validate(payload)

    assert request.model_dump(by_alias=True, mode="json") == payload
    assert "queryRequirements" in payload
    assert "provenance" in payload
    assert "uncertainties" in payload


def test_v2_contract_rejects_extra_fields_at_every_object_boundary() -> None:
    root_extra = _fixture()
    root_extra["inventedPermission"] = True

    with pytest.raises(ValidationError, match="inventedPermission"):
        FactBindingRequestV2.model_validate(root_extra)

    nested_extra = _fixture()
    nested_extra["queryRequirements"]["fields"][0]["schemaName"] = "dbo"

    with pytest.raises(ValidationError, match="schemaName"):
        FactBindingRequestV2.model_validate(nested_extra)


def test_v2_wire_contract_rejects_snake_case_fallback() -> None:
    payload = _fixture()
    payload["contract_version"] = payload.pop("contractVersion")

    with pytest.raises(ValidationError):
        FactBindingRequestV2.model_validate(payload)


def test_v2_wire_contract_rejects_json_type_coercion() -> None:
    payload = _fixture()
    payload["fact"]["nullable"] = 0

    with pytest.raises(ValidationError):
        FactBindingRequestV2.model_validate(payload)


def test_v1_legacy_model_cannot_accept_or_downgrade_v2_payload() -> None:
    with pytest.raises(ValidationError):
        FactBindingRequest.model_validate(_fixture())


def test_upstream_schema_source_record_is_frozen_and_machine_readable() -> None:
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))

    assert source == {
        "schemaId": "urn:rulereader:fact-binding-request:2.0.0",
        "contractVersion": "2.0.0",
        "sourceRepository": "RuleReader",
        "sourcePath": "contracts/fact-binding-request-2.0.0.schema.json",
        "sha256": "38fec6b22511984983e7e7fbbdb40afd58aeffd51b2de8ab73fdfb187024026b",
        "observedAt": "2026-08-27",
        "consumerFixture": ("tests/fixtures/fact-binding-request-2.0.0.synthetic-blocked.json"),
        "fixtureDataClassification": "synthetic-redacted",
    }
    assert source["sha256"] == FACT_BINDING_SCHEMA_SHA256_V2


def test_synthetic_fixture_passes_the_frozen_upstream_json_schema() -> None:
    schema = load_fact_binding_schema_v2()

    Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
    ).validate(_fixture())
