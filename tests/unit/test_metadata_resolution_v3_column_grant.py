"""Unit tests for V3 column-grant physical-reference resolution (DEV §5.3.1).

Tests the internal helper ``_resolve_column_grant_v3``: physical-identifier
indexing, case-sensitivity handling, duplicate detection, and the
column-grant → relation-grant → snapshot-relation → snapshot-column chain.

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataColumnResolutionErrorV3,
    MetadataResolutionInputErrorV3,
    _resolve_column_grant_v3,
)
from release_sql_bot.domain.project_bindings_v3 import (
    GovernedMetadataSnapshotV3,
    PhysicalColumnRefV3,
    ResolveMetadataRequestV3,
)
from tests.v3_metadata_support import (
    _APPROVAL_REF,
    _VALID_SHA,
    valid_resolve_metadata_request_v3_wire,
)

# Synthetic marker for sanitization tests
_SYNTHETIC_PRIVATE_MARKER = "SYNTHETIC_PRIVATE_MARKER"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_request():
    """Build a valid, internally-consistent request from the shared fixture."""
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3

    wire = valid_resolve_metadata_request_v3_wire()
    return ResolveMetadataRequestV3.model_validate(wire)


def _rebuild_snapshot_with_relations(relations, identifier_case_sensitivity="insensitive"):
    """Build a snapshot wire with custom relations and valid content hash."""
    from release_sql_bot.application.canonical import canonical_content_sha256

    wire = {
        "schemaVersion": "1.0.0",
        "snapshotId": "snap-1",
        "snapshotVersion": 1,
        "status": "approved",
        "dialect": "sqlserver",
        "identifierCaseSensitivity": identifier_case_sensitivity,
        "capturedAt": "2026-09-08T00:00:00+00:00",
        "sourceRef": {
            "sourceKind": "metadataReview",
            "artifactId": "artifact-1",
            "artifactVersion": "v1",
            "sha256": _VALID_SHA,
        },
        "relations": relations,
        "relationships": [],
        "approvalRef": dict(_APPROVAL_REF),
        "contentSha256": _VALID_SHA,
    }
    model = GovernedMetadataSnapshotV3.model_validate(wire)
    wire["contentSha256"] = canonical_content_sha256(model)
    return GovernedMetadataSnapshotV3.model_validate(wire)


def _rebuild_request_with_context(request, context):
    """Replace context in request, updating approval record's contextRef hash."""
    from release_sql_bot.application.canonical import canonical_content_sha256
    from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3

    context_wire = context.model_dump(by_alias=True, mode="json")
    ctx_sha = context_wire["contentSha256"]

    # Update approval record's contextRef to match new context hash
    approval_wire = request.approval_record.model_dump(by_alias=True, mode="json")
    approval_wire["contextRef"] = {
        "contextId": context.context_id,
        "contextVersion": context.context_version,
        "sha256": ctx_sha,
    }
    approval = ApprovalRecordV3.model_validate(approval_wire)
    approval_wire["contentSha256"] = canonical_content_sha256(approval)
    approval = ApprovalRecordV3.model_validate(approval_wire)

    # Rebuild request
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"] = context_wire
    req_wire["approvalRecord"] = approval.model_dump(by_alias=True, mode="json")

    return ResolveMetadataRequestV3.model_validate(req_wire)


def _rebuild_request_with_context_and_snapshot(request, context, snapshot):
    """Replace context and snapshot, updating all dependent hashes."""
    from release_sql_bot.application.canonical import canonical_content_sha256
    from release_sql_bot.domain.project_bindings_v3 import (
        ApprovalRecordV3,
        ProjectBindingContextV3,
    )

    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    snap_sha = snapshot_wire["contentSha256"]

    # Update context's metadataSnapshotRef and approvalRef
    context_wire = context.model_dump(by_alias=True, mode="json")
    context_wire["metadataSnapshotRef"] = {
        "snapshotId": snapshot.snapshot_id,
        "snapshotVersion": snapshot.snapshot_version,
        "sha256": snap_sha,
    }

    # Update approval record
    approval_wire = request.approval_record.model_dump(by_alias=True, mode="json")
    approval_wire["contextRef"] = {
        "contextId": context.context_id,
        "contextVersion": context.context_version,
        "sha256": "placeholder",  # will be updated after context hash is computed
    }
    approval_wire["snapshotRef"] = {
        "snapshotId": snapshot.snapshot_id,
        "snapshotVersion": snapshot.snapshot_version,
        "sha256": snap_sha,
    }

    # First pass: compute context hash with placeholder approvalRef
    context_wire["approvalRef"] = {
        "approvalId": approval_wire["approvalId"],
        "policyVersion": approval_wire["policyVersion"],
        "approvedAt": approval_wire["approvedAt"],
    }
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_wire = context.model_dump(by_alias=True, mode="json")
    ctx_sha = context_wire["contentSha256"]

    # Update approval with real context hash
    approval_wire["contextRef"]["sha256"] = ctx_sha
    approval = ApprovalRecordV3.model_validate(approval_wire)
    approval_wire["contentSha256"] = canonical_content_sha256(approval)
    approval = ApprovalRecordV3.model_validate(approval_wire)

    # Rebuild request
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"] = context_wire
    req_wire["metadataSnapshot"] = snapshot_wire
    req_wire["approvalRecord"] = approval.model_dump(by_alias=True, mode="json")

    return ResolveMetadataRequestV3.model_validate(req_wire)


