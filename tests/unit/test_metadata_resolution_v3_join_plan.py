"""Unit tests for V3 JOIN-plan resolution (DEV §5.3.6 ext).

Tests the internal helper ``_resolve_joins_v3``: constructing a restricted
authorization connection plan using BFS from the factValue relation.

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
    MetadataJoinClosureErrorV3,
    MetadataJoinEvidenceErrorV3,
    MetadataJoinPlanErrorV3,
    MetadataResolutionInputErrorV3,
    _resolve_joins_v3,
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
    wire = valid_resolve_metadata_request_v3_wire()
    return ResolveMetadataRequestV3.model_validate(wire)


def _add_entity_key_to_wire(req_wire: dict) -> None:
    """Add syntheticKey field, field binding, and entity key authorization."""
    base_req_id = req_wire["bindingRequest"]["requestId"]
    # Add syntheticKey field if not present
    existing_field_ids = {
        f["fieldId"] for f in req_wire["bindingRequest"]["queryRequirements"]["fields"]
    }
    if "syntheticKey" not in existing_field_ids:
        req_wire["bindingRequest"]["queryRequirements"]["fields"].append(
            {
                "fieldId": "syntheticKey",
                "role": "entityKey",
                "logicalName": "synthetic_entity_key",
                "dataType": "string",
                "required": True,
                "evidenceIds": ["ev-fact-declaration"],
            }
        )
    # Clear and rebuild field bindings for syntheticKey
    req_wire["projectContext"]["fieldBindingAuthorizations"] = [
        fb
        for fb in req_wire["projectContext"]["fieldBindingAuthorizations"]
        if fb["fieldId"] != "syntheticKey"
    ]
    # Find the factValue's relation grant (entity key must be in same relation)
    fact_value_field = None
    for f in req_wire["bindingRequest"]["queryRequirements"]["fields"]:
        if f["fieldId"] == "factValue":
            fact_value_field = f
            break
    # Find the field binding for factValue to get its relation
    fact_rel_grant = None
    if fact_value_field:
        for fb in req_wire["projectContext"]["fieldBindingAuthorizations"]:
            if fb["fieldId"] == "factValue":
                for cg in req_wire["projectContext"]["columnGrants"]:
                    if cg["grantId"] == fb["columnGrantId"]:
                        fact_rel_grant = cg["relationGrantId"]
                        break
                break
    # Find a column grant in the factValue's relation for the entity key
    first_rel_grant = fact_rel_grant or req_wire["projectContext"]["relationGrants"][0]["grantId"]
    key_col = None
    for cg in req_wire["projectContext"]["columnGrants"]:
        if cg["relationGrantId"] == first_rel_grant and "key" in cg["columnName"].lower():
            key_col = cg["grantId"]
            break
    if key_col:
        req_wire["projectContext"]["fieldBindingAuthorizations"].append(
            {
                "authorizationId": "fba-key",
                "requestId": base_req_id,
                "fieldId": "syntheticKey",
                "role": "entityKey",
                "columnGrantId": key_col,
            }
        )
    # Clear and rebuild entity key authorizations
    req_wire["projectContext"]["entityKeyAuthorizations"] = [
        {
            "authorizationId": "eka-1",
            "requestId": base_req_id,
            "parameterName": "syntheticKey",
            "fieldId": "syntheticKey",
            "columnGrantId": key_col,
        }
    ]


def _reclose_context_only(req_wire: dict) -> ResolveMetadataRequestV3:
    """Reclose context/approval/handoff hashes from a modified wire."""
    from release_sql_bot.application.canonical import (
        canonical_content_sha256,
        canonical_sha256,
    )
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
    from release_sql_bot.domain.project_bindings_v3 import (
        ApprovalRecordV3,
    )

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
    payload_model = FactBindingRequestV3.model_validate(req_wire["handoffClosure"]["payload"])
    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(payload_model)
    return ResolveMetadataRequestV3.model_validate(req_wire)


def _build_two_relation_inner_request():
    """Build a valid request with 2 relations and 1 INNER JOIN grant."""
    wire = valid_resolve_metadata_request_v3_wire()
    req_wire = ResolveMetadataRequestV3.model_validate(wire).model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    # Add second relation
    req_wire["metadataSnapshot"]["relations"] = [
        {
            "schemaName": "dbo",
            "relationName": "fact_table",
            "relationKind": "table",
            "columns": [
                {"columnName": "fact_id", "sqlType": "int", "nullable": False},
                {"columnName": "fact_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "dim_table",
            "relationKind": "table",
            "columns": [
                {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "dim_value", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    # Relationship edge
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-1",
            "leftColumn": {
                "schemaName": "dbo",
                "relationName": "fact_table",
                "columnName": "fact_key",
            },
            "rightColumn": {
                "schemaName": "dbo",
                "relationName": "dim_table",
                "columnName": "dim_key",
            },
        }
    ]
    # Relation grants
    req_wire["projectContext"]["relationGrants"] = [
        {
            "grantId": "relgrant-fact",
            "schemaName": "dbo",
            "relationName": "fact_table",
            "access": "read",
        },
        {
            "grantId": "relgrant-dim",
            "schemaName": "dbo",
            "relationName": "dim_table",
            "access": "read",
        },
    ]
    # Column grants
    req_wire["projectContext"]["columnGrants"] = [
        {
            "grantId": "colgrant-fact-id",
            "relationGrantId": "relgrant-fact",
            "columnName": "fact_id",
        },
        {
            "grantId": "colgrant-fact-key",
            "relationGrantId": "relgrant-fact",
            "columnName": "fact_key",
        },
        {"grantId": "colgrant-dim-key", "relationGrantId": "relgrant-dim", "columnName": "dim_key"},
        {
            "grantId": "colgrant-dim-value",
            "relationGrantId": "relgrant-dim",
            "columnName": "dim_value",
        },
    ]
    # Join grant (INNER)
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-1",
            "leftColumnGrantId": "colgrant-fact-key",
            "rightColumnGrantId": "colgrant-dim-key",
            "joinType": "inner",
        }
    ]
    # Fields: keep factValue + syntheticKey, add dimValue
    fact_code = req_wire["bindingRequest"]["fact"]["factCode"]
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        {
            "fieldId": "factValue",
            "role": "value",
            "logicalName": fact_code,
            "dataType": "integer",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "fieldId": "syntheticKey",
            "role": "entityKey",
            "logicalName": "synthetic_entity_key",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "fieldId": "dimValue",
            "role": "value",
            "logicalName": "dim_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    # Field bindings (keep original + add new)
    req_wire["projectContext"]["fieldBindingAuthorizations"] = [
        {
            "authorizationId": "fba-value",
            "requestId": base_req_id,
            "fieldId": "factValue",
            "role": "value",
            "columnGrantId": "colgrant-fact-id",
        },
        {
            "authorizationId": "fba-key",
            "requestId": base_req_id,
            "fieldId": "syntheticKey",
            "role": "entityKey",
            "columnGrantId": "colgrant-fact-key",
        },
        {
            "authorizationId": "fba-dim",
            "requestId": base_req_id,
            "fieldId": "dimValue",
            "role": "value",
            "columnGrantId": "colgrant-dim-value",
        },
    ]
    # Entity grain + entity key auth
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-fact",
        }
    ]
    req_wire["projectContext"]["entityKeyAuthorizations"] = [
        {
            "authorizationId": "eka-1",
            "requestId": base_req_id,
            "parameterName": "syntheticKey",
            "fieldId": "syntheticKey",
            "columnGrantId": "colgrant-fact-key",
        }
    ]
    # Sync handoffClosure payload
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])

    # Close hashes (without evidence)
    request = _reclose_context_only(req_wire)

    # Add evidence
    wire = request.model_dump(by_alias=True, mode="json")
    payload_hash = wire["handoffClosure"]["payloadSha256"]
    wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement"],
        }
    ]
    return _reclose_context_only(wire)


# ===================================================================
# Section 1: Single relation, no joins needed
# ===================================================================


def test_single_relation_returns_empty() -> None:
    """Single relation with no joins → empty result."""
    request = _make_valid_request()
    result = _resolve_joins_v3(request)
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
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    request = _reclose_context_only(req_wire)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_joins_v3(request)
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


# ===================================================================
# Section 2: Two relations, INNER JOIN
# ===================================================================


def test_two_relation_inner_join_success() -> None:
    """Two relations with INNER JOIN → complete output wire."""
    request = _build_two_relation_inner_request()
    result = _resolve_joins_v3(request)

    assert len(result) == 1
    result_wire = result[0].model_dump(by_alias=True, mode="json")

    expected_wire = {
        "joinGrantId": "join-1",
        "leftSchemaName": "dbo",
        "leftRelationName": "fact_table",
        "leftColumnName": "fact_key",
        "rightSchemaName": "dbo",
        "rightRelationName": "dim_table",
        "rightColumnName": "dim_key",
        "joinType": "inner",
        "evidenceIds": ["ev-query-requirement"],
    }

    assert result_wire == expected_wire


# ===================================================================
# Section 3: LEFT JOIN direction
# ===================================================================


def test_left_join_base_a_success() -> None:
    """Base=A, A LEFT JOIN B → success."""
    # Build a 2-relation request with LEFT JOIN
    wire = valid_resolve_metadata_request_v3_wire()
    req_wire = ResolveMetadataRequestV3.model_validate(wire).model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    req_wire["metadataSnapshot"]["relations"] = [
        {
            "schemaName": "dbo",
            "relationName": "table_a",
            "relationKind": "table",
            "columns": [
                {"columnName": "a_id", "sqlType": "int", "nullable": False},
                {"columnName": "a_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_b",
            "relationKind": "table",
            "columns": [
                {"columnName": "b_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "b_value", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-1",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_a", "columnName": "a_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_b", "columnName": "b_key"},
        }
    ]
    req_wire["projectContext"]["relationGrants"] = [
        {"grantId": "relgrant-a", "schemaName": "dbo", "relationName": "table_a", "access": "read"},
        {"grantId": "relgrant-b", "schemaName": "dbo", "relationName": "table_b", "access": "read"},
    ]
    req_wire["projectContext"]["columnGrants"] = [
        {"grantId": "colgrant-a-id", "relationGrantId": "relgrant-a", "columnName": "a_id"},
        {"grantId": "colgrant-a-key", "relationGrantId": "relgrant-a", "columnName": "a_key"},
        {"grantId": "colgrant-b-key", "relationGrantId": "relgrant-b", "columnName": "b_key"},
        {"grantId": "colgrant-b-value", "relationGrantId": "relgrant-b", "columnName": "b_value"},
    ]
    # LEFT JOIN: table_a LEFT JOIN table_b
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-1",
            "leftColumnGrantId": "colgrant-a-key",
            "rightColumnGrantId": "colgrant-b-key",
            "joinType": "left",
        }
    ]
    fact_code = req_wire["bindingRequest"]["fact"]["factCode"]
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        {
            "fieldId": "factValue",
            "role": "value",
            "logicalName": fact_code,
            "dataType": "integer",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "fieldId": "bValue",
            "role": "value",
            "logicalName": "b_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    req_wire["projectContext"]["fieldBindingAuthorizations"] = [
        {
            "authorizationId": "fba-a",
            "requestId": base_req_id,
            "fieldId": "factValue",
            "role": "value",
            "columnGrantId": "colgrant-a-id",
        },
        {
            "authorizationId": "fba-b",
            "requestId": base_req_id,
            "fieldId": "bValue",
            "role": "value",
            "columnGrantId": "colgrant-b-value",
        },
    ]
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-a",
        }
    ]
    _add_entity_key_to_wire(req_wire)
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    request = _reclose_context_only(req_wire)

    # Add evidence
    wire = request.model_dump(by_alias=True, mode="json")
    payload_hash = wire["handoffClosure"]["payloadSha256"]
    wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement"],
        }
    ]
    request = _reclose_context_only(wire)

    result = _resolve_joins_v3(request)
    assert len(result) == 1
    assert result[0].join_grant_id == "join-1"
    assert str(result[0].join_type) == "left"
    # Direction preserved: left=table_a, right=table_b
    assert result[0].left_relation_name == "table_a"
    assert result[0].right_relation_name == "table_b"


def test_left_join_base_b_conflict() -> None:
    """Base=B, A LEFT JOIN B → JOIN_PLAN_DIRECTION_CONFLICT."""
    # Build a 2-relation request with LEFT JOIN where factValue is in table_b
    wire = valid_resolve_metadata_request_v3_wire()
    req_wire = ResolveMetadataRequestV3.model_validate(wire).model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    req_wire["metadataSnapshot"]["relations"] = [
        {
            "schemaName": "dbo",
            "relationName": "table_a",
            "relationKind": "table",
            "columns": [
                {"columnName": "a_id", "sqlType": "int", "nullable": False},
                {"columnName": "a_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_b",
            "relationKind": "table",
            "columns": [
                {"columnName": "b_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "b_value", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-1",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_a", "columnName": "a_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_b", "columnName": "b_key"},
        }
    ]
    req_wire["projectContext"]["relationGrants"] = [
        {"grantId": "relgrant-a", "schemaName": "dbo", "relationName": "table_a", "access": "read"},
        {"grantId": "relgrant-b", "schemaName": "dbo", "relationName": "table_b", "access": "read"},
    ]
    req_wire["projectContext"]["columnGrants"] = [
        {"grantId": "colgrant-a-id", "relationGrantId": "relgrant-a", "columnName": "a_id"},
        {"grantId": "colgrant-a-key", "relationGrantId": "relgrant-a", "columnName": "a_key"},
        {"grantId": "colgrant-b-key", "relationGrantId": "relgrant-b", "columnName": "b_key"},
        {"grantId": "colgrant-b-value", "relationGrantId": "relgrant-b", "columnName": "b_value"},
    ]
    # LEFT JOIN: table_a LEFT JOIN table_b
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-1",
            "leftColumnGrantId": "colgrant-a-key",
            "rightColumnGrantId": "colgrant-b-key",
            "joinType": "left",
        }
    ]
    fact_code = req_wire["bindingRequest"]["fact"]["factCode"]
    # factValue is in table_b (the "right" side of the LEFT JOIN)
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        {
            "fieldId": "factValue",
            "role": "value",
            "logicalName": fact_code,
            "dataType": "integer",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "fieldId": "aValue",
            "role": "value",
            "logicalName": "a_id",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    req_wire["projectContext"]["fieldBindingAuthorizations"] = [
        {
            "authorizationId": "fba-b",
            "requestId": base_req_id,
            "fieldId": "factValue",
            "role": "value",
            "columnGrantId": "colgrant-b-value",
        },
        {
            "authorizationId": "fba-a",
            "requestId": base_req_id,
            "fieldId": "aValue",
            "role": "value",
            "columnGrantId": "colgrant-a-id",
        },
    ]
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-b",
        }
    ]
    _add_entity_key_to_wire(req_wire)
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    request = _reclose_context_only(req_wire)

    # Add evidence
    wire = request.model_dump(by_alias=True, mode="json")
    payload_hash = wire["handoffClosure"]["payloadSha256"]
    wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement"],
        }
    ]
    request = _reclose_context_only(wire)

    with pytest.raises(MetadataJoinPlanErrorV3) as exc_info:
        _resolve_joins_v3(request)
    assert exc_info.value.code == "JOIN_PLAN_DIRECTION_CONFLICT"


# ===================================================================
# Section 4: Three-relation chain (BFS, not global grantId sort)
# ===================================================================


def _build_chain_request(
    *,
    join_types: tuple[str, str] = ("inner", "inner"),
    evidence_ids_list: list[list[str]] | None = None,
):
    """Build a 3-relation chain: A → B → C.

    Args:
        join_types: (A→B type, B→C type)
        evidence_ids_list: Optional evidence IDs per grant
    """
    wire = valid_resolve_metadata_request_v3_wire()
    req_wire = ResolveMetadataRequestV3.model_validate(wire).model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    req_wire["metadataSnapshot"]["relations"] = [
        {
            "schemaName": "dbo",
            "relationName": "table_a",
            "relationKind": "table",
            "columns": [
                {"columnName": "a_id", "sqlType": "int", "nullable": False},
                {"columnName": "a_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_b",
            "relationKind": "table",
            "columns": [
                {"columnName": "b_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "b_value", "sqlType": "int", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_c",
            "relationKind": "table",
            "columns": [
                {"columnName": "c_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "c_value", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-ab",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_a", "columnName": "a_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_b", "columnName": "b_key"},
        },
        {
            "relationshipId": "rel-bc",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_b", "columnName": "b_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_c", "columnName": "c_key"},
        },
    ]
    req_wire["projectContext"]["relationGrants"] = [
        {"grantId": "relgrant-a", "schemaName": "dbo", "relationName": "table_a", "access": "read"},
        {"grantId": "relgrant-b", "schemaName": "dbo", "relationName": "table_b", "access": "read"},
        {"grantId": "relgrant-c", "schemaName": "dbo", "relationName": "table_c", "access": "read"},
    ]
    req_wire["projectContext"]["columnGrants"] = [
        {"grantId": "colgrant-a-id", "relationGrantId": "relgrant-a", "columnName": "a_id"},
        {"grantId": "colgrant-a-key", "relationGrantId": "relgrant-a", "columnName": "a_key"},
        {"grantId": "colgrant-b-key", "relationGrantId": "relgrant-b", "columnName": "b_key"},
        {"grantId": "colgrant-b-value", "relationGrantId": "relgrant-b", "columnName": "b_value"},
        {"grantId": "colgrant-c-key", "relationGrantId": "relgrant-c", "columnName": "c_key"},
        {"grantId": "colgrant-c-value", "relationGrantId": "relgrant-c", "columnName": "c_value"},
    ]
    # Grant IDs deliberately reversed: join-z (A→B) > join-a (B→C)
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-z",
            "leftColumnGrantId": "colgrant-a-key",
            "rightColumnGrantId": "colgrant-b-key",
            "joinType": join_types[0],
        },
        {
            "grantId": "join-a",
            "leftColumnGrantId": "colgrant-b-key",
            "rightColumnGrantId": "colgrant-c-key",
            "joinType": join_types[1],
        },
    ]
    fact_code = req_wire["bindingRequest"]["fact"]["factCode"]
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        {
            "fieldId": "factValue",
            "role": "value",
            "logicalName": fact_code,
            "dataType": "integer",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "fieldId": "bValue",
            "role": "value",
            "logicalName": "b_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "fieldId": "cValue",
            "role": "value",
            "logicalName": "c_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    req_wire["projectContext"]["fieldBindingAuthorizations"] = [
        {
            "authorizationId": "fba-a",
            "requestId": base_req_id,
            "fieldId": "factValue",
            "role": "value",
            "columnGrantId": "colgrant-a-id",
        },
        {
            "authorizationId": "fba-b",
            "requestId": base_req_id,
            "fieldId": "bValue",
            "role": "value",
            "columnGrantId": "colgrant-b-value",
        },
        {
            "authorizationId": "fba-c",
            "requestId": base_req_id,
            "fieldId": "cValue",
            "role": "value",
            "columnGrantId": "colgrant-c-value",
        },
    ]
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-a",
        }
    ]
    _add_entity_key_to_wire(req_wire)
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    request = _reclose_context_only(req_wire)

    # Add evidence
    wire = request.model_dump(by_alias=True, mode="json")
    payload_hash = wire["handoffClosure"]["payloadSha256"]
    if evidence_ids_list is None:
        evidence_ids_list = [
            ["ev-query-requirement"],
            ["ev-fact-declaration"],
        ]
    wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-z",
            "evidenceIds": evidence_ids_list[0],
        },
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-a",
            "evidenceIds": evidence_ids_list[1],
        },
    ]
    return _reclose_context_only(wire)


def test_three_relation_chain_bfs_not_global_sort() -> None:
    """Three-relation chain: result is BFS order, not global grantId sort.

    A→B=join-z, B→C=join-a. BFS from A discovers join-z first, then join-a.
    If sorted globally, join-a would come first (wrong).
    """
    request = _build_chain_request()
    result = _resolve_joins_v3(request)

    # Convert to camelCase JSON wire for full array comparison
    result_wire = [item.model_dump(by_alias=True, mode="json") for item in result]

    # Expected: BFS order [join-z, join-a] with complete wire
    assert result_wire == [
        {
            "joinGrantId": "join-z",
            "leftSchemaName": "dbo",
            "leftRelationName": "table_a",
            "leftColumnName": "a_key",
            "rightSchemaName": "dbo",
            "rightRelationName": "table_b",
            "rightColumnName": "b_key",
            "joinType": "inner",
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "joinGrantId": "join-a",
            "leftSchemaName": "dbo",
            "leftRelationName": "table_b",
            "leftColumnName": "b_key",
            "rightSchemaName": "dbo",
            "rightRelationName": "table_c",
            "rightColumnName": "c_key",
            "joinType": "inner",
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]


# ===================================================================
# Section 5: Continuous LEFT JOIN
# ===================================================================


def test_continuous_left_join_success() -> None:
    """A LEFT JOIN B, then B LEFT JOIN C: both succeed.

    Second edge's left=B is not the original base A, but is in accumulated.
    """
    request = _build_chain_request(join_types=("left", "left"))
    result = _resolve_joins_v3(request)

    assert [r.join_grant_id for r in result] == ["join-z", "join-a"]
    assert str(result[0].join_type) == "left"
    assert str(result[1].join_type) == "left"

    # Direction preserved from grants
    assert result[0].left_relation_name == "table_a"
    assert result[0].right_relation_name == "table_b"
    assert result[1].left_relation_name == "table_b"
    assert result[1].right_relation_name == "table_c"


# ===================================================================
# Section 6: Reverse INNER grant
# ===================================================================


def test_reverse_inner_grant_traversal() -> None:
    """INNER JOIN where BFS enters from grant.right side.

    Output left/right still preserves grant's original direction.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    req_wire = ResolveMetadataRequestV3.model_validate(wire).model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    req_wire["metadataSnapshot"]["relations"] = [
        {
            "schemaName": "dbo",
            "relationName": "table_a",
            "relationKind": "table",
            "columns": [
                {"columnName": "a_id", "sqlType": "int", "nullable": False},
                {"columnName": "a_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_b",
            "relationKind": "table",
            "columns": [
                {"columnName": "b_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "b_value", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-1",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_a", "columnName": "a_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_b", "columnName": "b_key"},
        }
    ]
    req_wire["projectContext"]["relationGrants"] = [
        {"grantId": "relgrant-a", "schemaName": "dbo", "relationName": "table_a", "access": "read"},
        {"grantId": "relgrant-b", "schemaName": "dbo", "relationName": "table_b", "access": "read"},
    ]
    req_wire["projectContext"]["columnGrants"] = [
        {"grantId": "colgrant-a-id", "relationGrantId": "relgrant-a", "columnName": "a_id"},
        {"grantId": "colgrant-a-key", "relationGrantId": "relgrant-a", "columnName": "a_key"},
        {"grantId": "colgrant-b-key", "relationGrantId": "relgrant-b", "columnName": "b_key"},
        {"grantId": "colgrant-b-value", "relationGrantId": "relgrant-b", "columnName": "b_value"},
    ]
    # Grant direction: table_a ↔ table_b (INNER)
    # factValue is in table_b, so BFS starts from table_b (grant.right)
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-1",
            "leftColumnGrantId": "colgrant-a-key",
            "rightColumnGrantId": "colgrant-b-key",
            "joinType": "inner",
        }
    ]
    fact_code = req_wire["bindingRequest"]["fact"]["factCode"]
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        {
            "fieldId": "factValue",
            "role": "value",
            "logicalName": fact_code,
            "dataType": "integer",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "fieldId": "aValue",
            "role": "value",
            "logicalName": "a_id",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    req_wire["projectContext"]["fieldBindingAuthorizations"] = [
        {
            "authorizationId": "fba-b",
            "requestId": base_req_id,
            "fieldId": "factValue",
            "role": "value",
            "columnGrantId": "colgrant-b-value",
        },
        {
            "authorizationId": "fba-a",
            "requestId": base_req_id,
            "fieldId": "aValue",
            "role": "value",
            "columnGrantId": "colgrant-a-id",
        },
    ]
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-b",
        }
    ]
    _add_entity_key_to_wire(req_wire)
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    request = _reclose_context_only(req_wire)

    # Add evidence
    wire = request.model_dump(by_alias=True, mode="json")
    payload_hash = wire["handoffClosure"]["payloadSha256"]
    wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement"],
        }
    ]
    request = _reclose_context_only(wire)

    result = _resolve_joins_v3(request)
    assert len(result) == 1
    assert result[0].join_grant_id == "join-1"
    # Output preserves grant direction: left=table_a, right=table_b
    assert result[0].left_relation_name == "table_a"
    assert result[0].right_relation_name == "table_b"
    assert str(result[0].join_type) == "inner"


