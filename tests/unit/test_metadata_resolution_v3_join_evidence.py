"""Unit tests for V3 JOIN-authorization-evidence resolution (DEV §5.3.6 ext).

Tests the internal helper ``_resolve_join_evidence_v3``: verifying that
every selected join grant has a unique evidence association for the current
request, with payload hash and evidence-reference integrity.

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataColumnResolutionErrorV3,
    MetadataJoinEvidenceErrorV3,
    MetadataJoinResolutionErrorV3,
    _resolve_join_evidence_v3,
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


def _make_valid_request():
    """Build a valid, internally-consistent request from the shared fixture."""
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3

    wire = valid_resolve_metadata_request_v3_wire()
    return ResolveMetadataRequestV3.model_validate(wire)


def _reclose_context_only(req_wire: dict) -> object:
    """Reclose context/approval hashes from a modified wire (no handoff closure)."""
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
    return ResolveMetadataRequestV3.model_validate(req_wire)


def _reclose_all_hashes(req_wire: dict) -> object:
    """Reclose snapshot/context/approval/handoff hashes from a modified wire."""
    from release_sql_bot.application.canonical import (
        canonical_content_sha256,
        canonical_sha256,
    )
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
    from release_sql_bot.domain.project_bindings_v3 import (
        ApprovalRecordV3,
        GovernedMetadataSnapshotV3,
        ProjectBindingContextV3,
        ResolveMetadataRequestV3,
    )

    snapshot = GovernedMetadataSnapshotV3.model_validate(req_wire["metadataSnapshot"])
    req_wire["metadataSnapshot"]["contentSha256"] = canonical_content_sha256(snapshot)

    req_wire["projectContext"]["metadataSnapshotRef"]["sha256"] = req_wire["metadataSnapshot"][
        "contentSha256"
    ]
    context = ProjectBindingContextV3.model_validate(req_wire["projectContext"])
    req_wire["projectContext"]["contentSha256"] = canonical_content_sha256(context)

    req_wire["approvalRecord"]["snapshotRef"]["sha256"] = req_wire["metadataSnapshot"][
        "contentSha256"
    ]
    req_wire["approvalRecord"]["contextRef"]["sha256"] = req_wire["projectContext"]["contentSha256"]
    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    req_wire["approvalRecord"]["contentSha256"] = canonical_content_sha256(approval)

    payload_model = FactBindingRequestV3.model_validate(req_wire["handoffClosure"]["payload"])
    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(payload_model)

    return ResolveMetadataRequestV3.model_validate(req_wire)


def _build_three_relation_request(evidence_order: str = "natural"):
    """Build a valid request with 3 relations and 2 selected join grants.

    Starts from a valid 2-relation base and adds a third relation + grant.

    Args:
        evidence_order: "natural" for join-1 then join-b;
                        "reversed" for join-b then join-1.
    """
    # Start from valid multi-relation request (2 relations, 1 grant, valid evidence)
    base = _build_multi_relation_request()
    req_wire = base.model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    # Add third relation to snapshot
    req_wire["metadataSnapshot"]["relations"].append(
        {
            "schemaName": "dbo",
            "relationName": "dim_b",
            "relationKind": "table",
            "columns": [
                {"columnName": "dim_b_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "dim_b_value", "sqlType": "int", "nullable": False},
            ],
        }
    )
    # Add relationship edge for synthetic_table → dim_b
    req_wire["metadataSnapshot"]["relationships"].append(
        {
            "relationshipId": "rel-b",
            "leftColumn": {
                "schemaName": "dbo",
                "relationName": "synthetic_table",
                "columnName": "synthetic_key",
            },
            "rightColumn": {
                "schemaName": "dbo",
                "relationName": "dim_b",
                "columnName": "dim_b_key",
            },
        }
    )
    # Add relation + column grants for dim_b
    req_wire["projectContext"]["relationGrants"].append(
        {"grantId": "relgrant-b", "schemaName": "dbo", "relationName": "dim_b", "access": "read"}
    )
    req_wire["projectContext"]["columnGrants"].extend(
        [
            {
                "grantId": "colgrant-b-key",
                "relationGrantId": "relgrant-b",
                "columnName": "dim_b_key",
            },
            {
                "grantId": "colgrant-b-value",
                "relationGrantId": "relgrant-b",
                "columnName": "dim_b_value",
            },
        ]
    )
    # Add second join grant (synthetic_table ↔ dim_b)
    req_wire["projectContext"]["joinGrants"].append(
        {
            "grantId": "join-b",
            "leftColumnGrantId": "colgrant-key",
            "rightColumnGrantId": "colgrant-b-key",
            "joinType": "inner",
        }
    )
    # Add field + binding for dim_b_value
    req_wire["bindingRequest"]["queryRequirements"]["fields"].append(
        {
            "fieldId": "dimBValue",
            "role": "value",
            "logicalName": "dim_b_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        }
    )
    req_wire["projectContext"]["fieldBindingAuthorizations"].append(
        {
            "authorizationId": "fba-dim-b",
            "requestId": base_req_id,
            "fieldId": "dimBValue",
            "role": "value",
            "columnGrantId": "colgrant-b-value",
        }
    )

    # Close hashes (without evidence)
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    request = _reclose_all_hashes(req_wire)

    # Add evidence for both grants with correct payload hash
    wire = request.model_dump(by_alias=True, mode="json")
    payload_hash = wire["handoffClosure"]["payloadSha256"]

    associations = [
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement", "ev-example", "ev-query-requirement"],
        },
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-b",
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]

    if evidence_order == "reversed":
        associations = list(reversed(associations))

    wire["projectContext"]["joinAuthorizationEvidence"] = associations
    return _reclose_all_hashes(wire)


def _build_multi_relation_request():
    """Build a valid request with two relations, one join grant, and valid evidence."""
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3

    wire = valid_resolve_metadata_request_v3_wire()
    req_wire = ResolveMetadataRequestV3.model_validate(wire).model_dump(by_alias=True, mode="json")

    base_req_id = req_wire["bindingRequest"]["requestId"]

    # Add second relation to snapshot
    req_wire["metadataSnapshot"]["relations"] = [
        {
            "schemaName": "dbo",
            "relationName": "synthetic_table",
            "relationKind": "table",
            "columns": [
                {"columnName": "synthetic_value", "sqlType": "int", "nullable": False},
                {"columnName": "synthetic_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "relationKind": "table",
            "columns": [
                {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "dim_value", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    # Snapshot relationship edge
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-1",
            "leftColumn": {
                "schemaName": "dbo",
                "relationName": "synthetic_table",
                "columnName": "synthetic_key",
            },
            "rightColumn": {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "columnName": "dim_key",
            },
        }
    ]
    # Relation grant + column grant for second relation
    req_wire["projectContext"]["relationGrants"].append(
        {
            "grantId": "relgrant-dim",
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "access": "read",
        }
    )
    req_wire["projectContext"]["columnGrants"].append(
        {"grantId": "colgrant-dim", "relationGrantId": "relgrant-dim", "columnName": "dim_key"}
    )
    # Join grant
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-1",
            "leftColumnGrantId": "colgrant-key",
            "rightColumnGrantId": "colgrant-dim",
            "joinType": "inner",
        }
    ]
    # Second field + field binding
    req_wire["bindingRequest"]["queryRequirements"]["fields"].append(
        {
            "fieldId": "dimValue",
            "role": "value",
            "logicalName": "dimension_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        }
    )
    req_wire["projectContext"]["fieldBindingAuthorizations"].append(
        {
            "authorizationId": "fba-dim",
            "requestId": base_req_id,
            "fieldId": "dimValue",
            "role": "value",
            "columnGrantId": "colgrant-dim",
        }
    )
    # Entity grain references relgrant-1 (which exists in the base fixture)
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-1",
        }
    ]

    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    request = _reclose_all_hashes(req_wire)

    # Add evidence
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": wire["handoffClosure"]["payloadSha256"],
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement"],
        }
    ]
    return _reclose_all_hashes(wire)


# ===================================================================
# Section 1: Single relation, no joins needed
# ===================================================================


def test_single_relation_empty_evidence_succeeds() -> None:
    """Single relation with no joins → empty result, no error."""
    request = _make_valid_request()
    result = _resolve_join_evidence_v3(request)
    assert result == ()


def test_single_relation_with_extra_illegal_grant_blocked() -> None:
    """Even with single relation, an illegal physical grant is caught."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-bad",
            "leftColumnGrantId": "nonexistent-colgrant",
            "rightColumnGrantId": "colgrant-key",
            "joinType": "inner",
        }
    ]
    request = _reclose_all_hashes(req_wire)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_join_evidence_v3(request)
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


