"""Unit tests for V3 metadata-resolution public orchestrator.

Tests ``resolve_metadata_v3``: integrating all helpers and assembling
``BindingResolutionReportV3`` (success or blocked).

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

import logging
from copy import deepcopy

import pytest

import release_sql_bot.application.metadata_resolution_v3 as _mod
from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataResolutionStructureErrorV3,
    resolve_metadata_v3,
)
from release_sql_bot.application.usage_traceability_v3 import (
    compute_usage_traceability_sha256_v3,
)
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordV3,
    GovernedMetadataSnapshotV3,
    ProjectBindingContextV3,
    ResolveMetadataRequestV3,
)
from tests.v3_metadata_support import valid_resolve_metadata_request_v3_wire

# Reference for monkeypatch tests (must be at module level)
_resolve_metadata_input_v3_for_test = _mod._validate_resolution_input_v3

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_request() -> ResolveMetadataRequestV3:
    """Build a valid, internally-consistent request from the shared fixture."""
    wire = valid_resolve_metadata_request_v3_wire()
    return ResolveMetadataRequestV3.model_validate(wire)


def _reclose_all_hashes(req_wire: dict) -> ResolveMetadataRequestV3:
    """Reclose snapshot/context/approval/handoff hashes from a modified wire."""
    snapshot = GovernedMetadataSnapshotV3.model_validate(req_wire["metadataSnapshot"])
    req_wire["metadataSnapshot"]["contentSha256"] = canonical_content_sha256(snapshot)
    snap_hash = req_wire["metadataSnapshot"]["contentSha256"]
    req_wire["projectContext"]["metadataSnapshotRef"]["sha256"] = snap_hash
    context = ProjectBindingContextV3.model_validate(req_wire["projectContext"])
    req_wire["projectContext"]["contentSha256"] = canonical_content_sha256(context)
    ctx_hash = req_wire["projectContext"]["contentSha256"]
    req_wire["approvalRecord"]["snapshotRef"]["sha256"] = snap_hash
    req_wire["approvalRecord"]["contextRef"]["sha256"] = ctx_hash
    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    req_wire["approvalRecord"]["contentSha256"] = canonical_content_sha256(approval)
    # Reclose handoff closure payload hash
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3

    payload_model = FactBindingRequestV3.model_validate(req_wire["handoffClosure"]["payload"])
    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(payload_model)
    return ResolveMetadataRequestV3.model_validate(req_wire)


# ===================================================================
# Section 1: Single relation success
# ===================================================================


def test_single_relation_success_complete_wire() -> None:
    """Single relation → metadataResolved with complete output wire."""
    request = _make_valid_request()
    report = resolve_metadata_v3(request)

    assert report.status == "metadataResolved"
    assert report.executable is False
    assert report.issues == []
    assert report.resolved_joins == []
    assert report.resolved_aggregation is not None
    assert report.resolved_aggregation.mode == "none"
    assert report.resolved_time_range is not None
    assert report.resolved_time_range.mode == "none"

    # Verify requestRef
    assert report.request_ref.request_id == request.binding_request.request_id
    assert report.request_ref.payload_sha256 == request.handoff_closure.payload_sha256

    # Verify resolutionHashes are computed
    assert report.resolution_hashes.payload_sha256 == canonical_sha256(request.binding_request)
    assert report.resolution_hashes.context_sha256 == canonical_content_sha256(
        request.project_context
    )
    assert report.resolution_hashes.snapshot_sha256 == canonical_content_sha256(
        request.metadata_snapshot
    )

    # Verify handoffRefs
    assert report.handoff_refs.batch_sha256 == request.handoff_closure.batch_sha256
    assert report.handoff_refs.payload_sha256 == request.handoff_closure.payload_sha256


# ===================================================================
# Section 2: Multi-relation success (uses JOIN fixtures)
# ===================================================================


# ===================================================================
# Section 3: Structural failure → MetadataResolutionStructureErrorV3
# ===================================================================


def test_dict_input_raises_structure_error() -> None:
    """dict input → MetadataResolutionStructureErrorV3."""
    with pytest.raises(MetadataResolutionStructureErrorV3) as exc_info:
        resolve_metadata_v3({"not": "a request"})
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


def test_model_copy_structure_tampering_blocked() -> None:
    """Inject non-serializable value → MetadataResolutionStructureErrorV3.

    Modifies the internal __dict__ of a frozen model to inject a value
    that cannot be JSON-serialized. Simulates a model_copy bypass.
    """
    valid = _make_valid_request()

    # Modify the internal __dict__ directly (bypasses frozen __setattr__)
    valid.handoff_closure.__dict__["batch_sha256"] = frozenset({1, 2, 3})

    with pytest.raises(MetadataResolutionStructureErrorV3) as exc_info:
        resolve_metadata_v3(valid)
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


# ===================================================================
# Section 4: Blocked report - handoff payload mismatch
# ===================================================================


def test_handoff_payload_mismatch_blocked() -> None:
    """bindingRequest ≠ closure.payload → blocked with diagnostic hashes."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Modify bindingRequest but keep closure.payload as original
    req_wire["bindingRequest"]["queryRequirements"]["entity"]["entityType"] = "tampered"
    # closure.payload stays as original (not updated)
    # closure.payloadSha256 stays as original

    request = ResolveMetadataRequestV3.model_validate(req_wire)

    report = resolve_metadata_v3(request)
    assert report.status == "blocked"
    assert report.executable is False
    assert len(report.issues) == 1
    assert report.issues[0].code == "HANDOFF_BINDING_REQUEST_MISMATCH"
    assert report.issues[0].owner == "sqlBot"
    assert report.issues[0].impact == "blocker"

    # Six result fields must be empty/None
    assert report.resolved_fields == []
    assert report.resolved_entity_keys == []
    assert report.resolved_filters == []
    assert report.resolved_joins == []
    assert report.resolved_aggregation is None
    assert report.resolved_time_range is None

    # Carried payload hash ≠ recomputed hash (diagnostic evidence)
    assert report.request_ref.payload_sha256 == request.handoff_closure.payload_sha256
    assert report.resolution_hashes.payload_sha256 == canonical_sha256(request.binding_request)
    # They differ because bindingRequest was tampered but closure.payload wasn't
    assert report.request_ref.payload_sha256 != report.resolution_hashes.payload_sha256