# ===================================================================
# Section 7: Branch graph (BFS vs DFS)
# ===================================================================


def test_branch_graph_bfs_order() -> None:
    """Branch graph: A→B (join-a), A→C (join-z), B→D (join-0).

    BFS from A: discovers join-a and join-z (sorted: join-0 not connected to A,
    join-a first by grantId), then from B discovers join-0.

    Wait — let me reconsider. Adjacency from A: [join-a (A→B), join-z (A→C)].
    Sorted: join-a < join-z. So BFS: join-a first, then join-z.
    From B (after join-a): adjacency[B] = [join-a, join-0]. join-a visited.
    So join-0 next.
    Expected: [join-a, join-z, join-0].
    """
    wire = valid_resolve_metadata_request_v3_wire()
    req_wire = ResolveMetadataRequestV3.model_validate(wire).model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    req_wire["metadataSnapshot"]["relations"] = [
        {
            "schemaName": "dbo",
            "relationName": "table_a",
            "relationKind": "table",
            "columns": [
                {"columnName": "a_id", "sqlType": "int", "nullable": False},
                {"columnName": "a_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_b",
            "relationKind": "table",
            "columns": [
                {"columnName": "b_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "b_value", "sqlType": "int", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_c",
            "relationKind": "table",
            "columns": [
                {"columnName": "c_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "c_value", "sqlType": "int", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_d",
            "relationKind": "table",
            "columns": [
                {"columnName": "d_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "d_value", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-ab",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_a", "columnName": "a_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_b", "columnName": "b_key"},
        },
        {
            "relationshipId": "rel-ac",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_a", "columnName": "a_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_c", "columnName": "c_key"},
        },
        {
            "relationshipId": "rel-bd",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_b", "columnName": "b_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_d", "columnName": "d_key"},
        },
    ]
    req_wire["projectContext"]["relationGrants"] = [
        {"grantId": "relgrant-a", "schemaName": "dbo", "relationName": "table_a", "access": "read"},
        {"grantId": "relgrant-b", "schemaName": "dbo", "relationName": "table_b", "access": "read"},
        {"grantId": "relgrant-c", "schemaName": "dbo", "relationName": "table_c", "access": "read"},
        {"grantId": "relgrant-d", "schemaName": "dbo", "relationName": "table_d", "access": "read"},
    ]
    req_wire["projectContext"]["columnGrants"] = [
        {"grantId": "colgrant-a-id", "relationGrantId": "relgrant-a", "columnName": "a_id"},
        {"grantId": "colgrant-a-key", "relationGrantId": "relgrant-a", "columnName": "a_key"},
        {"grantId": "colgrant-b-key", "relationGrantId": "relgrant-b", "columnName": "b_key"},
        {"grantId": "colgrant-b-value", "relationGrantId": "relgrant-b", "columnName": "b_value"},
        {"grantId": "colgrant-c-key", "relationGrantId": "relgrant-c", "columnName": "c_key"},
        {"grantId": "colgrant-c-value", "relationGrantId": "relgrant-c", "columnName": "c_value"},
        {"grantId": "colgrant-d-key", "relationGrantId": "relgrant-d", "columnName": "d_key"},
        {"grantId": "colgrant-d-value", "relationGrantId": "relgrant-d", "columnName": "d_value"},
    ]
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-a",
            "leftColumnGrantId": "colgrant-a-key",
            "rightColumnGrantId": "colgrant-b-key",
            "joinType": "inner",
        },
        {
            "grantId": "join-z",
            "leftColumnGrantId": "colgrant-a-key",
            "rightColumnGrantId": "colgrant-c-key",
            "joinType": "inner",
        },
        {
            "grantId": "join-0",
            "leftColumnGrantId": "colgrant-b-key",
            "rightColumnGrantId": "colgrant-d-key",
            "joinType": "inner",
        },
    ]
    fact_code = req_wire["bindingRequest"]["fact"]["factCode"]
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        {
            "fieldId": "factValue",
            "role": "value",
            "logicalName": fact_code,
            "dataType": "integer",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "fieldId": "bValue",
            "role": "value",
            "logicalName": "b_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "fieldId": "cValue",
            "role": "value",
            "logicalName": "c_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "fieldId": "dValue",
            "role": "value",
            "logicalName": "d_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    req_wire["projectContext"]["fieldBindingAuthorizations"] = [
        {
            "authorizationId": "fba-a",
            "requestId": base_req_id,
            "fieldId": "factValue",
            "role": "value",
            "columnGrantId": "colgrant-a-id",
        },
        {
            "authorizationId": "fba-b",
            "requestId": base_req_id,
            "fieldId": "bValue",
            "role": "value",
            "columnGrantId": "colgrant-b-value",
        },
        {
            "authorizationId": "fba-c",
            "requestId": base_req_id,
            "fieldId": "cValue",
            "role": "value",
            "columnGrantId": "colgrant-c-value",
        },
        {
            "authorizationId": "fba-d",
            "requestId": base_req_id,
            "fieldId": "dValue",
            "role": "value",
            "columnGrantId": "colgrant-d-value",
        },
    ]
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-a",
        }
    ]
    _add_entity_key_to_wire(req_wire)
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    request = _reclose_context_only(req_wire)

    # Add evidence for all three grants
    wire = request.model_dump(by_alias=True, mode="json")
    payload_hash = wire["handoffClosure"]["payloadSha256"]
    wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-a",
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-z",
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-0",
            "evidenceIds": ["ev-example"],
        },
    ]
    request = _reclose_context_only(wire)

    result = _resolve_joins_v3(request)
    assert [r.join_grant_id for r in result] == ["join-a", "join-z", "join-0"]


# ===================================================================
# Section 8: Error propagation (disconnected, cycle, ambiguous)
# ===================================================================


def test_error_propagation_from_evidence() -> None:
    """Errors from evidence closure propagate through _resolve_joins_v3."""
    # Test: missing evidence association
    request = _build_chain_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    # Remove evidence for join-a
    req_wire["projectContext"]["joinAuthorizationEvidence"] = [
        ev
        for ev in req_wire["projectContext"]["joinAuthorizationEvidence"]
        if ev["joinGrantId"] != "join-a"
    ]
    request = _reclose_context_only(req_wire)

    with pytest.raises(MetadataJoinEvidenceErrorV3) as exc_info:
        _resolve_joins_v3(request)
    assert exc_info.value.code == "JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND"


# ===================================================================
# Section 9: Input/output isolation and model_copy
# ===================================================================


def test_input_not_mutated_on_success() -> None:
    """Successful resolution does not mutate the input request."""
    request = _build_chain_request()
    before = request.model_dump(by_alias=True, mode="json")
    _resolve_joins_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_output_list_isolation() -> None:
    """Modifying returned evidence_ids must not affect the original request."""
    request = _build_chain_request(
        evidence_ids_list=[
            ["ev-query-requirement", "ev-query-requirement", "ev-condition-usage"],
            ["ev-fact-declaration"],
        ]
    )
    result = _resolve_joins_v3(request)
    original_wire = request.model_dump(by_alias=True, mode="json")

    # Mutate the returned evidence_ids
    for r in result:
        r.evidence_ids.append("injected")

    after_wire = request.model_dump(by_alias=True, mode="json")
    assert after_wire == original_wire


def test_evidence_ids_preserve_order_and_duplicates() -> None:
    """evidenceIds preserve original order and duplicate count."""
    request = _build_chain_request(
        evidence_ids_list=[
            ["ev-example", "ev-query-requirement", "ev-example", "ev-condition-usage"],
            ["ev-fact-declaration"],
        ]
    )
    result = _resolve_joins_v3(request)

    assert result[0].evidence_ids == [
        "ev-example",
        "ev-query-requirement",
        "ev-example",
        "ev-condition-usage",
    ]
    assert result[1].evidence_ids == ["ev-fact-declaration"]


def test_model_copy_injected_illegal_association_blocked() -> None:
    """Inject non-serializable value into defined field → STRUCTURE_INVALID.

    Simulates a model_copy bypass by directly modifying a defined field's
    value to something that can't be JSON-serialized. The model_dump
    call in _revalidate_request_structure catches this.
    """

    valid = _build_chain_request()

    # Directly modify a defined field to contain a non-serializable value
    # This bypasses Pydantic's type checking (frozen model)
    association = valid.project_context.join_authorization_evidence[0]
    object.__setattr__(
        association,
        "evidence_ids",
        [frozenset({1, 2, 3})],  # Not JSON-serializable
    )

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_joins_v3(valid)
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


# ===================================================================
# Section 10: Sanitization
# ===================================================================


def test_sanitization_does_not_leak_marker(caplog: pytest.LogCaptureFixture) -> None:
    """Error messages must not leak the actual marker value."""
    request = _build_chain_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    marker = "maliciousMarker"
    for ev in req_wire["projectContext"]["joinAuthorizationEvidence"]:
        if ev["joinGrantId"] == "join-a":
            ev["requestId"] = marker
            break
    request = _reclose_context_only(req_wire)

    with pytest.raises(MetadataJoinEvidenceErrorV3) as exc_info:
        _resolve_joins_v3(request)
    assert exc_info.value.code == "JOIN_REQUEST_NOT_IN_CONTEXT"
    err = exc_info.value
    assert marker not in err.code
    assert marker not in str(err)
    assert marker not in repr(err)


# ===================================================================
# Section 11: Branch graph reorder consistency
# ===================================================================


def test_branch_graph_reorder_produces_same_result() -> None:
    """Reversing input arrays does not change BFS result."""
    original = _build_two_relation_for_branch()
    result_original = _resolve_joins_v3(original)
    original_wire = [r.model_dump(by_alias=True, mode="json") for r in result_original]

    # Deep copy and reorder
    wire = original.model_dump(by_alias=True, mode="json")

    # Reverse grants, relationships, fields, evidence
    wire["projectContext"]["joinGrants"] = list(reversed(wire["projectContext"]["joinGrants"]))
    wire["metadataSnapshot"]["relationships"] = list(
        reversed(wire["metadataSnapshot"]["relationships"])
    )
    wire["bindingRequest"]["queryRequirements"]["fields"] = list(
        reversed(wire["bindingRequest"]["queryRequirements"]["fields"])
    )
    wire["projectContext"]["joinAuthorizationEvidence"] = list(
        reversed(wire["projectContext"]["joinAuthorizationEvidence"])
    )

    # Sync payload and recompute evidence payload hash (fields order changed)
    wire["handoffClosure"]["payload"] = deepcopy(wire["bindingRequest"])
    new_payload_hash = canonical_sha256(
        ResolveMetadataRequestV3.model_validate(wire).binding_request
    )
    for ev in wire["projectContext"]["joinAuthorizationEvidence"]:
        ev["payloadSha256"] = new_payload_hash
    reordered = _reclose_context_only(wire)

    result_reordered = _resolve_joins_v3(reordered)
    reordered_wire = [r.model_dump(by_alias=True, mode="json") for r in result_reordered]

    assert original_wire == reordered_wire
    # Verify BFS order is [join-a, join-z, join-0]
    assert [r["joinGrantId"] for r in original_wire] == ["join-a", "join-z", "join-0"]


def _build_two_relation_for_branch():
    """Build a 4-relation branch graph: A→B (join-a), A→C (join-z), B→D (join-0)."""
    wire = valid_resolve_metadata_request_v3_wire()
    req_wire = ResolveMetadataRequestV3.model_validate(wire).model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    req_wire["metadataSnapshot"]["relations"] = [
        {
            "schemaName": "dbo",
            "relationName": "table_a",
            "relationKind": "table",
            "columns": [
                {"columnName": "a_id", "sqlType": "int", "nullable": False},
                {"columnName": "a_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_b",
            "relationKind": "table",
            "columns": [
                {"columnName": "b_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "b_value", "sqlType": "int", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_c",
            "relationKind": "table",
            "columns": [
                {"columnName": "c_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "c_value", "sqlType": "int", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_d",
            "relationKind": "table",
            "columns": [
                {"columnName": "d_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "d_value", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-ab",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_a", "columnName": "a_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_b", "columnName": "b_key"},
        },
        {
            "relationshipId": "rel-ac",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_a", "columnName": "a_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_c", "columnName": "c_key"},
        },
        {
            "relationshipId": "rel-bd",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_b", "columnName": "b_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_d", "columnName": "d_key"},
        },
    ]
    req_wire["projectContext"]["relationGrants"] = [
        {"grantId": "relgrant-a", "schemaName": "dbo", "relationName": "table_a", "access": "read"},
        {"grantId": "relgrant-b", "schemaName": "dbo", "relationName": "table_b", "access": "read"},
        {"grantId": "relgrant-c", "schemaName": "dbo", "relationName": "table_c", "access": "read"},
        {"grantId": "relgrant-d", "schemaName": "dbo", "relationName": "table_d", "access": "read"},
    ]
    req_wire["projectContext"]["columnGrants"] = [
        {"grantId": "colgrant-a-id", "relationGrantId": "relgrant-a", "columnName": "a_id"},
        {"grantId": "colgrant-a-key", "relationGrantId": "relgrant-a", "columnName": "a_key"},
        {"grantId": "colgrant-b-key", "relationGrantId": "relgrant-b", "columnName": "b_key"},
        {"grantId": "colgrant-b-value", "relationGrantId": "relgrant-b", "columnName": "b_value"},
        {"grantId": "colgrant-c-key", "relationGrantId": "relgrant-c", "columnName": "c_key"},
        {"grantId": "colgrant-c-value", "relationGrantId": "relgrant-c", "columnName": "c_value"},
        {"grantId": "colgrant-d-key", "relationGrantId": "relgrant-d", "columnName": "d_key"},
        {"grantId": "colgrant-d-value", "relationGrantId": "relgrant-d", "columnName": "d_value"},
    ]
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-a",
            "leftColumnGrantId": "colgrant-a-key",
            "rightColumnGrantId": "colgrant-b-key",
            "joinType": "inner",
        },
        {
            "grantId": "join-z",
            "leftColumnGrantId": "colgrant-a-key",
            "rightColumnGrantId": "colgrant-c-key",
            "joinType": "inner",
        },
        {
            "grantId": "join-0",
            "leftColumnGrantId": "colgrant-b-key",
            "rightColumnGrantId": "colgrant-d-key",
            "joinType": "inner",
        },
    ]
    fact_code = req_wire["bindingRequest"]["fact"]["factCode"]
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        {
            "fieldId": "factValue",
            "role": "value",
            "logicalName": fact_code,
            "dataType": "integer",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "fieldId": "bValue",
            "role": "value",
            "logicalName": "b_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "fieldId": "cValue",
            "role": "value",
            "logicalName": "c_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "fieldId": "dValue",
            "role": "value",
            "logicalName": "d_value",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    req_wire["projectContext"]["fieldBindingAuthorizations"] = [
        {
            "authorizationId": "fba-a",
            "requestId": base_req_id,
            "fieldId": "factValue",
            "role": "value",
            "columnGrantId": "colgrant-a-id",
        },
        {
            "authorizationId": "fba-b",
            "requestId": base_req_id,
            "fieldId": "bValue",
            "role": "value",
            "columnGrantId": "colgrant-b-value",
        },
        {
            "authorizationId": "fba-c",
            "requestId": base_req_id,
            "fieldId": "cValue",
            "role": "value",
            "columnGrantId": "colgrant-c-value",
        },
        {
            "authorizationId": "fba-d",
            "requestId": base_req_id,
            "fieldId": "dValue",
            "role": "value",
            "columnGrantId": "colgrant-d-value",
        },
    ]
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-a",
        }
    ]
    _add_entity_key_to_wire(req_wire)
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    request = _reclose_context_only(req_wire)

    # Add evidence
    wire = request.model_dump(by_alias=True, mode="json")
    payload_hash = wire["handoffClosure"]["payloadSha256"]
    wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-a",
            "evidenceIds": ["ev-query-requirement"],
        },
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-z",
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-0",
            "evidenceIds": ["ev-example"],
        },
    ]
    return _reclose_context_only(wire)


# ===================================================================
# Section 12: Error propagation (disconnected, cycle, parallel)
# ===================================================================


def test_disconnected_raises_join_closure_disconnected() -> None:
    """Removing B→C grant leaves relations disconnected."""

    request = _build_chain_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Remove the B→C grant (join-a)
    req_wire["projectContext"]["joinGrants"] = [
        jg for jg in req_wire["projectContext"]["joinGrants"] if jg["grantId"] != "join-a"
    ]
    # Remove associated evidence
    req_wire["projectContext"]["joinAuthorizationEvidence"] = [
        ev
        for ev in req_wire["projectContext"]["joinAuthorizationEvidence"]
        if ev["joinGrantId"] != "join-a"
    ]
    request = _reclose_context_only(req_wire)

    with pytest.raises(MetadataJoinClosureErrorV3) as exc_info:
        _resolve_joins_v3(request)
    assert exc_info.value.code == "JOIN_CLOSURE_DISCONNECTED"


def test_cycle_raises_join_closure_ambiguous() -> None:
    """Adding A→C grant creates a cycle → JOIN_CLOSURE_AMBIGUOUS."""

    request = _build_chain_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    # Add A→C relationship and grant
    req_wire["metadataSnapshot"]["relationships"].append(
        {
            "relationshipId": "rel-ac",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_a", "columnName": "a_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_c", "columnName": "c_key"},
        }
    )
    req_wire["projectContext"]["joinGrants"].append(
        {
            "grantId": "join-cycle",
            "leftColumnGrantId": "colgrant-a-key",
            "rightColumnGrantId": "colgrant-c-key",
            "joinType": "inner",
        }
    )
    # Add evidence for the new grant
    payload_hash = req_wire["handoffClosure"]["payloadSha256"]
    req_wire["projectContext"]["joinAuthorizationEvidence"].append(
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-cycle",
            "evidenceIds": ["ev-example"],
        }
    )
    request = _reclose_context_only(req_wire)

    with pytest.raises(MetadataJoinClosureErrorV3) as exc_info:
        _resolve_joins_v3(request)
    assert exc_info.value.code == "JOIN_CLOSURE_AMBIGUOUS"


def test_wrong_payload_hash_raises_evidence_mismatch() -> None:
    """Wrong payloadSha256 in evidence → JOIN_EVIDENCE_PAYLOAD_HASH_MISMATCH."""

    request = _build_chain_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Corrupt payload hash for join-a
    for ev in req_wire["projectContext"]["joinAuthorizationEvidence"]:
        if ev["joinGrantId"] == "join-a":
            ev["payloadSha256"] = "f" * 64
            break

    # Reclose context/approval (payload hash in evidence is intentionally wrong)
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
    request = ResolveMetadataRequestV3.model_validate(req_wire)

    with pytest.raises(MetadataJoinEvidenceErrorV3) as exc_info:
        _resolve_joins_v3(request)
    assert exc_info.value.code == "JOIN_EVIDENCE_PAYLOAD_HASH_MISMATCH"


def test_dangling_evidence_id_raises_invalid() -> None:
    """Dangling evidenceId → JOIN_GRANT_EVIDENCE_INVALID."""

    request = _build_chain_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Replace evidenceIds with non-existent ID
    for ev in req_wire["projectContext"]["joinAuthorizationEvidence"]:
        if ev["joinGrantId"] == "join-a":
            ev["evidenceIds"] = ["nonexistent-evidence"]
            break

    request = _reclose_context_only(req_wire)

    with pytest.raises(MetadataJoinEvidenceErrorV3) as exc_info:
        _resolve_joins_v3(request)
    assert exc_info.value.code == "JOIN_GRANT_EVIDENCE_INVALID"


# ===================================================================
# Section 13: Case sensitivity
# ===================================================================


def test_case_insensitive_physical_names_succeed() -> None:
    """insensitive snapshot: case-different relation names still resolve."""
    request = _build_chain_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Change relation grant name to uppercase
    for rg in req_wire["projectContext"]["relationGrants"]:
        if rg["grantId"] == "relgrant-a":
            rg["relationName"] = "TABLE_A"
            break

    request = _reclose_context_only(req_wire)
    result = _resolve_joins_v3(request)

    # Should succeed and use snapshot's original spelling (table_a)
    assert len(result) == 2
    assert result[0].left_relation_name == "table_a"


def test_case_sensitive_physical_names_rejected() -> None:
    """sensitive snapshot: case-different relation names are rejected."""

    request = _build_chain_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Set snapshot to case-sensitive
    req_wire["metadataSnapshot"]["identifierCaseSensitivity"] = "sensitive"
    # Change relation grant name to uppercase
    for rg in req_wire["projectContext"]["relationGrants"]:
        if rg["grantId"] == "relgrant-a":
            rg["relationName"] = "TABLE_A"
            break

    request = _reclose_context_only(req_wire)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_joins_v3(request)
    assert exc_info.value.code == "RELATION_NOT_IN_SNAPSHOT"


# ===================================================================
# Section 14: True model_copy bypass
# ===================================================================


def test_true_model_copy_bypass_blocked() -> None:
    """model_copy with empty evidence_ids → STRUCTURE_INVALID.

    Uses model_copy (not object.__setattr__) to create an invalid association,
    then calls _resolve_joins_v3 directly.
    """

    valid = _build_chain_request()
    original_wire = valid.model_dump(by_alias=True, mode="json")

    # Create invalid association via model_copy
    invalid_assoc = valid.project_context.join_authorization_evidence[0].model_copy(
        update={"evidence_ids": []}  # min_length=1 violated
    )

    # Build tampered context via model_copy
    tampered_context = valid.project_context.model_copy(
        update={"join_authorization_evidence": [invalid_assoc]}
    )
    tampered_request = valid.model_copy(update={"project_context": tampered_context})

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_joins_v3(tampered_request)
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"

    # Original request must be unchanged
    assert valid.model_dump(by_alias=True, mode="json") == original_wire


# ===================================================================
# Section 15: Failure path input immutability + log sanitization
# ===================================================================


def test_failure_path_input_immutable() -> None:
    """Direction conflict failure does not mutate input."""
    # Build LEFT JOIN conflict: base=B, A LEFT JOIN B
    wire = valid_resolve_metadata_request_v3_wire()
    req_wire = ResolveMetadataRequestV3.model_validate(wire).model_dump(by_alias=True, mode="json")
    base_req_id = req_wire["bindingRequest"]["requestId"]

    req_wire["metadataSnapshot"]["relations"] = [
        {
            "schemaName": "dbo",
            "relationName": "table_a",
            "relationKind": "table",
            "columns": [
                {"columnName": "a_id", "sqlType": "int", "nullable": False},
                {"columnName": "a_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "table_b",
            "relationKind": "table",
            "columns": [
                {"columnName": "b_key", "sqlType": "nvarchar(100)", "nullable": False},
                {"columnName": "b_value", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-1",
            "leftColumn": {"schemaName": "dbo", "relationName": "table_a", "columnName": "a_key"},
            "rightColumn": {"schemaName": "dbo", "relationName": "table_b", "columnName": "b_key"},
        }
    ]
    req_wire["projectContext"]["relationGrants"] = [
        {"grantId": "relgrant-a", "schemaName": "dbo", "relationName": "table_a", "access": "read"},
        {"grantId": "relgrant-b", "schemaName": "dbo", "relationName": "table_b", "access": "read"},
    ]
    req_wire["projectContext"]["columnGrants"] = [
        {"grantId": "colgrant-a-id", "relationGrantId": "relgrant-a", "columnName": "a_id"},
        {"grantId": "colgrant-a-key", "relationGrantId": "relgrant-a", "columnName": "a_key"},
        {"grantId": "colgrant-b-key", "relationGrantId": "relgrant-b", "columnName": "b_key"},
        {"grantId": "colgrant-b-value", "relationGrantId": "relgrant-b", "columnName": "b_value"},
    ]
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-1",
            "leftColumnGrantId": "colgrant-a-key",
            "rightColumnGrantId": "colgrant-b-key",
            "joinType": "left",
        }
    ]
    fact_code = req_wire["bindingRequest"]["fact"]["factCode"]
    # factValue in table_b (right side of LEFT JOIN)
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        {
            "fieldId": "factValue",
            "role": "value",
            "logicalName": fact_code,
            "dataType": "integer",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
        {
            "fieldId": "aValue",
            "role": "value",
            "logicalName": "a_id",
            "dataType": "integer",
            "required": False,
            "evidenceIds": ["ev-query-requirement"],
        },
    ]
    req_wire["projectContext"]["fieldBindingAuthorizations"] = [
        {
            "authorizationId": "fba-b",
            "requestId": base_req_id,
            "fieldId": "factValue",
            "role": "value",
            "columnGrantId": "colgrant-b-value",
        },
        {
            "authorizationId": "fba-a",
            "requestId": base_req_id,
            "fieldId": "aValue",
            "role": "value",
            "columnGrantId": "colgrant-a-id",
        },
    ]
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-b",
        }
    ]
    _add_entity_key_to_wire(req_wire)
    req_wire["handoffClosure"]["payload"] = deepcopy(req_wire["bindingRequest"])
    request = _reclose_context_only(req_wire)

    # Add evidence
    wire = request.model_dump(by_alias=True, mode="json")
    payload_hash = wire["handoffClosure"]["payloadSha256"]
    wire["projectContext"]["joinAuthorizationEvidence"] = [
        {
            "requestId": base_req_id,
            "payloadSha256": payload_hash,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-query-requirement"],
        }
    ]
    request = _reclose_context_only(wire)

    # Save before state
    before = request.model_dump(by_alias=True, mode="json")

    with pytest.raises(MetadataJoinPlanErrorV3) as exc_info:
        _resolve_joins_v3(request)
    assert exc_info.value.code == "JOIN_PLAN_DIRECTION_CONFLICT"

    # Input must be unchanged
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_sanitization_includes_caplog(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Marker must not appear in caplog.text at any level."""
    import logging

    request = _build_chain_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    marker = "secretmarker"
    for ev in req_wire["projectContext"]["joinAuthorizationEvidence"]:
        if ev["joinGrantId"] == "join-a":
            ev["requestId"] = marker
            break
    request = _reclose_context_only(req_wire)

    with caplog.at_level(logging.DEBUG, logger="release_sql_bot"):
        with pytest.raises(MetadataJoinEvidenceErrorV3) as exc_info:
            _resolve_joins_v3(request)

    assert exc_info.value.code == "JOIN_REQUEST_NOT_IN_CONTEXT"
    err = exc_info.value
    assert marker not in err.code
    assert marker not in str(err)
    assert marker not in repr(err)
    assert marker not in caplog.text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
