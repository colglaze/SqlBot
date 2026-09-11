"""Unit tests for V3 join-grant physical-reference resolution (DEV §5.3.6).

Tests the internal helper ``_resolve_join_grant_v3``: validating a
specified join grant, resolving both column grants, and verifying
exactly one snapshot relationship edge matches (undirected).

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

import logging
from copy import deepcopy

import pytest

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataColumnResolutionErrorV3,
    MetadataJoinResolutionErrorV3,
    MetadataResolutionInputErrorV3,
    _resolve_join_grant_v3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordV3,
    GovernedMetadataSnapshotV3,
    JoinTypeV3,
    ProjectBindingContextV3,
    ResolveMetadataRequestV3,
)
from tests.v3_metadata_support import valid_resolve_metadata_request_v3_wire

# Synthetic marker for sanitization tests — valid stable-ID format
_SYNTHETIC_PRIVATE_MARKER = "syntheticPrivateMarker"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_request() -> ResolveMetadataRequestV3:
    """Build a valid, internally-consistent request from the shared fixture."""
    wire = valid_resolve_metadata_request_v3_wire()
    return ResolveMetadataRequestV3.model_validate(wire)


def _reclose_all_hashes_from_wire(
    req_wire: dict[str, object],
) -> ResolveMetadataRequestV3:
    """Reclose snapshot/context/approval hashes from a modified wire dict."""
    snapshot = GovernedMetadataSnapshotV3.model_validate(req_wire["metadataSnapshot"])
    req_wire["metadataSnapshot"]["contentSha256"] = canonical_content_sha256(snapshot)
    snapshot = GovernedMetadataSnapshotV3.model_validate(req_wire["metadataSnapshot"])

    req_wire["projectContext"]["metadataSnapshotRef"]["sha256"] = req_wire["metadataSnapshot"][
        "contentSha256"
    ]
    context = ProjectBindingContextV3.model_validate(req_wire["projectContext"])
    req_wire["projectContext"]["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(req_wire["projectContext"])

    req_wire["approvalRecord"]["snapshotRef"]["sha256"] = req_wire["metadataSnapshot"][
        "contentSha256"
    ]
    req_wire["approvalRecord"]["contextRef"]["sha256"] = req_wire["projectContext"]["contentSha256"]
    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    req_wire["approvalRecord"]["contentSha256"] = canonical_content_sha256(approval)

    return ResolveMetadataRequestV3.model_validate(req_wire)


def _build_join_request(
    *,
    join_type: str = "inner",
    case_sensitive: bool = False,
    extra_relationships: list[dict[str, object]] | None = None,
) -> ResolveMetadataRequestV3:
    """Build a request with two relations, column grants, a join grant, and a relationship."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Add a second relation to the snapshot
    req_wire["metadataSnapshot"]["relations"] = [
        *req_wire["metadataSnapshot"]["relations"],
        {
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "relationKind": "table",
            "columns": [
                {
                    "columnName": "dim_key",
                    "sqlType": "nvarchar(100)",
                    "nullable": False,
                },
            ],
        },
    ]
    # Set case sensitivity
    req_wire["metadataSnapshot"]["identifierCaseSensitivity"] = (
        "sensitive" if case_sensitive else "insensitive"
    )

    # Add relation grant for the second relation
    req_wire["projectContext"]["relationGrants"] = [
        *req_wire["projectContext"]["relationGrants"],
        {
            "grantId": "relgrant-2",
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "access": "read",
        },
    ]
    # Add column grants for the join endpoints
    req_wire["projectContext"]["columnGrants"] = [
        *req_wire["projectContext"]["columnGrants"],
        {
            "grantId": "colgrant-left",
            "relationGrantId": "relgrant-1",
            "columnName": "synthetic_key",
        },
        {
            "grantId": "colgrant-right",
            "relationGrantId": "relgrant-2",
            "columnName": "dim_key",
        },
    ]
    # Add the join grant
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-1",
            "leftColumnGrantId": "colgrant-left",
            "rightColumnGrantId": "colgrant-right",
            "joinType": join_type,
        }
    ]
    # Add the relationship edge (synthetic_key ↔ dim_key)
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-join-1",
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
    if extra_relationships:
        req_wire["metadataSnapshot"]["relationships"].extend(extra_relationships)

    # Reclose snapshot/context/approval hashes
    snapshot = GovernedMetadataSnapshotV3.model_validate(req_wire["metadataSnapshot"])
    req_wire["metadataSnapshot"]["contentSha256"] = canonical_content_sha256(snapshot)
    snapshot = GovernedMetadataSnapshotV3.model_validate(req_wire["metadataSnapshot"])

    req_wire["projectContext"]["metadataSnapshotRef"]["sha256"] = req_wire["metadataSnapshot"][
        "contentSha256"
    ]
    context = ProjectBindingContextV3.model_validate(req_wire["projectContext"])
    req_wire["projectContext"]["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(req_wire["projectContext"])

    req_wire["approvalRecord"]["contextRef"]["sha256"] = req_wire["projectContext"]["contentSha256"]
    req_wire["approvalRecord"]["snapshotRef"]["sha256"] = req_wire["metadataSnapshot"][
        "contentSha256"
    ]
    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    req_wire["approvalRecord"]["contentSha256"] = canonical_content_sha256(approval)

    # Handoff payload hash uses canonical_sha256 (full payload, no exclusion)
    req_wire["handoffClosure"]["payload"] = req_wire["bindingRequest"]
    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire["bindingRequest"])
    )
    return ResolveMetadataRequestV3.model_validate(req_wire)