# ===================================================================
# Section 5: Blocked report - context hash mismatch
# ===================================================================


def test_context_hash_mismatch_blocked() -> None:
    """context.contentSha256 mismatch → APPROVAL_CONTEXT_REF_MISMATCH."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Tamper context content but keep old hash
    req_wire["projectContext"]["metadataSnapshotRef"]["snapshotId"] = "tampered-snap"
    request = ResolveMetadataRequestV3.model_validate(req_wire)

    report = resolve_metadata_v3(request)
    assert report.status == "blocked"
    assert report.issues[0].code == "APPROVAL_CONTEXT_REF_MISMATCH"
    assert report.issues[0].owner == "metadataReview"

    # Output fields empty
    assert report.resolved_fields == []
    assert report.resolved_aggregation is None


# ===================================================================
# Section 6: Determinism and isolation
# ===================================================================


def test_same_input_produces_same_report() -> None:
    """Same input → identical report on repeated calls."""
    request = _make_valid_request()
    report1 = resolve_metadata_v3(request)
    report2 = resolve_metadata_v3(request)

    assert report1.model_dump(by_alias=True, mode="json") == report2.model_dump(
        by_alias=True, mode="json"
    )


def test_input_unchanged_after_success() -> None:
    """Input not mutated after successful resolution."""
    request = _make_valid_request()
    before = request.model_dump(by_alias=True, mode="json")
    resolve_metadata_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


# ===================================================================
# Section 7: Dual error ordering
# ===================================================================


def test_dual_error_returns_first() -> None:
    """When two errors exist, the first in sequence is returned."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    fact_code = req_wire["bindingRequest"]["fact"]["factCode"]

    # Change bindingRequest's ruleRef (and sync closure to pass earlier checks)
    req_wire["bindingRequest"]["ruleRef"]["ruleSetId"] = "WRONG_RULESET"
    req_wire["bindingRequest"]["ruleRef"]["ruleVersion"] = "WRONG_RULESET@v1"
    req_wire["bindingRequest"]["requestId"] = f"WRONG_RULESET@v1#{fact_code}"
    # Sync handoff closure to match (pass handoff identity + binding checks)
    req_wire["handoffClosure"]["ruleVersion"] = "WRONG_RULESET@v1"
    req_wire["handoffClosure"]["requestId"] = f"WRONG_RULESET@v1#{fact_code}"
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    # Recompute payload_sha256
    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        ResolveMetadataRequestV3.model_validate(req_wire).binding_request
    )
    # Make request_in_context also fail by removing from context
    req_wire["projectContext"]["requestIds"] = ["other-request@ctx#fact.other"]
    # Recompute context content_sha256 (requestIds changed)
    context = ProjectBindingContextV3.model_validate(req_wire["projectContext"])
    req_wire["projectContext"]["contentSha256"] = canonical_content_sha256(context)
    # Update approval record to match new context hash
    req_wire["approvalRecord"]["contextRef"]["sha256"] = req_wire["projectContext"]["contentSha256"]
    # Recompute approval record content hash
    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    req_wire["approvalRecord"]["contentSha256"] = canonical_content_sha256(approval)

    request = ResolveMetadataRequestV3.model_validate(req_wire)

    report = resolve_metadata_v3(request)
    assert report.status == "blocked"
    # First error in sequence is ruleRef mismatch (step 5 before step 6)
    assert report.issues[0].code == "RULE_REF_MISMATCH"


