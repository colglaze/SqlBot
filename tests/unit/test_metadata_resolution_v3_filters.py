"""Unit tests for V3 filter resolution (DEV §5.3.3).

Tests the internal helper ``_resolve_filters_v3``: mapping each filter
item to its authorized physical column reference via the already-resolved
field authorization chain.

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path

import pytest

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataBindingResolutionErrorV3,
    MetadataColumnResolutionErrorV3,
    MetadataFilterResolutionErrorV3,
    MetadataResolutionInputErrorV3,
    _resolve_filters_v3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordV3,
    GovernedMetadataSnapshotV3,
    ProjectBindingContextV3,
    ResolveMetadataRequestV3,
)
from tests.v3_metadata_support import valid_resolve_metadata_request_v3_wire

# Synthetic marker for sanitization tests — must be valid evidenceId format
_SYNTHETIC_PRIVATE_MARKER = "syntheticPrivateMarker"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_request() -> ResolveMetadataRequestV3:
    """Build a valid, internally-consistent request from the shared fixture."""
    wire = valid_resolve_metadata_request_v3_wire()
    return ResolveMetadataRequestV3.model_validate(wire)


def _make_request_with_filters(
    filter_items: list[dict[str, object]],
) -> ResolveMetadataRequestV3:
    """Build a valid request with the given filter items.

    Updates the binding request's filters.items and closes the handoff
    payload hash so the input gate passes.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingRequest"]["queryRequirements"]["filters"]["items"] = filter_items
    wire["handoffClosure"]["payload"] = wire["bindingRequest"]
    wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire["bindingRequest"])
    )
    return ResolveMetadataRequestV3.model_validate(wire)


def _sample_filter_item(
    *,
    filter_id: str = "filter-1",
    field_id: str = "factValue",
    evidence_ids: list[str] | None = None,
) -> dict[str, object]:
    """Build a minimal valid filter item referencing the given field."""
    return {
        "filterId": filter_id,
        "fieldId": field_id,
        "operator": "eq",
        "value": {"kind": "literal", "literal": 100},
        "nullPolicy": "fail",
        "required": True,
        "evidenceIds": evidence_ids if evidence_ids is not None else ["ev-query-requirement"],
    }


def _rebuild_with_snapshot_columns(
    request: ResolveMetadataRequestV3,
    column_names: list[str],
) -> ResolveMetadataRequestV3:
    """Replace snapshot columns and reclose snapshot/context/approval hashes.

    Keeps the relation and relation grant intact; only the target column
    list changes. All dependent canonical hashes are recomputed so the
    failure originates from the column-level check, not a hash mismatch.
    """
    # 1. Rebuild snapshot with the given columns
    snapshot_wire = request.metadata_snapshot.model_dump(by_alias=True, mode="json")
    base_columns = snapshot_wire["relations"][0]["columns"]
    # Map existing columns by name to preserve sqlType/nullable
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


# ===================================================================
# Section A: Normal resolution — order and field correspondence
# ===================================================================


def test_single_filter_item_resolves_full_wire():
    """A single filter item resolves to its field's full physical reference."""
    request = _make_request_with_filters([_sample_filter_item()])
    results = _resolve_filters_v3(request)

    assert len(results) == 1
    assert results[0].filter_id == "filter-1"
    assert results[0].field_id == "factValue"
    assert results[0].schema_name == "dbo"
    assert results[0].relation_name == "synthetic_table"
    assert results[0].column_name == "synthetic_value"
    assert results[0].evidence_ids == ["ev-query-requirement"]