# ===================================================================
# Section A: Valid inner and left joins
# ===================================================================


def test_inner_join_resolves():
    """inner join resolves to correct physical endpoints and type."""
    request = _build_join_request(join_type="inner")
    left, right, join_type = _resolve_join_grant_v3(request, "join-1")

    assert left.schema_name == "dbo"
    assert left.relation_name == "synthetic_table"
    assert left.column_name == "synthetic_key"
    assert right.schema_name == "dbo"
    assert right.relation_name == "synthetic_dim"
    assert right.column_name == "dim_key"
    assert join_type is JoinTypeV3.INNER


def test_left_join_preserves_direction():
    """LEFT join preserves left/right direction from the grant."""
    request = _build_join_request(join_type="left")
    left, right, join_type = _resolve_join_grant_v3(request, "join-1")

    # Left must be synthetic_table (from colgrant-left), right must be synthetic_dim
    assert left.relation_name == "synthetic_table"
    assert left.column_name == "synthetic_key"
    assert right.relation_name == "synthetic_dim"
    assert right.column_name == "dim_key"
    assert join_type is JoinTypeV3.LEFT


# ===================================================================
# Section B: Reverse relationship still matches (parameterized)
# ===================================================================


@pytest.mark.parametrize("join_type", ["inner", "left"])
def test_reverse_relationship_matches_for_both_join_types(join_type):
    """Relationship stored as dim→fact still matches fact→dim grant.

    Parameterized over inner and left: LEFT must preserve direction
    even when the relationship is stored in reverse.
    """
    reverse_rel = {
        "relationshipId": "rel-join-reverse",
        "leftColumn": {
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "columnName": "dim_key",
        },
        "rightColumn": {
            "schemaName": "dbo",
            "relationName": "synthetic_table",
            "columnName": "synthetic_key",
        },
    }
    request = _build_join_request(join_type=join_type)
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"]["relationships"] = [reverse_rel]
    request = _reclose_all_hashes_from_wire(req_wire)

    left, right, jt = _resolve_join_grant_v3(request, "join-1")
    # Grant direction: left=colgrant-left→synthetic_table, right=colgrant-right→synthetic_dim
    assert left.schema_name == "dbo"
    assert left.relation_name == "synthetic_table"
    assert left.column_name == "synthetic_key"
    assert right.schema_name == "dbo"
    assert right.relation_name == "synthetic_dim"
    assert right.column_name == "dim_key"
    assert jt is JoinTypeV3(join_type)


# ===================================================================
# Section C: Invalid / missing grant ID
# ===================================================================