# ===================================================================
# Section 8: Sanitization
# ===================================================================


def test_error_does_not_leak_synthetic_marker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Synthetic marker must not appear in error messages."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    marker = "secretMarker"
    req_wire["projectRef"]["projectId"] = marker

    request = ResolveMetadataRequestV3.model_validate(req_wire)

    with caplog.at_level(logging.DEBUG, logger="release_sql_bot"):
        report = resolve_metadata_v3(request)

    assert report.status == "blocked"
    # Marker should not appear in the issue message
    for issue in report.issues:
        assert marker not in issue.message


# ===================================================================
# Section 9: Issue mapping completeness
# ===================================================================


def test_issue_mapping_covers_all_codes() -> None:
    """Static mapping covers all known error codes."""
    from release_sql_bot.application.metadata_resolution_v3 import _ISSUE_MAPPING

    # Verify some critical codes are present
    critical_codes = [
        "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID",
        "HANDOFF_BINDING_REQUEST_MISMATCH",
        "PROJECT_REF_MISMATCH",
        "RULE_REF_MISMATCH",
        "REQUEST_NOT_IN_CONTEXT",
        "COLUMN_GRANT_NOT_FOUND",
        "FIELD_AUTHORIZATION_MISSING",
        "APPROVAL_CONTEXT_REF_MISMATCH",
        "JOIN_CLOSURE_DISCONNECTED",
        "JOIN_PLAN_DIRECTION_CONFLICT",
        "ENTITY_GRAIN_MAPPING_MISSING",
    ]
    for code in critical_codes:
        assert code in _ISSUE_MAPPING, f"Missing mapping for {code}"
        owner, message = _ISSUE_MAPPING[code]
        assert owner in ("businessRuleReview", "metadataReview", "sqlBot")
        assert len(message) > 0


# ===================================================================
# Section 10: Failed-report copy regression
# ===================================================================