def test_multiple_filter_items_preserve_input_order_with_two_fields():
    """Non-sorted input order is preserved; two different fields map correctly.

    Uses filter-c, filter-a, filter-b (non-sorted) referencing two distinct
    authorized fields with different physical columns.  The three results are
    converted to camelCase JSON wire and compared against hand-written
    expected dictionaries — the expectations are NOT derived from the
    function output or any shared mapping algorithm.
    """
    items = [
        _sample_filter_item(filter_id="filter-c", field_id="syntheticKey"),
        _sample_filter_item(filter_id="filter-a", field_id="factValue"),
        _sample_filter_item(filter_id="filter-b", field_id="syntheticKey"),
    ]
    request = _make_request_with_filters(items)
    results = _resolve_filters_v3(request)

    # Convert to camelCase JSON wire for exact per-item comparison
    wires = [r.model_dump(by_alias=True, mode="json") for r in results]

    # Hand-written expectations: each filter maps to its authorized field's
    # physical spelling.  filter-c/filter-b → syntheticKey → synthetic_key;
    # filter-a → factValue → synthetic_value.  None of these values are
    # derived from the function under test.
    expected = [
        {
            "filterId": "filter-c",
            "fieldId": "syntheticKey",
            "schemaName": "dbo",
            "relationName": "synthetic_table",
            "columnName": "synthetic_key",
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "filterId": "filter-a",
            "fieldId": "factValue",
            "schemaName": "dbo",
            "relationName": "synthetic_table",
            "columnName": "synthetic_value",
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "filterId": "filter-b",
            "fieldId": "syntheticKey",
            "schemaName": "dbo",
            "relationName": "synthetic_table",
            "columnName": "synthetic_key",
            "evidenceIds": ["ev-query-requirement"],
        },
    ]

    assert len(wires) == 3
    # Order + full wire compared together, item by item
    assert wires == expected

    # Self-verification: sorting the result by filterId would NOT match input.
    # This proves the test genuinely exercises order preservation.
    sorted_ids = [r.filter_id for r in sorted(results, key=lambda r: r.filter_id)]
    assert sorted_ids != [r.filter_id for r in results]


def test_empty_items_returns_empty_tuple_after_gates():
    """Empty filters.items still runs the gates, then returns an empty tuple."""
    request = _make_request_with_filters([])
    results = _resolve_filters_v3(request)
    assert results == ()


def test_same_field_referenced_by_multiple_filters_not_merged():
    """Multiple filters referencing the same field produce separate outputs."""
    items = [
        _sample_filter_item(filter_id="f1"),
        _sample_filter_item(filter_id="f2"),
    ]
    request = _make_request_with_filters(items)
    results = _resolve_filters_v3(request)

    assert len(results) == 2
    assert results[0].filter_id == "f1"
    assert results[1].filter_id == "f2"
    # Both map to the same physical column
    assert results[0].column_name == results[1].column_name == "synthetic_value"


def test_non_filter_role_field_resolves_by_actual_role():
    """A filter can reference a field whose role is not 'filter'.

    The factValue field has role='value'; the filter resolution uses the
    field's actual authorization role, not a forced role='filter'.
    """
    request = _make_request_with_filters([_sample_filter_item()])
    results = _resolve_filters_v3(request)

    assert len(results) == 1
    # factValue has role=value and maps to synthetic_value
    assert results[0].field_id == "factValue"
    assert results[0].column_name == "synthetic_value"


def test_output_evidence_ids_copy_from_filter_item_not_field():
    """ResolvedFilterV3.evidenceIds come from the filter item, not the field.

    The factValue field declares evidenceIds=['ev-fact-declaration'], while
    the filter item declares ['ev-query-requirement']. The output must match
    the filter item, proving the copy source is correct.
    """
    request = _make_request_with_filters([_sample_filter_item()])
    results = _resolve_filters_v3(request)

    assert results[0].evidence_ids == ["ev-query-requirement"]
    # The field's own evidence is different
    field_evidence = request.binding_request.query_requirements.fields[0].evidence_ids
    assert "ev-fact-declaration" in field_evidence


def test_item_evidence_ids_differ_from_field_and_filter_set():
    """Filter item evidenceIds can differ from both field and FilterSet evidenceIds."""
    # FilterSet declares ['ev-query-requirement']; field declares ['ev-fact-declaration']
    item = _sample_filter_item(evidence_ids=["ev-example"])
    request = _make_request_with_filters([item])
    results = _resolve_filters_v3(request)

    assert results[0].evidence_ids == ["ev-example"]


def test_output_is_new_objects():
    """Mutating output evidenceIds does not pollute the input request."""
    request = _make_request_with_filters([_sample_filter_item()])
    results = _resolve_filters_v3(request)

    results[0].evidence_ids.append("injected")

    items_evidence = request.binding_request.query_requirements.filters.items[0].evidence_ids
    assert "injected" not in items_evidence


