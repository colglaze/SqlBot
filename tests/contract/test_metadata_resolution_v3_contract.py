"""Contract tests for V3 ResolveMetadataRequestV3 (M2 第三子任务).

Tests the structural and type contract only. Does NOT test cross-field
consistency or resolution gating — those belong to resolve_metadata_v3.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from release_sql_bot.application.validate_approval_closure_v3 import (
    validate_approval_closure_v3,
)
from release_sql_bot.application.validate_handoff_closure_v3 import (
    validate_handoff_closure_v3,
)
from release_sql_bot.domain.fact_bindings_v2 import FactBindingRequestV2
from release_sql_bot.domain.project_bindings_v3 import (
    ResolveMetadataRequestV3,
)
from tests.v3_metadata_support import valid_resolve_metadata_request_v3_wire

# ---------------------------------------------------------------------------
# Section 1: Valid payload round-trip
# ===================================================================


def test_valid_request_round_trip() -> None:
    """Valid request camelCase wire round-trips with no loss."""
    wire = valid_resolve_metadata_request_v3_wire()
    req = ResolveMetadataRequestV3.model_validate(wire)
    dumped = req.model_dump(by_alias=True, mode="json")
    assert dumped == wire


def test_schema_version_is_1_0_0() -> None:
    """schemaVersion is fixed at '1.0.0'."""
    req = ResolveMetadataRequestV3.model_validate(
        valid_resolve_metadata_request_v3_wire(),
    )
    assert req.schema_version == "1.0.0"


# ===================================================================
# Section 2: Missing required fields
# ===================================================================


@pytest.mark.parametrize(
    "missing_field",
    [
        "schemaVersion",
        "projectRef",
        "handoffClosure",
        "bindingRequest",
        "projectContext",
        "metadataSnapshot",
        "approvalRecord",
    ],
)
def test_rejects_missing_required_field(missing_field: str) -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    del wire[missing_field]
    with pytest.raises(ValidationError):
        ResolveMetadataRequestV3.model_validate(wire)


# ===================================================================
# Section 3: Extra and snake_case rejection
# ===================================================================


def test_rejects_extra_top_level_field() -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    wire["inventedField"] = True
    with pytest.raises(ValidationError):
        ResolveMetadataRequestV3.model_validate(wire)


def test_rejects_extra_nested_field() -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    wire["projectRef"]["invented"] = True
    with pytest.raises(ValidationError):
        ResolveMetadataRequestV3.model_validate(wire)


def test_rejects_snake_case_top_level() -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    wire["project_ref"] = wire.pop("projectRef")
    with pytest.raises(ValidationError, match="snake_case"):
        ResolveMetadataRequestV3.model_validate(wire)


def test_rejects_snake_case_nested() -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    wire["projectRef"]["project_id"] = wire["projectRef"].pop("projectId")
    with pytest.raises(ValidationError, match="snake_case"):
        ResolveMetadataRequestV3.model_validate(wire)


# ===================================================================
# Section 4: Type coercion rejection
# ===================================================================


def test_rejects_integer_coercion_for_string() -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    wire["projectRef"]["projectId"] = 12345
    with pytest.raises(ValidationError):
        ResolveMetadataRequestV3.model_validate(wire)


def test_rejects_string_coercion_for_integer() -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    wire["projectRef"]["projectVersion"] = "1"
    with pytest.raises(ValidationError):
        ResolveMetadataRequestV3.model_validate(wire)


def test_rejects_string_coercion_for_boolean() -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    wire["metadataSnapshot"]["relations"][0]["columns"][0]["nullable"] = "false"
    with pytest.raises(ValidationError):
        ResolveMetadataRequestV3.model_validate(wire)


# ===================================================================
# Section 5: Domain-specific rejection
# ===================================================================


def test_rejects_binding_gap_report_field() -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingGapReport"] = {"status": "blocked"}
    with pytest.raises(ValidationError):
        ResolveMetadataRequestV3.model_validate(wire)


def test_rejects_repository_verified_field() -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    wire["repositoryVerified"] = True
    with pytest.raises(ValidationError):
        ResolveMetadataRequestV3.model_validate(wire)


def test_rejects_v2_style_rule_ref() -> None:
    """V2 uses ruleId semantics; V3 requires ruleSetId + provenance."""
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingRequest"]["ruleRef"] = {
        "ruleId": "RULE_V1",
        "schemaVersion": "2.0.0",
    }
    with pytest.raises(ValidationError):
        ResolveMetadataRequestV3.model_validate(wire)


def test_rejects_v2_binding_fixture() -> None:
    """Complete V2 fixture is rejected as bindingRequest.

    First prove the fixture is a valid V2 request, then prove V3 rejects it.
    """
    v2_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "fact-binding-request-2.0.0.synthetic-ready.json"
    )
    import json

    v2_payload = json.loads(v2_path.read_text(encoding="utf-8"))
    # Precondition: valid V2 request
    FactBindingRequestV2.model_validate(v2_payload)
    # V3 rejects it
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingRequest"] = v2_payload
    with pytest.raises(ValidationError):
        ResolveMetadataRequestV3.model_validate(wire)


# ===================================================================
# Section 6: Input immutability
# ===================================================================


def test_does_not_mutate_input_wire() -> None:
    wire = valid_resolve_metadata_request_v3_wire()
    original = deepcopy(wire)
    ResolveMetadataRequestV3.model_validate(wire)
    assert wire == original


# ===================================================================
# Section 7: Structurally-valid but semantically-inconsistent
# (allowed by this contract; subsequent resolver MUST block)
# ===================================================================


def test_draft_context_structurally_valid() -> None:
    """context with status=draft is structurally valid.

    Subsequent resolver must block; this contract allows construction.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    wire["projectContext"]["status"] = "draft"
    req = ResolveMetadataRequestV3.model_validate(wire)
    assert req.project_context.status == "draft"