def _make_request_with_snapshot(request, snapshot):
    """Replace snapshot in request, updating all dependent hashes."""
    from release_sql_bot.application.canonical import canonical_content_sha256
    from release_sql_bot.domain.project_bindings_v3 import (
        ApprovalRecordV3,
        ProjectBindingContextV3,
    )

    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    snap_sha = snapshot_wire["contentSha256"]

    # Update context's metadataSnapshotRef to point to new snapshot
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["metadataSnapshotRef"] = {
        "snapshotId": snapshot.snapshot_id,
        "snapshotVersion": snapshot.snapshot_version,
        "sha256": snap_sha,
    }
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_wire = context.model_dump(by_alias=True, mode="json")
    ctx_sha = context_wire["contentSha256"]

    # Update approval record with new context and snapshot refs
    approval_wire = request.approval_record.model_dump(by_alias=True, mode="json")
    approval_wire["contextRef"] = {
        "contextId": context.context_id,
        "contextVersion": context.context_version,
        "sha256": ctx_sha,
    }
    approval_wire["snapshotRef"] = {
        "snapshotId": snapshot.snapshot_id,
        "snapshotVersion": snapshot.snapshot_version,
        "sha256": snap_sha,
    }
    # Also update context's approvalRef to match
    context_wire["approvalRef"] = {
        "approvalId": approval_wire["approvalId"],
        "policyVersion": approval_wire["policyVersion"],
        "approvedAt": approval_wire["approvedAt"],
    }
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)
    context_wire = context.model_dump(by_alias=True, mode="json")
    ctx_sha = context_wire["contentSha256"]

    # Re-update approval with final context hash
    approval_wire["contextRef"]["sha256"] = ctx_sha
    approval = ApprovalRecordV3.model_validate(approval_wire)
    approval_wire["contentSha256"] = canonical_content_sha256(approval)
    approval = ApprovalRecordV3.model_validate(approval_wire)

    # Rebuild request
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"] = snapshot_wire
    req_wire["projectContext"] = context_wire
    req_wire["approvalRecord"] = approval.model_dump(by_alias=True, mode="json")

    return ResolveMetadataRequestV3.model_validate(req_wire)


# ===================================================================
# Section 1: Success path
# ===================================================================


def test_resolve_column_grant_returns_exact_physical_reference():
    """A valid column grant resolves to the exact three-part physical reference."""
    request = _make_valid_request()
    result = _resolve_column_grant_v3(request, "colgrant-value")

    assert isinstance(result, PhysicalColumnRefV3)
    assert result.schema_name == "dbo"
    assert result.relation_name == "synthetic_table"
    assert result.column_name == "synthetic_value"


def test_resolve_column_grant_returns_new_object():
    """The returned PhysicalColumnRefV3 is a new object; mutating it doesn't pollute input."""
    request = _make_valid_request()
    result = _resolve_column_grant_v3(request, "colgrant-value")

    # Mutate the result
    result.schema_name = "mutated_schema"
    result.relation_name = "mutated_relation"
    result.column_name = "mutated_column"

    # Original snapshot is unchanged
    assert request.metadata_snapshot.relations[0].schema_name == "dbo"
    assert request.metadata_snapshot.relations[0].relation_name == "synthetic_table"
    assert request.metadata_snapshot.relations[0].columns[0].column_name == "synthetic_value"


# ===================================================================
# Section 2: Input gate propagation
# ===================================================================


def test_input_gate_failure_preserved():
    """Input-gate failures propagate with their original code."""
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3

    request = _make_valid_request()
    # Tamper with projectRef to trigger PROJECT_REF_MISMATCH
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"


def test_input_gate_failure_skips_physical_resolution():
    """When input gate fails, no physical indexing or resolution happens."""
    from release_sql_bot.application.metadata_resolution_v3 import _build_snapshot_index
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3

    request = _make_valid_request()
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    # Spy on _build_snapshot_index using wraps to confirm it's NOT called
    with patch(
        "release_sql_bot.application.metadata_resolution_v3._build_snapshot_index",
        wraps=_build_snapshot_index,
    ) as spy_index:
        with pytest.raises(MetadataResolutionInputErrorV3):
            _resolve_column_grant_v3(tampered, "colgrant-value")
        assert spy_index.call_count == 0


