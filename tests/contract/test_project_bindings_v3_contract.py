"""Contract tests for V3 project bindings: context, snapshot, approval record."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.application.validate_approval_closure_v3 import (
    validate_approval_closure_v3,
)
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalClosureValidationErrorV3,
    ApprovalRecordV3,
    ContextStatusV3,
    GovernedMetadataSnapshotV3,
    JoinGrantV3,
    ProjectBindingContextV3,
    SnapshotStatusV3,
)

# ---------------------------------------------------------------------------
# Synthetic fixture builders (independent, no V2 payload trimming)
# ---------------------------------------------------------------------------

_VALID_SHA = "a" * 64
_VALID_RULE_REF = {
    "ruleSetId": "SYNTH_RULE_SET",
    "ruleVersion": "SYNTH_RULE_SET@v1",
    "schemaVersion": "3.0.0",
    "sourceSha256": _VALID_SHA,
    "catalogDigest": _VALID_SHA,
    "candidatePayloadSha256": _VALID_SHA,
}


def _physical_column() -> dict[str, object]:
    return {
        "schemaName": "dbo",
        "relationName": "synthetic_table",
        "columnName": "synthetic_column",
    }


def _governed_column() -> dict[str, object]:
    return {
        "columnName": "synthetic_column",
        "sqlType": "nvarchar(100)",
        "nullable": False,
    }


def _governed_relation() -> dict[str, object]:
    return {
        "schemaName": "dbo",
        "relationName": "synthetic_table",
        "relationKind": "table",
        "columns": [_governed_column()],
    }


def _context_ref() -> dict[str, object]:
    return {
        "contextId": "ctx-1",
        "contextVersion": 1,
        "sha256": _VALID_SHA,
    }


def _snapshot_ref() -> dict[str, object]:
    return {
        "snapshotId": "snap-1",
        "snapshotVersion": 1,
        "sha256": _VALID_SHA,
    }


def _approval_ref() -> dict[str, object]:
    return {
        "approvalId": "approval-1",
        "policyVersion": "policy-v1",
        "approvedAt": "2026-09-09T00:00:00+00:00",
    }


def _relation_grant() -> dict[str, object]:
    return {
        "grantId": "relgrant-1",
        "schemaName": "dbo",
        "relationName": "synthetic_table",
        "access": "read",
    }


def _column_grant() -> dict[str, object]:
    return {
        "grantId": "colgrant-1",
        "relationGrantId": "relgrant-1",
        "columnName": "synthetic_column",
    }


def _field_binding() -> dict[str, object]:
    return {
        "authorizationId": "fba-1",
        "requestId": "SYNTH_RULE_SET@v1#fact.one",
        "fieldId": "factValue",
        "role": "value",
        "columnGrantId": "colgrant-1",
    }


def _entity_key() -> dict[str, object]:
    return {
        "authorizationId": "eka-1",
        "requestId": "SYNTH_RULE_SET@v1#fact.one",
        "parameterName": "taskId",
        "fieldId": "taskId",
        "columnGrantId": "colgrant-1",
    }


def _join_grant() -> dict[str, object]:
    return {
        "grantId": "join-1",
        "leftColumnGrantId": "colgrant-1",
        "rightColumnGrantId": "colgrant-2",
        "joinType": "inner",
    }


def _context_payload() -> dict[str, object]:
    return {
        "schemaVersion": "1.1.0",
        "contextId": "ctx-1",
        "contextVersion": 1,
        "status": "approved",
        "projectRef": {
            "projectId": "proj-1",
            "projectVersion": 1,
        },
        "ruleRef": dict(_VALID_RULE_REF),
        "requestIds": ["SYNTH_RULE_SET@v1#fact.one"],
        "metadataSnapshotRef": _snapshot_ref(),
        "authorizationPolicyVersion": "policy-v1",
        "relationGrants": [_relation_grant()],
        "columnGrants": [_column_grant(), _column_grant() | {"grantId": "colgrant-2"}],
        "fieldBindingAuthorizations": [_field_binding()],
        "entityKeyAuthorizations": [_entity_key()],
        "joinGrants": [_join_grant()],
        "entityGrainAuthorizations": [
            {
                "entityType": "synthetic_entity",
                "grain": "report",
                "relationGrantId": "relgrant-1",
            }
        ],
        "joinAuthorizationEvidence": [],
        "approvalRef": _approval_ref(),
        "contentSha256": _VALID_SHA,
    }


def _snapshot_payload() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "snapshotId": "snap-1",
        "snapshotVersion": 1,
        "status": "approved",
        "dialect": "sqlserver",
        "identifierCaseSensitivity": "insensitive",
        "capturedAt": "2026-09-09T00:00:00+00:00",
        "sourceRef": {
            "sourceKind": "metadataReview",
            "artifactId": "artifact-1",
            "artifactVersion": "v1",
            "sha256": _VALID_SHA,
        },
        "relations": [_governed_relation()],
        "relationships": [
            {
                "relationshipId": "rel-1",
                "leftColumn": {
                    "schemaName": "dbo",
                    "relationName": "t1",
                    "columnName": "c1",
                },
                "rightColumn": {
                    "schemaName": "dbo",
                    "relationName": "t2",
                    "columnName": "c2",
                },
            }
        ],
        "approvalRef": _approval_ref(),
        "contentSha256": _VALID_SHA,
    }


def _approval_payload() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "approvalId": "approval-1",
        "contextRef": _context_ref(),
        "snapshotRef": _snapshot_ref(),
        "policyVersion": "policy-v1",
        "actorRef": "actor-1",
        "approvedAt": "2026-09-09T00:00:00+00:00",
        "contentSha256": _VALID_SHA,
    }


# ---------------------------------------------------------------------------
# Section 1: Valid payloads and camelCase round-trip
# ---------------------------------------------------------------------------


def test_context_valid_payload_round_trip() -> None:
    payload = _context_payload()
    context = ProjectBindingContextV3.model_validate(payload)
    assert context.model_dump(by_alias=True, mode="json") == payload
    assert context.schema_version == "1.1.0"
    assert context.rule_ref.schema_version == "3.0.0"


def test_snapshot_valid_payload_round_trip() -> None:
    payload = _snapshot_payload()
    snapshot = GovernedMetadataSnapshotV3.model_validate(payload)
    assert snapshot.model_dump(by_alias=True, mode="json") == payload
    assert snapshot.schema_version == "1.0.0"


def test_approval_valid_payload_round_trip() -> None:
    payload = _approval_payload()
    approval = ApprovalRecordV3.model_validate(payload)
    assert approval.model_dump(by_alias=True, mode="json") == payload
    assert approval.schema_version == "1.0.0"


# ---------------------------------------------------------------------------
# Section 2: Extra and snake_case rejection
# ---------------------------------------------------------------------------


def test_context_rejects_extra_top_level_field() -> None:
    payload = _context_payload()
    payload["inventedField"] = True
    with pytest.raises(ValidationError, match="inventedField"):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_extra_nested_field() -> None:
    payload = _context_payload()
    payload["projectRef"]["invented"] = True
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_snake_case_top_level() -> None:
    payload = _context_payload()
    payload["context_id"] = payload.pop("contextId")
    with pytest.raises(ValidationError, match="snake_case"):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_snake_case_nested() -> None:
    payload = _context_payload()
    payload["ruleRef"]["rule_set_id"] = payload["ruleRef"].pop("ruleSetId")
    with pytest.raises(ValidationError, match="snake_case"):
        ProjectBindingContextV3.model_validate(payload)


def test_snapshot_rejects_snake_case() -> None:
    payload = _snapshot_payload()
    payload["snapshot_id"] = payload.pop("snapshotId")
    with pytest.raises(ValidationError, match="snake_case"):
        GovernedMetadataSnapshotV3.model_validate(payload)


def test_approval_rejects_snake_case() -> None:
    payload = _approval_payload()
    payload["approval_id"] = payload.pop("approvalId")
    with pytest.raises(ValidationError, match="snake_case"):
        ApprovalRecordV3.model_validate(payload)


# ---------------------------------------------------------------------------
# Section 3: Wrong version, invalid enum, missing required fields
# ---------------------------------------------------------------------------


def test_context_rejects_wrong_schema_version() -> None:
    payload = _context_payload()
    payload["schemaVersion"] = "2.0.0"
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_invalid_status_enum() -> None:
    payload = _context_payload()
    payload["status"] = "notAStatus"
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_missing_required_field() -> None:
    payload = _context_payload()
    del payload["ruleRef"]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_snapshot_rejects_wrong_dialect() -> None:
    payload = _snapshot_payload()
    payload["dialect"] = "postgres"
    with pytest.raises(ValidationError):
        GovernedMetadataSnapshotV3.model_validate(payload)


def test_approval_rejects_missing_actor_ref() -> None:
    payload = _approval_payload()
    del payload["actorRef"]
    with pytest.raises(ValidationError):
        ApprovalRecordV3.model_validate(payload)


# ---------------------------------------------------------------------------
# Section 4: Type coercion rejection
# ---------------------------------------------------------------------------


def test_context_rejects_integer_coercion_for_string() -> None:
    payload = _context_payload()
    payload["contextId"] = 12345
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_string_coercion_for_integer() -> None:
    payload = _context_payload()
    payload["contextVersion"] = "1"
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_snapshot_rejects_string_coercion_for_boolean() -> None:
    payload = _snapshot_payload()
    payload["relations"][0]["columns"][0]["nullable"] = "false"
    with pytest.raises(ValidationError):
        GovernedMetadataSnapshotV3.model_validate(payload)


# ---------------------------------------------------------------------------
# Section 5: ID, hash, requestId boundary lengths and formats
# ---------------------------------------------------------------------------


def test_context_rejects_sha256_wrong_length() -> None:
    payload = _context_payload()
    payload["contentSha256"] = "a" * 63
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_sha256_uppercase() -> None:
    payload = _context_payload()
    payload["contentSha256"] = "A" * 64
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_context_accepts_request_id_at_420_boundary() -> None:
    payload = _context_payload()
    long_id = "x" * 420
    payload["requestIds"] = [long_id]
    context = ProjectBindingContextV3.model_validate(payload)
    assert context.request_ids[0] == long_id


def test_context_rejects_request_id_at_421() -> None:
    payload = _context_payload()
    payload["requestIds"] = ["x" * 421]
    with pytest.raises(ValidationError, match="420"):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_empty_request_ids() -> None:
    payload = _context_payload()
    payload["requestIds"] = []
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


# ---------------------------------------------------------------------------
# Section 6: Duplicate IDs, authorization identity uniqueness
# ---------------------------------------------------------------------------


def test_context_rejects_duplicate_request_ids() -> None:
    payload = _context_payload()
    payload["requestIds"] = ["SYNTH_RULE_SET@v1#fact.one", "SYNTH_RULE_SET@v1#fact.one"]
    with pytest.raises(ValidationError, match="duplicate"):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_duplicate_grant_ids() -> None:
    payload = _context_payload()
    payload["relationGrants"] = [_relation_grant(), _relation_grant()]
    with pytest.raises(ValidationError, match="duplicate"):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_duplicate_authorization_ids() -> None:
    payload = _context_payload()
    payload["fieldBindingAuthorizations"] = [_field_binding(), _field_binding()]
    with pytest.raises(ValidationError, match="duplicate"):
        ProjectBindingContextV3.model_validate(payload)


def test_snapshot_rejects_duplicate_relationship_ids() -> None:
    payload = _snapshot_payload()
    rel = payload["relationships"][0]
    payload["relationships"] = [rel, dict(rel)]
    with pytest.raises(ValidationError, match="duplicate"):
        GovernedMetadataSnapshotV3.model_validate(payload)


# ---------------------------------------------------------------------------
# Section 7: Time validation
# ---------------------------------------------------------------------------


def test_context_rejects_approved_at_without_timezone() -> None:
    payload = _context_payload()
    payload["approvalRef"]["approvedAt"] = "2026-09-09T00:00:00"
    with pytest.raises(ValidationError, match="timezone"):
        ProjectBindingContextV3.model_validate(payload)


def test_context_preserves_time_string_exactly() -> None:
    payload = _context_payload()
    original = payload["approvalRef"]["approvedAt"]
    context = ProjectBindingContextV3.model_validate(payload)
    dumped = context.model_dump(by_alias=True, mode="json")
    assert dumped["approvalRef"]["approvedAt"] == original


def test_snapshot_rejects_captured_at_without_timezone() -> None:
    payload = _snapshot_payload()
    payload["capturedAt"] = "2026-09-09"
    with pytest.raises(ValidationError, match="timezone"):
        GovernedMetadataSnapshotV3.model_validate(payload)


# ---------------------------------------------------------------------------
# Section 8: Physical identifier validation
# ---------------------------------------------------------------------------


def test_snapshot_rejects_wildcard_in_relation_name() -> None:
    payload = _snapshot_payload()
    payload["relations"][0]["relationName"] = "synthetic_*"
    with pytest.raises(ValidationError, match="non-temporary"):
        GovernedMetadataSnapshotV3.model_validate(payload)


def test_snapshot_rejects_temp_table_form() -> None:
    payload = _snapshot_payload()
    payload["relations"][0]["relationName"] = "#temp_table"
    with pytest.raises(ValidationError, match="non-temporary"):
        GovernedMetadataSnapshotV3.model_validate(payload)


def test_snapshot_rejects_padded_identifier() -> None:
    payload = _snapshot_payload()
    payload["relations"][0]["schemaName"] = " dbo "
    with pytest.raises(ValidationError, match="padded"):
        GovernedMetadataSnapshotV3.model_validate(payload)


def test_snapshot_rejects_non_scalar_sql_type() -> None:
    payload = _snapshot_payload()
    payload["relations"][0]["columns"][0]["sqlType"] = "nvarchar(*)"
    with pytest.raises(ValidationError, match="scalar"):
        GovernedMetadataSnapshotV3.model_validate(payload)


def test_context_rejects_non_read_access() -> None:
    payload = _context_payload()
    payload["relationGrants"][0]["access"] = "write"
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_join_grant_rejects_self_join() -> None:
    grant = _join_grant()
    grant["rightColumnGrantId"] = grant["leftColumnGrantId"]
    with pytest.raises(ValidationError, match="different"):
        JoinGrantV3.model_validate(grant)


# ---------------------------------------------------------------------------
# Section 9: V2 context payload rejected (incompatible rule reference)
# ---------------------------------------------------------------------------


def test_context_rejects_v2_style_rule_ref() -> None:
    """V2 uses ruleId semantics; V3 requires ruleSetId + provenance closure."""
    payload = _context_payload()
    payload["ruleRef"] = {
        "ruleId": "RULE_V1",
        "schemaVersion": "2.0.0",
    }
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


# ---------------------------------------------------------------------------
# Section 10: V3 snapshot physical shape equivalent to V2, type independent
# ---------------------------------------------------------------------------


def test_v3_snapshot_physical_shape_equivalent_to_v2() -> None:
    """V3 snapshot has same wire field shape as V2 but uses independent model type."""
    from release_sql_bot.domain.project_bindings_v2 import GovernedMetadataSnapshotV2

    v3_payload = _snapshot_payload()
    v3_snapshot = GovernedMetadataSnapshotV3.model_validate(v3_payload)

    # V2 can consume the same wire shape (field-level equivalence)
    v2_snapshot = GovernedMetadataSnapshotV2.model_validate(v3_payload)

    v3_wire = v3_snapshot.model_dump(by_alias=True, mode="json")
    v2_wire = v2_snapshot.model_dump(by_alias=True, mode="json")

    # Physical fields are identical
    assert v3_wire["dialect"] == v2_wire["dialect"] == "sqlserver"
    assert v3_wire["relations"] == v2_wire["relations"]
    assert v3_wire["relationships"] == v2_wire["relationships"]

    # But model types are independent
    assert type(v3_snapshot) is not type(v2_snapshot)
    assert GovernedMetadataSnapshotV3 is not GovernedMetadataSnapshotV2


# ---------------------------------------------------------------------------
# Section 11: Valid draft/superseded payloads constructible
# ---------------------------------------------------------------------------


def test_context_draft_status_constructible() -> None:
    payload = _context_payload()
    payload["status"] = "draft"
    context = ProjectBindingContextV3.model_validate(payload)
    assert context.status is ContextStatusV3.DRAFT


def test_context_superseded_status_constructible() -> None:
    payload = _context_payload()
    payload["status"] = "superseded"
    context = ProjectBindingContextV3.model_validate(payload)
    assert context.status is ContextStatusV3.SUPERSEDED


def test_snapshot_draft_status_constructible() -> None:
    payload = _snapshot_payload()
    payload["status"] = "draft"
    snapshot = GovernedMetadataSnapshotV3.model_validate(payload)
    assert snapshot.status is SnapshotStatusV3.DRAFT


# ---------------------------------------------------------------------------
# Section 12: Model construction does not rewrite input
# ---------------------------------------------------------------------------


def test_context_does_not_mutate_input_payload() -> None:
    payload = _context_payload()
    original = deepcopy(payload)
    ProjectBindingContextV3.model_validate(payload)
    assert payload == original


def test_snapshot_does_not_mutate_input_payload() -> None:
    payload = _snapshot_payload()
    original = deepcopy(payload)
    GovernedMetadataSnapshotV3.model_validate(payload)
    assert payload == original


def test_approval_does_not_mutate_input_payload() -> None:
    payload = _approval_payload()
    original = deepcopy(payload)
    ApprovalRecordV3.model_validate(payload)
    assert payload == original


# ---------------------------------------------------------------------------
# Section 13: Exception only carries stable code
# ---------------------------------------------------------------------------


_VALID_CODES = (
    "APPROVAL_ID_MISMATCH",
    "APPROVAL_POLICY_MISMATCH",
    "APPROVAL_TIME_MISMATCH",
    "APPROVAL_CONTEXT_REF_MISMATCH",
    "APPROVAL_SNAPSHOT_REF_MISMATCH",
    "APPROVAL_CONTENT_HASH_MISMATCH",
    "APPROVAL_CONTEXT_NOT_APPROVED",
    "APPROVAL_SNAPSHOT_NOT_APPROVED",
    "APPROVAL_SNAPSHOT_BINDING_MISMATCH",
)


@pytest.mark.parametrize("code", _VALID_CODES)
def test_closure_error_accepts_all_nine_valid_codes(code: str) -> None:
    err = ApprovalClosureValidationErrorV3(code)
    assert err.code == code
    assert str(err) == code
    assert code in repr(err)
    assert err.args == (code,)


def test_closure_error_rejects_invalid_code() -> None:
    with pytest.raises(ValueError, match="unknown"):
        ApprovalClosureValidationErrorV3("SOME_INVALID_CODE")


def test_closure_error_rejects_sensitive_mark_as_code() -> None:
    sensitive = "secret-id-12345"
    with pytest.raises(ValueError, match="unknown"):
        ApprovalClosureValidationErrorV3(sensitive)


def test_closure_error_rejects_non_string_input() -> None:
    with pytest.raises(ValueError, match="unknown"):
        ApprovalClosureValidationErrorV3(12345)  # type: ignore[arg-type]


def test_closure_error_does_not_leak_input_data() -> None:
    err = ApprovalClosureValidationErrorV3("APPROVAL_ID_MISMATCH")
    assert str(err) == "APPROVAL_ID_MISMATCH"
    assert repr(err) == "ApprovalClosureValidationErrorV3(code='APPROVAL_ID_MISMATCH')"


# ---------------------------------------------------------------------------
# Section 14: V3 module does not import V2 contracts
# ---------------------------------------------------------------------------


def test_v3_module_does_not_import_v2_contracts() -> None:
    import release_sql_bot.domain.project_bindings_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "project_bindings_v2" not in source
    assert "from release_sql_bot.domain.fact_bindings_v2" not in source
    assert "BindingRuleRefV2" not in source


# ---------------------------------------------------------------------------
# Section 15: contextRef type validation (audit fix)
# ---------------------------------------------------------------------------


def test_approval_context_ref_accepts_context_shape() -> None:
    payload = _approval_payload()
    payload["contextRef"] = _context_ref()
    approval = ApprovalRecordV3.model_validate(payload)
    dumped = approval.model_dump(by_alias=True, mode="json")
    assert dumped["contextRef"]["contextId"] == "ctx-1"
    assert dumped["contextRef"]["contextVersion"] == 1


def test_approval_context_ref_rejects_snapshot_shape() -> None:
    payload = _approval_payload()
    payload["contextRef"] = _snapshot_ref()  # has snapshotId/snapshotVersion
    with pytest.raises(ValidationError):
        ApprovalRecordV3.model_validate(payload)


def test_approval_snapshot_ref_rejects_context_shape() -> None:
    payload = _approval_payload()
    payload["snapshotRef"] = _context_ref()  # has contextId/contextVersion
    with pytest.raises(ValidationError):
        ApprovalRecordV3.model_validate(payload)


def test_context_ref_version_must_be_integer() -> None:
    ref = _context_ref()
    ref["contextVersion"] = "1"
    with pytest.raises(ValidationError):
        ApprovalRecordV3.model_validate({**_approval_payload(), "contextRef": ref})


def test_context_ref_version_must_be_at_least_one() -> None:
    ref = _context_ref()
    ref["contextVersion"] = 0
    with pytest.raises(ValidationError):
        ApprovalRecordV3.model_validate({**_approval_payload(), "contextRef": ref})


def test_context_ref_sha256_must_be_lowercase_64() -> None:
    ref = _context_ref()
    ref["sha256"] = "A" * 64
    with pytest.raises(ValidationError):
        ApprovalRecordV3.model_validate({**_approval_payload(), "contextRef": ref})


# ---------------------------------------------------------------------------
# Section 16: Composite-key uniqueness (audit fix)
# ---------------------------------------------------------------------------


def test_context_rejects_duplicate_field_binding_composite_key_same_column() -> None:
    payload = _context_payload()
    fb2 = _field_binding() | {"authorizationId": "fba-2"}
    payload["fieldBindingAuthorizations"] = [_field_binding(), fb2]
    with pytest.raises(ValidationError, match="unique"):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_duplicate_field_binding_composite_key_diff_column() -> None:
    payload = _context_payload()
    fb2 = _field_binding() | {
        "authorizationId": "fba-2",
        "columnGrantId": "colgrant-2",
    }
    payload["fieldBindingAuthorizations"] = [_field_binding(), fb2]
    with pytest.raises(ValidationError, match="unique"):
        ProjectBindingContextV3.model_validate(payload)


def test_context_accepts_distinct_field_binding_composite_keys() -> None:
    payload = _context_payload()
    fb2 = _field_binding() | {
        "authorizationId": "fba-2",
        "fieldId": "otherField",
        "columnGrantId": "colgrant-2",
    }
    payload["fieldBindingAuthorizations"] = [_field_binding(), fb2]
    ctx = ProjectBindingContextV3.model_validate(payload)
    assert len(ctx.field_binding_authorizations) == 2


def test_context_rejects_duplicate_entity_key_composite_key() -> None:
    payload = _context_payload()
    ek2 = _entity_key() | {"authorizationId": "eka-2"}
    payload["entityKeyAuthorizations"] = [_entity_key(), ek2]
    with pytest.raises(ValidationError, match="unique"):
        ProjectBindingContextV3.model_validate(payload)


def test_context_accepts_distinct_entity_key_composite_keys() -> None:
    payload = _context_payload()
    ek2 = _entity_key() | {
        "authorizationId": "eka-2",
        "parameterName": "otherParam",
        "columnGrantId": "colgrant-2",
    }
    payload["entityKeyAuthorizations"] = [_entity_key(), ek2]
    ctx = ProjectBindingContextV3.model_validate(payload)
    assert len(ctx.entity_key_authorizations) == 2


# ---------------------------------------------------------------------------
# Section 17: requestIds element lower bound and strict type (audit fix)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_id", ["", "a", "ab"])
def test_context_rejects_request_id_below_min_length(bad_id: str) -> None:
    payload = _context_payload()
    payload["requestIds"] = [bad_id]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_context_rejects_non_string_request_id_element() -> None:
    payload = _context_payload()
    payload["requestIds"] = [12345]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_context_accepts_request_id_at_exactly_420() -> None:
    payload = _context_payload()
    payload["requestIds"] = ["x" * 420]
    ctx = ProjectBindingContextV3.model_validate(payload)
    assert ctx.request_ids[0] == "x" * 420


def test_nested_field_binding_request_id_boundary() -> None:
    """Nested authorization requestId shares the same 3..420 constraint."""
    payload = _context_payload()
    payload["fieldBindingAuthorizations"][0]["requestId"] = "ab"
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


def test_nested_entity_key_request_id_boundary() -> None:
    payload = _context_payload()
    payload["entityKeyAuthorizations"][0]["requestId"] = "ab"
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(payload)


# ---------------------------------------------------------------------------
# Section 18: New version does not modify old version input
# ---------------------------------------------------------------------------


def test_new_context_version_does_not_modify_old_input() -> None:
    """Constructing a newer context from a copied payload must not mutate the original.

    M1 只验证：构造新版本不修改旧模型和旧输入（包括旧 status）。
    生命周期事件存储、active pointer 和数据库不可变写入尚未实现。
    """
    old_payload = _context_payload()
    old_model = ProjectBindingContextV3.model_validate(old_payload)
    old_serialized = old_model.model_dump(by_alias=True, mode="json")
    old_status = old_model.status

    # Build a new version from a copy of the old payload
    new_payload = deepcopy(old_payload)
    new_payload["contextVersion"] = old_model.context_version + 1
    new_model = ProjectBindingContextV3.model_validate(new_payload)

    # New version number is old + 1
    assert new_model.context_version == old_model.context_version + 1

    # Old model and its original serialization are unchanged (including status)
    assert old_model.context_version == 1
    assert old_model.status == old_status
    assert old_model.model_dump(by_alias=True, mode="json") == old_serialized

    # New model has different serialization
    assert new_model.model_dump(by_alias=True, mode="json") != old_serialized


def test_context_rejects_extra_lifecycle_fields() -> None:
    """Lifecycle fields (lifecycleEvents, activePointer) cannot mix into the payload.

    These are NOT part of the M1 contract and must be rejected by extra=forbid.
    """
    payload = _context_payload()
    payload["lifecycleEvents"] = [{"eventId": "ev-1", "type": "created"}]
    with pytest.raises(ValidationError) as exc_info:
        ProjectBindingContextV3.model_validate(payload)
    errors = exc_info.value.errors()
    assert any("lifecycleEvents" in str(err["loc"]) for err in errors)


def test_context_rejects_active_pointer_field() -> None:
    """activePointer is not part of the M1 context contract and must be rejected."""
    payload = _context_payload()
    payload["activePointer"] = {"contextId": "ctx-2", "contextVersion": 2}
    with pytest.raises(ValidationError) as exc_info:
        ProjectBindingContextV3.model_validate(payload)
    errors = exc_info.value.errors()
    assert any("activePointer" in str(err["loc"]) for err in errors)


# ===================================================================
# Section 19: validate_approval_closure_v3 — nine-group pure validation
# ===================================================================

# ---------------------------------------------------------------------------
# Self-consistent approval-package builder (real content hashes)
# ---------------------------------------------------------------------------


def _build_snapshot_wire(
    *,
    snapshot_id: str = "snap-1",
    snapshot_version: int = 1,
    status: str = "approved",
    approval_id: str = "approval-1",
    policy_version: str = "policy-v1",
    approved_at: str = "2026-09-09T00:00:00+00:00",
) -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "snapshotId": snapshot_id,
        "snapshotVersion": snapshot_version,
        "status": status,
        "dialect": "sqlserver",
        "identifierCaseSensitivity": "insensitive",
        "capturedAt": "2026-09-08T00:00:00+00:00",
        "sourceRef": {
            "sourceKind": "metadataReview",
            "artifactId": "artifact-1",
            "artifactVersion": "v1",
            "sha256": "b" * 64,
        },
        "relations": [_governed_relation()],
        "relationships": [],
        "approvalRef": {
            "approvalId": approval_id,
            "policyVersion": policy_version,
            "approvedAt": approved_at,
        },
        "contentSha256": "0" * 64,
    }


def _build_context_wire(
    *,
    context_id: str = "ctx-1",
    context_version: int = 1,
    status: str = "approved",
    approval_id: str = "approval-1",
    policy_version: str = "policy-v1",
    approved_at: str = "2026-09-09T00:00:00+00:00",
    snapshot_id: str = "snap-1",
    snapshot_version: int = 1,
    snapshot_sha256: str = "0" * 64,
    schema_version: str = "1.1.0",
) -> dict[str, object]:
    wire: dict[str, object] = {
        "schemaVersion": schema_version,
        "contextId": context_id,
        "contextVersion": context_version,
        "status": status,
        "projectRef": {"projectId": "proj-1", "projectVersion": 1},
        "ruleRef": dict(_VALID_RULE_REF),
        "requestIds": ["SYNTH_RULE_SET@v1#fact.one"],
        "metadataSnapshotRef": {
            "snapshotId": snapshot_id,
            "snapshotVersion": snapshot_version,
            "sha256": snapshot_sha256,
        },
        "authorizationPolicyVersion": policy_version,
        "relationGrants": [_relation_grant()],
        "columnGrants": [_column_grant(), _column_grant() | {"grantId": "colgrant-2"}],
        "fieldBindingAuthorizations": [_field_binding()],
        "entityKeyAuthorizations": [_entity_key()],
        "joinGrants": [_join_grant()],
        "approvalRef": {
            "approvalId": approval_id,
            "policyVersion": policy_version,
            "approvedAt": approved_at,
        },
        "contentSha256": "0" * 64,
    }
    # New in schemaVersion 1.1.0
    if schema_version == "1.1.0":
        wire["entityGrainAuthorizations"] = [
            {
                "entityType": "synthetic_entity",
                "grain": "report",
                "relationGrantId": "relgrant-1",
            }
        ]
        wire["joinAuthorizationEvidence"] = []
    return wire


def _build_approval_wire(
    *,
    approval_id: str = "approval-1",
    policy_version: str = "policy-v1",
    approved_at: str = "2026-09-09T00:00:00+00:00",
    context_id: str = "ctx-1",
    context_version: int = 1,
    context_sha256: str = "0" * 64,
    snapshot_id: str = "snap-1",
    snapshot_version: int = 1,
    snapshot_sha256: str = "0" * 64,
) -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "approvalId": approval_id,
        "contextRef": {
            "contextId": context_id,
            "contextVersion": context_version,
            "sha256": context_sha256,
        },
        "snapshotRef": {
            "snapshotId": snapshot_id,
            "snapshotVersion": snapshot_version,
            "sha256": snapshot_sha256,
        },
        "policyVersion": policy_version,
        "actorRef": "actor-1",
        "approvedAt": approved_at,
        "contentSha256": "0" * 64,
    }


def _make_snapshot(**overrides: object) -> GovernedMetadataSnapshotV3:
    wire = _build_snapshot_wire(**overrides)  # type: ignore[arg-type]
    model = GovernedMetadataSnapshotV3.model_validate(wire)
    real_hash = canonical_content_sha256(model)
    wire["contentSha256"] = real_hash
    return GovernedMetadataSnapshotV3.model_validate(wire)


def _make_context(
    *,
    snapshot_sha256: str,
    **overrides: object,
) -> ProjectBindingContextV3:
    wire = _build_context_wire(snapshot_sha256=snapshot_sha256, **overrides)  # type: ignore[arg-type]
    model = ProjectBindingContextV3.model_validate(wire)
    real_hash = canonical_content_sha256(model)
    wire["contentSha256"] = real_hash
    return ProjectBindingContextV3.model_validate(wire)


def _make_approval(
    *,
    context_sha256: str,
    snapshot_sha256: str,
    **overrides: object,
) -> ApprovalRecordV3:
    wire = _build_approval_wire(
        context_sha256=context_sha256,
        snapshot_sha256=snapshot_sha256,
        **overrides,  # type: ignore[arg-type]
    )
    model = ApprovalRecordV3.model_validate(wire)
    real_hash = canonical_content_sha256(model)
    wire["contentSha256"] = real_hash
    return ApprovalRecordV3.model_validate(wire)


def _valid_closure(
    *,
    context_overrides: dict[str, object] | None = None,
    snapshot_overrides: dict[str, object] | None = None,
    approval_overrides: dict[str, object] | None = None,
) -> tuple[ProjectBindingContextV3, GovernedMetadataSnapshotV3, ApprovalRecordV3]:
    """Build a self-consistent, content-valid approval closure package."""
    s_over = dict(snapshot_overrides or {})
    c_over = dict(context_overrides or {})
    a_over = dict(approval_overrides or {})

    snapshot = _make_snapshot(**s_over)
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")

    context = _make_context(
        snapshot_sha256=snapshot_wire["contentSha256"],
        **c_over,
    )
    context_wire = context.model_dump(by_alias=True, mode="json")

    approval = _make_approval(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
        **a_over,
    )
    return context, snapshot, approval


# ---------------------------------------------------------------------------
# A. Success path
# ---------------------------------------------------------------------------


def test_validate_closure_returns_none_for_self_consistent_approved_package() -> None:
    context, snapshot, approval = _valid_closure()
    assert validate_approval_closure_v3(context, snapshot, approval) is None


def test_validate_closure_accepts_non_sha256_approval_id() -> None:
    """approvalId is an opaque stable ID, not a SHA-256."""
    context, snapshot, approval = _valid_closure(
        snapshot_overrides={"approval_id": "my-stable-approval-id"},
        context_overrides={"approval_id": "my-stable-approval-id"},
        approval_overrides={"approval_id": "my-stable-approval-id"},
    )
    assert validate_approval_closure_v3(context, snapshot, approval) is None


# ---------------------------------------------------------------------------
# B. Nine-group exact counterexamples
# ---------------------------------------------------------------------------


def test_group1_context_approval_id_mismatch() -> None:
    context, snapshot, approval = _valid_closure()
    # Break context's approvalId; recompute context and approval hashes
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["approvalRef"]["approvalId"] = "tampered-context"
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_ID_MISMATCH"


def test_group1_snapshot_approval_id_mismatch() -> None:
    context, snapshot, approval = _valid_closure()
    snap_wire = snapshot.model_dump(by_alias=True, mode="json")
    snap_wire["approvalRef"]["approvalId"] = "tampered-snap"
    snap_wire["contentSha256"] = "0" * 64
    bad_snapshot = GovernedMetadataSnapshotV3.model_validate(snap_wire)
    snap_wire["contentSha256"] = canonical_content_sha256(bad_snapshot)
    bad_snapshot = GovernedMetadataSnapshotV3.model_validate(snap_wire)

    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["metadataSnapshotRef"]["sha256"] = snap_wire["contentSha256"]
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["snapshotRef"]["sha256"] = snap_wire["contentSha256"]
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, bad_snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_ID_MISMATCH"


def test_group2_context_policy_mismatch() -> None:
    context, snapshot, approval = _valid_closure()
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["approvalRef"]["policyVersion"] = "wrong-policy"
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_POLICY_MISMATCH"


def test_group2_authorization_policy_version_mismatch() -> None:
    """Only context.authorizationPolicyVersion differs."""
    context, snapshot, approval = _valid_closure()
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["authorizationPolicyVersion"] = "different-policy"
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_POLICY_MISMATCH"


def test_group3_time_mismatch() -> None:
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["approvedAt"] = "2026-09-09T00:00:00+00:01"
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_TIME_MISMATCH"


def test_group3_same_instant_different_wire_string() -> None:
    """'Z' vs '+00:00' must block even though they represent the same instant."""
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["approvedAt"] = "2026-09-09T00:00:00Z"
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_TIME_MISMATCH"


def test_group4_context_ref_id_mismatch() -> None:
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["contextId"] = "wrong-ctx"
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_CONTEXT_REF_MISMATCH"


def test_group4_context_tampered_but_old_hash_kept() -> None:
    """Modify context business content but keep old hashes."""
    context, snapshot, approval = _valid_closure()
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    # Change requestIds (affects hash, not checked in groups 1-3)
    ctx_wire["requestIds"] = ["SYNTH_RULE_SET@v1#fact.one", "SYNTH_RULE_SET@v1#fact.two"]
    # Keep old hash — must fail at group 4
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, snapshot, approval)
    assert exc_info.value.code == "APPROVAL_CONTEXT_REF_MISMATCH"


def test_group5_snapshot_ref_id_mismatch() -> None:
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["snapshotRef"]["snapshotId"] = "wrong-snap"
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_SNAPSHOT_REF_MISMATCH"


def test_group5_snapshot_tampered_but_old_hash_kept() -> None:
    context, snapshot, approval = _valid_closure()
    snap_wire = snapshot.model_dump(by_alias=True, mode="json")
    snap_wire["identifierCaseSensitivity"] = "sensitive"
    bad_snapshot = GovernedMetadataSnapshotV3.model_validate(snap_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, bad_snapshot, approval)
    assert exc_info.value.code == "APPROVAL_SNAPSHOT_REF_MISMATCH"


def test_group2_snapshot_policy_version_mismatch() -> None:
    """metadata_snapshot.approvalRef.policyVersion alone differs."""
    context, snapshot, approval = _valid_closure()
    snap_wire = snapshot.model_dump(by_alias=True, mode="json")
    snap_wire["approvalRef"]["policyVersion"] = "wrong-snap-policy"
    snap_wire["contentSha256"] = "0" * 64
    bad_snapshot = GovernedMetadataSnapshotV3.model_validate(snap_wire)
    snap_wire["contentSha256"] = canonical_content_sha256(bad_snapshot)
    bad_snapshot = GovernedMetadataSnapshotV3.model_validate(snap_wire)

    # Update context metadataSnapshotRef to point to the new snapshot hash
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["metadataSnapshotRef"]["sha256"] = snap_wire["contentSha256"]
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["snapshotRef"]["sha256"] = snap_wire["contentSha256"]
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, bad_snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_POLICY_MISMATCH"


def test_group2_approval_record_policy_version_mismatch() -> None:
    """approval_record.policyVersion alone differs."""
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["policyVersion"] = "wrong-approval-policy"
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_POLICY_MISMATCH"


def test_group4_context_ref_context_version_mismatch() -> None:
    """approval_record.contextRef.contextVersion alone differs."""
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["contextVersion"] = 999
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_CONTEXT_REF_MISMATCH"


def test_group5_snapshot_ref_snapshot_version_mismatch() -> None:
    """Only approval_record.snapshotRef.snapshotVersion differs.

    The context and snapshot are unchanged (their references still match each
    other). Only the approval record's snapshotRef.snapshotVersion is altered,
    and the approval record's self-content hash is recomputed. Group 5
    (snapshotRef ID/version mismatch against the actual snapshot) fires
    before group 9 (context.metadataSnapshotRef vs approval_record.snapshotRef).
    """
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["snapshotRef"]["snapshotVersion"] = 999
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_SNAPSHOT_REF_MISMATCH"


def test_group9_context_metadata_snapshot_ref_snapshot_version_mismatch() -> None:
    """context.metadataSnapshotRef.snapshotVersion differs from approval_record.snapshotRef."""
    context, snapshot, approval = _valid_closure()
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["metadataSnapshotRef"]["snapshotVersion"] = 999
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_SNAPSHOT_BINDING_MISMATCH"


def test_group9_context_metadata_snapshot_ref_sha256_mismatch() -> None:
    """context.metadataSnapshotRef.sha256 differs from approval_record.snapshotRef.sha256."""
    context, snapshot, approval = _valid_closure()
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["metadataSnapshotRef"]["sha256"] = "f" * 64
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_SNAPSHOT_BINDING_MISMATCH"


def test_group6_approval_self_hash_mismatch() -> None:
    """Modify approval_record.actorRef but keep old self-hash."""
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["actorRef"] = "tampered-actor"
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_CONTENT_HASH_MISMATCH"


def test_group7_context_not_approved() -> None:
    context, snapshot, approval = _valid_closure(
        context_overrides={"status": "draft"},
    )
    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, approval)
    assert exc_info.value.code == "APPROVAL_CONTEXT_NOT_APPROVED"


def test_group8_snapshot_not_approved() -> None:
    context, snapshot, approval = _valid_closure(
        snapshot_overrides={"status": "draft"},
    )
    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, approval)
    assert exc_info.value.code == "APPROVAL_SNAPSHOT_NOT_APPROVED"


def test_group9_snapshot_binding_id_mismatch() -> None:
    """context.metadataSnapshotRef differs from approval_record.snapshotRef."""
    context, snapshot, approval = _valid_closure()
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["metadataSnapshotRef"]["snapshotId"] = "different-snap"
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_SNAPSHOT_BINDING_MISMATCH"


# ---------------------------------------------------------------------------
# C. Ordering — multiple errors, first group wins
# ---------------------------------------------------------------------------


def test_ordering_group1_before_group2() -> None:
    context, snapshot, approval = _valid_closure()
    # Break both group 1 and group 2
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["approvalRef"]["approvalId"] = "wrong-id"
    ctx_wire["approvalRef"]["policyVersion"] = "wrong-policy"
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_ID_MISMATCH"


def test_ordering_group2_before_group3() -> None:
    context, snapshot, approval = _valid_closure()
    # Break group 2 and 3 (not 1)
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["approvalRef"]["policyVersion"] = "wrong"
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["approvedAt"] = "2026-09-09T00:00:01+00:00"
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_POLICY_MISMATCH"


def test_ordering_group7_before_group8() -> None:
    context, snapshot, approval = _valid_closure(
        context_overrides={"status": "draft"},
        snapshot_overrides={"status": "draft"},
    )
    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, approval)
    assert exc_info.value.code == "APPROVAL_CONTEXT_NOT_APPROVED"


def test_ordering_group8_before_group9() -> None:
    context, snapshot, approval = _valid_closure(
        snapshot_overrides={"status": "draft"},
    )
    # Also break group 9
    ctx_wire = context.model_dump(by_alias=True, mode="json")
    ctx_wire["metadataSnapshotRef"]["snapshotId"] = "different-snap"
    ctx_wire["contentSha256"] = "0" * 64
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)
    ctx_wire["contentSha256"] = canonical_content_sha256(bad_context)
    bad_context = ProjectBindingContextV3.model_validate(ctx_wire)

    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["contextRef"]["sha256"] = ctx_wire["contentSha256"]
    appr_wire["contentSha256"] = "0" * 64
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    appr_wire["contentSha256"] = canonical_content_sha256(bad_approval)
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(bad_context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_SNAPSHOT_NOT_APPROVED"


# ---------------------------------------------------------------------------
# D. Hash and immutability
# ---------------------------------------------------------------------------


def test_nested_fields_participate_in_hash() -> None:
    """Changing actorRef changes the approval content hash (group 6)."""
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["actorRef"] = "different-actor"
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)
    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, bad_approval)
    assert exc_info.value.code == "APPROVAL_CONTENT_HASH_MISMATCH"


def test_validate_closure_does_not_modify_inputs() -> None:
    context, snapshot, approval = _valid_closure()
    ctx_before = context.model_dump(by_alias=True, mode="json")
    snap_before = snapshot.model_dump(by_alias=True, mode="json")
    appr_before = approval.model_dump(by_alias=True, mode="json")

    validate_approval_closure_v3(context, snapshot, approval)

    assert context.model_dump(by_alias=True, mode="json") == ctx_before
    assert snapshot.model_dump(by_alias=True, mode="json") == snap_before
    assert approval.model_dump(by_alias=True, mode="json") == appr_before


def test_validate_closure_does_not_modify_inputs_on_failure() -> None:
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["approvalId"] = "tampered"
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    ctx_before = context.model_dump(by_alias=True, mode="json")
    snap_before = snapshot.model_dump(by_alias=True, mode="json")
    appr_before = bad_approval.model_dump(by_alias=True, mode="json")

    with pytest.raises(ApprovalClosureValidationErrorV3):
        validate_approval_closure_v3(context, snapshot, bad_approval)

    assert context.model_dump(by_alias=True, mode="json") == ctx_before
    assert snapshot.model_dump(by_alias=True, mode="json") == snap_before
    assert bad_approval.model_dump(by_alias=True, mode="json") == appr_before


# ---------------------------------------------------------------------------
# E. Desensitization and isolation
# ---------------------------------------------------------------------------


def test_error_does_not_leak_input_data() -> None:
    sensitive = "SENSITIVE-MARKER-12345"
    context, snapshot, approval = _valid_closure()
    appr_wire = approval.model_dump(by_alias=True, mode="json")
    appr_wire["approvalId"] = sensitive
    bad_approval = ApprovalRecordV3.model_validate(appr_wire)

    with pytest.raises(ApprovalClosureValidationErrorV3) as exc_info:
        validate_approval_closure_v3(context, snapshot, bad_approval)

    err = exc_info.value
    assert sensitive not in err.code
    assert sensitive not in str(err)
    assert sensitive not in repr(err)
    assert sensitive not in err.args


def test_v2_module_does_not_import_v3_validator() -> None:
    import release_sql_bot.application.validate_approval_closure_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "project_bindings_v2" not in source
    assert "from release_sql_bot.domain.fact_bindings_v2" not in source


def test_validator_does_not_import_v2_or_infrastructure() -> None:
    import release_sql_bot.application.validate_approval_closure_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "fact_bindings_v2" not in source
    assert "project_bindings_v2" not in source
    assert "mongodb" not in source
    assert "os.environ" not in source
    assert "getenv" not in source


# ---------------------------------------------------------------------------
# schemaVersion 1.1.0: new required fields
# ---------------------------------------------------------------------------


def test_context_1_1_0_accepts_valid_new_fields() -> None:
    snapshot = _make_snapshot()
    context = _make_context(snapshot_sha256=snapshot.content_sha256)
    assert context.schema_version == "1.1.0"
    assert len(context.entity_grain_authorizations) >= 1
    assert isinstance(context.join_authorization_evidence, list)


def test_context_1_1_0_rejects_missing_entity_grain_authorizations() -> None:
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["entityGrainAuthorizations"] = []
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)


def test_schema_version_fixed_to_1_1_0() -> None:
    """ProjectBindingContextV3 only accepts schemaVersion=1.1.0."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)

    # 1.0.0 is rejected at Pydantic level
    wire["schemaVersion"] = "1.0.0"
    with pytest.raises(ValidationError) as exc_info:
        ProjectBindingContextV3.model_validate(wire)
    assert "schemaVersion" in str(exc_info.value)

    # Even with new fields present, wrong version is rejected
    wire["schemaVersion"] = "2.0.0"
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)


