from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from release_sql_bot.application.metadata_resolution_v2 import resolve_metadata_v2
from release_sql_bot.domain.project_bindings_v2 import ResolveMetadataRequestV2
from tests.phase2g_support import resolve_metadata_payload


def test_phase2g_wire_contract_is_lossless_strict_camel_case() -> None:
    payload = resolve_metadata_payload()

    request = ResolveMetadataRequestV2.model_validate(payload)

    assert request.model_dump(by_alias=True, mode="json") == payload


def test_phase2g_contract_rejects_extra_and_snake_case_fields() -> None:
    extra = resolve_metadata_payload()
    extra["projectContext"]["implicitPermission"] = True

    with pytest.raises(ValidationError, match="implicitPermission"):
        ResolveMetadataRequestV2.model_validate(extra)

    snake = resolve_metadata_payload()
    snake["bindingGapReport"]["request_id"] = snake["bindingGapReport"].pop("requestId")

    with pytest.raises(ValidationError, match="snake_case"):
        ResolveMetadataRequestV2.model_validate(snake)


def test_phase2g_contract_rejects_wrong_version_and_type_coercion() -> None:
    wrong_version = resolve_metadata_payload()
    wrong_version["projectContext"]["schemaVersion"] = "2.0.0"

    with pytest.raises(ValidationError):
        ResolveMetadataRequestV2.model_validate(wrong_version)

    coerced = resolve_metadata_payload()
    coerced["metadataSnapshot"]["snapshotVersion"] = "1"

    with pytest.raises(ValidationError):
        ResolveMetadataRequestV2.model_validate(coerced)


def test_phase2g_contract_rejects_duplicate_ids_and_wildcards() -> None:
    duplicate = resolve_metadata_payload()
    duplicate["projectContext"]["requestIds"].append(duplicate["projectContext"]["requestIds"][0])

    with pytest.raises(ValidationError, match="duplicate IDs"):
        ResolveMetadataRequestV2.model_validate(duplicate)

    wildcard = resolve_metadata_payload()
    wildcard["metadataSnapshot"]["relations"][0]["relationName"] = "synthetic_*"

    with pytest.raises(ValidationError, match="non-temporary"):
        ResolveMetadataRequestV2.model_validate(wildcard)


def test_resolution_report_is_camel_case_and_never_executable() -> None:
    report = resolve_metadata_v2(
        ResolveMetadataRequestV2.model_validate(resolve_metadata_payload())
    )
    serialized = report.model_dump(by_alias=True, mode="json")

    assert serialized["schemaVersion"] == "1.0.0"
    assert serialized["status"] == "metadataResolved"
    assert serialized["executable"] is False
    assert "readyForGeneration" not in str(serialized)
    assert "reviewStatus" not in str(serialized)


def test_phase2g_input_models_do_not_share_mutable_payload_state() -> None:
    payload = resolve_metadata_payload()
    original = deepcopy(payload)

    resolve_metadata_v2(ResolveMetadataRequestV2.model_validate(payload))

    assert payload == original