# ===================================================================
# Section 3: Grant ID format validation
# ===================================================================


@pytest.mark.parametrize(
    "bad_id",
    [
        1245,  # not a string
        "",  # empty
        "  leading-space",  # leading space
        "trailing-space  ",  # trailing space
        "has space",  # internal space
        "has/slash",  # slash
        "has\\backslash",  # backslash
        "a" * 201,  # too long
    ],
)
def test_invalid_grant_id_format_rejected(bad_id):
    """Non-string, empty, whitespace-padded, illegal-char, and oversized IDs are rejected."""
    request = _make_valid_request()
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(request, bad_id)
    assert exc_info.value.code == "COLUMN_GRANT_ID_INVALID"


@pytest.mark.parametrize(
    "valid_id",
    [
        "a",
        "A1",
        "grant-1",
        "grant.2",
        "grant_3",
        "grant:4",
        "a" * 200,
    ],
)
def test_valid_grant_id_format_accepted(valid_id):
    """Valid boundary formats pass format validation and proceed to lookup."""
    request = _make_valid_request()
    # Use a valid format but non-existent ID — should fail at COLUMN_GRANT_ID_INVALID check
    # only if format is bad; otherwise should proceed to COLUMN_GRANT_NOT_FOUND
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(request, valid_id)
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


# ===================================================================
# Section 4: Missing references
# ===================================================================


def test_column_grant_not_found():
    """Non-existent column grant ID is rejected."""
    request = _make_valid_request()
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(request, "nonexistent-grant")
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