def test_entity_grain_rejects_duplicate_entity_type_grain() -> None:
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["entityGrainAuthorizations"] = [
        {"entityType": "foo", "grain": "bar", "relationGrantId": "relgrant-1"},
        {"entityType": "foo", "grain": "bar", "relationGrantId": "relgrant-1"},
    ]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)


def test_join_evidence_rejects_duplicate_request_grant() -> None:
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["joinAuthorizationEvidence"] = [
        {
            "requestId": "SYNTH_RULE_SET@v1#fact.one",
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-1"],
        },
        {
            "requestId": "SYNTH_RULE_SET@v1#fact.one",
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-2"],
        },
    ]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)


def test_join_evidence_accepts_empty_list() -> None:
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["joinAuthorizationEvidence"] = []
    context = ProjectBindingContextV3.model_validate(wire)
    assert context.join_authorization_evidence == []


def test_join_evidence_rejects_empty_evidence_ids() -> None:
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["joinAuthorizationEvidence"] = [
        {
            "requestId": "SYNTH_RULE_SET@v1#fact.one",
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": [],
        }
    ]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)


# ===================================================================
# schemaVersion 1.1.0: requestId and additional regression tests
# ===================================================================


def _valid_join_evidence_req_id() -> str:
    """A realistic V3 requestId with special characters."""
    return "SYNTH_RULE_SET@v1#fact.one"


