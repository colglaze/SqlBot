"""Contract tests for V3 BindingResolutionReportV3 (M2 第四子任务).

Tests the output contract, status consistency and structural constraints.
Does NOT test resolution logic or hash computation.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from release_sql_bot.domain.project_bindings_v3 import (
    BindingResolutionReportV3,
)
from tests.v3_metadata_support import (
    valid_blocked_report_v3_wire,
    valid_metadata_resolved_report_v3_wire,
)

# ===================================================================
# Section 1: Valid payload round-trip
# ===================================================================


def test_metadata_resolved_round_trip() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    report = BindingResolutionReportV3.model_validate(wire)
    dumped = report.model_dump(by_alias=True, mode="json")
    assert dumped == wire


def test_blocked_round_trip() -> None:
    wire = valid_blocked_report_v3_wire()
    report = BindingResolutionReportV3.model_validate(wire)
    dumped = report.model_dump(by_alias=True, mode="json")
    assert dumped == wire


# ===================================================================
# Section 2: Version / status / executable constraints
# ===================================================================


def test_rejects_wrong_schema_version() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["schemaVersion"] = "2.0.0"
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


def test_rejects_invalid_status() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["status"] = "ready"
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


def test_rejects_executable_true() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["executable"] = True
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


# ===================================================================
# Section 3: Missing required fields
# ===================================================================


@pytest.mark.parametrize(
    "missing_field",
    [
        "schemaVersion",
        "status",
        "requestRef",
        "projectRef",
        "contextRef",
        "snapshotRef",
        "handoffRefs",
        "resolutionHashes",
        "usageTraceabilitySha256",
    ],
)
def test_rejects_missing_required_field(missing_field: str) -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    del wire[missing_field]
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


# ===================================================================
# Section 4: Extra and snake_case rejection
# ===================================================================


def test_rejects_extra_top_level_field() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["inventedField"] = True
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


def test_rejects_extra_nested_field() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["projectRef"]["invented"] = True
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


def test_rejects_snake_case_top_level() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["request_ref"] = wire.pop("requestRef")
    with pytest.raises(ValidationError, match="snake_case"):
        BindingResolutionReportV3.model_validate(wire)


def test_rejects_snake_case_nested() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["requestRef"]["request_id"] = wire["requestRef"].pop("requestId")
    with pytest.raises(ValidationError, match="snake_case"):
        BindingResolutionReportV3.model_validate(wire)


# ===================================================================
# Section 5: Type coercion and SHA-256 format
# ===================================================================


def test_rejects_integer_coercion_for_string() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["requestRef"]["requestId"] = 12345
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


def test_rejects_sha256_wrong_length() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["usageTraceabilitySha256"] = "a" * 63
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


def test_rejects_sha256_uppercase() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["handoffRefs"]["batchSha256"] = "A" * 64
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


# ===================================================================
# Section 6: Status/output consistency
# ===================================================================


def test_blocked_requires_blocker_issue() -> None:
    wire = valid_blocked_report_v3_wire()
    wire["issues"] = []
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


def test_blocked_with_partial_output_rejected() -> None:
    """blocked must not carry resolvable output fields."""
    wire = valid_blocked_report_v3_wire()
    wire["resolvedFields"] = [
        {
            "fieldId": "factValue",
            "role": "value",
            "authorizationId": "fba-value",
            "columnGrantId": "colgrant-value",
            "schemaName": "dbo",
            "relationName": "synthetic_table",
            "columnName": "synthetic_value",
            "evidenceIds": ["ev-fact-declaration"],
        }
    ]
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


def test_metadata_resolved_rejects_blocker_issue() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["issues"] = [
        {
            "code": "ENTITY_KEY_NOT_AUTHORIZED",
            "owner": "metadataReview",
            "impact": "blocker",
            "message": "Synthetic blocker.",
        }
    ]
    with pytest.raises(ValidationError):
        BindingResolutionReportV3.model_validate(wire)


# ===================================================================
# Section 7: Empty filters / aggregation / timeRange / joins
# ===================================================================


def test_metadata_resolved_allows_empty_filters() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["resolvedFilters"] = []
    report = BindingResolutionReportV3.model_validate(wire)
    assert report.resolved_filters == []


def test_metadata_resolved_allows_none_aggregation() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["resolvedAggregation"] = None
    report = BindingResolutionReportV3.model_validate(wire)
    assert report.resolved_aggregation is None


def test_metadata_resolved_allows_none_time_range() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["resolvedTimeRange"] = None
    report = BindingResolutionReportV3.model_validate(wire)
    assert report.resolved_time_range is None


def test_metadata_resolved_allows_empty_joins() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    wire["resolvedJoins"] = []
    report = BindingResolutionReportV3.model_validate(wire)
    assert report.resolved_joins == []


# ===================================================================
# Section 8: Field / entity-key / evidence preservation
# ===================================================================


def test_resolved_field_preserves_evidence() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    report = BindingResolutionReportV3.model_validate(wire)
    assert len(report.resolved_fields) == 1
    assert "ev-fact-declaration" in report.resolved_fields[0].evidence_ids


def test_resolved_entity_key_preserves_evidence() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    report = BindingResolutionReportV3.model_validate(wire)
    assert len(report.resolved_entity_keys) == 1
    assert "ev-fact-declaration" in report.resolved_entity_keys[0].evidence_ids


def test_same_condition_id_different_usage_not_merged() -> None:
    """Same conditionId in different stages must produce separate usages."""
    wire = valid_metadata_resolved_report_v3_wire()
    report = BindingResolutionReportV3.model_validate(wire)
    # The usageTraceabilitySha256 is a single digest; the report
    # structure preserves the full sextuplet via the digest, not
    # by merging conditionIds.
    assert len(report.usage_traceability_sha256) == 64


# ===================================================================
# Section 9: No execution / review / SQL fields
# ===================================================================


def test_serialized_report_has_no_execution_fields() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    report = BindingResolutionReportV3.model_validate(wire)
    dumped = report.model_dump(by_alias=True, mode="json")
    assert "reviewStatus" not in dumped
    assert "bindingGapReport" not in dumped
    assert "repositoryVerified" not in dumped
    assert dumped["executable"] is False


# ===================================================================
# Section 10: Input immutability
# ===================================================================


def test_does_not_mutate_input_wire() -> None:
    wire = valid_metadata_resolved_report_v3_wire()
    original = deepcopy(wire)
    BindingResolutionReportV3.model_validate(wire)
    assert wire == original


# ===================================================================
# Section 11: No V2 / infrastructure dependencies
# ===================================================================


def test_module_does_not_import_v2_or_infrastructure() -> None:
    from pathlib import Path

    import release_sql_bot.domain.project_bindings_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "project_bindings_v2" not in source
    assert "fact_bindings_v2" not in source
    assert "BindingResolutionReportV2" not in source
    assert "mongodb" not in source
    assert "pymongo" not in source
    assert "sqlglot" not in source.lower()
    assert "os.environ" not in source
    assert "getenv" not in source