def test_relation_grant_not_found():
    """Column grant referencing non-existent relation grant is rejected."""
    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    request = _make_valid_request()
    # Add a column grant with a bad relationGrantId
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["columnGrants"] = [
        *context_wire["columnGrants"],
        {
            "grantId": "colgrant-bad-relation",
            "relationGrantId": "nonexistent-relation-grant",
            "columnName": "some_column",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-bad-relation")
    assert exc_info.value.code == "RELATION_GRANT_NOT_FOUND"


def test_relation_not_in_snapshot():
    """Relation grant referencing a relation not in snapshot is rejected."""
    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    request = _make_valid_request()
    # Add a relation grant for a non-existent relation
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        *context_wire["relationGrants"],
        {
            "grantId": "relgrant-missing",
            "schemaName": "missing_schema",
            "relationName": "missing_table",
            "access": "read",
        },
    ]
    # Add a column grant pointing to it
    context_wire["columnGrants"] = [
        *context_wire["columnGrants"],
        {
            "grantId": "colgrant-missing-rel",
            "relationGrantId": "relgrant-missing",
            "columnName": "some_column",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-missing-rel")
    assert exc_info.value.code == "RELATION_NOT_IN_SNAPSHOT"


def test_column_not_in_snapshot():
    """Column grant referencing a column not in snapshot is rejected."""
    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    request = _make_valid_request()
    # Add a column grant for a column that doesn't exist in the snapshot
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["columnGrants"] = [
        *context_wire["columnGrants"],
        {
            "grantId": "colgrant-missing-col",
            "relationGrantId": "relgrant-1",
            "columnName": "nonexistent_column",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-missing-col")
    assert exc_info.value.code == "COLUMN_NOT_IN_SNAPSHOT"


# ===================================================================
# Section 5: Case sensitivity
# ===================================================================


@pytest.mark.parametrize(
    "case_mode,changed_component",
    [
        ("sensitive", "schemaName"),
        ("sensitive", "relationName"),
        ("sensitive", "columnName"),
        ("insensitive", "schemaName"),
        ("insensitive", "relationName"),
        ("insensitive", "columnName"),
    ],
)
def test_case_mode_and_component_matrix(case_mode, changed_component):
    """Case-sensitivity matrix: mode × changed component.

    Snapshot uses original spelling (dbo/synthetic_table/synthetic_value).
    Grant uses a case-variant of exactly one component.
    Other two components remain identical to the snapshot.

    Expected:
    - sensitive: any single-component case mismatch fails
      (schemaName/relationName → RELATION_NOT_IN_SNAPSHOT,
       columnName → COLUMN_NOT_IN_SNAPSHOT)
    - insensitive: all succeed, returning exact snapshot spelling
    """
    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    # Snapshot with original spelling
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "synthetic_table",
            "relationKind": "table",
            "columns": [
                {"columnName": "synthetic_value", "sqlType": "int", "nullable": False},
            ],
        }
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, case_mode)

    # Build grant with exactly one component changed in case
    schema_name = "dbo"
    relation_name = "synthetic_table"
    column_name = "synthetic_value"

    if changed_component == "schemaName":
        schema_name = "DBO" if case_mode == "sensitive" else "DBO"
    elif changed_component == "relationName":
        relation_name = "SYNTHETIC_TABLE"
    elif changed_component == "columnName":
        column_name = "SYNTHETIC_VALUE"

    # Precondition: the changed component differs but casefolds equal
    original = {
        "schemaName": "dbo",
        "relationName": "synthetic_table",
        "columnName": "synthetic_value",
    }
    changed_val = {
        "schemaName": schema_name,
        "relationName": relation_name,
        "columnName": column_name,
    }
    assert changed_val[changed_component] != original[changed_component], (
        f"Expected {changed_component} to differ"
    )
    assert changed_val[changed_component].casefold() == original[changed_component].casefold(), (
        f"Expected {changed_component} to be casefold-equal"
    )
    # Other components must be identical
    for key in ("schemaName", "relationName", "columnName"):
        if key != changed_component:
            assert changed_val[key] == original[key], f"Expected {key} to be unchanged"

    request = _make_valid_request()
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": schema_name,
            "relationName": relation_name,
            "access": "read",
        }
    ]
    context_wire["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": column_name,
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context_and_snapshot(request, context, snapshot)

    # Verify input gate passes
    from release_sql_bot.application.metadata_resolution_v3 import _validate_resolution_input_v3

    _validate_resolution_input_v3(tampered)

    if case_mode == "sensitive":
        with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
            _resolve_column_grant_v3(tampered, "colgrant-value")
        if changed_component in ("schemaName", "relationName"):
            assert exc_info.value.code == "RELATION_NOT_IN_SNAPSHOT"
        else:
            assert exc_info.value.code == "COLUMN_NOT_IN_SNAPSHOT"
    else:
        # insensitive: should succeed with exact snapshot spelling
        result = _resolve_column_grant_v3(tampered, "colgrant-value")
        assert result.schema_name == "dbo"
        assert result.relation_name == "synthetic_table"
        assert result.column_name == "synthetic_value"


def test_casefold_vs_lower_unicode():
    """Uses str.casefold() not str.lower() for insensitive comparison.

    The German ß (U+00DF) casefolds to "ss" but lower() keeps it as "ß".
    """
    # Create a snapshot with a relation using ß in insensitive mode
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "straße",  # ß casefolds to "ss"
            "relationKind": "table",
            "columns": [
                {
                    "columnName": "id",
                    "sqlType": "int",
                    "nullable": False,
                },
            ],
        }
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    request = _make_valid_request()
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "strasse",  # "ss" should match "straße" via casefold
            "access": "read",
        }
    ]
    context_wire["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "id",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context_and_snapshot(request, context, snapshot)

    # Should succeed because casefold("straße") == casefold("strasse")
    result = _resolve_column_grant_v3(tampered, "colgrant-value")
    assert result.relation_name == "straße"  # exact snapshot spelling


# ===================================================================
# Section 6: Grant ID case sensitivity
# ===================================================================


def test_grant_id_case_sensitive():
    """Grant ID lookup is always case-sensitive, even in insensitive snapshots."""
    request = _make_valid_request()
    # colgrant-value exists, but COLGRANT-VALUE should not match
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(request, "COLGRANT-VALUE")
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


# ===================================================================
# Section 7: Duplicate detection
# ===================================================================


def test_duplicate_relation_ambiguous():
    """Duplicate relation keys in snapshot are rejected."""
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col1", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "table1",  # exact duplicate
            "relationKind": "table",
            "columns": [{"columnName": "col2", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    request = _make_valid_request()
    tampered = _make_request_with_snapshot(request, snapshot)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_RELATION_AMBIGUOUS"


def test_duplicate_relation_casefold_ambiguous():
    """In insensitive mode, case-fold collisions are ambiguous."""
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "MyTable",
            "relationKind": "table",
            "columns": [{"columnName": "col1", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "mytable",  # casefold collision
            "relationKind": "table",
            "columns": [{"columnName": "col2", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    request = _make_valid_request()
    tampered = _make_request_with_snapshot(request, snapshot)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_RELATION_AMBIGUOUS"


def test_duplicate_column_ambiguous():
    """Duplicate column keys within a relation are rejected."""
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "mytable",
            "relationKind": "table",
            "columns": [
                {"columnName": "col1", "sqlType": "int", "nullable": False},
                {
                    "columnName": "col1",  # exact duplicate
                    "sqlType": "int",
                    "nullable": False,
                },
            ],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    request = _make_valid_request()
    tampered = _make_request_with_snapshot(request, snapshot)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_COLUMN_AMBIGUOUS"


def test_column_casefold_collision_ambiguous():
    """In insensitive mode, case-fold collisions on column names are ambiguous."""
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "mytable",
            "relationKind": "table",
            "columns": [
                {"columnName": "Value", "sqlType": "int", "nullable": False},
                {"columnName": "value", "sqlType": "int", "nullable": False},  # casefold collision
            ],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    request = _make_valid_request()
    tampered = _make_request_with_snapshot(request, snapshot)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_COLUMN_AMBIGUOUS"


def test_column_case_sensitive_coexistence_and_exact_match():
    """In sensitive mode, columns differing only in case can coexist; grant matches exactly."""
    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    relations = [
        {
            "schemaName": "dbo",
            "relationName": "mytable",
            "relationKind": "table",
            "columns": [
                {"columnName": "Value", "sqlType": "int", "nullable": False},
                {"columnName": "value", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "sensitive")

    request = _make_valid_request()

    # Grant targeting "Value" (uppercase V)
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "mytable",
            "access": "read",
        },
    ]
    context_wire["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "Value",  # uppercase V
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context_and_snapshot(request, context, snapshot)

    # Should succeed and return the exact "Value" spelling
    result = _resolve_column_grant_v3(tampered, "colgrant-value")
    assert result.column_name == "Value"

    # Second call: target "value" (lowercase v) using the same snapshot
    context_wire2 = deepcopy(context_wire)
    context_wire2["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "value",  # lowercase v
        },
    ]
    context2 = ProjectBindingContextV3.model_validate(context_wire2)
    context_wire2["contentSha256"] = canonical_content_sha256(context2)
    context2 = ProjectBindingContextV3.model_validate(context_wire2)

    tampered2 = _rebuild_request_with_context_and_snapshot(request, context2, snapshot)

    # Verify input gate passes
    from release_sql_bot.application.metadata_resolution_v3 import _validate_resolution_input_v3

    _validate_resolution_input_v3(tampered2)

    result2 = _resolve_column_grant_v3(tampered2, "colgrant-value")
    assert result2.schema_name == "dbo"
    assert result2.relation_name == "mytable"
    assert result2.column_name == "value"


def test_sensitive_relation_case_coexistence():
    """In sensitive mode, relations differing only in case can coexist and resolve exactly."""
    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    # Two relations: TableA and tablea (same schema, different case)
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "TableA",
            "relationKind": "table",
            "columns": [{"columnName": "col_a", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "tablea",  # different case from TableA
            "relationKind": "table",
            "columns": [{"columnName": "col_aa", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "sensitive")

    request = _make_valid_request()

    # Grant targeting TableA (uppercase T, uppercase A)
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "TableA",
            "access": "read",
        },
    ]
    context_wire["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "col_a",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered_a = _rebuild_request_with_context_and_snapshot(request, context, snapshot)

    # Verify input gate passes
    from release_sql_bot.application.metadata_resolution_v3 import _validate_resolution_input_v3

    _validate_resolution_input_v3(tampered_a)

    # Should succeed and return exact "TableA" spelling
    result_a = _resolve_column_grant_v3(tampered_a, "colgrant-value")
    assert result_a.relation_name == "TableA"
    assert result_a.column_name == "col_a"

    # Now target tablea (lowercase t, lowercase a)
    context_wire2 = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire2["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "tablea",
            "access": "read",
        },
    ]
    context_wire2["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "col_aa",
        },
    ]
    context2 = ProjectBindingContextV3.model_validate(context_wire2)
    context_wire2["contentSha256"] = canonical_content_sha256(context2)
    context2 = ProjectBindingContextV3.model_validate(context_wire2)

    tampered_b = _rebuild_request_with_context_and_snapshot(request, context2, snapshot)
    result_b = _resolve_column_grant_v3(tampered_b, "colgrant-value")
    assert result_b.relation_name == "tablea"
    assert result_b.column_name == "col_aa"


def test_nontarget_relation_duplicate_detected():
    """Duplicate relation in a non-target relation is detected even when target is valid.

    Three actual calls: baseline (success) → duplicate (fail) → restored (success).
    """
    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    request = _make_valid_request()
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "target_table",
            "access": "read",
        },
    ]
    context_wire["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "target_col",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    # Step 1: baseline — no duplicate, target resolves successfully
    relations_baseline = [
        {
            "schemaName": "dbo",
            "relationName": "target_table",
            "relationKind": "table",
            "columns": [{"columnName": "target_col", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "dup_table",
            "relationKind": "table",
            "columns": [{"columnName": "col1", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot_baseline = _rebuild_snapshot_with_relations(relations_baseline, "insensitive")
    tampered_baseline = _rebuild_request_with_context_and_snapshot(
        request, context, snapshot_baseline
    )

    from release_sql_bot.application.metadata_resolution_v3 import _validate_resolution_input_v3

    _validate_resolution_input_v3(tampered_baseline)

    baseline_result = _resolve_column_grant_v3(tampered_baseline, "colgrant-value")
    assert baseline_result.relation_name == "target_table"
    assert baseline_result.column_name == "target_col"

    # Step 2: duplicate — add a second dup_table relation (non-target)
    relations_duplicate = deepcopy(relations_baseline)
    relations_duplicate.append(
        {
            "schemaName": "dbo",
            "relationName": "dup_table",  # duplicate relation key
            "relationKind": "table",
            "columns": [{"columnName": "col2", "sqlType": "int", "nullable": False}],
        }
    )
    snapshot_duplicate = _rebuild_snapshot_with_relations(relations_duplicate, "insensitive")
    tampered_duplicate = _rebuild_request_with_context_and_snapshot(
        request, context, snapshot_duplicate
    )

    _validate_resolution_input_v3(tampered_duplicate)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered_duplicate, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_RELATION_AMBIGUOUS"

    # Step 3: restored — remove the duplicate, target resolves successfully again
    relations_restored = deepcopy(relations_baseline)
    snapshot_restored = _rebuild_snapshot_with_relations(relations_restored, "insensitive")
    tampered_restored = _rebuild_request_with_context_and_snapshot(
        request, context, snapshot_restored
    )

    _validate_resolution_input_v3(tampered_restored)

    restored_result = _resolve_column_grant_v3(tampered_restored, "colgrant-value")
    assert restored_result.relation_name == baseline_result.relation_name
    assert restored_result.column_name == baseline_result.column_name


def test_nontarget_column_duplicate_detected():
    """Duplicate column in a non-target relation is detected even when target is valid.

    Three actual calls: baseline (success) → duplicate (fail) → restored (success).
    """
    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    request = _make_valid_request()
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "target_table",
            "access": "read",
        },
    ]
    context_wire["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "target_col",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    # Step 1: baseline — no duplicate column, target resolves successfully
    relations_baseline = [
        {
            "schemaName": "dbo",
            "relationName": "target_table",
            "relationKind": "table",
            "columns": [{"columnName": "target_col", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "other_table",
            "relationKind": "table",
            "columns": [{"columnName": "dup_col", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot_baseline = _rebuild_snapshot_with_relations(relations_baseline, "insensitive")
    tampered_baseline = _rebuild_request_with_context_and_snapshot(
        request, context, snapshot_baseline
    )

    from release_sql_bot.application.metadata_resolution_v3 import _validate_resolution_input_v3

    _validate_resolution_input_v3(tampered_baseline)

    baseline_result = _resolve_column_grant_v3(tampered_baseline, "colgrant-value")
    assert baseline_result.relation_name == "target_table"
    assert baseline_result.column_name == "target_col"

    # Step 2: duplicate — add a duplicate column to other_table (non-target)
    relations_duplicate = deepcopy(relations_baseline)
    # Find other_table and add a duplicate column
    for rel in relations_duplicate:
        if rel["relationName"] == "other_table":
            rel["columns"].append({"columnName": "dup_col", "sqlType": "int", "nullable": False})
    snapshot_duplicate = _rebuild_snapshot_with_relations(relations_duplicate, "insensitive")
    tampered_duplicate = _rebuild_request_with_context_and_snapshot(
        request, context, snapshot_duplicate
    )

    _validate_resolution_input_v3(tampered_duplicate)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered_duplicate, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_COLUMN_AMBIGUOUS"

    # Step 3: restored — remove the duplicate column, target resolves successfully again
    relations_restored = deepcopy(relations_baseline)
    snapshot_restored = _rebuild_snapshot_with_relations(relations_restored, "insensitive")
    tampered_restored = _rebuild_request_with_context_and_snapshot(
        request, context, snapshot_restored
    )

    _validate_resolution_input_v3(tampered_restored)

    restored_result = _resolve_column_grant_v3(tampered_restored, "colgrant-value")
    assert restored_result.relation_name == baseline_result.relation_name
    assert restored_result.column_name == baseline_result.column_name


@pytest.mark.parametrize(
    "order",
    [
        "AB",
        "BA",
    ],
)
def test_relation_ambiguity_detected_before_column_regardless_of_order(order):
    """Relation ambiguity must be detected before column ambiguity, regardless of array order.

    Regression test for BUG-20260910-02: the original implementation checked columns
    inside the relation loop, so a duplicate column in an earlier relation would mask
    a duplicate relation key in a later relation.

    - Relation A: has duplicate column (col1 appears twice)
    - Relation B: same relation key as A (dbo/table1), but no duplicate columns
    """
    relation_a = {
        "schemaName": "dbo",
        "relationName": "table1",
        "relationKind": "table",
        "columns": [
            {"columnName": "col1", "sqlType": "int", "nullable": False},
            {"columnName": "col1", "sqlType": "int", "nullable": False},  # duplicate
        ],
    }
    relation_b = {
        "schemaName": "dbo",
        "relationName": "table1",  # same key as A
        "relationKind": "table",
        "columns": [
            {"columnName": "col_unique", "sqlType": "int", "nullable": False},
        ],
    }

    if order == "AB":
        relations = [relation_a, relation_b]
    else:
        relations = [relation_b, relation_a]

    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    request = _make_valid_request()
    tampered = _make_request_with_snapshot(request, snapshot)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_RELATION_AMBIGUOUS", (
        f"Expected SNAPSHOT_RELATION_AMBIGUOUS for order={order}, got {exc_info.value.code}"
    )


def test_column_ambiguity_detected_after_relation_passes():
    """When relations are unique but a column is duplicated, SNAPSHOT_COLUMN_AMBIGUOUS is raised."""
    # Only relation A (with duplicate columns), no relation B
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [
                {"columnName": "col1", "sqlType": "int", "nullable": False},
                {"columnName": "col1", "sqlType": "int", "nullable": False},  # duplicate
            ],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    request = _make_valid_request()
    tampered = _make_request_with_snapshot(request, snapshot)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_COLUMN_AMBIGUOUS"


def test_same_column_name_different_relation_legal():
    """Same column name in different relations is legal."""
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "id", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "table2",
            "relationKind": "table",
            "columns": [{"columnName": "id", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    request = _make_valid_request()
    # The snapshot has table1 and table2, but the context's grants point to
    # synthetic_table. Rebuild the context to point to table1.
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "table1",
            "access": "read",
        },
    ]
    context_wire["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "id",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context_and_snapshot(request, context, snapshot)

    result = _resolve_column_grant_v3(tampered, "colgrant-value")
    assert result.relation_name == "table1"
    assert result.column_name == "id"


# ===================================================================
# Section 8: Cross-relation column lookup
# ===================================================================


def test_column_in_wrong_relation_rejected():
    """Column existing only in another relation is not found."""
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col_a", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "table2",
            "relationKind": "table",
            "columns": [{"columnName": "target_col", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    request = _make_valid_request()
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "table1",  # points to table1
            "access": "read",
        },
    ]
    context_wire["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "target_col",  # but target_col is in table2
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context_and_snapshot(request, context, snapshot)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "COLUMN_NOT_IN_SNAPSHOT"


# ===================================================================
# Section 9: Failure ordering
# ===================================================================


def test_input_gate_before_physical_error_bidirectional():
    """Input gate failure precedes physical errors; fixing input gate reveals physical error."""
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3

    # Build a snapshot with duplicate relations (physical error)
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col1", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col2", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    request = _make_valid_request()
    tampered = _make_request_with_snapshot(request, snapshot)

    # Step 1: Both errors present (input gate + physical) — input gate fires first
    wire = tampered.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    both_errors = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_column_grant_v3(both_errors, "colgrant-value")
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"

    # Step 2: Fix only the input gate error — physical error now fires
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_RELATION_AMBIGUOUS"


def test_invalid_id_before_snapshot_ambiguity_bidirectional():
    """Invalid grant ID format precedes snapshot ambiguity; fixing ID reveals ambiguity."""
    # Snapshot with duplicate relations
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col1", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col2", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    request = _make_valid_request()
    tampered = _make_request_with_snapshot(request, snapshot)

    # Step 1: Invalid ID — fails at format check
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "has space")
    assert exc_info.value.code == "COLUMN_GRANT_ID_INVALID"

    # Step 2: Fix ID to valid format — now hits snapshot ambiguity
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_RELATION_AMBIGUOUS"


def test_relation_ambiguity_before_column_ambiguity_bidirectional():
    """Relation ambiguity precedes column ambiguity; fixing relation reveals column ambiguity."""
    # Snapshot with BOTH duplicate relations AND duplicate columns (in a third relation)
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col1", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "table1",  # duplicate relation key
            "relationKind": "table",
            "columns": [{"columnName": "col2", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "table2",
            "relationKind": "table",
            "columns": [
                {"columnName": "dup_col", "sqlType": "int", "nullable": False},
                {"columnName": "dup_col", "sqlType": "int", "nullable": False},  # duplicate
            ],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    request = _make_valid_request()
    # Point grants to table2 (which has duplicate columns)
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "table2",
            "access": "read",
        },
    ]
    context_wire["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "dup_col",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context_and_snapshot(request, context, snapshot)

    # Step 1: Both errors present — relation ambiguity fires first
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_RELATION_AMBIGUOUS"

    # Step 2: Fix relation ambiguity (remove one table1) — column ambiguity now fires
    relations_fixed = [
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col1", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "table2",
            "relationKind": "table",
            "columns": [
                {"columnName": "dup_col", "sqlType": "int", "nullable": False},
                {"columnName": "dup_col", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    snapshot_fixed = _rebuild_snapshot_with_relations(relations_fixed, "insensitive")
    tampered_fixed = _make_request_with_snapshot(request, snapshot_fixed)
    # Re-point grants to table2
    context_wire2 = tampered_fixed.project_context.model_dump(by_alias=True, mode="json")
    context_wire2["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "table2",
            "access": "read",
        },
    ]
    context_wire2["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "dup_col",
        },
    ]
    context2 = ProjectBindingContextV3.model_validate(context_wire2)
    context_wire2["contentSha256"] = canonical_content_sha256(context2)
    context2 = ProjectBindingContextV3.model_validate(context_wire2)
    tampered_fixed = _rebuild_request_with_context_and_snapshot(request, context2, snapshot_fixed)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered_fixed, "colgrant-value")
    assert exc_info.value.code == "SNAPSHOT_COLUMN_AMBIGUOUS"


def test_snapshot_ambiguity_before_grant_not_found_bidirectional():
    """Snapshot ambiguity precedes grant-not-found; fixing snapshot reveals grant-not-found."""
    from release_sql_bot.domain.project_bindings_v3 import (
        ProjectBindingContextV3,
    )

    # Snapshot with duplicate relations
    relations = [
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col1", "sqlType": "int", "nullable": False}],
        },
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col2", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot = _rebuild_snapshot_with_relations(relations, "insensitive")

    request = _make_valid_request()
    # Point grants to table1 with col1
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["relationGrants"] = [
        {
            "grantId": "relgrant-1",
            "schemaName": "dbo",
            "relationName": "table1",
            "access": "read",
        },
    ]
    context_wire["columnGrants"] = [
        {
            "grantId": "colgrant-value",
            "relationGrantId": "relgrant-1",
            "columnName": "col1",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context_and_snapshot(request, context, snapshot)

    # Step 1: Both errors present (ambiguous snapshot + non-existent grant) — ambiguity fires first
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered, "nonexistent-grant")
    assert exc_info.value.code == "SNAPSHOT_RELATION_AMBIGUOUS"

    # Step 2: Fix snapshot ambiguity (remove duplicate) — grant-not-found now fires
    relations_fixed = [
        {
            "schemaName": "dbo",
            "relationName": "table1",
            "relationKind": "table",
            "columns": [{"columnName": "col1", "sqlType": "int", "nullable": False}],
        },
    ]
    snapshot_fixed = _rebuild_snapshot_with_relations(relations_fixed, "insensitive")
    tampered_fixed = _rebuild_request_with_context_and_snapshot(request, context, snapshot_fixed)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(tampered_fixed, "nonexistent-grant")
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


# ===================================================================
# Section 10: Input immutability
# ===================================================================


def test_input_not_mutated_on_success():
    """The actual input request is not mutated by a successful resolution."""
    request = _make_valid_request()
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    _resolve_column_grant_v3(request, "colgrant-value")
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_input_not_mutated_on_failure():
    """The actual input request is not mutated by a failed resolution."""
    request = _make_valid_request()
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    with pytest.raises(MetadataColumnResolutionErrorV3):
        _resolve_column_grant_v3(request, "nonexistent-grant")
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


# ===================================================================
# Section 11: Sensitive marker sanitization
# ===================================================================


def test_sensitive_marker_not_in_resolution_error(caplog: pytest.LogCaptureFixture) -> None:
    """Column-resolution errors with sensitive marker as grant ID do not leak the marker.

    Uses the marker itself as a valid-format but non-existent column_grant_id.
    """
    caplog.set_level(logging.DEBUG)
    request = _make_valid_request()

    # Precondition: marker is a valid grant ID format and not in the context grants
    import re

    assert re.match(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$", _SYNTHETIC_PRIVATE_MARKER)
    assert len(_SYNTHETIC_PRIVATE_MARKER) <= 200
    existing_ids = {cg.grant_id for cg in request.project_context.column_grants}
    assert _SYNTHETIC_PRIVATE_MARKER not in existing_ids

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_column_grant_v3(request, _SYNTHETIC_PRIVATE_MARKER)
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"
    assert _SYNTHETIC_PRIVATE_MARKER not in str(exc_info.value)
    assert _SYNTHETIC_PRIVATE_MARKER not in repr(exc_info.value)
    for record in caplog.records:
        assert _SYNTHETIC_PRIVATE_MARKER not in record.message


# ===================================================================
# Section 12: No infrastructure dependencies
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
