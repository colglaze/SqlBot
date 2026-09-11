"""Unit tests for V3 join-closure selection (DEV §5.3.6.1).

Tests the internal helper ``_select_join_closure_v3``: deterministic
authorization connectivity that selects the join grants connecting all
relations required by the resolved fields.

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

import random
from copy import deepcopy

import pytest

from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataColumnResolutionErrorV3,
    MetadataJoinClosureErrorV3,
    MetadataJoinResolutionErrorV3,
    MetadataResolutionInputErrorV3,
    _select_join_closure_v3,
)
from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3
from tests.v3_metadata_support import _reclose_all_hashes_from_wire

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_closure_request(
    *,
    relations: list[dict[str, object]],
    field_relation_ids: list[int],
    join_grants: list[dict[str, object]],
    relationships: list[dict[str, object]],
    case_sensitive: bool = False,
) -> ResolveMetadataRequestV3:
    """Build a request for join-closure tests.

    Args:
        relations: List of relation definitions.  Each must have
            schemaName, relationName, relationKind, columns.
        field_relation_ids: Indices into ``relations`` for which a
            field + field authorization + column grant is created.
        join_grants: List of join grant definitions, each with
            grantId, leftRelationId, rightRelationId, joinType.
        relationships: List of snapshot relationship edges, each with
            relationshipId, leftRelationId, rightRelationId.
        case_sensitive: Identifier case sensitivity.
    """
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Set case sensitivity
    req_wire["metadataSnapshot"]["identifierCaseSensitivity"] = (
        "sensitive" if case_sensitive else "insensitive"
    )

    # Build relations (keep relation 0 = synthetic_table from base fixture)
    base_relations = req_wire["metadataSnapshot"]["relations"]
    all_relations = list(base_relations)
    for rel in relations:
        all_relations.append(rel)
    req_wire["metadataSnapshot"]["relations"] = all_relations

    # Collect all relation IDs that need column grants:
    # both field-bearing relations and join-grant endpoint relations.
    all_needed_rel_ids = set(field_relation_ids)
    for jg in join_grants:
        all_needed_rel_ids.add(jg["leftRelationId"])
        all_needed_rel_ids.add(jg["rightRelationId"])

    # Build relation grants and column grants.
    # The base fixture already has relgrant-1 → synthetic_table (relation 0).
    # For new relations, use offset IDs to avoid conflicts.
    relation_grants = list(req_wire["projectContext"]["relationGrants"])
    column_grants = list(req_wire["projectContext"]["columnGrants"])
    fields = list(req_wire["bindingRequest"]["queryRequirements"]["fields"])
    field_auths = list(req_wire["projectContext"]["fieldBindingAuthorizations"])

    # Map: relation_id -> column grant ID (created for all needed relations)
    relation_to_colgrant: dict[int, str] = {}

    for rel_id in sorted(all_needed_rel_ids):
        rel = all_relations[rel_id]
        rel_name = rel["relationName"]
        schema_name = rel["schemaName"]
        col_name = rel["columns"][0]["columnName"]

        # Relation grant: reuse existing for relation 0, create new for others
        if rel_id == 0:
            rg_id = "relgrant-1"  # base fixture already has this → synthetic_table
        else:
            rg_id = f"relgrant-new-{rel_id}"
            if not any(rg["grantId"] == rg_id for rg in relation_grants):
                relation_grants.append(
                    {
                        "grantId": rg_id,
                        "schemaName": schema_name,
                        "relationName": rel_name,
                        "access": "read",
                    }
                )

        # Column grant for this relation
        cg_id = f"colgrant-rel-{rel_id}"
        if not any(cg["grantId"] == cg_id for cg in column_grants):
            column_grants.append(
                {
                    "grantId": cg_id,
                    "relationGrantId": rg_id,
                    "columnName": col_name,
                }
            )
        relation_to_colgrant[rel_id] = cg_id

    # Add fields + authorizations only for field-bearing relations
    for idx, rel_id in enumerate(field_relation_ids):
        cg_id = relation_to_colgrant[rel_id]
        field_id = f"field-{idx}"
        fields.append(
            {
                "fieldId": field_id,
                "role": "value",
                "logicalName": f"field_{idx}",
                "dataType": "string",
                "required": True,
                "evidenceIds": ["ev-fact-declaration"],
            }
        )
        field_auths.append(
            {
                "authorizationId": f"fba-{idx}",
                "requestId": req_wire["bindingRequest"]["requestId"],
                "fieldId": field_id,
                "role": "value",
                "columnGrantId": cg_id,
            }
        )

    req_wire["projectContext"]["relationGrants"] = relation_grants
    req_wire["projectContext"]["columnGrants"] = column_grants
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = fields
    req_wire["projectContext"]["fieldBindingAuthorizations"] = field_auths

    # Build join grants
    grants = []
    for jg in join_grants:
        grants.append(
            {
                "grantId": jg["grantId"],
                "leftColumnGrantId": relation_to_colgrant[jg["leftRelationId"]],
                "rightColumnGrantId": relation_to_colgrant[jg["rightRelationId"]],
                "joinType": jg.get("joinType", "inner"),
            }
        )
    req_wire["projectContext"]["joinGrants"] = grants

    # Build relationships
    rel_edges = []
    for rel in relationships:
        left_rel = all_relations[rel["leftRelationId"]]
        right_rel = all_relations[rel["rightRelationId"]]
        rel_edges.append(
            {
                "relationshipId": rel["relationshipId"],
                "leftColumn": {
                    "schemaName": left_rel["schemaName"],
                    "relationName": left_rel["relationName"],
                    "columnName": left_rel["columns"][0]["columnName"],
                },
                "rightColumn": {
                    "schemaName": right_rel["schemaName"],
                    "relationName": right_rel["relationName"],
                    "columnName": right_rel["columns"][0]["columnName"],
                },
            }
        )
    req_wire["metadataSnapshot"]["relationships"] = rel_edges

    return _reclose_all_hashes_from_wire(req_wire)


def _make_valid_request() -> ResolveMetadataRequestV3:
    """Build a valid, internally-consistent request from the shared fixture."""
    from tests.v3_metadata_support import valid_resolve_metadata_request_v3_wire

    wire = valid_resolve_metadata_request_v3_wire()
    return ResolveMetadataRequestV3.model_validate(wire)


# ===================================================================
# Section A: Single relation and two-relation success
# ===================================================================


def test_single_relation_returns_empty():
    """A single required relation needs no joins."""
    # Only relation 0 (synthetic_table) has an authorized field
    request = _build_closure_request(
        relations=[],
        field_relation_ids=[0],
        join_grants=[],
        relationships=[],
    )
    result = _select_join_closure_v3(request)
    assert result == ()


def test_two_relations_single_grant_succeeds():
    """Two relations connected by one grant returns that grant."""
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            }
        ],
        field_relation_ids=[0, 1],  # synthetic_table + synthetic_dim
        join_grants=[
            {"grantId": "join-z", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        ],
    )
    result = _select_join_closure_v3(request)
    assert result == ("join-z",)


# ===================================================================
# Section B: Three-relation unique chain (sorted output)
# ===================================================================


def test_three_relations_unique_chain_sorted_output():
    """Three relations in a unique chain return grants sorted by ID.

    Relations: 0-1-2, grants join-b (0-1) and join-a (1-2).
    Input grant order is non-sorted; output must be ascending.
    """
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            },
            {
                "schemaName": "dbo",
                "relationName": "synthetic_fact",
                "relationKind": "table",
                "columns": [
                    {"columnName": "fact_key", "sqlType": "int", "nullable": False},
                ],
            },
        ],
        field_relation_ids=[0, 1, 2],
        join_grants=[
            # Intentionally non-sorted grantIds
            {"grantId": "join-b", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
            {"grantId": "join-a", "leftRelationId": 1, "rightRelationId": 2, "joinType": "inner"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
            {"relationshipId": "rel-1-2", "leftRelationId": 1, "rightRelationId": 2},
        ],
    )
    result = _select_join_closure_v3(request)
    assert result == ("join-a", "join-b")


# ===================================================================
# Section C: Order independence
# ===================================================================


def test_order_independence_fields_grants_relationships():
    """Output is identical regardless of input ordering."""
    base_relations = [
        {
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "relationKind": "table",
            "columns": [
                {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "synthetic_fact",
            "relationKind": "table",
            "columns": [
                {"columnName": "fact_key", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    grants = [
        {"grantId": "join-b", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
        {"grantId": "join-a", "leftRelationId": 1, "rightRelationId": 2, "joinType": "inner"},
    ]
    rels = [
        {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        {"relationshipId": "rel-1-2", "leftRelationId": 1, "rightRelationId": 2},
    ]

    def build_with_order(field_order, grant_order, rel_order):
        return _build_closure_request(
            relations=base_relations,
            field_relation_ids=field_order,
            join_grants=grant_order,
            relationships=rel_order,
        )

    # Reference order
    ref = _select_join_closure_v3(build_with_order([0, 1, 2], grants, rels))
    # Permuted
    for f_order in [[2, 0, 1], [1, 2, 0]]:
        for g_order in [list(reversed(grants)), grants]:
            for r_order in [list(reversed(rels)), rels]:
                result = _select_join_closure_v3(build_with_order(f_order, g_order, r_order))
                assert result == ref, f"order independence failed: {result} != {ref}"


# ===================================================================
# Section D: Disconnected relations
# ===================================================================


def test_three_relations_disconnected():
    """Three relations with only one connecting grant are disconnected."""
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            },
            {
                "schemaName": "dbo",
                "relationName": "synthetic_fact",
                "relationKind": "table",
                "columns": [
                    {"columnName": "fact_key", "sqlType": "int", "nullable": False},
                ],
            },
        ],
        field_relation_ids=[0, 1, 2],
        join_grants=[
            # Only connects 0-1, relation 2 is isolated
            {"grantId": "join-0-1", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
            {"relationshipId": "rel-1-2", "leftRelationId": 1, "rightRelationId": 2},
        ],
    )
    with pytest.raises(MetadataJoinClosureErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "JOIN_CLOSURE_DISCONNECTED"


# ===================================================================
# Section E: Ambiguity (cycle and parallel)
# ===================================================================


def test_three_relations_cycle_ambiguous():
    """Three relations fully connected (cycle) are ambiguous."""
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            },
            {
                "schemaName": "dbo",
                "relationName": "synthetic_fact",
                "relationKind": "table",
                "columns": [
                    {"columnName": "fact_key", "sqlType": "int", "nullable": False},
                ],
            },
        ],
        field_relation_ids=[0, 1, 2],
        join_grants=[
            {"grantId": "join-0-1", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
            {"grantId": "join-1-2", "leftRelationId": 1, "rightRelationId": 2, "joinType": "inner"},
            {"grantId": "join-0-2", "leftRelationId": 0, "rightRelationId": 2, "joinType": "inner"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
            {"relationshipId": "rel-1-2", "leftRelationId": 1, "rightRelationId": 2},
            {"relationshipId": "rel-0-2", "leftRelationId": 0, "rightRelationId": 2},
        ],
    )
    with pytest.raises(MetadataJoinClosureErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "JOIN_CLOSURE_AMBIGUOUS"


def test_two_relations_parallel_grants_ambiguous():
    """Two parallel grants between the same relations are ambiguous.

    Only one relationship edge is provided so both grants validate
    (each matches exactly one edge), but the two candidates for two
    relations exceed the unique-closure threshold (relations-1=1).
    """
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            }
        ],
        field_relation_ids=[0, 1],
        join_grants=[
            {
                "grantId": "join-first",
                "leftRelationId": 0,
                "rightRelationId": 1,
                "joinType": "inner",
            },
            {
                "grantId": "join-second",
                "leftRelationId": 0,
                "rightRelationId": 1,
                "joinType": "inner",
            },
        ],
        relationships=[
            # Single edge: both grants match it → both validate → ambiguous
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        ],
    )
    with pytest.raises(MetadataJoinClosureErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "JOIN_CLOSURE_AMBIGUOUS"


# ===================================================================
# Section F: Relationship exists but no grant
# ===================================================================


def test_relationship_without_grant_disconnected():
    """Snapshot has the edge but no join grant → disconnected."""
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            }
        ],
        field_relation_ids=[0, 1],
        join_grants=[],  # No join grants at all
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        ],
    )
    with pytest.raises(MetadataJoinClosureErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "JOIN_CLOSURE_DISCONNECTED"


# ===================================================================
# Section G: Bridge relation not auto-included
# ===================================================================


def test_bridge_relation_not_auto_included():
    """A bridge relation without authorized fields is not auto-added.

    Relations 0-2 are required (have fields), relation 1 is a bridge.
    Only path is 0-1-2, but relation 1 has no authorized field.
    """
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "bridge_table",
                "relationKind": "table",
                "columns": [
                    {"columnName": "bridge_key", "sqlType": "int", "nullable": False},
                ],
            },
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            },
        ],
        # Only fields in relations 0 (synthetic_table) and 2 (synthetic_dim)
        field_relation_ids=[0, 2],
        join_grants=[
            {"grantId": "join-0-1", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
            {"grantId": "join-1-2", "leftRelationId": 1, "rightRelationId": 2, "joinType": "inner"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
            {"relationshipId": "rel-1-2", "leftRelationId": 1, "rightRelationId": 2},
        ],
    )
    # Bridge relation 1 is not required, so grants connecting through it
    # don't connect two required relations → disconnected
    with pytest.raises(MetadataJoinClosureErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "JOIN_CLOSURE_DISCONNECTED"


# ===================================================================
# Section H: Extra grants
# ===================================================================


def test_extra_grant_not_connecting_required_excluded():
    """An extra grant connecting relations outside the required set is excluded."""
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            },
            {
                "schemaName": "dbo",
                "relationName": "unrelated_table",
                "relationKind": "table",
                "columns": [
                    {"columnName": "unrelated_key", "sqlType": "int", "nullable": False},
                ],
            },
        ],
        # Only relations 0 and 1 are required (have authorized fields)
        field_relation_ids=[0, 1],
        join_grants=[
            # This grant connects required relations 0-1
            {
                "grantId": "join-required",
                "leftRelationId": 0,
                "rightRelationId": 1,
                "joinType": "inner",
            },
            # This grant connects relation 1 to unrelated relation 2
            {
                "grantId": "join-extra",
                "leftRelationId": 1,
                "rightRelationId": 2,
                "joinType": "inner",
            },
        ],
        relationships=[
            {"relationshipId": "rel-req", "leftRelationId": 0, "rightRelationId": 1},
            {"relationshipId": "rel-extra", "leftRelationId": 1, "rightRelationId": 2},
        ],
    )
    result = _select_join_closure_v3(request)
    # Only join-required connects two required relations
    assert result == ("join-required",)


def test_extra_illegal_grant_blocks():
    """An extra grant that fails validation blocks the entire selection."""
    # Build a valid request first, then inject a bad grant directly
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            }
        ],
        field_relation_ids=[0, 1],
        join_grants=[
            {
                "grantId": "join-valid",
                "leftRelationId": 0,
                "rightRelationId": 1,
                "joinType": "inner",
            },
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        ],
    )
    # Inject a grant whose leftColumnGrantId does not exist
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"]["joinGrants"].append(
        {
            "grantId": "join-bad",
            "leftColumnGrantId": "nonexistent-column-grant",
            "rightColumnGrantId": "colgrant-rel-1",
            "joinType": "inner",
        }
    )
    req_wire["projectContext"]["joinGrants"].sort(key=lambda g: g["grantId"])
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


# ===================================================================
# Section I: LEFT join direction preserved
# ===================================================================


def test_left_join_direction_preserved_in_output():
    """LEFT join grant's direction and type are preserved in validation.

    The output is grant IDs only, but the grant must validate correctly
    with its original LEFT direction during _resolve_join_grant_v3.
    """
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            }
        ],
        field_relation_ids=[0, 1],
        join_grants=[
            {"grantId": "join-left", "leftRelationId": 0, "rightRelationId": 1, "joinType": "left"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        ],
    )
    result = _select_join_closure_v3(request)
    assert result == ("join-left",)


# ===================================================================
# Section J: Case sensitivity
# ===================================================================


def test_insensitive_casefold_matches_relations():
    """insensitive mode normalizes relation names for connectivity."""
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "DBO",
                "relationName": "SYNTHETIC_DIM",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            }
        ],
        field_relation_ids=[0, 1],
        join_grants=[
            {"grantId": "join-0-1", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        ],
        case_sensitive=False,
    )
    result = _select_join_closure_v3(request)
    assert result == ("join-0-1",)


def test_sensitive_mode_with_matching_case_succeeds():
    """sensitive mode succeeds when identifiers match exactly.

    Both snapshot relation and grant use the same casing (DBO/SYNTHETIC_DIM),
    so the field authorization and join validation succeed.
    """
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "DBO",
                "relationName": "SYNTHETIC_DIM",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            }
        ],
        field_relation_ids=[0, 1],
        join_grants=[
            {
                "grantId": "join-0-1",
                "leftRelationId": 0,
                "rightRelationId": 1,
                "joinType": "inner",
            },
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        ],
        case_sensitive=True,
    )
    result = _select_join_closure_v3(request)
    assert result == ("join-0-1",)


# ===================================================================
# Section K: Input gate and individual grant errors propagate
# ===================================================================


def test_input_gate_error_propagates():
    """Input-gate failures propagate before any join processing."""
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            }
        ],
        field_relation_ids=[0, 1],
        join_grants=[
            {"grantId": "join-0-1", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        ],
    )
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _select_join_closure_v3(tampered)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"


def test_individual_grant_error_propagates():
    """A join grant referencing a non-existent column grant propagates."""
    # Build a valid request first, then inject a bad grant directly
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            }
        ],
        field_relation_ids=[0, 1],
        join_grants=[
            {"grantId": "join-0-1", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        ],
    )
    # Inject a grant whose leftColumnGrantId does not exist
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"]["joinGrants"].append(
        {
            "grantId": "join-bad",
            "leftColumnGrantId": "nonexistent-column-grant",
            "rightColumnGrantId": "colgrant-rel-1",
            "joinType": "inner",
        }
    )
    # Sort by grantId so join-bad is processed after join-0-1 (which succeeds)
    req_wire["projectContext"]["joinGrants"].sort(key=lambda g: g["grantId"])
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


# ===================================================================
# Section L: Immutability
# ===================================================================


def test_input_not_mutated_on_success():
    """The actual input is not mutated by a successful closure selection."""
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            }
        ],
        field_relation_ids=[0, 1],
        join_grants=[
            {"grantId": "join-0-1", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
        ],
    )
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    _select_join_closure_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_input_not_mutated_on_failure():
    """The actual input is not mutate by a failed closure selection."""
    request = _build_closure_request(
        relations=[
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            },
            {
                "schemaName": "dbo",
                "relationName": "synthetic_fact",
                "relationKind": "table",
                "columns": [
                    {"columnName": "fact_key", "sqlType": "int", "nullable": False},
                ],
            },
        ],
        field_relation_ids=[0, 1, 2],
        join_grants=[
            {"grantId": "join-0-1", "leftRelationId": 0, "rightRelationId": 1, "joinType": "inner"},
        ],
        relationships=[
            {"relationshipId": "rel-0-1", "leftRelationId": 0, "rightRelationId": 1},
            {"relationshipId": "rel-1-2", "leftRelationId": 1, "rightRelationId": 2},
        ],
    )
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    with pytest.raises(MetadataJoinClosureErrorV3):
        _select_join_closure_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before, "closure selector mutated its input"


# ===================================================================
# Section M: Single-relation grant gate regression
#
# Regression: the early-return for <=1 required relation must happen
# AFTER all join grants are validated, so that grants referencing
# non-required relations are still checked.
# ===================================================================


def _build_single_relation_request_with_extra_grant(
    *, bad_grant: dict[str, object] | None = None
) -> ResolveMetadataRequestV3:
    """Build a request with one required relation (relation 0) plus an
    extra join grant connecting relation 0 to a non-required relation 1.

    Relation 1 has column grants (needed by the join grant) but NO field,
    so it is NOT in the required set.
    """
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Add relation 1 (synthetic_dim) with a column
    req_wire["metadataSnapshot"]["relations"] = [
        *req_wire["metadataSnapshot"]["relations"],
        {
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "relationKind": "table",
            "columns": [
                {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
    ]

    # Add relation grant + column grant for relation 1 (no field!)
    req_wire["projectContext"]["relationGrants"] = [
        *req_wire["projectContext"]["relationGrants"],
        {
            "grantId": "relgrant-dim",
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "access": "read",
        },
    ]
    req_wire["projectContext"]["columnGrants"] = [
        *req_wire["projectContext"]["columnGrants"],
        {
            "grantId": "colgrant-dim",
            "relationGrantId": "relgrant-dim",
            "columnName": "dim_key",
        },
    ]

    # Add the extra join grant connecting relation 0 to relation 1
    if bad_grant is not None:
        req_wire["projectContext"]["joinGrants"] = [bad_grant]
    else:
        req_wire["projectContext"]["joinGrants"] = [
            {
                "grantId": "join-extra",
                "leftColumnGrantId": "colgrant-key",  # relation 0
                "rightColumnGrantId": "colgrant-dim",  # relation 1
                "joinType": "inner",
            }
        ]

    # Add the relationship edge
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-extra",
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

    return _reclose_all_hashes_from_wire(req_wire)


def test_single_relation_no_grant_returns_empty():
    """No grants at all → empty tuple (single required relation)."""
    # Only relation 0 has a field; no join grants
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"]["joinGrants"] = []
    req_wire["metadataSnapshot"]["relationships"] = []
    request = _reclose_all_hashes_from_wire(req_wire)

    result = _select_join_closure_v3(request)
    assert result == ()


def test_single_relation_extra_valid_grant_returns_empty():
    """One required relation + a valid extra grant → empty tuple.

    The extra grant must be fully validated even though it connects
    to a non-required relation.  After validation succeeds, the
    single required relation means no closure is needed.
    """
    request = _build_single_relation_request_with_extra_grant()
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))

    result = _select_join_closure_v3(request)
    assert result == ()

    # Input not mutated
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_single_relation_extra_grant_bad_column_blocks():
    """Extra grant referencing non-existent column grant is rejected.

    Even with only one required relation, the bad grant must be
    detected — this is the regression the fix addresses.
    """
    bad_grant = {
        "grantId": "join-bad",
        "leftColumnGrantId": "nonexistent-column-grant",
        "rightColumnGrantId": "colgrant-dim",
        "joinType": "inner",
    }
    request = _build_single_relation_request_with_extra_grant(bad_grant=bad_grant)
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"

    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_single_relation_extra_grant_missing_relationship_blocks():
    """Extra grant with valid columns but no matching relationship is rejected."""
    # Build a valid request, then remove the relationship edge
    request = _build_single_relation_request_with_extra_grant()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["metadataSnapshot"]["relationships"] = []  # Remove the edge
    request = _reclose_all_hashes_from_wire(req_wire)
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "JOIN_RELATIONSHIP_NOT_FOUND"

    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_single_relation_extra_grant_ambiguous_relationship_blocks():
    """Extra grant with duplicate matching relationships is rejected."""
    request = _build_single_relation_request_with_extra_grant()
    req_wire = request.model_dump(by_alias=True, mode="json")
    # Add a duplicate relationship edge (different ID, same endpoints)
    req_wire["metadataSnapshot"]["relationships"].append(
        {
            "relationshipId": "rel-extra-dup",
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
    )
    request = _reclose_all_hashes_from_wire(req_wire)
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "JOIN_RELATIONSHIP_AMBIGUOUS"

    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


# ===================================================================
# Section N: Grant check order (different error codes)
# ===================================================================


def test_grant_check_order_different_error_codes():
    """Two bad grants with different error codes: grantId order determines which is reported.

    Three required relations (0, 1, 2), each with a field.
    join-a (grantId sorted first) connects 0-1 with a non-existent column grant.
    join-z (grantId sorted second) connects 1-2 with valid columns but
    no matching relationship edge.
    Must report COLUMN_GRANT_NOT_FOUND first.
    Fixing only join-a must then report JOIN_RELATIONSHIP_NOT_FOUND.
    """
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Add relations 1 (synthetic_dim) and 2 (synthetic_fact)
    req_wire["metadataSnapshot"]["relations"] = [
        *req_wire["metadataSnapshot"]["relations"],
        {
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "relationKind": "table",
            "columns": [
                {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
        {
            "schemaName": "dbo",
            "relationName": "synthetic_fact",
            "relationKind": "table",
            "columns": [
                {"columnName": "fact_key", "sqlType": "int", "nullable": False},
            ],
        },
    ]
    req_wire["projectContext"]["relationGrants"] = [
        *req_wire["projectContext"]["relationGrants"],
        {
            "grantId": "relgrant-dim",
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "access": "read",
        },
        {
            "grantId": "relgrant-fact",
            "schemaName": "dbo",
            "relationName": "synthetic_fact",
            "access": "read",
        },
    ]
    req_wire["projectContext"]["columnGrants"] = [
        *req_wire["projectContext"]["columnGrants"],
        {"grantId": "colgrant-dim", "relationGrantId": "relgrant-dim", "columnName": "dim_key"},
        {"grantId": "colgrant-fact", "relationGrantId": "relgrant-fact", "columnName": "fact_key"},
    ]
    # Add fields in relations 1 and 2 so they become required
    req_wire["bindingRequest"]["queryRequirements"]["fields"].extend(
        [
            {
                "fieldId": "f-dim",
                "role": "value",
                "logicalName": "f_dim",
                "dataType": "string",
                "required": True,
                "evidenceIds": ["ev-fact-declaration"],
            },
            {
                "fieldId": "f-fact",
                "role": "value",
                "logicalName": "f_fact",
                "dataType": "string",
                "required": True,
                "evidenceIds": ["ev-fact-declaration"],
            },
        ]
    )
    req_wire["projectContext"]["fieldBindingAuthorizations"].extend(
        [
            {
                "authorizationId": "fba-dim",
                "requestId": req_wire["bindingRequest"]["requestId"],
                "fieldId": "f-dim",
                "role": "value",
                "columnGrantId": "colgrant-dim",
            },
            {
                "authorizationId": "fba-fact",
                "requestId": req_wire["bindingRequest"]["requestId"],
                "fieldId": "f-fact",
                "role": "value",
                "columnGrantId": "colgrant-fact",
            },
        ]
    )

    # Two bad grants, intentionally in reverse grantId order in the array
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-z",
            "leftColumnGrantId": "colgrant-dim",  # valid column
            "rightColumnGrantId": "colgrant-fact",  # valid column
            "joinType": "inner",
        },
        {
            "grantId": "join-a",
            "leftColumnGrantId": "nonexistent-cg",  # bad: COLUMN_GRANT_NOT_FOUND
            "rightColumnGrantId": "colgrant-dim",
            "joinType": "inner",
        },
    ]
    # Only the 0-1 edge exists; join-z (1-2) has no matching relationship
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-0-1",
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
    request = _reclose_all_hashes_from_wire(req_wire)
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))

    # Both errors present → join-a (sorted first) wins
    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _select_join_closure_v3(request)
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"

    # Fix only join-a → join-z's error surfaces
    req_wire = request.model_dump(by_alias=True, mode="json")
    for g in req_wire["projectContext"]["joinGrants"]:
        if g["grantId"] == "join-a":
            g["leftColumnGrantId"] = "colgrant-key"  # Fix it
    fixed = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataJoinResolutionErrorV3) as exc_info:
        _select_join_closure_v3(fixed)
    assert exc_info.value.code == "JOIN_RELATIONSHIP_NOT_FOUND"

    after = request.model_dump(by_alias=True, mode="json")
    assert after == before, "original request was mutated"


# ===================================================================
# Section O: Order independence (deep-copy + shuffle)
# ===================================================================


def test_order_independence_from_same_wire():
    """Shuffling fields/grants/relationships does not change the result.

    Starts from the same valid wire each time; only reorders arrays.
    """

    def build_base() -> dict[str, object]:
        request = _make_valid_request()
        wire = request.model_dump(by_alias=True, mode="json")
        # Add relation 1 and 2
        wire["metadataSnapshot"]["relations"] = [
            *wire["metadataSnapshot"]["relations"],
            {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "relationKind": "table",
                "columns": [
                    {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
                ],
            },
            {
                "schemaName": "dbo",
                "relationName": "synthetic_fact",
                "relationKind": "table",
                "columns": [
                    {"columnName": "fact_key", "sqlType": "int", "nullable": False},
                ],
            },
        ]
        wire["projectContext"]["relationGrants"] = [
            *wire["projectContext"]["relationGrants"],
            {
                "grantId": "relgrant-dim",
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "access": "read",
            },
            {
                "grantId": "relgrant-fact",
                "schemaName": "dbo",
                "relationName": "synthetic_fact",
                "access": "read",
            },
        ]
        wire["projectContext"]["columnGrants"] = [
            *wire["projectContext"]["columnGrants"],
            {"grantId": "colgrant-dim", "relationGrantId": "relgrant-dim", "columnName": "dim_key"},
            {
                "grantId": "colgrant-fact",
                "relationGrantId": "relgrant-fact",
                "columnName": "fact_key",
            },
        ]
        # Keep base fields (factValue, syntheticKey) and add new fields
        # for relations 1 and 2 so all three become required.
        wire["bindingRequest"]["queryRequirements"]["fields"].extend(
            [
                {
                    "fieldId": "f1",
                    "role": "value",
                    "logicalName": "f1",
                    "dataType": "string",
                    "required": True,
                    "evidenceIds": ["ev-fact-declaration"],
                },
                {
                    "fieldId": "f2",
                    "role": "value",
                    "logicalName": "f2",
                    "dataType": "string",
                    "required": True,
                    "evidenceIds": ["ev-fact-declaration"],
                },
            ]
        )
        wire["projectContext"]["fieldBindingAuthorizations"].extend(
            [
                {
                    "authorizationId": "fba1",
                    "requestId": wire["bindingRequest"]["requestId"],
                    "fieldId": "f1",
                    "role": "value",
                    "columnGrantId": "colgrant-dim",
                },
                {
                    "authorizationId": "fba2",
                    "requestId": wire["bindingRequest"]["requestId"],
                    "fieldId": "f2",
                    "role": "value",
                    "columnGrantId": "colgrant-fact",
                },
            ]
        )
        # Grants: join-m (0-1) and join-a (1-2)
        wire["projectContext"]["joinGrants"] = [
            {
                "grantId": "join-m",
                "leftColumnGrantId": "colgrant-key",
                "rightColumnGrantId": "colgrant-dim",
                "joinType": "inner",
            },
            {
                "grantId": "join-a",
                "leftColumnGrantId": "colgrant-dim",
                "rightColumnGrantId": "colgrant-fact",
                "joinType": "inner",
            },
        ]
        # Relationships with actual column references
        wire["metadataSnapshot"]["relationships"] = [
            {
                "relationshipId": "r01",
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
            },
            {
                "relationshipId": "r12",
                "leftColumn": {
                    "schemaName": "dbo",
                    "relationName": "synthetic_dim",
                    "columnName": "dim_key",
                },
                "rightColumn": {
                    "schemaName": "dbo",
                    "relationName": "synthetic_fact",
                    "columnName": "fact_key",
                },
            },
        ]
        return wire

    base = build_base()
    ref = _select_join_closure_v3(_reclose_all_hashes_from_wire(base))
    assert ref == ("join-a", "join-m")

    # Shuffle arrays and verify same result
    for _ in range(5):
        wire = deepcopy(base)
        random.shuffle(wire["bindingRequest"]["queryRequirements"]["fields"])
        random.shuffle(wire["projectContext"]["joinGrants"])
        random.shuffle(wire["metadataSnapshot"]["relationships"])
        result = _select_join_closure_v3(_reclose_all_hashes_from_wire(wire))
        assert result == ref, f"order independence failed: {result} != {ref}"


# ===================================================================
# Section P: Case sensitivity with actual different spellings
# ===================================================================


def test_case_insensitive_with_different_spelling():
    """insensitive mode: relationship endpoint uses different casing than snapshot."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Add relation 1 with lowercase name
    req_wire["metadataSnapshot"]["relations"] = [
        *req_wire["metadataSnapshot"]["relations"],
        {
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "relationKind": "table",
            "columns": [
                {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
    ]
    req_wire["metadataSnapshot"]["identifierCaseSensitivity"] = "insensitive"
    req_wire["projectContext"]["relationGrants"] = [
        *req_wire["projectContext"]["relationGrants"],
        {
            "grantId": "relgrant-dim",
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "access": "read",
        },
    ]
    req_wire["projectContext"]["columnGrants"] = [
        *req_wire["projectContext"]["columnGrants"],
        {"grantId": "colgrant-dim", "relationGrantId": "relgrant-dim", "columnName": "dim_key"},
    ]
    # Field in relation 1
    req_wire["bindingRequest"]["queryRequirements"]["fields"].append(
        {
            "fieldId": "f-dim",
            "role": "value",
            "logicalName": "f_dim",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        }
    )
    req_wire["projectContext"]["fieldBindingAuthorizations"].append(
        {
            "authorizationId": "fba-dim",
            "requestId": req_wire["bindingRequest"]["requestId"],
            "fieldId": "f-dim",
            "role": "value",
            "columnGrantId": "colgrant-dim",
        }
    )
    # Join grant
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-0-1",
            "leftColumnGrantId": "colgrant-key",
            "rightColumnGrantId": "colgrant-dim",
            "joinType": "inner",
        },
    ]
    # Relationship with DIFFERENT casing on one endpoint
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-0-1",
            "leftColumn": {
                "schemaName": "DBO",
                "relationName": "SYNTHETIC_TABLE",
                "columnName": "SYNTHETIC_KEY",
            },
            "rightColumn": {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "columnName": "dim_key",
            },
        }
    ]
    request = _reclose_all_hashes_from_wire(req_wire)

    result = _select_join_closure_v3(request)
    assert result == ("join-0-1",)