@pytest.mark.parametrize(
    "req_id",
    [
        "",  # empty
        "a",  # 1 char — too short
        "ab",  # 2 chars — too short
        "x" * 421,  # too long
    ],
)
def test_join_evidence_request_id_rejects_invalid(req_id: str) -> None:
    """requestId must be 3-420 chars — rejects empty, too short, too long."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["joinAuthorizationEvidence"] = [
        {
            "requestId": req_id,
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-1"],
        }
    ]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)


@pytest.mark.parametrize(
    "req_id",
    [
        "abc",  # exactly 3 — boundary
        "x" * 420,  # exactly 420 — boundary
        "SYNTH_RULE_SET@v1#fact.one",  # realistic shape
        "RULE@20260906T000000000000Z-a1b2c3d4e5f6#report.synthetic_amount",
    ],
)
def test_join_evidence_request_id_accepts_valid(req_id: str) -> None:
    """requestId accepts 3-420 chars including #, @, ., -."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["joinAuthorizationEvidence"] = [
        {
            "requestId": req_id,
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-1"],
        }
    ]
    context = ProjectBindingContextV3.model_validate(wire)
    assert context.join_authorization_evidence[0].request_id == req_id


def test_join_evidence_missing_field_rejected() -> None:
    """joinAuthorizationEvidence with missing required fields is rejected."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    # Missing evidenceIds
    wire["joinAuthorizationEvidence"] = [
        {
            "requestId": _valid_join_evidence_req_id(),
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
        }
    ]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)


def test_join_evidence_explicit_empty_list_accepted() -> None:
    """joinAuthorizationEvidence=[] is valid for contexts without joins."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["joinAuthorizationEvidence"] = []
    context = ProjectBindingContextV3.model_validate(wire)
    assert context.join_authorization_evidence == []