# ===================================================================
# Section 2: Deterministic error ordering (sorting defect regression)
# ===================================================================


def test_deterministic_error_ordering_original() -> None:
    """With two local-reference errors, the sorted-first error is reported.

    Uses a valid multi-relation setup so physical validation passes.
    Two evidence associations with different local reference errors;
    sorting by (requestId, joinGrantId) determines which fires first.
    """
    request = _build_multi_relation_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    # Two evidence associations with different local reference errors:
    # - Record 1: nonexistent requestId → JOIN_REQUEST_NOT_IN_CONTEXT
    # - Record 2: nonexistent grantId → JOIN_GRANT_NOT_FOUND
    # After sorting by (requestId, joinGrantId):
    #   ("A-invalid", "join-1") < (base_req_id, "nonexistent-grant")
    # because "A" < "S" in ASCII
    req_wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": req_wire["handoffClosure"]["payloadSha256"],
            "joinGrantId": "nonexistent-grant",
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "requestId": "A-invalid",
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-1"],
        },
    ]
    request = _reclose_all_hashes(req_wire)

    with pytest.raises((MetadataJoinEvidenceErrorV3, MetadataJoinResolutionErrorV3)) as exc_info:
        _resolve_join_evidence_v3(request)
    # Sorted: ("A-invalid", "join-1") < (base_req_id, "nonexistent-grant")
    # So JOIN_REQUEST_NOT_IN_CONTEXT fires first
    assert exc_info.value.code == "JOIN_REQUEST_NOT_IN_CONTEXT"


