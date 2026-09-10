"""Unit tests for V3 field and entity-key binding resolution (DEV §5.3.2).

Tests the internal helper ``_resolve_fields_and_entity_keys_v3``: field
authorization matching, entity-key authorization closure, and the
fail-fast error codes.

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from pathlib import Path

import pytest

from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataBindingResolutionErrorV3,
    MetadataColumnResolutionErrorV3,
    MetadataResolutionInputErrorV3,
    _resolve_fields_and_entity_keys_v3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.project_bindings_v3 import (
    ProjectBindingContextV3,
    ResolveMetadataRequestV3,
)
from tests.v3_metadata_support import valid_resolve_metadata_request_v3_wire

# Synthetic marker for sanitization tests — must be valid fieldId format
_SYNTHETIC_PRIVATE_MARKER = "syntheticPrivateMarker"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_request() -> ResolveMetadataRequestV3:
    """Build a valid, internally-consistent request from the shared fixture."""
    wire = valid_resolve_metadata_request_v3_wire()
    return ResolveMetadataRequestV3.model_validate(wire)


def _rebuild_request_with_context(request, context) -> ResolveMetadataRequestV3:
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


def _rebuild_request_with_payload_context(
    request, binding_request, context
) -> ResolveMetadataRequestV3:
    """Replace both binding request (payload) and context, closing all hashes."""
    from release_sql_bot.application.canonical import (
        canonical_content_sha256,
        canonical_sha256,
    )
    from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3

    # Close handoff payload hash
    payload_hash = canonical_sha256(FactBindingRequestV3.model_validate(binding_request))

    context_wire = context.model_dump(by_alias=True, mode="json")
    ctx_sha = context_wire["contentSha256"]

    # Update approval record's contextRef
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
    req_wire["bindingRequest"] = binding_request
    req_wire["handoffClosure"]["payload"] = binding_request
    req_wire["handoffClosure"]["payloadSha256"] = payload_hash

    return ResolveMetadataRequestV3.model_validate(req_wire)


# ===================================================================
# Section A: Normal complete chain
# ===================================================================


def test_resolve_fields_and_entity_keys_success():
    """A valid request resolves all fields and entity keys with exact references."""
    request = _make_valid_request()
    fields, entity_keys = _resolve_fields_and_entity_keys_v3(request)

    # Should have at least factValue and syntheticKey fields
    assert len(fields) >= 2
    field_ids = [f.field_id for f in fields]
    assert "factValue" in field_ids
    assert "syntheticKey" in field_ids

    # Check factValue field
    fact_value_field = next(f for f in fields if f.field_id == "factValue")
    assert fact_value_field.role == "value"
    assert fact_value_field.authorization_id == "fba-value"
    assert fact_value_field.column_grant_id == "colgrant-value"
    assert fact_value_field.schema_name == "dbo"
    assert fact_value_field.relation_name == "synthetic_table"
    assert fact_value_field.column_name == "synthetic_value"
    assert "ev-fact-declaration" in fact_value_field.evidence_ids

    # Check entity key
    assert len(entity_keys) == 1
    ek = entity_keys[0]
    assert ek.parameter_name == "syntheticKey"
    assert ek.field_id == "syntheticKey"
    assert ek.authorization_id == "eka-1"
    assert ek.column_grant_id == "colgrant-key"
    assert ek.schema_name == "dbo"
    assert ek.relation_name == "synthetic_table"
    assert ek.column_name == "synthetic_key"
    assert "ev-fact-declaration" in ek.evidence_ids


def test_output_is_new_objects():
    """Output DTOs are new objects; mutating them doesn't pollute input."""
    request = _make_valid_request()
    fields, entity_keys = _resolve_fields_and_entity_keys_v3(request)

    # Mutate output
    fields[0].evidence_ids.append("injected")
    entity_keys[0].evidence_ids.append("injected")

    # Original request is unchanged
    assert "injected" not in request.binding_request.query_requirements.fields[0].evidence_ids
    assert "injected" not in request.binding_request.query_requirements.entity.evidence_ids


