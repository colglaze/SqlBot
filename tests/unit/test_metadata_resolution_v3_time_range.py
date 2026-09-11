"""Unit tests for V3 time-range resolution (DEV §5.3.5).

Tests the internal helper ``_resolve_time_range_v3``: mapping the
time-range declaration to its authorized physical column reference.

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError as _PydanticValidationError

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataBindingResolutionErrorV3,
    MetadataColumnResolutionErrorV3,
    MetadataResolutionInputErrorV3,
    _resolve_time_range_v3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordV3,
    GovernedMetadataSnapshotV3,
    ProjectBindingContextV3,
    ResolveMetadataRequestV3,
)
from tests.v3_metadata_support import valid_resolve_metadata_request_v3_wire

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_request() -> ResolveMetadataRequestV3:
    """Build a valid, internally-consistent request from the shared fixture."""
    wire = valid_resolve_metadata_request_v3_wire()
    return ResolveMetadataRequestV3.model_validate(wire)


def _make_request_with_time_range(
    time_range: dict[str, object],
    extra_fields: list[dict[str, object]] | None = None,
) -> ResolveMetadataRequestV3:
    """Build a valid request with the given time-range declaration."""
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingRequest"]["queryRequirements"]["timeRange"] = time_range
    if extra_fields:
        wire["bindingRequest"]["queryRequirements"]["fields"] = [
            *wire["bindingRequest"]["queryRequirements"]["fields"],
            *extra_fields,
        ]
    from release_sql_bot.application.canonical import canonical_sha256
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3

    wire["handoffClosure"]["payload"] = wire["bindingRequest"]
    wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire["bindingRequest"])
    )
    return ResolveMetadataRequestV3.model_validate(wire)


def _make_request_with_time_field(
    time_range: dict[str, object],
) -> ResolveMetadataRequestV3:
    """Build a valid request with a fully-authorized syntheticTime field.

    Adds the time field to queryRequirements.fields, a matching
    FieldBindingAuthorization + ColumnGrant, and the snapshot column,
    with all dependent hashes reclosed.
    """
    request = _make_valid_request()
    # Add the time field + authorization + column grant + snapshot column
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["bindingRequest"]["queryRequirements"]["timeRange"] = time_range
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *req_wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "syntheticTime",
            "role": "time",
            "logicalName": "synthetic_time",
            "dataType": "datetime",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    req_wire["projectContext"]["fieldBindingAuthorizations"] = [
        *req_wire["projectContext"]["fieldBindingAuthorizations"],
        {
            "authorizationId": "fba-time",
            "requestId": req_wire["bindingRequest"]["requestId"],
            "fieldId": "syntheticTime",
            "role": "time",
            "columnGrantId": "colgrant-time",
        },
    ]
    req_wire["projectContext"]["columnGrants"] = [
        *req_wire["projectContext"]["columnGrants"],
        {
            "grantId": "colgrant-time",
            "relationGrantId": "relgrant-1",
            "columnName": "synthetic_time",
        },
    ]
    req_wire["metadataSnapshot"]["relations"][0]["columns"] = [
        *req_wire["metadataSnapshot"]["relations"][0]["columns"],
        {
            "columnName": "synthetic_time",
            "sqlType": "datetime",
            "nullable": False,
        },
    ]
    # Reclose snapshot/context/approval hashes using dump-modify-validate
    snapshot = GovernedMetadataSnapshotV3.model_validate(req_wire["metadataSnapshot"])
    req_wire["metadataSnapshot"]["contentSha256"] = canonical_content_sha256(snapshot)
    snapshot = GovernedMetadataSnapshotV3.model_validate(req_wire["metadataSnapshot"])
    snapshot_sha = req_wire["metadataSnapshot"]["contentSha256"]

    context_wire = req_wire["projectContext"]
    context_wire["metadataSnapshotRef"]["sha256"] = snapshot_sha
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_sha = context_wire["contentSha256"]

    approval_wire = req_wire["approvalRecord"]
    approval_wire["snapshotRef"]["sha256"] = snapshot_sha
    approval_wire["contextRef"]["sha256"] = context_sha
    approval = ApprovalRecordV3.model_validate(approval_wire)
    approval_wire["contentSha256"] = canonical_content_sha256(approval)
    approval = ApprovalRecordV3.model_validate(approval_wire)

    req_wire["metadataSnapshot"] = snapshot.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"] = context.model_dump(by_alias=True, mode="json")
    req_wire["approvalRecord"] = approval.model_dump(by_alias=True, mode="json")
    req_wire["handoffClosure"]["payload"] = req_wire["bindingRequest"]
    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire["bindingRequest"])
    )
    return ResolveMetadataRequestV3.model_validate(req_wire)


def _rebuild_with_snapshot_columns(
    request: ResolveMetadataRequestV3,
    column_names: list[str],
) -> ResolveMetadataRequestV3:
    """Replace snapshot columns and reclose snapshot/context/approval hashes."""
    # 1. Rebuild snapshot
    snapshot_wire = request.metadata_snapshot.model_dump(by_alias=True, mode="json")
    base_columns = snapshot_wire["relations"][0]["columns"]
    by_name = {c["columnName"]: c for c in base_columns}
    snapshot_wire["relations"][0]["columns"] = [by_name[name] for name in column_names]
    snapshot = GovernedMetadataSnapshotV3.model_validate(snapshot_wire)
    snapshot_wire["contentSha256"] = canonical_content_sha256(snapshot)
    snapshot = GovernedMetadataSnapshotV3.model_validate(snapshot_wire)
    snapshot_sha = snapshot_wire["contentSha256"]

    # 2. Rebuild context
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["metadataSnapshotRef"]["sha256"] = snapshot_sha
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_sha = context_wire["contentSha256"]

    # 3. Rebuild approval
    approval_wire = request.approval_record.model_dump(by_alias=True, mode="json")
    approval_wire["contextRef"]["sha256"] = context_sha
    approval_wire["snapshotRef"]["sha256"] = snapshot_sha
    approval = ApprovalRecordV3.model_validate(approval_wire)
    approval_wire["contentSha256"] = canonical_content_sha256(approval)
    approval = ApprovalRecordV3.model_validate(approval_wire)

    # 4. Rebuild full request
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"] = snapshot.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"] = context.model_dump(by_alias=True, mode="json")
    req_wire["approvalRecord"] = approval.model_dump(by_alias=True, mode="json")

    return ResolveMetadataRequestV3.model_validate(req_wire)


# ===================================================================
# Section A: Three modes — full output wire
# ===================================================================


def test_none_mode_full_wire():
    """none mode returns null physical identifiers after gates pass."""
    time_range = {
        "mode": "none",
        "timeFieldId": None,
        "start": None,
        "end": None,
        "timezone": None,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_time_range(time_range)
    result = _resolve_time_range_v3(request)

    assert result.mode == "none"
    assert result.time_field_id is None
    assert result.time_schema_name is None
    assert result.time_relation_name is None
    assert result.time_column_name is None
    assert result.evidence_ids == ["ev-query-requirement"]


def test_asof_mode_full_wire():
    """asOf mode maps the time field to its authorized physical reference."""
    time_range = {
        "mode": "asOf",
        "timeFieldId": "syntheticTime",
        "start": None,
        "end": {
            "kind": "literal",
            "value": "2026-09-11T00:00:00+00:00",
            "inclusive": True,
        },
        "timezone": "UTC",
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_time_field(time_range)
    result = _resolve_time_range_v3(request)

    assert result.mode == "asOf"
    assert result.time_field_id == "syntheticTime"
    assert result.time_schema_name == "dbo"
    assert result.time_relation_name == "synthetic_table"
    assert result.time_column_name == "synthetic_time"
    assert result.evidence_ids == ["ev-query-requirement"]


def test_between_mode_full_wire():
    """between mode maps the time field to its authorized physical reference."""
    time_range = {
        "mode": "between",
        "timeFieldId": "syntheticTime",
        "start": {
            "kind": "literal",
            "value": "2026-09-01T00:00:00+00:00",
            "inclusive": True,
        },
        "end": {
            "kind": "parameter",
            "parameterName": "syntheticKey",
            "inclusive": False,
        },
        "timezone": "Asia/Shanghai",
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_time_field(time_range)
    result = _resolve_time_range_v3(request)

    assert result.mode == "between"
    assert result.time_field_id == "syntheticTime"
    assert result.time_schema_name == "dbo"
    assert result.time_relation_name == "synthetic_table"
    assert result.time_column_name == "synthetic_time"
    assert result.evidence_ids == ["ev-query-requirement"]


# ===================================================================
# Section B: Boundary variations do not change physical mapping
# ===================================================================


@pytest.mark.parametrize(
    "end_boundary",
    [
        {"kind": "literal", "value": "2026-09-11T00:00:00+00:00", "inclusive": True},
        {"kind": "parameter", "parameterName": "syntheticKey", "inclusive": False},
    ],
)
def test_asof_boundary_variations_same_mapping(end_boundary):
    """Different valid boundaries produce the same physical mapping."""
    time_range = {
        "mode": "asOf",
        "timeFieldId": "syntheticTime",
        "start": None,
        "end": end_boundary,
        "timezone": "UTC",
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_time_field(time_range)
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    result = _resolve_time_range_v3(request)

    assert result.mode == "asOf"
    assert result.time_field_id == "syntheticTime"
    assert result.time_schema_name == "dbo"
    assert result.time_relation_name == "synthetic_table"
    assert result.time_column_name == "synthetic_time"
    # Input must not be mutated
    assert request.model_dump(by_alias=True, mode="json") == before


# ===================================================================
# Section C: evidenceIds source and order
# ===================================================================


def test_time_range_evidence_ids_from_declaration():
    """evidenceIds come from the time-range declaration, not the field.

    Uses a non-sorted list that differs from the field evidence.
    """
    evidence_ids = ["ev-query-requirement", "ev-example", "ev-fact-declaration"]
    assert evidence_ids != sorted(evidence_ids), "evidenceIds already sorted"

    time_range = {
        "mode": "none",
        "timeFieldId": None,
        "start": None,
        "end": None,
        "timezone": None,
        "evidenceIds": evidence_ids,
    }
    request = _make_request_with_time_range(time_range)
    result = _resolve_time_range_v3(request)

    assert result.evidence_ids == evidence_ids


# ===================================================================
# Section D: Time field not authorized
# ===================================================================


def test_time_field_not_authorized():
    """Referencing a time field with no authorization raises FIELD_AUTHORIZATION_MISSING."""
    extra_fields = [
        {
            "fieldId": "unauthorizedTime",
            "role": "time",
            "logicalName": "unauthorized_time",
            "dataType": "datetime",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        }
    ]
    time_range = {
        "mode": "asOf",
        "timeFieldId": "unauthorizedTime",
        "start": None,
        "end": {"kind": "literal", "value": "2026-09-11T00:00:00+00:00", "inclusive": True},
        "timezone": "UTC",
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_time_range(time_range, extra_fields=extra_fields)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_time_range_v3(request)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


# ===================================================================
# Section E: Snapshot column missing → COLUMN_NOT_IN_SNAPSHOT
# ===================================================================


def test_snapshot_column_missing_raises_column_not_in_snapshot():
    """Removing only the target snapshot column yields COLUMN_NOT_IN_SNAPSHOT."""
    time_range = {
        "mode": "asOf",
        "timeFieldId": "syntheticTime",
        "start": None,
        "end": {"kind": "literal", "value": "2026-09-11T00:00:00+00:00", "inclusive": True},
        "timezone": "UTC",
        "evidenceIds": ["ev-query-requirement"],
    }
    # Baseline succeeds
    base = _make_request_with_time_field(time_range)
    base_result = _resolve_time_range_v3(base)
    assert base_result.time_column_name == "synthetic_time"

    # Remove synthetic_time from snapshot columns (keep synthetic_value/synthetic_key)
    tampered = _rebuild_with_snapshot_columns(
        base, column_names=["synthetic_value", "synthetic_key"]
    )

    # Input gate passes (hashes are consistent)
    from release_sql_bot.application.metadata_resolution_v3 import (
        _validate_resolution_input_v3,
    )

    _validate_resolution_input_v3(tampered)

    # Snapshot the actual tampered object before resolution
    tampered_before = deepcopy(tampered.model_dump(by_alias=True, mode="json"))

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_time_range_v3(tampered)
    assert exc_info.value.code == "COLUMN_NOT_IN_SNAPSHOT"

    # The actual tampered input must not be mutated
    tampered_after = tampered.model_dump(by_alias=True, mode="json")
    assert tampered_after == tampered_before, (
        "time-range resolver mutated its input on column-missing failure"
    )

    # Restore the column and reclose → success
    restored = _rebuild_with_snapshot_columns(
        base, column_names=["synthetic_value", "synthetic_key", "synthetic_time"]
    )
    restored_result = _resolve_time_range_v3(restored)
    assert restored_result.time_column_name == "synthetic_time"


# ===================================================================
# Section F: none mode still runs input and entity-key gates
# ===================================================================


def _build_time_range_for_mode(mode: str) -> dict[str, object]:
    """Build a valid time-range declaration for the given mode.

    Only none/asOf/between are valid per the V3 consumer contract.
    none has no time field reference; asOf/between do.
    """
    if mode not in ("none", "asOf", "between"):
        raise ValueError(f"invalid timeRange mode {mode!r}: only none/asOf/between are accepted")
    literal_boundary = {
        "kind": "literal",
        "value": "2026-09-11T00:00:00+00:00",
        "inclusive": True,
    }
    # none mode has no time field reference
    if mode == "none":
        return {
            "mode": "none",
            "timeFieldId": None,
            "start": None,
            "end": None,
            "timezone": None,
            "evidenceIds": ["ev-query-requirement"],
        }
    if mode == "asOf":
        return {
            "mode": "asOf",
            "timeFieldId": "syntheticTime",
            "start": None,
            "end": literal_boundary,
            "timezone": "UTC",
            "evidenceIds": ["ev-query-requirement"],
        }
    # between
    return {
        "mode": "between",
        "timeFieldId": "syntheticTime",
        "start": literal_boundary,
        "end": {
            "kind": "parameter",
            "parameterName": "syntheticKey",
            "inclusive": False,
        },
        "timezone": "UTC",
        "evidenceIds": ["ev-query-requirement"],
    }


@pytest.mark.parametrize("mode", ["none", "asOf", "between"])
def test_input_gate_failure_blocks_all_no_ref_modes(mode):
    """Input-gate failures propagate even for all valid time-range modes."""
    time_range = _build_time_range_for_mode(mode)
    if mode == "none":
        request = _make_request_with_time_range(time_range)
    else:
        request = _make_request_with_time_field(time_range)
    # Guard: the constructed request must reflect the parameterized mode
    assert request.binding_request.query_requirements.time_range.mode == mode

    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_time_range_v3(tampered)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"


@pytest.mark.parametrize("mode", ["none", "asOf", "between"])
def test_entity_key_closure_failure_blocks_all_modes(mode):
    """Entity-key closure failure propagates for all modes."""
    time_range = _build_time_range_for_mode(mode)
    if mode == "none":
        request = _make_request_with_time_range(time_range)
    else:
        request = _make_request_with_time_field(time_range)
    # Guard: the constructed request must reflect the parameterized mode
    assert request.binding_request.query_requirements.time_range.mode == mode

    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["entityKeyAuthorizations"] = []
    context_wire["contentSha256"] = canonical_content_sha256(
        ProjectBindingContextV3.model_validate(context_wire)
    )
    context = ProjectBindingContextV3.model_validate(context_wire)

    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"] = context_wire
    approval_wire = request.approval_record.model_dump(by_alias=True, mode="json")
    approval_wire["contextRef"] = {
        "contextId": context.context_id,
        "contextVersion": context.context_version,
        "sha256": context_wire["contentSha256"],
    }
    approval = ApprovalRecordV3.model_validate(approval_wire)
    approval_wire["contentSha256"] = canonical_content_sha256(approval)
    approval = ApprovalRecordV3.model_validate(approval_wire)
    req_wire["approvalRecord"] = approval.model_dump(by_alias=True, mode="json")

    tampered = ResolveMetadataRequestV3.model_validate(req_wire)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_time_range_v3(tampered)
    assert exc_info.value.code == "ENTITY_KEY_AUTHORIZATION_MISSING"


# ===================================================================
# Section G: Structural violations caught by existing gates
# ===================================================================


def test_dangling_time_field_reference_rejected_by_consumer():
    """A timeFieldId not in the request fields is rejected by the consumer."""
    time_range = {
        "mode": "asOf",
        "timeFieldId": "nonexistentTimeField",
        "start": None,
        "end": {"kind": "literal", "value": "2026-09-11T00:00:00+00:00", "inclusive": True},
        "timezone": "UTC",
        "evidenceIds": ["ev-query-requirement"],
    }
    with pytest.raises(_PydanticValidationError) as exc_info:
        _make_request_with_time_range(time_range)
    assert "unknown fields" in str(exc_info.value)


def test_dangling_boundary_parameter_rejected_by_consumer():
    """A boundary referencing a non-existent fact parameter is rejected."""
    extra_fields = [
        {
            "fieldId": "syntheticTime",
            "role": "time",
            "logicalName": "synthetic_time",
            "dataType": "datetime",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        }
    ]
    time_range = {
        "mode": "between",
        "timeFieldId": "syntheticTime",
        "start": {
            "kind": "parameter",
            "parameterName": "nonexistentParam",
            "inclusive": True,
        },
        "end": {
            "kind": "parameter",
            "parameterName": "syntheticKey",
            "inclusive": False,
        },
        "timezone": "UTC",
        "evidenceIds": ["ev-query-requirement"],
    }
    with pytest.raises(_PydanticValidationError) as exc_info:
        _make_request_with_time_range(time_range, extra_fields=extra_fields)
    assert "unknown fact parameter" in str(exc_info.value)


def test_dangling_time_range_evidence_rejected_by_consumer():
    """An evidenceIds reference to non-existent evidence is rejected."""
    time_range = {
        "mode": "none",
        "timeFieldId": None,
        "start": None,
        "end": None,
        "timezone": None,
        "evidenceIds": ["ev-does-not-exist"],
    }
    with pytest.raises(_PydanticValidationError) as exc_info:
        _make_request_with_time_range(time_range)
    assert "unknown evidence" in str(exc_info.value)


def test_structure_rejection_maps_to_input_error():
    """Illegal nested content is mapped to the structural input error."""
    request = _make_valid_request()
    # Bypass the constructor to inject a non-valid nested structure
    tampered = request.model_copy(update={"project_ref": "not-a-project-ref"})

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_time_range_v3(tampered)
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


# ===================================================================
# Section H: Immutability
# ===================================================================


def test_input_not_mutated_on_success():
    """The actual input is not mutated by a successful time-range resolution."""
    request = _make_valid_request()
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    _resolve_time_range_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_input_not_mutated_on_failure():
    """The actual input is not mutated by a failed time-range resolution."""
    extra_fields = [
        {
            "fieldId": "unauthorizedTime",
            "role": "time",
            "logicalName": "unauthorized_time",
            "dataType": "datetime",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        }
    ]
    time_range = {
        "mode": "asOf",
        "timeFieldId": "unauthorizedTime",
        "start": None,
        "end": {"kind": "literal", "value": "2026-09-11T00:00:00+00:00", "inclusive": True},
        "timezone": "UTC",
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_time_range(time_range, extra_fields=extra_fields)
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))

    with pytest.raises(MetadataBindingResolutionErrorV3):
        _resolve_time_range_v3(request)

    after = request.model_dump(by_alias=True, mode="json")
    assert after == before, "time-range resolver mutated its input"


def test_returned_evidence_ids_are_independent_copy():
    """Mutating returned evidenceIds does not affect the original request."""
    time_range = {
        "mode": "none",
        "timeFieldId": None,
        "start": None,
        "end": None,
        "timezone": None,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_time_range(time_range)
    result = _resolve_time_range_v3(request)
    result.evidence_ids.append("injected")

    assert "injected" not in request.binding_request.query_requirements.time_range.evidence_ids


# ===================================================================
# Section I: mappingCandidate does not grant authority
# ===================================================================


def test_mapping_candidate_change_does_not_affect_time_range():
    """Changing mappingCandidate does not change time-range resolution."""
    time_range = {
        "mode": "asOf",
        "timeFieldId": "syntheticTime",
        "start": None,
        "end": {"kind": "literal", "value": "2026-09-11T00:00:00+00:00", "inclusive": True},
        "timezone": "UTC",
        "evidenceIds": ["ev-query-requirement"],
    }
    # Version A: unresolved
    wire_a = valid_resolve_metadata_request_v3_wire()
    wire_a["bindingRequest"]["queryRequirements"]["timeRange"] = deepcopy(time_range)
    wire_a["bindingRequest"]["queryRequirements"]["fields"] = [
        *wire_a["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "syntheticTime",
            "role": "time",
            "logicalName": "synthetic_time",
            "dataType": "datetime",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    wire_a["projectContext"]["fieldBindingAuthorizations"] = [
        *wire_a["projectContext"]["fieldBindingAuthorizations"],
        {
            "authorizationId": "fba-time",
            "requestId": wire_a["bindingRequest"]["requestId"],
            "fieldId": "syntheticTime",
            "role": "time",
            "columnGrantId": "colgrant-time",
        },
    ]
    wire_a["projectContext"]["columnGrants"] = [
        *wire_a["projectContext"]["columnGrants"],
        {
            "grantId": "colgrant-time",
            "relationGrantId": "relgrant-1",
            "columnName": "synthetic_time",
        },
    ]
    wire_a["metadataSnapshot"]["relations"][0]["columns"] = [
        *wire_a["metadataSnapshot"]["relations"][0]["columns"],
        {"columnName": "synthetic_time", "sqlType": "datetime", "nullable": False},
    ]
    wire_a["bindingRequest"]["mappingCandidate"] = {
        "factCode": wire_a["bindingRequest"]["fact"]["factCode"],
        "mappingStatus": "unresolved",
        "viewName": None,
        "viewField": None,
        "viewActive": None,
        "reviewStatus": "candidate",
        "note": "unresolved",
    }
    # Reclose snapshot/context/approval hashes after adding the time field
    from release_sql_bot.application.canonical import canonical_sha256
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3

    snapshot = GovernedMetadataSnapshotV3.model_validate(wire_a["metadataSnapshot"])
    wire_a["metadataSnapshot"]["contentSha256"] = canonical_content_sha256(snapshot)
    snapshot = GovernedMetadataSnapshotV3.model_validate(wire_a["metadataSnapshot"])
    snapshot_sha = wire_a["metadataSnapshot"]["contentSha256"]

    ctx_wire = wire_a["projectContext"]
    ctx_wire["metadataSnapshotRef"]["sha256"] = snapshot_sha
    ctx = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(ctx)
    ctx = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = wire_a["approvalRecord"]
    appr_wire["snapshotRef"]["sha256"] = snapshot_sha
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(appr)

    wire_a["handoffClosure"]["payload"] = wire_a["bindingRequest"]
    wire_a["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire_a["bindingRequest"])
    )
    request_a = ResolveMetadataRequestV3.model_validate(wire_a)

    # Version B: mapped
    wire_b = deepcopy(wire_a)
    wire_b["bindingRequest"]["mappingCandidate"] = {
        "factCode": wire_b["bindingRequest"]["fact"]["factCode"],
        "mappingStatus": "mapped",
        "viewName": "view_b",
        "viewField": "field_b",
        "viewActive": True,
        "reviewStatus": "candidate",
        "note": "mapped candidate B",
    }
    wire_b["handoffClosure"]["payload"] = wire_b["bindingRequest"]
    wire_b["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire_b["bindingRequest"])
    )
    request_b = ResolveMetadataRequestV3.model_validate(wire_b)

    result_a = _resolve_time_range_v3(request_a)
    result_b = _resolve_time_range_v3(request_b)

    assert result_a.mode == result_b.mode == "asOf"
    assert result_a.time_field_id == result_b.time_field_id == "syntheticTime"
    assert result_a.time_schema_name == result_b.time_schema_name == "dbo"
    assert result_a.time_relation_name == result_b.time_relation_name == "synthetic_table"
    assert result_a.time_column_name == result_b.time_column_name == "synthetic_time"
    assert result_a.evidence_ids == result_b.evidence_ids == ["ev-query-requirement"]


def test_mapping_candidate_cannot_compensate_missing_authorization():
    """A mapped candidate does not compensate for missing field authorization."""
    extra_fields = [
        {
            "fieldId": "orphanTime",
            "role": "time",
            "logicalName": "orphan_time",
            "dataType": "datetime",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        }
    ]
    time_range = {
        "mode": "asOf",
        "timeFieldId": "orphanTime",
        "start": None,
        "end": {"kind": "literal", "value": "2026-09-11T00:00:00+00:00", "inclusive": True},
        "timezone": "UTC",
        "evidenceIds": ["ev-query-requirement"],
    }
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingRequest"]["queryRequirements"]["timeRange"] = time_range
    wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *wire["bindingRequest"]["queryRequirements"]["fields"],
        *extra_fields,
    ]
    wire["bindingRequest"]["mappingCandidate"] = {
        "factCode": wire["bindingRequest"]["fact"]["factCode"],
        "mappingStatus": "mapped",
        "viewName": "view_x",
        "viewField": "field_x",
        "viewActive": True,
        "reviewStatus": "candidate",
        "note": "mapped but field unauthorized",
    }
    from release_sql_bot.application.canonical import canonical_sha256
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3

    wire["handoffClosure"]["payload"] = wire["bindingRequest"]
    wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire["bindingRequest"])
    )
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_time_range_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"