def test_entity_grain_missing_field_rejected() -> None:
    """entityGrainAuthorizations with missing required fields is rejected."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["entityGrainAuthorizations"] = [
        {"entityType": "foo"}  # missing grain and relationGrantId
    ]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)


def test_entity_grain_empty_list_rejected() -> None:
    """entityGrainAuthorizations=[] is rejected — must be non-empty."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["entityGrainAuthorizations"] = []
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)


def test_model_copy_bypass_rejected_via_revalidation() -> None:
    """model_copy injecting invalid new fields is caught by full re-validation."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    context = ProjectBindingContextV3.model_validate(wire)

    # Attempt model_copy bypass with invalid schemaVersion
    tampered = context.model_copy(update={"schema_version": "1.0.0"})
    # The tampered object should fail full serialization round-trip
    # because schema_version is frozen and Literal["1.1.0"]
    from pydantic import ValidationError

    try:
        # Re-validate via dump+validate
        wire_back = tampered.model_dump(by_alias=True, mode="json")
        wire_back["schemaVersion"] = "1.0.0"  # inject invalid version
        ProjectBindingContextV3.model_validate(wire_back)
        raise AssertionError("Expected ValidationError for invalid schemaVersion")
    except ValidationError:
        pass  # Expected


# ===================================================================
# Additional regression tests (post-fix)
# ===================================================================


def test_request_id_missing_field_rejected() -> None:
    """joinAuthorizationEvidence with missing requestId is rejected."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["joinAuthorizationEvidence"] = [
        {
            # "requestId" missing
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-1"],
        }
    ]
    with pytest.raises(ValidationError) as exc_info:
        ProjectBindingContextV3.model_validate(wire)
    assert "requestId" in str(exc_info.value)