def test_parameter_name_different_from_field_id_succeeds():
    """Parameter name differs from fieldId but explicit authorization makes it work.

    Parameter name remains "syntheticKey", but the field ID is changed to "entityKeyField".
    Both field authorization and entity-key authorization use fieldId="entityKeyField".
    """
    request = _make_valid_request()

    # Modify the field ID from "syntheticKey" to "entityKeyField"
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Update the field in queryRequirements
    for field in req_wire["bindingRequest"]["queryRequirements"]["fields"]:
        if field["fieldId"] == "syntheticKey":
            field["fieldId"] = "entityKeyField"

    # Update field authorization to use new fieldId
    for auth in req_wire["projectContext"]["fieldBindingAuthorizations"]:
        if auth["fieldId"] == "syntheticKey":
            auth["fieldId"] = "entityKeyField"

    # Update entity-key authorization to use new fieldId
    for auth in req_wire["projectContext"]["entityKeyAuthorizations"]:
        if auth["fieldId"] == "syntheticKey":
            auth["fieldId"] = "entityKeyField"

    # Build context
    context = ProjectBindingContextV3.model_validate(req_wire["projectContext"])
    from release_sql_bot.application.canonical import canonical_content_sha256

    req_wire["projectContext"]["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(req_wire["projectContext"])

    # Build binding request
    binding_request = deepcopy(req_wire["bindingRequest"])

    # Precondition: parameterName != fieldId
    assert (
        "syntheticKey" in req_wire["bindingRequest"]["queryRequirements"]["entity"]["keyParameters"]
    )
    field_ids = [f["fieldId"] for f in req_wire["bindingRequest"]["queryRequirements"]["fields"]]
    assert "entityKeyField" in field_ids
    assert "syntheticKey" not in field_ids

    tampered = _rebuild_request_with_payload_context(request, binding_request, context)

    # Verify input gate passes
    from release_sql_bot.application.metadata_resolution_v3 import _validate_resolution_input_v3

    _validate_resolution_input_v3(tampered)

    fields, entity_keys = _resolve_fields_and_entity_keys_v3(tampered)

    # Find the entity key
    assert len(entity_keys) == 1
    ek = entity_keys[0]
    assert ek.parameter_name == "syntheticKey"  # parameter name unchanged
    assert ek.field_id == "entityKeyField"  # field ID is different

    # Find the corresponding field
    matching_field = next(f for f in fields if f.field_id == "entityKeyField")
    assert matching_field.role == "entityKey"
    assert matching_field.authorization_id == "fba-key"  # field authorization ID


# ===================================================================
# Section B: Field authorization scope
# ===================================================================


def test_field_authorization_missing():
    """Field without matching authorization is rejected."""
    request = _make_valid_request()

    # Add a field with no authorization to the binding request
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *req_wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "extrafield",
            "role": "value",
            "logicalName": "extra_field",
            "dataType": "string",
            "required": False,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    # Need to update handoff closure payload too
    req_wire["handoffClosure"]["payload"] = req_wire["bindingRequest"]
    from release_sql_bot.application.canonical import canonical_sha256

    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire["bindingRequest"])
    )

    tampered = ResolveMetadataRequestV3.model_validate(req_wire)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


def test_field_authorization_wrong_role():
    """Field authorization with same fieldId but different role doesn't match."""
    request = _make_valid_request()

    # Change the role of the factValue authorization to entityKey
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    for auth in context_wire["fieldBindingAuthorizations"]:
        if auth["fieldId"] == "factValue":
            auth["role"] = "entityKey"  # wrong role
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


def test_field_authorization_different_request_id():
    """Authorization for a different requestId doesn't match."""
    request = _make_valid_request()

    # Change all authorizations to a different requestId
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    for auth in context_wire["fieldBindingAuthorizations"]:
        auth["requestId"] = "other-request"
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


def test_field_authorization_exact_field_id_match():
    """Field authorization requires exact fieldId match (no fuzzy matching)."""
    request = _make_valid_request()

    # Change authorization fieldId to a different valid lowercase name
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    for auth in context_wire["fieldBindingAuthorizations"]:
        if auth["fieldId"] == "factValue":
            auth["fieldId"] = "factvalue"  # different valid lowercase name
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


def test_column_grant_resolution_failure_propagates():
    """When column grant resolution fails, the error propagates."""
    request = _make_valid_request()

    # Point the field authorization to a non-existent column grant
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    for auth in context_wire["fieldBindingAuthorizations"]:
        if auth["fieldId"] == "factValue":
            auth["columnGrantId"] = "nonexistent-grant"
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataColumnResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "COLUMN_GRANT_NOT_FOUND"