# ===================================================================
# Section B: No-dedup boundary — same filterId, evidence order
# ===================================================================


def test_same_filter_id_different_content_preserved():
    """Two items with the same filterId but different content are preserved.

    The current contract imposes no filterId uniqueness constraint on
    filter items, so both must be retained as separate outputs.
    """
    items = [
        _sample_filter_item(filter_id="dup", field_id="factValue"),
        _sample_filter_item(filter_id="dup", field_id="syntheticKey"),
    ]
    request = _make_request_with_filters(items)
    results = _resolve_filters_v3(request)

    assert len(results) == 2
    # Both retained, in input order
    assert results[0].filter_id == "dup"
    assert results[0].field_id == "factValue"
    assert results[0].column_name == "synthetic_value"
    assert results[1].filter_id == "dup"
    assert results[1].field_id == "syntheticKey"
    assert results[1].column_name == "synthetic_key"


def test_multiple_evidence_ids_preserved_in_order():
    """Multiple evidenceIds on a filter item are copied in original order.

    Uses a deliberately non-sorted list so the test cannot pass by
    accident if the implementation were to sort or deduplicate.  The
    expected order equals the input order, not the sorted order.
    """
    evidence_ids = ["ev-query-requirement", "ev-example", "ev-fact-declaration"]
    # Guard: the fixture itself must not already be sorted, else this test
    # would silently lose its ability to detect ordering bugs.
    assert evidence_ids != sorted(evidence_ids), (
        "test fixture evidenceIds are already sorted — not a valid order test"
    )
    item = _sample_filter_item(evidence_ids=evidence_ids)
    request = _make_request_with_filters([item])
    results = _resolve_filters_v3(request)

    # Full-list equality in original order (not set, not sorted)
    assert results[0].evidence_ids == evidence_ids

    # --- Meta-check: the test must actually be able to fail on ordering bugs.
    # Temporarily wrap the real helper so it sorts each output's evidenceIds.
    # With a sorted output, the order-sensitive assertion above must FAIL,
    # proving the test is meaningful.  After the check we restore the real
    # function (nothing is left patched).
    import release_sql_bot.application.metadata_resolution_v3 as _mod

    _real_fn = _mod._resolve_filters_v3

    def _sorting_wrapper(request):
        result = _real_fn(request)
        return tuple(
            r.model_validate(
                {
                    **r.model_dump(by_alias=True, mode="json"),
                    "evidenceIds": sorted(r.evidence_ids),
                }
            )
            for r in result
        )

    _mod._resolve_filters_v3 = _sorting_wrapper
    try:
        sorted_results = _mod._resolve_filters_v3(request)
        assert sorted_results[0].evidence_ids != evidence_ids, (
            "order test is vacuous: sorted output still matches input"
        )
    finally:
        _mod._resolve_filters_v3 = _real_fn

    # After restoring the real function, the original assertion still holds.
    restored_results = _resolve_filters_v3(request)
    assert restored_results[0].evidence_ids == evidence_ids


def test_duplicate_evidence_ids_not_deduplicated():
    """Duplicate evidenceIds within a filter item are preserved as-is.

    The current contract does not enforce uniqueness within a single filter
    item's evidenceIds, so the helper must not deduplicate them.
    """
    item = _sample_filter_item(evidence_ids=["ev-example", "ev-example"])
    request = _make_request_with_filters([item])
    results = _resolve_filters_v3(request)

    assert results[0].evidence_ids == ["ev-example", "ev-example"]


# ===================================================================
# Section C: Operator / value pass-through (no interpretation)
# ===================================================================


def test_literal_filter_maps_physical_only():
    """A literal filter resolves to its physical column; value is not interpreted."""
    item = _sample_filter_item(filter_id="lit-filter")
    request = _make_request_with_filters([item])
    results = _resolve_filters_v3(request)

    assert len(results) == 1
    assert results[0].filter_id == "lit-filter"
    assert results[0].column_name == "synthetic_value"