def test_deterministic_error_ordering_reversed() -> None:
    """Same test with reversed input order — same error is reported."""
    request = _build_multi_relation_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    # Reversed input order
    req_wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": "A-invalid",
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-1"],
        },
        {
            "requestId": base_req_id,
            "payloadSha256": req_wire["handoffClosure"]["payloadSha256"],
            "joinGrantId": "nonexistent-grant",
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    request = _reclose_all_hashes(req_wire)

    with pytest.raises((MetadataJoinEvidenceErrorV3, MetadataJoinResolutionErrorV3)) as exc_info:
        _resolve_join_evidence_v3(request)
    assert exc_info.value.code == "JOIN_REQUEST_NOT_IN_CONTEXT"


def test_second_error_fires_when_first_fixed() -> None:
    """After fixing the first local-reference error, the second fires.

    Verifies that the deterministic ordering is not just masking the second error.
    """
    request = _build_multi_relation_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    # Fix the first error: use valid requestId for join-1 association
    # Second error remains: nonexistent grantId for the other association
    req_wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": req_wire["handoffClosure"]["payloadSha256"],
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "requestId": base_req_id,
            "payloadSha256": req_wire["handoffClosure"]["payloadSha256"],
            "joinGrantId": "nonexistent-grant",
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    request = _reclose_all_hashes(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_evidence_v3(request)
    assert exc_info.value.code == "JOIN_GRANT_NOT_FOUND"


# ===================================================================
# Section 3: Non-empty success result (3 relations, 2 grants)
# ===================================================================


def test_three_relation_success_returns_sorted() -> None:
    """Three relations with two selected grants → sorted result with full wire comparison."""
    request = _build_three_relation_request(evidence_order="reversed")
    result = _resolve_join_evidence_v3(request)

    # Convert result to camelCase JSON wire for full comparison
    result_wire = [assoc.model_dump(by_alias=True, mode="json") for assoc in result]

    # Expected: sorted by grantId (join-1, join-b), evidenceIds preserved with duplicates
    expected_req_id = request.binding_request.request_id
    expected_hash = canonical_sha256(request.binding_request)

    expected_wire = [
        {
            "requestId": expected_req_id,
            "payloadSha256": expected_hash,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement", "ev-example", "ev-query-requirement"],
        },
        {
            "requestId": expected_req_id,
            "payloadSha256": expected_hash,
            "joinGrantId": "join-b",
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]

    assert result_wire == expected_wire


# ===================================================================
# Section 4: Return isolation
# ===================================================================


def test_return_isolation_modification_does_not_affect_request() -> None:
    """Modifying returned evidenceIds must not affect the original request."""
    request = _build_three_relation_request(evidence_order="natural")
    result = _resolve_join_evidence_v3(request)

    # Snapshot the original wire
    original_wire = request.model_dump(by_alias=True, mode="json")

    # Mutate the returned evidence_ids
    for assoc in result:
        assoc.evidence_ids.append("injected-evidence")

    # Original request must be unchanged
    after_wire = request.model_dump(by_alias=True, mode="json")
    assert after_wire == original_wire


# ===================================================================
# Section 5: Missing coverage
# ===================================================================


def test_missing_one_association_raises() -> None:
    """Three relations but missing one grant's association → ASSOCIATION_NOT_FOUND."""
    # Use the working 3-relation helper but remove one association
    request = _build_three_relation_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Only keep evidence for join-1, remove join-b
    req_wire["projectContext"]["joinAuthorizationEvidence"] = [
        evidence
        for evidence in req_wire["projectContext"]["joinAuthorizationEvidence"]
        if evidence["joinGrantId"] == "join-1"
    ]
    request = _reclose_all_hashes(req_wire)

    with pytest.raises(MetadataJoinEvidenceErrorV3) as exc_info:
        _resolve_join_evidence_v3(request)
    assert exc_info.value.code == "JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND"


# ===================================================================
# Section 6: Two-request isolation
# ===================================================================


def test_two_request_isolation() -> None:
    """Two requests sharing a context: evidence checked independently."""
    # Build a valid multi-relation base
    base = _build_multi_relation_request()
    wire = base.model_dump(by_alias=True, mode="json")

    # Add a second requestId to context
    second_req_id = "second-request@ctx#fact.other"
    wire["projectContext"]["requestIds"].append(second_req_id)

    # Add evidence for both requests
    payload_hash = wire["handoffClosure"]["payloadSha256"]
    wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": second_req_id,
            "payloadSha256": "b" * 64,  # wrong hash for second request (but we don't check it)
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "requestId": wire["bindingRequest"]["requestId"],
            "payloadSha256": payload_hash,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    request = _reclose_all_hashes(wire)

    # Should succeed — current request's evidence is valid
    result = _resolve_join_evidence_v3(request)
    assert len(result) == 1
    assert result[0].join_grant_id == "join-1"


# ===================================================================
# Section 7: Unselected but illegal association still blocked
# ===================================================================


def _build_unselected_grant_request():
    """Build a request where the join grant exists in context but is NOT selected.

    Starts from a valid 2-relation request, then removes the dimValue field
    so the dim relation is unnecessary. The join grant still exists in context
    but _select_join_closure_v3 returns ().
    """
    from release_sql_bot.application.metadata_resolution_v3 import (
        _select_join_closure_v3,
    )

    base = _build_multi_relation_request()
    req_wire = base.model_dump(by_alias=True, mode="json")

    # Remove the dimValue field so the dim relation is not needed
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        f
        for f in req_wire["bindingRequest"]["queryRequirements"]["fields"]
        if f["fieldId"] != "dimValue"
    ]
    # Sync handoffClosure payload
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])

    # Reclose handoff closure payload hash
    new_payload_hash = canonical_sha256(
        ResolveMetadataRequestV3.model_validate(req_wire).binding_request
    )
    req_wire["handoffClosure"]["payloadSha256"] = new_payload_hash

    # Update evidence payloadSha256 to match new binding request
    for evidence in req_wire["projectContext"]["joinAuthorizationEvidence"]:
        if evidence["joinGrantId"] == "join-1":
            evidence["payloadSha256"] = new_payload_hash
            break

    # Reclose all remaining hashes
    request = _reclose_context_only(req_wire)

    # Verify: join grant exists in context but is NOT selected
    assert any(
        jg["grantId"] == "join-1"
        for jg in request.model_dump(by_alias=True, mode="json")["projectContext"]["joinGrants"]
    )
    assert _select_join_closure_v3(request) == ()

    return request


