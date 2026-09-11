"""Unit tests for V3 aggregation resolution (DEV §5.3.4).

Tests the internal helper ``_resolve_aggregation_v3``: verifying that
every field referenced by the aggregation declaration is covered by an
authorized field result, then copying the six declared fields verbatim
into a new ``ResolvedAggregationV3``.

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError as _PydanticValidationError

from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataBindingResolutionErrorV3,
    MetadataColumnResolutionErrorV3,
    MetadataResolutionInputErrorV3,
    _resolve_aggregation_v3,
)
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


def _make_request_with_aggregation(
    aggregation: dict[str, object],
    extra_fields: list[dict[str, object]] | None = None,
) -> ResolveMetadataRequestV3:
    """Build a valid request with the given aggregation declaration.

    Args:
        aggregation: The aggregation declaration to use.
        extra_fields: Additional fields to add to queryRequirements.fields
            WITHOUT field authorization, so the consumer reference closure
            passes but resolution fails with FIELD_AUTHORIZATION_MISSING.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingRequest"]["queryRequirements"]["aggregation"] = aggregation
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


# ===================================================================
# Section A: Four modes — full output wire
# ===================================================================


def test_none_mode_full_wire():
    """none mode copies all six fields verbatim with empty lists."""
    aggregation = {
        "mode": "none",
        "function": None,
        "inputFieldIds": [],
        "groupByFieldIds": [],
        "distinct": None,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    result = _resolve_aggregation_v3(request)

    assert result.mode == "none"
    assert result.function is None
    assert result.input_field_ids == []
    assert result.group_by_field_ids == []
    assert result.distinct is None
    assert result.evidence_ids == ["ev-query-requirement"]


def test_precomputed_mode_full_wire():
    """precomputed mode preserves empty compute fields."""
    aggregation = {
        "mode": "precomputed",
        "function": None,
        "inputFieldIds": [],
        "groupByFieldIds": [],
        "distinct": None,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    result = _resolve_aggregation_v3(request)

    assert result.mode == "precomputed"
    assert result.function is None
    assert result.input_field_ids == []
    assert result.group_by_field_ids == []
    assert result.distinct is None
    assert result.evidence_ids == ["ev-query-requirement"]


def test_exists_mode_full_wire():
    """exists mode preserves empty compute fields."""
    aggregation = {
        "mode": "exists",
        "function": None,
        "inputFieldIds": [],
        "groupByFieldIds": [],
        "distinct": None,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    result = _resolve_aggregation_v3(request)

    assert result.mode == "exists"
    assert result.function is None
    assert result.input_field_ids == []
    assert result.group_by_field_ids == []
    assert result.distinct is None
    assert result.evidence_ids == ["ev-query-requirement"]


def test_compute_mode_full_wire():
    """compute mode with function, inputs, groupBy, distinct."""
    aggregation = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": ["factValue"],
        "groupByFieldIds": [],
        "distinct": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    result = _resolve_aggregation_v3(request)

    assert result.mode == "compute"
    assert result.function == "sum"
    assert result.input_field_ids == ["factValue"]
    assert result.group_by_field_ids == []
    assert result.distinct is True
    assert result.evidence_ids == ["ev-query-requirement"]


# ===================================================================
# Section B: compute functions and distinct preservation
# ===================================================================


@pytest.mark.parametrize("function", ["sum", "count", "countDistinct", "avg", "min", "max"])
def test_compute_all_functions_preserved(function):
    """All six aggregation function enums are copied verbatim."""
    aggregation = {
        "mode": "compute",
        "function": function,
        "inputFieldIds": ["factValue"],
        "groupByFieldIds": [],
        "distinct": False,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    result = _resolve_aggregation_v3(request)

    assert result.mode == "compute"
    assert result.function == function
    assert result.input_field_ids == ["factValue"]
    assert result.distinct is False


@pytest.mark.parametrize("distinct", [True, False])
def test_compute_distinct_preserved(distinct):
    """distinct=True/False are both preserved as-is (not rewritten)."""
    aggregation = {
        "mode": "compute",
        "function": "count",
        "inputFieldIds": ["factValue"],
        "groupByFieldIds": [],
        "distinct": distinct,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    result = _resolve_aggregation_v3(request)

    assert result.distinct is distinct


def test_count_distinct_not_rewritten():
    """countDistinct is preserved; not rewritten to count or distinct=None."""
    aggregation = {
        "mode": "compute",
        "function": "countDistinct",
        "inputFieldIds": ["factValue"],
        "groupByFieldIds": [],
        "distinct": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    result = _resolve_aggregation_v3(request)

    assert result.function == "countDistinct"
    assert result.distinct is True


# ===================================================================
# Section C: Multi-field references — order preservation, no dedup
# ===================================================================


def test_multi_field_non_sorted_order_preserved():
    """Non-sorted inputFieldIds/groupByFieldIds referencing different fields.

    References factValue (->synthetic_value) and syntheticKey (->synthetic_key)
    in a deliberately non-sorted order.  Full lists are asserted in order —
    not as sets.
    """
    input_ids = ["syntheticKey", "factValue", "syntheticKey"]
    group_ids = ["syntheticKey", "factValue", "syntheticKey"]
    # Guard: inputs must not already be sorted, else the test is vacuous.
    assert input_ids != sorted(input_ids), "inputFieldIds already sorted"
    assert group_ids != sorted(group_ids), "groupByFieldIds already sorted"

    aggregation = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": input_ids,
        "groupByFieldIds": group_ids,
        "distinct": False,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    result = _resolve_aggregation_v3(request)

    # Non-sorted order preserved exactly
    assert result.input_field_ids == input_ids
    assert result.group_by_field_ids == group_ids
    # Duplicated reference NOT removed
    assert result.input_field_ids.count("syntheticKey") == 2
    assert result.group_by_field_ids.count("syntheticKey") == 2


# ===================================================================
# Section D: Aggregation evidenceIds source and order
# ===================================================================


def test_aggregation_evidence_ids_from_declaration_not_field():
    """evidenceIds come from the aggregation declaration, not the fields.

    Uses a non-sorted list that differs from the field evidence to prove
    the copy source and ordering.
    """
    evidence_ids = ["ev-query-requirement", "ev-example", "ev-fact-declaration"]
    # Guard: must not already be sorted, else the test loses ordering power.
    assert evidence_ids != sorted(evidence_ids), "evidenceIds already sorted"

    aggregation = {
        "mode": "none",
        "function": None,
        "inputFieldIds": [],
        "groupByFieldIds": [],
        "distinct": None,
        "evidenceIds": evidence_ids,
    }
    request = _make_request_with_aggregation(aggregation)
    result = _resolve_aggregation_v3(request)

    # Non-sorted order preserved exactly (not sorted, not set)
    assert result.evidence_ids == evidence_ids


# ===================================================================
# Section E: Referenced field authorization missing / column missing
# ===================================================================


def test_referenced_field_not_authorized():
    """Referencing a field with no authorization raises FIELD_AUTHORIZATION_MISSING."""
    extra_fields = [
        {
            "fieldId": "unauthorizedField",
            "role": "value",
            "logicalName": "unauthorized",
            "dataType": "string",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        }
    ]
    aggregation = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": ["unauthorizedField"],
        "groupByFieldIds": [],
        "distinct": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation, extra_fields=extra_fields)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_aggregation_v3(request)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


def test_group_by_field_not_authorized():
    """Unauthorized groupBy field raises FIELD_AUTHORIZATION_MISSING."""
    extra_fields = [
        {
            "fieldId": "unauthorizedField",
            "role": "groupBy",
            "logicalName": "unauthorized_group",
            "dataType": "string",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        }
    ]
    aggregation = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": ["factValue"],
        "groupByFieldIds": ["unauthorizedField"],
        "distinct": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation, extra_fields=extra_fields)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_aggregation_v3(request)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


def _rebuild_with_snapshot_columns(
    request: ResolveMetadataRequestV3,
    column_names: list[str],
) -> ResolveMetadataRequestV3:
    """Replace snapshot columns and reclose snapshot/context/approval hashes.

    Keeps the relation and relation grant intact; only the target column
    list changes.  All dependent canonical hashes are recomputed so a
    failure originates from the column-level check, not a hash mismatch.
    """
    # 1. Rebuild snapshot with the given columns
    snapshot_wire = request.metadata_snapshot.model_dump(by_alias=True, mode="json")
    base_columns = snapshot_wire["relations"][0]["columns"]
    by_name = {c["columnName"]: c for c in base_columns}
    snapshot_wire["relations"][0]["columns"] = [by_name[name] for name in column_names]
    snapshot = GovernedMetadataSnapshotV3.model_validate(snapshot_wire)
    snapshot_wire["contentSha256"] = canonical_content_sha256(snapshot)
    snapshot = GovernedMetadataSnapshotV3.model_validate(snapshot_wire)
    snapshot_sha = snapshot_wire["contentSha256"]

    # 2. Rebuild context pointing at the new snapshot hash
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["metadataSnapshotRef"]["sha256"] = snapshot_sha
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_sha = context_wire["contentSha256"]

    # 3. Rebuild approval pointing at the new context + snapshot hashes
    approval_wire = request.approval_record.model_dump(by_alias=True, mode="json")
    approval_wire["contextRef"]["sha256"] = context_sha
    approval_wire["snapshotRef"]["sha256"] = snapshot_sha
    approval = ApprovalRecordV3.model_validate(approval_wire)
    approval_wire["contentSha256"] = canonical_content_sha256(approval)
    approval = ApprovalRecordV3.model_validate(approval_wire)

    # 4. Rebuild the full request
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"] = snapshot.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"] = context.model_dump(by_alias=True, mode="json")
    req_wire["approvalRecord"] = approval.model_dump(by_alias=True, mode="json")

    return ResolveMetadataRequestV3.model_validate(req_wire)


def test_snapshot_column_missing_raises_column_not_in_snapshot():
    """Removing only the target snapshot column yields COLUMN_NOT_IN_SNAPSHOT.

    Field authorization, column grant, relation grant, and the snapshot
    relation all remain present.  Only the target column is removed, and all
    dependent hashes are reclosed so the failure originates from the
    column-level check — not a hash mismatch or COLUMN_GRANT_NOT_FOUND.
    """
    aggregation = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": ["factValue"],
        "groupByFieldIds": [],
        "distinct": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    # Baseline succeeds
    base = _make_request_with_aggregation(aggregation)
    base_result = _resolve_aggregation_v3(base)
    assert base_result.input_field_ids == ["factValue"]

    # Remove synthetic_value from snapshot columns (keep synthetic_key)
    tampered = _rebuild_with_snapshot_columns(base, column_names=["synthetic_key"])

    # Input gate passes (hashes are consistent)
    from release_sql_bot.application.metadata_resolution_v3 import (
        _validate_resolution_input_v3,
    )

    _validate_resolution_input_v3(tampered)

    # Snapshot the actual tampered object before resolution
    tampered_before = deepcopy(tampered.model_dump(by_alias=True, mode="json"))

    # The failure must be COLUMN_NOT_IN_SNAPSHOT
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_aggregation_v3(tampered)
    assert exc_info.value.code == "COLUMN_NOT_IN_SNAPSHOT"

    # The actual tampered input must not be mutated by the failure
    tampered_after = tampered.model_dump(by_alias=True, mode="json")
    assert tampered_after == tampered_before, (
        "aggregation resolver mutated its input on column-missing failure"
    )

    # Restore the column and reclose → success
    restored = _rebuild_with_snapshot_columns(
        base, column_names=["synthetic_value", "synthetic_key"]
    )
    restored_result = _resolve_aggregation_v3(restored)
    assert restored_result.input_field_ids == ["factValue"]


# ===================================================================
# Section F: Gates still run for none/precomputed/exists
# ===================================================================


@pytest.mark.parametrize("mode", ["none", "precomputed", "exists"])
def test_input_gate_failure_blocks_all_no_ref_modes(mode):
    """Input-gate failures propagate even for modes with no field refs.

    Uses _make_request_with_aggregation so the request's aggregation.mode
    genuinely equals the parameter, then tampers with projectRef.
    """
    aggregation = {
        "mode": mode,
        "function": None,
        "inputFieldIds": [],
        "groupByFieldIds": [],
        "distinct": None,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    # Guard: the constructed request must reflect the parameterized mode
    assert request.binding_request.query_requirements.aggregation.mode == mode

    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_aggregation_v3(tampered)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"


@pytest.mark.parametrize("mode", ["none", "precomputed", "exists"])
def test_entity_key_closure_failure_blocks_all_no_ref_modes(mode):
    """Entity-key closure failure propagates even for modes with no field refs.

    The aggregation mode is varied across none/precomputed/exists — the
    failure must come from the field/entity-key resolution step, with
    correct hash reclosure.
    """
    aggregation = {
        "mode": mode,
        "function": None,
        "inputFieldIds": [],
        "groupByFieldIds": [],
        "distinct": None,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    # Guard: the constructed request must reflect the parameterized mode
    assert request.binding_request.query_requirements.aggregation.mode == mode

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
        _resolve_aggregation_v3(tampered)
    assert exc_info.value.code == "ENTITY_KEY_AUTHORIZATION_MISSING"


# ===================================================================
# Section G: Structural violations caught by existing gates
# ===================================================================


def test_dangling_field_reference_rejected_by_consumer():
    """A field reference not in the request fields is rejected by the consumer.

    The request consumer's reference closure rejects unknown field IDs
    before resolution even starts — no resolution-specific code path.
    """
    aggregation = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": ["nonexistentField"],
        "groupByFieldIds": [],
        "distinct": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    # The consumer-level reference closure rejects this at construction time
    with pytest.raises(_PydanticValidationError) as exc_info:
        _make_request_with_aggregation(aggregation)
    assert "unknown fields" in str(exc_info.value)


def test_dangling_aggregation_evidence_rejected_by_consumer():
    """An aggregation evidenceIds reference to non-existent evidence is rejected.

    The consumer's evidence-reference closure validates this at construction.
    """
    aggregation = {
        "mode": "none",
        "function": None,
        "inputFieldIds": [],
        "groupByFieldIds": [],
        "distinct": None,
        "evidenceIds": ["ev-does-not-exist"],
    }
    with pytest.raises(_PydanticValidationError) as exc_info:
        _make_request_with_aggregation(aggregation)
    assert "unknown evidence" in str(exc_info.value)


def test_structure_rejection_maps_to_input_error():
    """Illegal nested content is mapped to the structural input error.

    Build a valid request, then use model_copy to bypass the constructor
    and inject structurally-invalid content, confirming the input gate maps
    it to METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID.
    """
    request = _make_valid_request()
    # Bypass the constructor to inject a non-valid nested structure
    tampered = request.model_copy(update={"project_ref": "not-a-project-ref"})

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_aggregation_v3(tampered)
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


# ===================================================================
# Section H: Immutability
# ===================================================================


def test_input_not_mutated_on_success():
    """The actual input is not mutated by a successful aggregation resolution."""
    request = _make_valid_request()
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    _resolve_aggregation_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_input_not_mutated_on_failure():
    """The actual input is not mutated by a failed aggregation resolution."""
    extra_fields = [
        {
            "fieldId": "unauthorizedField",
            "role": "value",
            "logicalName": "unauthorized",
            "dataType": "string",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        }
    ]
    aggregation = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": ["unauthorizedField"],
        "groupByFieldIds": [],
        "distinct": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation, extra_fields=extra_fields)
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))

    with pytest.raises(MetadataBindingResolutionErrorV3):
        _resolve_aggregation_v3(request)

    after = request.model_dump(by_alias=True, mode="json")
    assert after == before, "aggregation resolver mutated its input"


def test_returned_lists_are_independent_copies():
    """Mutating returned lists does not affect the original request."""
    aggregation = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": ["factValue"],
        "groupByFieldIds": ["factValue"],
        "distinct": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_aggregation(aggregation)
    result = _resolve_aggregation_v3(request)

    result.input_field_ids.append("injected")
    result.group_by_field_ids.append("injected")
    result.evidence_ids.append("injected")

    orig = request.binding_request.query_requirements.aggregation
    assert "injected" not in orig.input_field_ids
    assert "injected" not in orig.group_by_field_ids
    assert "injected" not in orig.evidence_ids


# ===================================================================
# Section I: mappingCandidate does not grant authority
# ===================================================================


def test_mapping_candidate_change_does_not_affect_aggregation():
    """Changing mappingCandidate does not change aggregation resolution."""
    aggregation = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": ["factValue"],
        "groupByFieldIds": [],
        "distinct": True,
        "evidenceIds": ["ev-query-requirement"],
    }

    # Version A: unresolved
    wire_a = valid_resolve_metadata_request_v3_wire()
    wire_a["bindingRequest"]["queryRequirements"]["aggregation"] = dict(aggregation)
    wire_a["bindingRequest"]["mappingCandidate"] = {
        "factCode": wire_a["bindingRequest"]["fact"]["factCode"],
        "mappingStatus": "unresolved",
        "viewName": None,
        "viewField": None,
        "viewActive": None,
        "reviewStatus": "candidate",
        "note": "unresolved",
    }
    from release_sql_bot.application.canonical import canonical_sha256
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3

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

    result_a = _resolve_aggregation_v3(request_a)
    result_b = _resolve_aggregation_v3(request_b)

    assert result_a.mode == result_b.mode == "compute"
    assert result_a.function == result_b.function == "sum"
    assert result_a.input_field_ids == result_b.input_field_ids == ["factValue"]
    assert result_a.group_by_field_ids == result_b.group_by_field_ids == []
    assert result_a.distinct == result_b.distinct is True
    assert result_a.evidence_ids == result_b.evidence_ids == ["ev-query-requirement"]


def test_mapping_candidate_cannot_compensate_missing_authorization():
    """A mapped candidate does not compensate for missing field authorization."""
    extra_fields = [
        {
            "fieldId": "orphanField",
            "role": "value",
            "logicalName": "orphan",
            "dataType": "string",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        }
    ]
    aggregation = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": ["orphanField"],
        "groupByFieldIds": [],
        "distinct": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingRequest"]["queryRequirements"]["aggregation"] = aggregation
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
        _resolve_aggregation_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"