@pytest.mark.parametrize(
    "bad_id",
    ["", "   ", "invalid space", "-bad", "a" * 201, None, 123, True],
)
def test_invalid_grant_id_format(bad_id):
    """Invalid join_grant_id formats raise JOIN_GRANT_ID_INVALID.

    Includes non-string values (None, int, bool) to verify the type check.
    """
    request = _build_join_request()
    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, bad_id)
    assert exc_info.value.code == "JOIN_GRANT_ID_INVALID"


def test_grant_not_found():
    """A well-formed but non-existent grant ID raises JOIN_GRANT_NOT_FOUND."""
    request = _build_join_request()
    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "nonexistent-grant")
    assert exc_info.value.code == "JOIN_GRANT_NOT_FOUND"


# ===================================================================
# Section D: Relationship without join grant
# ===================================================================


def test_relationship_without_join_grant_rejected():
    """Having a relationship but no join grant is rejected.

    Starts from a successful request, clears only joinGrants while
    keeping the relationship and both column authorizations intact.
    """
    # Start from a fully valid request
    request = _build_join_request()
    left, right, jt = _resolve_join_grant_v3(request, "join-1")
    assert left.column_name == "synthetic_key"  # sanity: baseline succeeds

    # Clear only the join grants, keep everything else
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"]["joinGrants"] = []
    # Relationship must still be present
    assert len(req_wire["metadataSnapshot"]["relationships"]) > 0
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "JOIN_GRANT_NOT_FOUND"

    # Restore the grant and reclose → success
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-1",
            "leftColumnGrantId": "colgrant-left",
            "rightColumnGrantId": "colgrant-right",
            "joinType": "inner",
        }
    ]
    restored = _reclose_all_hashes_from_wire(req_wire)
    left, right, jt = _resolve_join_grant_v3(restored, "join-1")
    assert left.column_name == "synthetic_key"


# ===================================================================
# Section E: Column grant / snapshot column missing
# ===================================================================


def test_left_column_grant_missing():
    """Missing left column grant propagates COLUMN_GRANT_NOT_FOUND."""
    request = _build_join_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"]["joinGrants"][0]["leftColumnGrantId"] = "nonexistent-cg"
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


def test_snapshot_column_missing():
    """Target snapshot column missing propagates COLUMN_NOT_IN_SNAPSHOT."""
    request = _build_join_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    for cg in req_wire["projectContext"]["columnGrants"]:
        if cg["grantId"] == "colgrant-right":
            cg["columnName"] = "nonexistent_column"
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "COLUMN_NOT_IN_SNAPSHOT"


def test_left_failure_before_right_dual_error():
    """Left column grant failure is detected before right when both errors coexist.

    Left: points to a non-existent grant → COLUMN_GRANT_NOT_FOUND.
    Right: column grant exists but columnName points to a missing snapshot
    column → COLUMN_NOT_IN_SNAPSHOT.

    Both errors present: must report COLUMN_GRANT_NOT_FOUND first.
    Fixing only the left must then report COLUMN_NOT_IN_SNAPSHOT.
    Fixing both must succeed.
    """
    request = _build_join_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    # Left error: non-existent grant
    req_wire["projectContext"]["joinGrants"][0]["leftColumnGrantId"] = "nonexistent-cg"
    # Right error: missing snapshot column
    for cg in req_wire["projectContext"]["columnGrants"]:
        if cg["grantId"] == "colgrant-right":
            cg["columnName"] = "nonexistent_column"
    dual_error = _reclose_all_hashes_from_wire(req_wire)

    # Both errors present → left (COLUMN_GRANT_NOT_FOUND) wins
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(dual_error, "join-1")
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"

    # Fix only the left grant → right error surfaces
    req_wire["projectContext"]["joinGrants"][0]["leftColumnGrantId"] = "colgrant-left"
    fixed_left = _reclose_all_hashes_from_wire(req_wire)
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(fixed_left, "join-1")
    assert exc_info.value.code == "COLUMN_NOT_IN_SNAPSHOT"

    # Fix both → success
    for cg in req_wire["projectContext"]["columnGrants"]:
        if cg["grantId"] == "colgrant-right":
            cg["columnName"] = "dim_key"
    fixed_both = _reclose_all_hashes_from_wire(req_wire)
    left, right, jt = _resolve_join_grant_v3(fixed_both, "join-1")
    assert left.column_name == "synthetic_key"
    assert right.column_name == "dim_key"