def test_unselected_grant_with_valid_evidence_succeeds() -> None:
    """Unselected grant with valid evidence → success (empty result)."""
    request = _build_unselected_grant_request()
    result = _resolve_join_evidence_v3(request)
    assert result == ()


def test_unselected_grant_with_wrong_payload_blocked() -> None:
    """Unselected grant with wrong payload → JOIN_EVIDENCE_PAYLOAD_HASH_MISMATCH."""
    base = _build_unselected_grant_request()
    req_wire = base.model_dump(by_alias=True, mode="json")

    # Corrupt the payload hash in the evidence
    for evidence in req_wire["projectContext"]["joinAuthorizationEvidence"]:
        if evidence["joinGrantId"] == "join-1":
            evidence["payloadSha256"] = "f" * 64  # wrong hash
            break

    # Reclose context/approval hashes (evidence payload hash is intentionally wrong)
    req_wire["projectContext"]["joinAuthorizationEvidence"][0]["payloadSha256"] = "f" * 64
    request = _reclose_context_only(req_wire)

    with pytest.raises(MetadataJoinEvidenceErrorV3) as exc_info:
        _resolve_join_evidence_v3(request)
    assert exc_info.value.code == "JOIN_EVIDENCE_PAYLOAD_HASH_MISMATCH"