def test_blocked_report_uses_verified_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blocked report diagnostic hashes use the verified copy, not the original.

    Proves the fix by:
    1. Saving context hash before any modification
    2. Wrapping the real input gate so it mutates the caller's original
       request AFTER validation but BEFORE _build_blocked_report reads it
    3. Asserting the report uses the pre-mutation hash
    """
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Create PROJECT_REF_MISMATCH: request.projectRef ≠ context.projectRef
    req_wire["projectRef"]["projectId"] = "tampered-project"
    request = ResolveMetadataRequestV3.model_validate(req_wire)

    # Save the context hash BEFORE any mutation (this is what the report should use)
    hash_before_mutation = canonical_content_sha256(request.project_context)

    # Save the ORIGINAL function before patching
    original_validate = _mod._validate_resolution_input_v3

    # Track whether mutation actually happened
    mutation_count = 0

    def patched_validate(req):
        nonlocal mutation_count
        try:
            return original_validate(req)
        finally:
            # Mutate the CALLER'S original request after validation completes
            # but before _build_blocked_report reads it
            nonlocal mutation_count
            mutation_count += 1
            request.project_context.__dict__["request_ids"].append("injected-request-id")

    monkeypatch.setattr(_mod, "_validate_resolution_input_v3", patched_validate)

    report = resolve_metadata_v3(request)

    # Mutation must have occurred
    assert mutation_count == 1, "mutation did not happen"

    # Report should be blocked with PROJECT_REF_MISMATCH
    assert report.status == "blocked"
    assert report.issues[0].code == "PROJECT_REF_MISMATCH"

    # The diagnostic hash must equal the PRE-mutation hash (from verified copy)
    assert report.resolution_hashes.context_sha256 == hash_before_mutation

    # The original request's context hash must now differ (was mutated)
    hash_after_mutation = canonical_content_sha256(request.project_context)
    assert hash_after_mutation != hash_before_mutation
    # And the report hash should NOT match the mutated original
    assert report.resolution_hashes.context_sha256 != hash_after_mutation


# ===================================================================
# Section 11: Complete success report with full wire comparison
# ===================================================================


def test_single_relation_success_full_wire() -> None:
    """Single relation → complete camelCase wire comparison."""
    request = _make_valid_request()
    report = resolve_metadata_v3(request)

    wire = report.model_dump(by_alias=True, mode="json")

    # Build complete expected wire from authoritative input
    br = request.binding_request
    pc = request.project_context
    ms = request.metadata_snapshot
    hc = request.handoff_closure

    # Expected resolvedFields: factValue (role=value) + syntheticKey (role=entityKey)
    expected_fact_value = {
        "fieldId": "factValue",
        "role": "value",
        "authorizationId": "fba-value",
        "columnGrantId": "colgrant-value",
        "schemaName": "dbo",
        "relationName": "synthetic_table",
        "columnName": "synthetic_value",
        "evidenceIds": ["ev-fact-declaration"],
    }
    expected_synthetic_key_field = {
        "fieldId": "syntheticKey",
        "role": "entityKey",
        "authorizationId": "fba-key",
        "columnGrantId": "colgrant-key",
        "schemaName": "dbo",
        "relationName": "synthetic_table",
        "columnName": "synthetic_key",
        "evidenceIds": ["ev-fact-declaration"],
    }
    # Expected aggregation (mode=none from fixture)
    expected_aggregation = {
        "mode": "none",
        "function": None,
        "inputFieldIds": [],
        "groupByFieldIds": [],
        "distinct": None,
        "evidenceIds": ["ev-query-requirement"],
    }

    # Expected timeRange (mode=none from fixture)
    expected_time_range = {
        "mode": "none",
        "timeFieldId": None,
        "timeSchemaName": None,
        "timeRelationName": None,
        "timeColumnName": None,
        "evidenceIds": ["ev-query-requirement"],
    }

    expected_wire = {
        "schemaVersion": "1.0.0",
        "status": "metadataResolved",
        "executable": False,
        "requestRef": {
            "requestId": br.request_id,
            "ruleRef": br.rule_ref.model_dump(by_alias=True, mode="json"),
            "payloadSha256": hc.payload_sha256,
        },
        "projectRef": request.project_ref.model_dump(by_alias=True, mode="json"),
        "contextRef": {
            "contextId": pc.context_id,
            "contextVersion": pc.context_version,
            "sha256": pc.content_sha256,
        },
        "snapshotRef": {
            "snapshotId": ms.snapshot_id,
            "snapshotVersion": ms.snapshot_version,
            "sha256": ms.content_sha256,
        },
        "handoffRefs": {
            "batchSha256": hc.batch_sha256,
            "payloadSha256": hc.payload_sha256,
            "contractSchemaId": hc.contract_schema_id,
            "contractSchemaSha256": hc.contract_schema_sha256,
        },
        "resolutionHashes": {
            "payloadSha256": canonical_sha256(br),
            "contextSha256": canonical_content_sha256(pc),
            "snapshotSha256": canonical_content_sha256(ms),
        },
        "resolvedFields": [expected_fact_value, expected_synthetic_key_field],
        "resolvedEntityKeys": [
            {
                "parameterName": "syntheticKey",
                "fieldId": "syntheticKey",
                "authorizationId": "eka-1",
                "columnGrantId": "colgrant-key",
                "schemaName": "dbo",
                "relationName": "synthetic_table",
                "columnName": "synthetic_key",
                "evidenceIds": ["ev-fact-declaration"],
            }
        ],
        "resolvedFilters": [],
        "resolvedAggregation": expected_aggregation,
        "resolvedTimeRange": expected_time_range,
        "resolvedJoins": [],
        "usageTraceabilitySha256": compute_usage_traceability_sha256_v3(
            request.binding_request.usages
        ),
        "issues": [],
    }

    assert wire == expected_wire


# ===================================================================
# Section 12: Usage traceability with same conditionId
# ===================================================================


def _build_two_usage_request() -> ResolveMetadataRequestV3:
    """Build a valid request with TWO usages sharing conditionId but different ruleCode."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Get the existing usage as template
    original_usage = req_wire["bindingRequest"]["usages"][0]

    # Create a second usage with same conditionId but different ruleCode
    second_usage = {
        "stage": original_usage["stage"],
        "ruleCode": "DIFFERENT_RULE",
        "priority": original_usage["priority"] + 1,
        "conditionId": original_usage["conditionId"],  # SAME conditionId
        "conditionPath": original_usage["conditionPath"],
        "outcome": original_usage["outcome"],
        "evidenceIds": ["ev-query-requirement"],
    }

    # Add the second usage to both bindingRequest and closure.payload
    req_wire["bindingRequest"]["usages"] = [original_usage, second_usage]
    req_wire["handoffClosure"]["payload"]["usages"] = [original_usage, second_usage]

    # Recompute handoff payload hash
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3

    payload_model = FactBindingRequestV3.model_validate(req_wire["handoffClosure"]["payload"])
    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(payload_model)

    return ResolveMetadataRequestV3.model_validate(req_wire)