def test_parameter_filter_maps_physical_only():
    """A parameter filter resolves to its physical column; parameter is not interpreted."""
    item = {
        "filterId": "param-filter",
        "fieldId": "factValue",
        "operator": "eq",
        "value": {"kind": "parameter", "parameterName": "syntheticKey"},
        "nullPolicy": "fail",
        "required": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_filters([item])
    results = _resolve_filters_v3(request)

    assert len(results) == 1
    assert results[0].filter_id == "param-filter"
    assert results[0].column_name == "synthetic_value"


def test_unary_null_filter_maps_physical_only():
    """A unary null filter (no value) resolves to its physical column."""
    item = {
        "filterId": "null-filter",
        "fieldId": "factValue",
        "operator": "is_null",
        "value": None,
        "nullPolicy": "pass",
        "required": True,
        "evidenceIds": ["ev-query-requirement"],
    }
    request = _make_request_with_filters([item])
    results = _resolve_filters_v3(request)

    assert len(results) == 1
    assert results[0].filter_id == "null-filter"
    assert results[0].column_name == "synthetic_value"


# ===================================================================
# Section D: No partial output on later filter failure
# ===================================================================


def test_later_filter_failure_no_partial_output():
    """When a later filter's evidence is dangling, no partial results are returned.

    Two filters reference authorized fields. The first has valid evidence,
    the second has dangling evidence. The function must raise (not return
    the first result). Fixing only the second evidence makes both succeed.
    """
    valid_item = _sample_filter_item(filter_id="ok-filter", field_id="factValue")
    dangling_item = _sample_filter_item(
        filter_id="bad-filter",
        field_id="syntheticKey",
        evidence_ids=["ev-does-not-exist"],
    )

    # First filter alone succeeds
    request_first_only = _make_request_with_filters([valid_item])
    results = _resolve_filters_v3(request_first_only)
    assert len(results) == 1
    assert results[0].filter_id == "ok-filter"

    # Both together: second fails → exception, no partial output
    request_both = _make_request_with_filters([valid_item, dangling_item])
    before = deepcopy(request_both.model_dump(by_alias=True, mode="json"))
    with pytest.raises(MetadataFilterResolutionErrorV3) as exc_info:
        _resolve_filters_v3(request_both)
    assert exc_info.value.code == "FILTER_EVIDENCE_REFERENCE_INVALID"
    after = request_both.model_dump(by_alias=True, mode="json")
    assert after == before, "filter resolver mutated its input on failure"

    # Fix only the second item's evidence → both succeed
    fixed_item = _sample_filter_item(
        filter_id="bad-filter",
        field_id="syntheticKey",
        evidence_ids=["ev-query-requirement"],
    )
    request_fixed = _make_request_with_filters([valid_item, fixed_item])
    results = _resolve_filters_v3(request_fixed)
    assert len(results) == 2
    assert results[0].filter_id == "ok-filter"
    assert results[0].column_name == "synthetic_value"
    assert results[1].filter_id == "bad-filter"
    assert results[1].column_name == "synthetic_key"


# ===================================================================
# Section E: Snapshot column missing → COLUMN_NOT_IN_SNAPSHOT
# ===================================================================


def test_snapshot_column_missing_raises_column_not_in_snapshot():
    """Removing only the target snapshot column yields COLUMN_NOT_IN_SNAPSHOT.

    Field authorization, column grant, relation grant, and the snapshot
    relation all remain present. Only the target column is removed, and all
    dependent hashes are reclosed so the failure originates from the
    column-level check — not a hash mismatch or COLUMN_GRANT_NOT_FOUND.
    """
    # Start with a valid request containing a filter referencing factValue
    base = _make_request_with_filters([_sample_filter_item()])
    before = deepcopy(base.model_dump(by_alias=True, mode="json"))

    # Remove synthetic_value from snapshot columns (keep synthetic_key)
    tampered = _rebuild_with_snapshot_columns(base, column_names=["synthetic_key"])

    # Verify the input gate passes (hashes are consistent)
    from release_sql_bot.application.metadata_resolution_v3 import _validate_resolution_input_v3

    _validate_resolution_input_v3(tampered)

    # Snapshot the actual request object that will be passed to the resolver
    tampered_before = deepcopy(tampered.model_dump(by_alias=True, mode="json"))

    # The failure must be COLUMN_NOT_IN_SNAPSHOT, not a hash error
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_filters_v3(tampered)
    assert exc_info.value.code == "COLUMN_NOT_IN_SNAPSHOT"

    # The actual input object (tampered) must not be mutated by the failure
    tampered_after = tampered.model_dump(by_alias=True, mode="json")
    assert tampered_after == tampered_before, "filter resolver mutated its input on failure"
    # base must also remain unchanged (was only used to build tampered)
    assert base.model_dump(by_alias=True, mode="json") == before, "base request was mutated"

    # Restore the column and reclose → success
    restored = _rebuild_with_snapshot_columns(
        base, column_names=["synthetic_value", "synthetic_key"]
    )
    results = _resolve_filters_v3(restored)
    assert len(results) == 1
    assert results[0].column_name == "synthetic_value"


# ===================================================================
# Section F: Input gate priority over filter evidence check
# ===================================================================


def test_field_auth_missing_before_filter_evidence_check():
    """Field authorization failure takes priority over filter evidence check.

    A request with BOTH a field-auth-missing condition and a dangling
    filter evidence reference must first raise FIELD_AUTHORIZATION_MISSING.
    Fixing only the authorization (keeping the dangling evidence) must then
    raise FILTER_EVIDENCE_REFERENCE_INVALID.
    """
    # Add a new field with NO authorization
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "filterField",
            "role": "filter",
            "logicalName": "filter_field",
            "dataType": "string",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    # Filter 1 references the unauthorized field (valid evidence)
    # Filter 2 references an authorized field but with dangling evidence
    wire["bindingRequest"]["queryRequirements"]["filters"]["items"] = [
        {
            "filterId": "auth-missing-filter",
            "fieldId": "filterField",
            "operator": "eq",
            "value": {"kind": "literal", "literal": 1},
            "nullPolicy": "fail",
            "required": True,
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "filterId": "dangling-filter",
            "fieldId": "factValue",
            "operator": "eq",
            "value": {"kind": "literal", "literal": 1},
            "nullPolicy": "fail",
            "required": True,
            "evidenceIds": ["ev-does-not-exist"],
        },
    ]
    wire["handoffClosure"]["payload"] = wire["bindingRequest"]
    wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire["bindingRequest"])
    )
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    # First: field auth missing takes priority
    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_filters_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"

    # Now fix only the field authorization (keep dangling evidence)
    context_wire = tampered.project_context.model_dump(by_alias=True, mode="json")
    context_wire["fieldBindingAuthorizations"] = [
        *context_wire["fieldBindingAuthorizations"],
        {
            "authorizationId": "fba-filter",
            "requestId": tampered.binding_request.request_id,
            "fieldId": "filterField",
            "role": "filter",
            "columnGrantId": "colgrant-key",  # points to existing snapshot column
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    req_wire = tampered.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"] = context_wire
    approval_wire = tampered.approval_record.model_dump(by_alias=True, mode="json")
    approval_wire["contextRef"] = {
        "contextId": context.context_id,
        "contextVersion": context.context_version,
        "sha256": context_wire["contentSha256"],
    }
    approval = ApprovalRecordV3.model_validate(approval_wire)
    approval_wire["contentSha256"] = canonical_content_sha256(approval)
    approval = ApprovalRecordV3.model_validate(approval_wire)
    req_wire["approvalRecord"] = approval.model_dump(by_alias=True, mode="json")

    fixed_auth = ResolveMetadataRequestV3.model_validate(req_wire)

    # Now the field resolves, but the dangling evidence is caught
    with pytest.raises(MetadataFilterResolutionErrorV3) as exc_info:
        _resolve_filters_v3(fixed_auth)
    assert exc_info.value.code == "FILTER_EVIDENCE_REFERENCE_INVALID"


# ===================================================================
# Section G: Propagation of existing errors
# ===================================================================


def test_input_gate_failure_propagates():
    """Input-gate failures propagate before any filter check."""
    request = _make_valid_request()
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_filters_v3(tampered)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"


def test_field_authorization_missing_propagates():
    """A filter referencing an unauthorized field propagates the field error."""
    # Add a new field with no authorization, then a filter referencing it
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "unauthorizedField",
            "role": "filter",
            "logicalName": "unauthorized",
            "dataType": "string",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    # Add a filter referencing the unauthorized field
    wire["bindingRequest"]["queryRequirements"]["filters"]["items"] = [
        {
            "filterId": "bad-filter",
            "fieldId": "unauthorizedField",
            "operator": "eq",
            "value": {"kind": "literal", "literal": 1},
            "nullPolicy": "fail",
            "required": True,
            "evidenceIds": ["ev-query-requirement"],
        }
    ]
    wire["handoffClosure"]["payload"] = wire["bindingRequest"]
    wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire["bindingRequest"])
    )
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_filters_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