def test_draft_snapshot_structurally_valid() -> None:
    """metadataSnapshot with status=draft is structurally valid.

    Subsequent resolver must block; this contract allows construction.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    wire["metadataSnapshot"]["status"] = "draft"
    req = ResolveMetadataRequestV3.model_validate(wire)
    assert req.metadata_snapshot.status == "draft"


def test_closure_identity_mismatch_structurally_valid() -> None:
    """closure.payload != bindingRequest is structurally valid.

    Subsequent resolver must block; this contract allows construction.
    The bindingRequest is internally consistent (requestId == ruleVersion#factCode)
    but differs from the closure's identity.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    wire["bindingRequest"]["ruleRef"]["ruleSetId"] = "DIFFERENT"
    wire["bindingRequest"]["ruleRef"]["ruleVersion"] = "DIFFERENT@v1"
    wire["bindingRequest"]["requestId"] = "DIFFERENT@v1#fact.one"
    wire["bindingRequest"]["fact"]["factCode"] = "fact.one"
    wire["bindingRequest"]["mappingCandidate"]["factCode"] = "fact.one"
    for field in wire["bindingRequest"]["queryRequirements"]["fields"]:
        if field["fieldId"] == "factValue":
            field["logicalName"] = "fact.one"
    req = ResolveMetadataRequestV3.model_validate(wire)
    assert req.binding_request.request_id != req.handoff_closure.request_id


def test_project_ref_mismatch_structurally_valid() -> None:
    """projectRef != projectContext.projectRef is structurally valid.

    Subsequent resolver must block; this contract allows construction.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    wire["projectRef"]["projectId"] = "different-proj"
    req = ResolveMetadataRequestV3.model_validate(wire)
    assert req.project_ref.project_id != req.project_context.project_ref.project_id


# ===================================================================
# Section 8: Serialization does not contain execution/review fields
# ===================================================================


def test_serialized_request_has_no_execution_fields() -> None:
    """Serialized request does not contain executable/reviewStatus/etc."""
    req = ResolveMetadataRequestV3.model_validate(
        valid_resolve_metadata_request_v3_wire(),
    )
    dumped = req.model_dump(by_alias=True, mode="json")
    assert "executable" not in dumped
    assert "reviewStatus" not in dumped
    assert "bindingGapReport" not in dumped
    assert "repositoryVerified" not in dumped


# ===================================================================
# Section 9: Fixture self-consistency (offline content-closure only)
# ===================================================================


def test_fixture_passes_both_closure_validators() -> None:
    """The synthetic fixture passes both content-closure validators.

    This proves offline self-consistency only; it does NOT constitute
    repository attestation or metadataResolved.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    req = ResolveMetadataRequestV3.model_validate(wire)
    # Handoff closure passes its 6-group validation
    assert validate_handoff_closure_v3(req.handoff_closure) is None
    # Approval closure passes its 9-group validation
    assert (
        validate_approval_closure_v3(
            req.project_context,
            req.metadata_snapshot,
            req.approval_record,
        )
        is None
    )


def test_fixture_identity_consistency() -> None:
    """bindingRequest == closure.payload; projectRef/ruleRef/requestId consistent."""
    from release_sql_bot.application.canonical import canonical_sha256

    wire = valid_resolve_metadata_request_v3_wire()
    req = ResolveMetadataRequestV3.model_validate(wire)
    # bindingRequest is identical to closure.payload
    assert req.binding_request == req.handoff_closure.payload
    # Hash evidence: payloadSha256 == canonical_sha256(payload)
    assert req.handoff_closure.payload_sha256 == canonical_sha256(
        req.handoff_closure.payload,
    )
    # Handoff closure passes its own validation
    assert validate_handoff_closure_v3(req.handoff_closure) is None
    # projectRef fully equal (not just projectId)
    assert req.project_ref == req.project_context.project_ref
    # ruleRef fully equal (all six fields)
    assert req.binding_request.rule_ref == req.project_context.rule_ref
    # requestId in context.requestIds
    assert req.binding_request.request_id in req.project_context.request_ids
    # All field/entity-key authorizations reference current requestId
    for fba in req.project_context.field_binding_authorizations:
        assert fba.request_id == req.binding_request.request_id
    for eka in req.project_context.entity_key_authorizations:
        assert eka.request_id == req.binding_request.request_id


def _assert_fixture_entity_key_complete(
    req: ResolveMetadataRequestV3,
) -> None:
    """Assert entity-key and factValue reference closure with unique lookups.

    Shared by the happy-path test and the two diagnostic tests.
    Proves synthetic authorization reference closure only.
    """
    br = req.binding_request

    # 1. queryRequirements.fields has entityKey field
    ek_fields = [f for f in br.query_requirements.fields if f.role == "entityKey"]
    assert len(ek_fields) == 1
    assert ek_fields[0].field_id == "syntheticKey"
    assert ek_fields[0].data_type == "string"

    # 2. fact.parameters has entityKey parameter
    ek_params = [p for p in br.fact.parameters if p.role == "entityKey"]
    assert len(ek_params) == 1
    assert ek_params[0].name == "syntheticKey"
    assert ek_params[0].data_type == "string"

    # 3. entity.keyParameters contains syntheticKey
    assert "syntheticKey" in br.query_requirements.entity.key_parameters

    # 4. Unique entityKey field binding and authorization share IDs
    fba_key = [f for f in req.project_context.field_binding_authorizations if f.role == "entityKey"]
    eka_list = req.project_context.entity_key_authorizations
    assert len(fba_key) == 1
    assert len(eka_list) == 1
    fba = fba_key[0]
    eka = eka_list[0]
    assert fba.request_id == eka.request_id == br.request_id
    assert fba.field_id == eka.field_id == "syntheticKey"
    assert fba.column_grant_id == eka.column_grant_id

    # 5. columnGrantId exists in context.columnGrants
    col_grant_ids = [cg.grant_id for cg in req.project_context.column_grants]
    assert fba.column_grant_id in col_grant_ids

    # 6. entityKey physical reference chain — each level asserts uniqueness
    col_grants = [
        cg for cg in req.project_context.column_grants if cg.grant_id == fba.column_grant_id
    ]
    assert len(col_grants) == 1, "expected exactly one entityKey column grant"
    col_grant = col_grants[0]

    rel_grants = [
        rg
        for rg in req.project_context.relation_grants
        if rg.grant_id == col_grant.relation_grant_id
    ]
    assert len(rel_grants) == 1, "expected exactly one entityKey relation grant"
    rel_grant = rel_grants[0]

    snap_rels = [
        r
        for r in req.metadata_snapshot.relations
        if r.schema_name == rel_grant.schema_name
        if r.relation_name == rel_grant.relation_name
    ]
    assert len(snap_rels) == 1, "expected exactly one snapshot relation for entityKey"
    snap_rel = snap_rels[0]

    snap_cols = [c for c in snap_rel.columns if c.column_name == col_grant.column_name]
    assert len(snap_cols) == 1, "expected exactly one entityKey snapshot column"
    snap_col = snap_cols[0]
    # 7. String-compatible type
    assert snap_col.sql_type == "nvarchar(100)"

    # 8. factValue maps to a separate integer column
    fba_value = [f for f in req.project_context.field_binding_authorizations if f.role == "value"]
    assert len(fba_value) == 1
    assert fba_value[0].field_id == "factValue"
    assert fba_value[0].column_grant_id != fba.column_grant_id

    val_col_grants = [
        cg
        for cg in req.project_context.column_grants
        if cg.grant_id == fba_value[0].column_grant_id
    ]
    assert len(val_col_grants) == 1, "expected exactly one factValue column grant"
    val_col_grant = val_col_grants[0]

    val_rel_grants = [
        rg
        for rg in req.project_context.relation_grants
        if rg.grant_id == val_col_grant.relation_grant_id
    ]
    assert len(val_rel_grants) == 1, "expected exactly one factValue relation grant"
    val_rel_grant = val_rel_grants[0]

    val_snap_rels = [
        r
        for r in req.metadata_snapshot.relations
        if r.schema_name == val_rel_grant.schema_name
        if r.relation_name == val_rel_grant.relation_name
    ]
    assert len(val_snap_rels) == 1, "expected exactly one snapshot relation for factValue"
    val_snap_rel = val_snap_rels[0]

    val_snap_cols = [c for c in val_snap_rel.columns if c.column_name == val_col_grant.column_name]
    assert len(val_snap_cols) == 1, "expected exactly one factValue snapshot column"
    val_snap_col = val_snap_cols[0]
    assert val_snap_col.sql_type == "int"
    assert val_col_grant.column_name != col_grant.column_name


def test_fixture_entity_key_complete() -> None:
    """syntheticKey has complete authorization reference closure.

    Proves synthetic authorization reference closure only; does NOT
    constitute eight-step resolution.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    req = ResolveMetadataRequestV3.model_validate(wire)
    _assert_fixture_entity_key_complete(req)


def test_entity_key_unique_assertion_fails_with_duplicate_relation() -> None:
    """Diagnostic: duplicate snapshot relation triggers the entityKey assertion.

    Uses the same assertion helper as the happy-path test.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    # Inject a duplicate snapshot relation
    extra_rel = deepcopy(wire["metadataSnapshot"]["relations"][0])
    wire["metadataSnapshot"]["relations"].append(extra_rel)
    # Structural validation succeeds (extra=forbid not triggered)
    req = ResolveMetadataRequestV3.model_validate(wire)
    # The entityKey uniqueness assertion fails
    with pytest.raises(
        AssertionError,
        match="expected exactly one snapshot relation for entityKey",
    ):
        _assert_fixture_entity_key_complete(req)


def test_entity_key_unique_assertion_fails_with_duplicate_column() -> None:
    """Diagnostic: duplicate snapshot column triggers the entityKey assertion.

    Uses the same assertion helper as the happy-path test.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    # Inject a duplicate entity-key column into the target relation
    extra_col = deepcopy(wire["metadataSnapshot"]["relations"][0]["columns"][1])
    wire["metadataSnapshot"]["relations"][0]["columns"].append(extra_col)
    # Structural validation succeeds
    req = ResolveMetadataRequestV3.model_validate(wire)
    # The entityKey uniqueness assertion fails
    with pytest.raises(
        AssertionError,
        match="expected exactly one entityKey snapshot column",
    ):
        _assert_fixture_entity_key_complete(req)


def test_fixture_snapshot_ref_matches_actual() -> None:
    """context.metadataSnapshotRef matches the actual snapshot ID/version/hash."""
    wire = valid_resolve_metadata_request_v3_wire()
    req = ResolveMetadataRequestV3.model_validate(wire)
    snap = req.metadata_snapshot
    snap_ref = req.project_context.metadata_snapshot_ref
    assert snap_ref.snapshot_id == snap.snapshot_id
    assert snap_ref.snapshot_version == snap.snapshot_version
    assert snap_ref.sha256 == snap.content_sha256


def test_fixture_two_calls_unpolluted() -> None:
    """Two consecutive fixture calls return independent objects."""
    wire1 = valid_resolve_metadata_request_v3_wire()
    wire2 = valid_resolve_metadata_request_v3_wire()
    assert wire1 is not wire2
    assert wire1["handoffClosure"] is not wire2["handoffClosure"]
    assert wire1["bindingRequest"] is not wire2["bindingRequest"]
    # Mutating one does not affect the other
    wire1["projectRef"]["projectId"] = "mutated"
    assert wire2["projectRef"]["projectId"] == "proj-1"


# ===================================================================
# Section 10: No V2 / infrastructure dependencies
# ===================================================================


def test_module_does_not_import_v2_or_infrastructure() -> None:
    import release_sql_bot.domain.project_bindings_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "project_bindings_v2" not in source
    assert "fact_bindings_v2" not in source
    assert "BindingRuleRefV2" not in source
    assert "mongodb" not in source
    assert "pymongo" not in source
    assert "sqlglot" not in source.lower()
    assert "os.environ" not in source
    assert "getenv" not in source