def test_usage_traceability_includes_all_six_tuples() -> None:
    """Same conditionId with different ruleCode → both included in digest."""
    request = _build_two_usage_request()

    # Verify precondition: two usages with same conditionId but different ruleCode
    usages = request.binding_request.usages
    assert len(usages) == 2, f"Expected 2 usages, got {len(usages)}"
    assert usages[0].condition_id == usages[1].condition_id
    assert usages[0].rule_code != usages[1].rule_code
    # Verify upstream 4-tuple identity differs
    id0 = (usages[0].stage, usages[0].rule_code, usages[0].condition_id, usages[0].condition_path)
    id1 = (usages[1].stage, usages[1].rule_code, usages[1].condition_id, usages[1].condition_path)
    assert id0 != id1

    # Save original wire for later comparison
    original_wire = request.model_dump(by_alias=True, mode="json")

    # First parse
    report = resolve_metadata_v3(request)
    assert report.status == "metadataResolved"
    digest1 = report.usage_traceability_sha256
    assert digest1 == compute_usage_traceability_sha256_v3(request.binding_request.usages)

    # Modify only the second usage's priority and reclose
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["bindingRequest"]["usages"][1]["priority"] = (
        req_wire["bindingRequest"]["usages"][1]["priority"] + 1
    )
    req_wire["handoffClosure"]["payload"]["usages"][1]["priority"] = (
        req_wire["handoffClosure"]["payload"]["usages"][1]["priority"] + 1
    )
    # Recompute payload hash
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3

    payload_model = FactBindingRequestV3.model_validate(req_wire["handoffClosure"]["payload"])
    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(payload_model)
    request2 = ResolveMetadataRequestV3.model_validate(req_wire)

    # Second parse
    report2 = resolve_metadata_v3(request2)
    assert report2.status == "metadataResolved"
    digest2 = report2.usage_traceability_sha256
    assert digest2 == compute_usage_traceability_sha256_v3(request2.binding_request.usages)

    # Digests must differ
    assert digest1 != digest2

    # Original request must be unchanged (compare full wire)
    assert request.model_dump(by_alias=True, mode="json") == original_wire


# ===================================================================
# Section 13: Error mapping completeness (exact set)
# ===================================================================