def test_entity_key_closure_failure_propagates():
    """Entity-key closure failures propagate before filter mapping."""
    request = _make_valid_request()
    # Remove entity-key authorization to trigger entity-key failure
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["entityKeyAuthorizations"] = []
    context_wire["contentSha256"] = canonical_content_sha256(
        ProjectBindingContextV3.model_validate(context_wire)
    )
    context = ProjectBindingContextV3.model_validate(context_wire)

    # Rebuild request with the modified context
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"] = context_wire
    # Update approval record's contextRef hash
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
        _resolve_filters_v3(tampered)
    assert exc_info.value.code == "ENTITY_KEY_AUTHORIZATION_MISSING"


def test_column_grant_resolution_failure_propagates():
    """Column-grant resolution failures propagate from field resolution."""
    request = _make_valid_request()
    # Point a field authorization to a non-existent column grant
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    for auth in context_wire["fieldBindingAuthorizations"]:
        if auth["fieldId"] == "factValue":
            auth["columnGrantId"] = "nonexistent-grant"
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

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_filters_v3(tampered)
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


def test_no_partial_results_on_field_failure():
    """When a later field fails, no partial filter results are returned."""
    wire = valid_resolve_metadata_request_v3_wire()
    # Add a field without authorization
    wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "noauth",
            "role": "filter",
            "logicalName": "no_auth",
            "dataType": "string",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    # Filter referencing the unauthorized field
    wire["bindingRequest"]["queryRequirements"]["filters"]["items"] = [
        {
            "filterId": "partial-filter",
            "fieldId": "noauth",
            "operator": "eq",
            "value": {"kind": "literal", "literal": 1},
            "nullPolicy": "fail",
            "required": True,
            "evidenceIds": ["ev-query-requirement"],
        }
    ]
    wire["handoffClosure"]["payload"] = wire["bindingRequest"]
    wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire["bindingRequest"])
    )
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataBindingResolutionErrorV3):
        _resolve_filters_v3(tampered)