def test_case_sensitive_with_different_spelling_disconnected():
    """sensitive mode: relationship endpoint with different casing → no match."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    req_wire["metadataSnapshot"]["relations"] = [
        *req_wire["metadataSnapshot"]["relations"],
        {
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "relationKind": "table",
            "columns": [
                {"columnName": "dim_key", "sqlType": "nvarchar(100)", "nullable": False},
            ],
        },
    ]
    req_wire["metadataSnapshot"]["identifierCaseSensitivity"] = "sensitive"
    req_wire["projectContext"]["relationGrants"] = [
        *req_wire["projectContext"]["relationGrants"],
        {
            "grantId": "relgrant-dim",
            "schemaName": "dbo",
            "relationName": "synthetic_dim",
            "access": "read",
        },
    ]
    req_wire["projectContext"]["columnGrants"] = [
        *req_wire["projectContext"]["columnGrants"],
        {"grantId": "colgrant-dim", "relationGrantId": "relgrant-dim", "columnName": "dim_key"},
    ]
    req_wire["bindingRequest"]["queryRequirements"]["fields"].append(
        {
            "fieldId": "f-dim",
            "role": "value",
            "logicalName": "f_dim",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        }
    )
    req_wire["projectContext"]["fieldBindingAuthorizations"].append(
        {
            "authorizationId": "fba-dim",
            "requestId": req_wire["bindingRequest"]["requestId"],
            "fieldId": "f-dim",
            "role": "value",
            "columnGrantId": "colgrant-dim",
        }
    )
    req_wire["projectContext"]["joinGrants"] = [
        {
            "grantId": "join-0-1",
            "leftColumnGrantId": "colgrant-key",
            "rightColumnGrantId": "colgrant-dim",
            "joinType": "inner",
        },
    ]
    # Relationship with different casing on one endpoint
    req_wire["metadataSnapshot"]["relationships"] = [
        {
            "relationshipId": "rel-0-1",
            "leftColumn": {
                "schemaName": "DBO",
                "relationName": "SYNTHETIC_TABLE",
                "columnName": "SYNTHETIC_KEY",
            },
            "rightColumn": {
                "schemaName": "dbo",
                "relationName": "synthetic_dim",
                "columnName": "dim_key",
            },
        }
    ]
    request = _reclose_all_hashes_from_wire(req_wire)

    # In sensitive mode, DBO ≠ dbo, so the relationship doesn't match →
    # the grant fails validation → JOIN_RELATIONSHIP_NOT_FOUND
    with pytest.raises(Exception) as exc_info:
        _select_join_closure_v3(request)
    assert "JOIN_RELATIONSHIP_NOT_FOUND" in str(exc_info.value)