def test_error_mapping_exact_set() -> None:
    """Static mapping covers exactly all known error codes."""
    from release_sql_bot.application.metadata_resolution_v3 import _ISSUE_MAPPING

    # All known codes from actual exception classes
    expected_codes = {
        # Input gate
        "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID",
        "HANDOFF_BINDING_REQUEST_MISMATCH",
        "PROJECT_REF_MISMATCH",
        "RULE_REF_MISMATCH",
        "REQUEST_NOT_IN_CONTEXT",
        # Handoff
        "HANDOFF_STRUCTURE_INVALID",
        "HANDOFF_SCHEMA_SOURCE_INVALID",
        "HANDOFF_SCHEMA_REF_MISMATCH",
        "HANDOFF_PAYLOAD_SCHEMA_INVALID",
        "HANDOFF_IDENTITY_MISMATCH",
        "HANDOFF_PAYLOAD_HASH_MISMATCH",
        # Approval
        "APPROVAL_ID_MISMATCH",
        "APPROVAL_POLICY_MISMATCH",
        "APPROVAL_TIME_MISMATCH",
        "APPROVAL_CONTEXT_REF_MISMATCH",
        "APPROVAL_SNAPSHOT_REF_MISMATCH",
        "APPROVAL_CONTENT_HASH_MISMATCH",
        "APPROVAL_CONTEXT_NOT_APPROVED",
        "APPROVAL_SNAPSHOT_NOT_APPROVED",
        "APPROVAL_SNAPSHOT_BINDING_MISMATCH",
        # Column
        "COLUMN_GRANT_ID_INVALID",
        "SNAPSHOT_RELATION_AMBIGUOUS",
        "SNAPSHOT_COLUMN_AMBIGUOUS",
        "COLUMN_GRANT_NOT_FOUND",
        "RELATION_GRANT_NOT_FOUND",
        "RELATION_NOT_IN_SNAPSHOT",
        "COLUMN_NOT_IN_SNAPSHOT",
        # Field/Entity
        "FIELD_AUTHORIZATION_MISSING",
        "ENTITY_KEY_AUTHORIZATION_MISSING",
        "ENTITY_KEY_AUTHORIZATION_AMBIGUOUS",
        "ENTITY_KEY_FIELD_NOT_FOUND",
        "ENTITY_KEY_FIELD_ROLE_MISMATCH",
        "ENTITY_KEY_COLUMN_GRANT_MISMATCH",
        # Filter
        "FILTER_EVIDENCE_REFERENCE_INVALID",
        # Entity-grain
        "ENTITY_GRAIN_MAPPING_MISSING",
        "ENTITY_GRAIN_GRANT_INVALID",
        "ENTITY_GRAIN_RELATION_MISMATCH",
        # JOIN evidence
        "JOIN_GRANT_ID_INVALID",
        "JOIN_GRANT_NOT_FOUND",
        "JOIN_ENDPOINTS_IDENTICAL",
        "JOIN_RELATIONSHIP_NOT_FOUND",
        "JOIN_RELATIONSHIP_AMBIGUOUS",
        "JOIN_CLOSURE_DISCONNECTED",
        "JOIN_CLOSURE_AMBIGUOUS",
        "JOIN_PLAN_DIRECTION_CONFLICT",
        "JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND",
        "JOIN_EVIDENCE_PAYLOAD_HASH_MISMATCH",
        "JOIN_GRANT_EVIDENCE_INVALID",
        "JOIN_REQUEST_NOT_IN_CONTEXT",
    }

    assert set(_ISSUE_MAPPING.keys()) == expected_codes

    # Verify each mapping has valid owner and non-empty message
    for _code, (owner, message) in _ISSUE_MAPPING.items():
        assert owner in ("businessRuleReview", "metadataReview", "sqlBot")
        assert len(message) > 0


# ===================================================================
# Section 14: Unknown program error propagates
# ===================================================================


def test_unknown_program_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown RuntimeError propagates, not converted to blocked."""
    request = _make_valid_request()

    def raise_runtime_error(req):
        raise RuntimeError("unexpected internal error")

    import release_sql_bot.application.metadata_resolution_v3 as mod

    monkeypatch.setattr(mod, "_validate_resolution_input_v3", raise_runtime_error)

    with pytest.raises(RuntimeError, match="unexpected internal error"):
        resolve_metadata_v3(request)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