# ===================================================================
# Section F: Grant exists but relationship missing
# ===================================================================


def test_grant_exists_but_relationship_missing():
    """Join grant exists but no matching relationship raises JOIN_RELATIONSHIP_NOT_FOUND."""
    request = _build_join_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"]["relationships"] = []
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "JOIN_RELATIONSHIP_NOT_FOUND"


# ===================================================================
# Section G: Ambiguous relationships (order-independent + case-insensitive)
# ===================================================================


def test_duplicate_same_direction_relationship_rejected():
    """Two identical-direction relationships raise JOIN_RELATIONSHIP_AMBIGUOUS."""
    request = _build_join_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"]["relationships"].append(
        deepcopy(req_wire["metadataSnapshot"]["relationships"][0])
    )
    req_wire["metadataSnapshot"]["relationships"][1]["relationshipId"] = "rel-dup"
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "JOIN_RELATIONSHIP_AMBIGUOUS"


def test_duplicate_same_direction_order_independent():
    """Ambiguity is detected regardless of relationship array order."""
    request = _build_join_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"]["relationships"].append(
        deepcopy(req_wire["metadataSnapshot"]["relationships"][0])
    )
    req_wire["metadataSnapshot"]["relationships"][1]["relationshipId"] = "rel-dup"
    # Reverse the order
    req_wire["metadataSnapshot"]["relationships"] = list(
        reversed(req_wire["metadataSnapshot"]["relationships"])
    )
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "JOIN_RELATIONSHIP_AMBIGUOUS"


def test_reverse_duplicate_relationship_rejected():
    """A reverse-direction duplicate also raises JOIN_RELATIONSHIP_AMBIGUOUS."""
    reverse_rel = {
        "relationshipId": "rel-reverse",
        "leftColumn": {
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "columnName": "dim_key",
        },
        "rightColumn": {
            "schemaName": "dbo",
            "relationName": "synthetic_table",
            "columnName": "synthetic_key",
        },
    }
    request = _build_join_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"]["relationships"].append(reverse_rel)
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "JOIN_RELATIONSHIP_AMBIGUOUS"


def test_reverse_duplicate_order_independent():
    """Reverse ambiguity is detected regardless of relationship array order."""
    reverse_rel = {
        "relationshipId": "rel-reverse",
        "leftColumn": {
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "columnName": "dim_key",
        },
        "rightColumn": {
            "schemaName": "dbo",
            "relationName": "synthetic_table",
            "columnName": "synthetic_key",
        },
    }
    request = _build_join_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"]["relationships"].append(reverse_rel)
    # Reverse the order
    req_wire["metadataSnapshot"]["relationships"] = list(
        reversed(req_wire["metadataSnapshot"]["relationships"])
    )
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "JOIN_RELATIONSHIP_AMBIGUOUS"


def test_case_insensitive_duplicate_relationship_rejected():
    """In insensitive mode, a relationship differing only in case is a duplicate.

    Uses a different relationshipId so it is not rejected by the unique-ID
    check before reaching the edge-matching logic.
    """
    request = _build_join_request(join_type="inner", case_sensitive=False)
    req_wire = request.model_dump(by_alias=True, mode="json")
    # Add a second relationship that normalizes to the same edge but with
    # different casing and a different ID
    case_variant = {
        "relationshipId": "rel-case-variant",
        "leftColumn": {
            "schemaName": "DBO",
            "relationName": "SYNTHETIC_TABLE",
            "columnName": "SYNTHETIC_KEY",
        },
        "rightColumn": {
            "schemaName": "DBO",
            "relationName": "SYNTHETIC_DIM",
            "columnName": "DIM_KEY",
        },
    }
    req_wire["metadataSnapshot"]["relationships"].append(case_variant)
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "JOIN_RELATIONSHIP_AMBIGUOUS"