def test_later_field_failure_no_partial_results():
    """When a later field fails, no partial results are returned."""
    request = _make_valid_request()

    # Add a second field that will fail (no authorization)
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    # Add a field without authorization to the binding request
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"] = context_wire
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *req_wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "noauthfield",
            "role": "value",
            "logicalName": "no_auth",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    req_wire["handoffClosure"]["payload"] = req_wire["bindingRequest"]
    from release_sql_bot.application.canonical import canonical_sha256

    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire["bindingRequest"])
    )

    tampered = ResolveMetadataRequestV3.model_validate(req_wire)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


# ===================================================================
# Section C: Entity-key closure
# ===================================================================


def test_entity_key_authorization_missing():
    """Entity key without authorization is rejected."""
    request = _make_valid_request()

    # Remove all entity-key authorizations
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["entityKeyAuthorizations"] = []
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "ENTITY_KEY_AUTHORIZATION_MISSING"


def test_entity_key_authorization_ambiguous():
    """Multiple entity-key authorizations for same parameter are rejected."""
    request = _make_valid_request()

    # Add a second entity-key authorization for the same parameter but different fieldId
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["entityKeyAuthorizations"] = [
        *context_wire["entityKeyAuthorizations"],
        {
            "authorizationId": "eka-ambiguous",
            "requestId": request.binding_request.request_id,
            "parameterName": "syntheticKey",
            "fieldId": "factValue",  # different fieldId
            "columnGrantId": "colgrant-value",
        },
    ]
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "ENTITY_KEY_AUTHORIZATION_AMBIGUOUS"


def test_entity_key_field_not_found():
    """Entity-key authorization pointing to non-existent field is rejected."""
    request = _make_valid_request()

    # Point entity-key authorization to a field that doesn't exist
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    for auth in context_wire["entityKeyAuthorizations"]:
        auth["fieldId"] = "nonexistentfield"
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "ENTITY_KEY_FIELD_NOT_FOUND"


def test_entity_key_field_role_mismatch():
    """Entity-key authorization pointing to a non-entityKey field is rejected."""
    request = _make_valid_request()

    # Point entity-key authorization to factValue (role=value, not entityKey)
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    for auth in context_wire["entityKeyAuthorizations"]:
        auth["fieldId"] = "factValue"  # role is "value", not "entityKey"
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "ENTITY_KEY_FIELD_ROLE_MISMATCH"


def test_entity_key_column_grant_mismatch():
    """Entity-key and field authorization using different column grants is rejected."""
    request = _make_valid_request()

    # Change entity-key authorization to use a different column grant
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    for auth in context_wire["entityKeyAuthorizations"]:
        auth["columnGrantId"] = "colgrant-value"  # different from colgrant-key
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "ENTITY_KEY_COLUMN_GRANT_MISMATCH"


def test_entity_key_fix_and_succeed():
    """Fixing the entity-key error allows successful resolution."""
    request = _make_valid_request()

    # First verify it fails with wrong column grant
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    for auth in context_wire["entityKeyAuthorizations"]:
        auth["columnGrantId"] = "colgrant-value"
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered_bad = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataBindingResolutionErrorV3):
        _resolve_fields_and_entity_keys_v3(tampered_bad)

    # Now fix it and verify success
    fields, entity_keys = _resolve_fields_and_entity_keys_v3(request)
    assert len(entity_keys) == 1
    assert entity_keys[0].column_grant_id == "colgrant-key"


# ===================================================================
# Section D: mappingCandidate does not grant authority
# ===================================================================


def test_missing_field_authorization_with_mapping_candidate_still_rejected():
    """mappingCandidate does not compensate for missing field authorization."""
    request = _make_valid_request()

    # Add a field without authorization
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *req_wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "unauthorizedfield",
            "role": "value",
            "logicalName": "unauthorized",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    req_wire["handoffClosure"]["payload"] = req_wire["bindingRequest"]
    from release_sql_bot.application.canonical import canonical_sha256

    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire["bindingRequest"])
    )

    tampered = ResolveMetadataRequestV3.model_validate(req_wire)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