# ===================================================================
# Section H: Filter evidence reference validation
# ===================================================================


def test_dangling_filter_evidence_reference_raises_new_code():
    """A filter item referencing a non-existent evidence ID is rejected."""
    item = _sample_filter_item(evidence_ids=["ev-does-not-exist"])
    request = _make_request_with_filters([item])

    with pytest.raises(MetadataFilterResolutionErrorV3) as exc_info:
        _resolve_filters_v3(request)
    assert exc_info.value.code == "FILTER_EVIDENCE_REFERENCE_INVALID"


def test_dangling_evidence_error_does_not_leak_marker(caplog: pytest.LogCaptureFixture) -> None:
    """The dangling-evidence error and logs contain no sensitive marker."""
    caplog.set_level(logging.DEBUG)
    item = _sample_filter_item(evidence_ids=[_SYNTHETIC_PRIVATE_MARKER])
    request = _make_request_with_filters([item])

    with pytest.raises(MetadataFilterResolutionErrorV3) as exc_info:
        _resolve_filters_v3(request)
    assert exc_info.value.code == "FILTER_EVIDENCE_REFERENCE_INVALID"
    assert _SYNTHETIC_PRIVATE_MARKER not in str(exc_info.value)
    assert _SYNTHETIC_PRIVATE_MARKER not in repr(exc_info.value)
    for record in caplog.records:
        assert _SYNTHETIC_PRIVATE_MARKER not in record.message


# ===================================================================
# Section I: mappingCandidate does not grant authority
# ===================================================================