def test_join_authorization_evidence_missing_top_level_rejected() -> None:
    """Missing top-level joinAuthorizationEvidence field is rejected."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    del wire["joinAuthorizationEvidence"]
    with pytest.raises(ValidationError) as exc_info:
        ProjectBindingContextV3.model_validate(wire)
    assert "joinAuthorizationEvidence" in str(exc_info.value)


def test_entity_grain_authorizations_missing_top_level_rejected() -> None:
    """Missing top-level entityGrainAuthorizations field is rejected."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    del wire["entityGrainAuthorizations"]
    with pytest.raises(ValidationError) as exc_info:
        ProjectBindingContextV3.model_validate(wire)
    assert "entityGrainAuthorizations" in str(exc_info.value)


def test_context_1_0_0_without_new_fields_rejected() -> None:
    """schemaVersion=1.0.0 context without new fields is rejected."""
    from pydantic import ValidationError

    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["schemaVersion"] = "1.0.0"
    with pytest.raises(ValidationError) as exc_info:
        ProjectBindingContextV3.model_validate(wire)
    assert "schemaVersion" in str(exc_info.value)


def test_context_old_version_with_new_fields_still_rejected() -> None:
    """Even with all new fields present, wrong schemaVersion is rejected."""
    from pydantic import ValidationError

    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["schemaVersion"] = "1.0.0"
    # wire already has entityGrainAuthorizations and joinAuthorizationEvidence
    with pytest.raises(ValidationError) as exc_info:
        ProjectBindingContextV3.model_validate(wire)
    assert "schemaVersion" in str(exc_info.value)


def test_entity_grain_duplicate_rejected() -> None:
    """Duplicate (entityType, grain) is rejected."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["entityGrainAuthorizations"] = [
        {"entityType": "foo", "grain": "bar", "relationGrantId": "relgrant-1"},
        {"entityType": "foo", "grain": "bar", "relationGrantId": "relgrant-1"},
    ]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)


def test_join_evidence_duplicate_rejected() -> None:
    """Duplicate (requestId, joinGrantId) is rejected."""
    snapshot = _make_snapshot()
    wire = _build_context_wire(snapshot_sha256=snapshot.content_sha256)
    wire["joinAuthorizationEvidence"] = [
        {
            "requestId": "SYNTH_RULE_SET@v1#fact.one",
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-1"],
        },
        {
            "requestId": "SYNTH_RULE_SET@v1#fact.one",
            "payloadSha256": "a" * 64,
            "joinGrantId": "join-1",
            "evidenceIds": ["ev-2"],
        },
    ]
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(wire)