# ===================================================================
# Section H: Case sensitivity
# ===================================================================


def test_insensitive_casefold_matches():
    """insensitive mode matches identifiers differing only in case."""
    request = _build_join_request(join_type="inner", case_sensitive=False)
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"]["relationships"][0]["leftColumn"]["relationName"] = (
        "SYNTHETIC_TABLE"
    )
    request = _reclose_all_hashes_from_wire(req_wire)

    left, right, join_type = _resolve_join_grant_v3(request, "join-1")
    assert left.column_name == "synthetic_key"
    assert join_type is JoinTypeV3.INNER


def test_sensitive_case_mismatch_rejected():
    """sensitive mode rejects identifiers differing only in case."""
    request = _build_join_request(join_type="inner", case_sensitive=True)
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"]["relationships"][0]["leftColumn"]["relationName"] = (
        "SYNTHETIC_TABLE"
    )
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "JOIN_RELATIONSHIP_NOT_FOUND"


# ===================================================================
# Section I: Identical endpoints rejected
# ===================================================================


def test_identical_endpoints_rejected():
    """Two column grants resolving to the same physical column are rejected."""
    request = _build_join_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    for cg in req_wire["projectContext"]["columnGrants"]:
        if cg["grantId"] == "colgrant-right":
            cg["relationGrantId"] = "relgrant-1"
            cg["columnName"] = "synthetic_key"
    req_wire["metadataSnapshot"]["relationships"] = []
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, "join-1")
    assert exc_info.value.code == "JOIN_ENDPOINTS_IDENTICAL"


# ===================================================================
# Section J: Input gate priority (dual error)
# ===================================================================


def test_input_gate_failure_priority_dual_error():
    """Input-gate failures propagate before join checks.

    Both projectRef mismatch and illegal grant ID are present.
    Must report PROJECT_REF_MISMATCH first.
    Fixing only projectRef must then report JOIN_GRANT_ID_INVALID.
    """
    request = _build_join_request()
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    # Both errors present → input gate wins
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_join_grant_v3(tampered, "invalid id with space")
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"

    # Fix only projectRef → grant ID error surfaces
    wire["projectRef"] = {"projectId": "proj-1", "projectVersion": 1}
    fixed_ref = ResolveMetadataRequestV3.model_validate(wire)
    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(fixed_ref, "invalid id with space")
    assert exc_info.value.code == "JOIN_GRANT_ID_INVALID"


# ===================================================================
# Section K: Immutability
# ===================================================================


def test_input_not_mutated_on_success():
    """The actual input is not mutated by a successful join resolution."""
    request = _build_join_request()
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    _resolve_join_grant_v3(request, "join-1")
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_input_not_mutated_on_failure():
    """The actual input is not mutated by a failed join resolution."""
    request = _build_join_request()
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    with pytest.raises(MetadataJoinResolutionErrorV3):
        _resolve_join_grant_v3(request, "nonexistent-grant")
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before, "join resolver mutated its input"


# ===================================================================
# Section L: Exception sanitization
# ===================================================================


def test_grant_not_found_does_not_leak_marker(caplog: pytest.LogCaptureFixture) -> None:
    """JOIN_GRANT_NOT_FOUND error and logs contain no sensitive marker.

    Uses a synthetic marker that conforms to the stable-ID format as the
    non-existent grant ID, and verifies it is not leaked.
    """
    caplog.set_level(logging.DEBUG)
    request = _build_join_request()
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _resolve_join_grant_v3(request, _SYNTHETIC_PRIVATE_MARKER)
    assert exc_info.value.code == "JOIN_GRANT_NOT_FOUND"
    assert _SYNTHETIC_PRIVATE_MARKER not in str(exc_info.value)
    assert _SYNTHETIC_PRIVATE_MARKER not in repr(exc_info.value)
    for record in caplog.records:
        assert _SYNTHETIC_PRIVATE_MARKER not in record.message

    # Input not mutated
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before, "join resolver mutated its input"