def test_mapping_candidate_change_does_not_affect_resolution():
    """Changing mappingCandidate does not change filter resolution results."""
    item = _sample_filter_item()

    # Version A: unresolved mapping candidate
    wire_a = valid_resolve_metadata_request_v3_wire()
    wire_a["bindingRequest"]["mappingCandidate"] = {
        "factCode": wire_a["bindingRequest"]["fact"]["factCode"],
        "mappingStatus": "unresolved",
        "viewName": None,
        "viewField": None,
        "viewActive": None,
        "reviewStatus": "candidate",
        "note": "unresolved",
    }
    wire_a["bindingRequest"]["queryRequirements"]["filters"]["items"] = [item]
    wire_a["handoffClosure"]["payload"] = wire_a["bindingRequest"]
    wire_a["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire_a["bindingRequest"])
    )
    request_a = ResolveMetadataRequestV3.model_validate(wire_a)

    # Version B: mapped candidate with physical source hints
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

    results_a = _resolve_filters_v3(request_a)
    results_b = _resolve_filters_v3(request_b)

    assert len(results_a) == len(results_b) == 1
    assert results_a[0].filter_id == results_b[0].filter_id
    assert results_a[0].field_id == results_b[0].field_id
    assert results_a[0].schema_name == results_b[0].schema_name
    assert results_a[0].relation_name == results_b[0].relation_name
    assert results_a[0].column_name == results_b[0].column_name


def test_mapping_candidate_cannot_compensate_missing_authorization():
    """A mapped candidate does not compensate for missing field authorization."""
    wire = valid_resolve_metadata_request_v3_wire()
    # Add a field with no authorization
    wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "orphanField",
            "role": "filter",
            "logicalName": "orphan",
            "dataType": "string",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    # Give it a mapped candidate-looking setup (mappingCandidate is per-request,
    # not per-field, but this confirms it doesn't help)
    wire["bindingRequest"]["mappingCandidate"] = {
        "factCode": wire["bindingRequest"]["fact"]["factCode"],
        "mappingStatus": "mapped",
        "viewName": "view_x",
        "viewField": "field_x",
        "viewActive": True,
        "reviewStatus": "candidate",
        "note": "mapped but field unauthorized",
    }
    wire["bindingRequest"]["queryRequirements"]["filters"]["items"] = [
        {
            "filterId": "orphan-filter",
            "fieldId": "orphanField",
            "operator": "eq",
            "value": {"kind": "literal", "literal": 1},
            "nullPolicy": "fail",
            "required": True,
            "evidenceIds": ["ev-query-requirement"],
        }
    ]
    wire["handoffClosure"]["payload"] = wire["bindingRequest"]
    wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire["bindingRequest"])
    )
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_filters_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


# ===================================================================
# Section J: Immutability
# ===================================================================


def test_input_not_mutated_on_success():
    """The actual input is not mutated by a successful filter resolution."""
    request = _make_request_with_filters([_sample_filter_item()])
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    _resolve_filters_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_input_not_mutated_on_failure():
    """The actual input is not mutated by a failed filter resolution."""
    request = _make_request_with_filters([_sample_filter_item(evidence_ids=["ev-does-not-exist"])])
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    with pytest.raises(MetadataFilterResolutionErrorV3):
        _resolve_filters_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before, "filter resolver mutated its input"


def test_returned_evidence_ids_mutation_does_not_pollute_input():
    """Mutating returned evidenceIds does not affect the original request."""
    request = _make_request_with_filters([_sample_filter_item()])
    results = _resolve_filters_v3(request)
    results[0].evidence_ids.append("injected")

    items_evidence = request.binding_request.query_requirements.filters.items[0].evidence_ids
    assert "injected" not in items_evidence


# ===================================================================
# Section K: No infrastructure dependencies
# ===================================================================


def test_module_does_not_import_v2_or_infrastructure() -> None:
    import release_sql_bot.application.metadata_resolution_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "project_bindings_v2" not in source
    assert "fact_bindings_v2" not in source
    assert "BindingResolutionReportV2" not in source
    assert "mongodb" not in source
    assert "pymongo" not in source
    assert "sqlglot" not in source.lower()
    assert "os.environ" not in source
    assert "getenv" not in source