def test_mapping_candidate_does_not_affect_authorized_resolution():
    """Different mappingCandidate values don't change authorized field resolution."""
    # Version A: mappingCandidate with viewName="view_a"
    req_wire_a = _make_valid_request().model_dump(by_alias=True, mode="json")
    req_wire_a["bindingRequest"]["mappingCandidate"] = {
        "factCode": req_wire_a["bindingRequest"]["fact"]["factCode"],
        "mappingStatus": "mapped",
        "viewName": "view_a",
        "viewField": "field_a",
        "viewActive": True,
        "reviewStatus": "candidate",
        "note": "Mapping candidate A",
    }
    req_wire_a["handoffClosure"]["payload"] = req_wire_a["bindingRequest"]
    from release_sql_bot.application.canonical import canonical_sha256

    req_wire_a["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire_a["bindingRequest"])
    )
    request_a = ResolveMetadataRequestV3.model_validate(req_wire_a)

    # Version B: mappingCandidate with viewName="view_b"
    req_wire_b = deepcopy(req_wire_a)
    req_wire_b["bindingRequest"]["mappingCandidate"]["viewName"] = "view_b"
    req_wire_b["bindingRequest"]["mappingCandidate"]["viewField"] = "field_b"
    req_wire_b["bindingRequest"]["mappingCandidate"]["note"] = "Mapping candidate B"
    req_wire_b["handoffClosure"]["payload"] = req_wire_b["bindingRequest"]
    req_wire_b["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire_b["bindingRequest"])
    )
    request_b = ResolveMetadataRequestV3.model_validate(req_wire_b)

    fields_a, entity_keys_a = _resolve_fields_and_entity_keys_v3(request_a)
    fields_b, entity_keys_b = _resolve_fields_and_entity_keys_v3(request_b)

    # Results should be identical regardless of mappingCandidate
    assert len(fields_a) == len(fields_b)
    for fa, fb in zip(fields_a, fields_b, strict=True):
        assert fa.field_id == fb.field_id
        assert fa.schema_name == fb.schema_name
        assert fa.relation_name == fb.relation_name
        assert fa.column_name == fb.column_name


# ===================================================================
# Section E: Ordering and isolation
# ===================================================================


def test_input_gate_failure_precedes_binding_errors():
    """Input gate failures propagate before binding checks."""
    request = _make_valid_request()
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"


def test_field_error_before_entity_key_error():
    """Field authorization errors are detected before entity-key errors."""
    request = _make_valid_request()

    # Remove entity-key authorization AND make a field fail
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["entityKeyAuthorizations"] = []  # would cause ENTITY_KEY_AUTHORIZATION_MISSING
    # Also change field authorization to cause FIELD_AUTHORIZATION_MISSING
    for auth in context_wire["fieldBindingAuthorizations"]:
        if auth["fieldId"] == "factValue":
            auth["role"] = "filter"  # wrong role
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    # Field errors should be detected first
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"


def test_input_not_mutated_on_success():
    """The original request is not mutated by a successful resolution."""
    request = _make_valid_request()
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    _resolve_fields_and_entity_keys_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_input_not_mutated_on_failure():
    """The actual input is not mutated by a failed resolution."""
    request = _make_valid_request()
    # Create a tampered copy that will fail input gate
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)
    before = deepcopy(tampered.model_dump(by_alias=True, mode="json"))

    with pytest.raises(MetadataResolutionInputErrorV3):
        _resolve_fields_and_entity_keys_v3(tampered)

    after = tampered.model_dump(by_alias=True, mode="json")
    assert after == before, "binding resolver mutated its input"


def test_input_not_mutated_on_field_error():
    """The actual input is not mutated when field authorization fails."""
    request = _make_valid_request()

    # Create a request that will fail with FIELD_AUTHORIZATION_MISSING
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *req_wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "extrafield2",
            "role": "value",
            "logicalName": "extra",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    req_wire["handoffClosure"]["payload"] = req_wire["bindingRequest"]
    from release_sql_bot.application.canonical import canonical_sha256

    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire["bindingRequest"])
    )

    tampered = ResolveMetadataRequestV3.model_validate(req_wire)
    before = deepcopy(tampered.model_dump(by_alias=True, mode="json"))

    with pytest.raises(MetadataBindingResolutionErrorV3):
        _resolve_fields_and_entity_keys_v3(tampered)

    after = tampered.model_dump(by_alias=True, mode="json")
    assert after == before, "binding resolver mutated its input"