def test_unselected_grant_with_dangling_evidence_blocked() -> None:
    """Unselected grant with dangling evidence → JOIN_GRANT_EVIDENCE_INVALID."""
    base = _build_unselected_grant_request()
    req_wire = base.model_dump(by_alias=True, mode="json")

    # Replace evidenceIds with a non-existent ID
    for evidence in req_wire["projectContext"]["joinAuthorizationEvidence"]:
        if evidence["joinGrantId"] == "join-1":
            evidence["evidenceIds"] = ["nonexistent-evidence"]
            break

    # Reclose context/approval hashes
    request = _reclose_context_only(req_wire)

    with pytest.raises(MetadataJoinEvidenceErrorV3) as exc_info:
        _resolve_join_evidence_v3(request)
    assert exc_info.value.code == "JOIN_GRANT_EVIDENCE_INVALID"


# ===================================================================
# Section 8: Real model_copy bypass
# ===================================================================


def test_model_copy_injected_illegal_association_blocked() -> None:
    """model_copy injecting illegal association → STRUCTURE_INVALID.

    Uses model_copy to inject an association with an extra field
    (extra=forbid). The model_dump → model_validate round-trip fails,
    mapping to METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID.
    """
    from release_sql_bot.application.metadata_resolution_v3 import (
        MetadataResolutionInputErrorV3,
    )

    # Start from a valid request
    valid = _build_multi_relation_request()

    # Build a tampered context wire with an extra illegal field
    context_wire = valid.project_context.model_dump(by_alias=True, mode="json")
    context_wire["joinAuthorizationEvidence"].append(
        {
            "requestId": "injected-request",
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-1"],
            "extraForbiddenField": "malicious",
        }
    )

    # Reconstruct via model_copy — this bypasses Pydantic validation
    # but the extra field will be caught during the helper's re-validation
    tampered_context = ProjectBindingContextV3.model_copy(
        valid.project_context,
        update={"join_authorization_evidence": context_wire["joinAuthorizationEvidence"]},
    )
    tampered_request = valid.model_copy(update={"project_context": tampered_context})

    # Direct call — revalidation catches the extra field
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_join_evidence_v3(tampered_request)
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


# ===================================================================
# Section 9: Sanitization
# ===================================================================


def test_sanitization_does_not_leak_marker(caplog: pytest.LogCaptureFixture) -> None:
    """Error messages must not leak the actual marker value."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    marker = "secretentity"
    req_wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": marker,
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-1"],
        }
    ]
    request = _reclose_all_hashes(req_wire)

    with pytest.raises(MetadataJoinEvidenceErrorV3) as exc_info:
        _resolve_join_evidence_v3(request)

    err = exc_info.value
    assert marker not in err.code
    assert marker not in str(err)
    assert marker not in repr(err)


def test_sanitization_does_not_leak_in_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Sensitive marker must not appear in log output."""
    import logging

    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    marker = "secretrequestid"
    req_wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": marker,
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-1"],
        }
    ]
    request = _reclose_all_hashes(req_wire)

    with caplog.at_level(logging.ERROR, logger="release_sql_bot"):
        with pytest.raises(MetadataJoinEvidenceErrorV3):
            _resolve_join_evidence_v3(request)

    assert marker not in caplog.text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