def test_input_not_mutated_on_entity_key_error():
    """The actual input is not mutated when entity-key authorization fails."""
    request = _make_valid_request()

    # Remove entity-key authorization
    context_wire = request.project_context.model_dump(by_alias=True, mode="json")
    context_wire["entityKeyAuthorizations"] = []
    context = ProjectBindingContextV3.model_validate(context_wire)
    from release_sql_bot.application.canonical import canonical_content_sha256

    context_wire["contentSha256"] = canonical_content_sha256(context)
    context = ProjectBindingContextV3.model_validate(context_wire)

    tampered = _rebuild_request_with_context(request, context)
    before = deepcopy(tampered.model_dump(by_alias=True, mode="json"))

    with pytest.raises(MetadataBindingResolutionErrorV3):
        _resolve_fields_and_entity_keys_v3(tampered)

    after = tampered.model_dump(by_alias=True, mode="json")
    assert after == before, "binding resolver mutated its input"


def test_immutability_proven_by_before_after_comparison():
    """Prove input immutability by comparing before/after wire snapshots.

    This test verifies that the actual input object passed to the resolver
    is not modified during either success or failure paths.
    """
    # Test 1: Success path - input should not be mutated
    request = _make_valid_request()
    before = deepcopy(request.model_dump(by_alias=True, mode="json"))
    _resolve_fields_and_entity_keys_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before, "success path mutated input"

    # Test 2: Input gate failure - input should not be mutated
    request2 = _make_valid_request()
    wire = request2.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered2 = ResolveMetadataRequestV3.model_validate(wire)
    before2 = deepcopy(tampered2.model_dump(by_alias=True, mode="json"))
    with pytest.raises(MetadataResolutionInputErrorV3):
        _resolve_fields_and_entity_keys_v3(tampered2)
    after2 = tampered2.model_dump(by_alias=True, mode="json")
    assert after2 == before2, "input gate failure mutated input"

    # Test 3: Field authorization failure - input should not be mutated
    request3 = _make_valid_request()
    req_wire = request3.model_dump(by_alias=True, mode="json")
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *req_wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": "extrafield99",
            "role": "value",
            "logicalName": "extra",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    req_wire["handoffClosure"]["payload"] = req_wire["bindingRequest"]
    from release_sql_bot.application.canonical import canonical_sha256

    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire["bindingRequest"])
    )
    tampered3 = ResolveMetadataRequestV3.model_validate(req_wire)
    before3 = deepcopy(tampered3.model_dump(by_alias=True, mode="json"))
    with pytest.raises(MetadataBindingResolutionErrorV3):
        _resolve_fields_and_entity_keys_v3(tampered3)
    after3 = tampered3.model_dump(by_alias=True, mode="json")
    assert after3 == before3, "field auth failure mutated input"


def test_sensitive_marker_not_leaked(caplog: pytest.LogCaptureFixture) -> None:
    """Sensitive marker in failed binding resolution is not leaked."""
    caplog.set_level(logging.DEBUG)
    request = _make_valid_request()

    # Create a request that will fail with FIELD_AUTHORIZATION_MISSING
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["bindingRequest"]["queryRequirements"]["fields"] = [
        *req_wire["bindingRequest"]["queryRequirements"]["fields"],
        {
            "fieldId": _SYNTHETIC_PRIVATE_MARKER,
            "role": "value",
            "logicalName": "marker_field",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        },
    ]
    req_wire["handoffClosure"]["payload"] = req_wire["bindingRequest"]
    from release_sql_bot.application.canonical import canonical_sha256

    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire["bindingRequest"])
    )

    tampered = ResolveMetadataRequestV3.model_validate(req_wire)

    with pytest.raises(MetadataBindingResolutionErrorV3) as exc_info:
        _resolve_fields_and_entity_keys_v3(tampered)
    assert exc_info.value.code == "FIELD_AUTHORIZATION_MISSING"
    assert _SYNTHETIC_PRIVATE_MARKER not in str(exc_info.value)
    assert _SYNTHETIC_PRIVATE_MARKER not in repr(exc_info.value)
    for record in caplog.records:
        assert _SYNTHETIC_PRIVATE_MARKER not in record.message


# ===================================================================
# Section F: No infrastructure dependencies
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
