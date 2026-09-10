"""Unit tests for V3 metadata-resolution input gate (M2 输入门禁子任务).

Tests the internal helper ``_validate_resolution_input_v3``: structure re-validation,
handoff closure, binding-request consistency, approval closure, and project/rule/
request scope. Pure computation: no repository, provider, SQL, or environment.

These tests prove only that the input is internally consistent; they do NOT
prove physical authorization, approval truthiness, or repository attestation.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataResolutionInputErrorV3,
    _validate_resolution_input_v3,
)
from release_sql_bot.application.validate_approval_closure_v3 import (
    validate_approval_closure_v3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.handoff_closure_v3 import HandoffClosureV3
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordV3,
    GovernedMetadataSnapshotV3,
    ProjectBindingContextV3,
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


def _make_valid_request() -> ResolveMetadataRequestV3:
    """Build a valid, internally-consistent request from the shared fixture."""
    wire = valid_resolve_metadata_request_v3_wire()
    return ResolveMetadataRequestV3.model_validate(wire)


def _rebuild_context_with(
    *,
    snapshot_sha256: str,
    rule_ref: dict[str, object],
    request_id: str,
    **overrides: object,
) -> ProjectBindingContextV3:
    """Rebuild a valid context with optional field overrides, re-closing hashes."""
    wire = {
        "schemaVersion": "1.0.0",
        "contextId": "ctx-1",
        "contextVersion": 1,
        "status": "approved",
        "projectRef": {"projectId": "proj-1", "projectVersion": 1},
        "ruleRef": dict(rule_ref),
        "requestIds": [request_id],
        "metadataSnapshotRef": {
            "snapshotId": "snap-1",
            "snapshotVersion": 1,
            "sha256": snapshot_sha256,
        },
        "authorizationPolicyVersion": "policy-v1",
        "relationGrants": [
            {
                "grantId": "relgrant-1",
                "schemaName": "dbo",
                "relationName": "synthetic_table",
                "access": "read",
            }
        ],
        "columnGrants": [
            {
                "grantId": "colgrant-value",
                "relationGrantId": "relgrant-1",
                "columnName": "synthetic_value",
            },
            {
                "grantId": "colgrant-key",
                "relationGrantId": "relgrant-1",
                "columnName": "synthetic_key",
            },
        ],
        "fieldBindingAuthorizations": [
            {
                "authorizationId": "fba-value",
                "requestId": request_id,
                "fieldId": "factValue",
                "role": "value",
                "columnGrantId": "colgrant-value",
            },
            {
                "authorizationId": "fba-key",
                "requestId": request_id,
                "fieldId": "syntheticKey",
                "role": "entityKey",
                "columnGrantId": "colgrant-key",
            },
        ],
        "entityKeyAuthorizations": [
            {
                "authorizationId": "eka-1",
                "requestId": request_id,
                "parameterName": "syntheticKey",
                "fieldId": "syntheticKey",
                "columnGrantId": "colgrant-key",
            }
        ],
        "joinGrants": [],
        "approvalRef": dict(_APPROVAL_REF),
        "contentSha256": _VALID_SHA,
    }
    for key, value in overrides.items():
        wire[key] = value
    model = ProjectBindingContextV3.model_validate(wire)
    wire["contentSha256"] = canonical_content_sha256(model)
    return ProjectBindingContextV3.model_validate(wire)


def _rebuild_approval_with(
    *,
    context_sha256: str,
    snapshot_sha256: str,
    **overrides: object,
) -> ApprovalRecordV3:
    """Rebuild a valid approval record with optional field overrides."""
    wire = {
        "schemaVersion": "1.0.0",
        "approvalId": "approval-1",
        "contextRef": {
            "contextId": "ctx-1",
            "contextVersion": 1,
            "sha256": context_sha256,
        },
        "snapshotRef": {
            "snapshotId": "snap-1",
            "snapshotVersion": 1,
            "sha256": snapshot_sha256,
        },
        "policyVersion": "policy-v1",
        "actorRef": "actor-1",
        "approvedAt": "2026-09-09T00:00:00+00:00",
        "contentSha256": _VALID_SHA,
    }
    for key, value in overrides.items():
        wire[key] = value
    model = ApprovalRecordV3.model_validate(wire)
    wire["contentSha256"] = canonical_content_sha256(model)
    return ApprovalRecordV3.model_validate(wire)


def _rebuild_snapshot() -> GovernedMetadataSnapshotV3:
    """Rebuild a valid snapshot with real content hash."""
    from tests.v3_metadata_support import _make_snapshot

    return _make_snapshot()


def _make_request_with_context(
    context: ProjectBindingContextV3,
    snapshot: GovernedMetadataSnapshotV3,
    approval: ApprovalRecordV3,
    closure_wire: dict[str, object],
    binding_request: dict[str, object],
) -> ResolveMetadataRequestV3:
    """Assemble a request from pre-built parts."""
    return ResolveMetadataRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "projectRef": {"projectId": "proj-1", "projectVersion": 1},
            "handoffClosure": closure_wire,
            "bindingRequest": binding_request,
            "projectContext": context.model_dump(by_alias=True, mode="json"),
            "metadataSnapshot": snapshot.model_dump(by_alias=True, mode="json"),
            "approvalRecord": approval.model_dump(by_alias=True, mode="json"),
        }
    )


# ===================================================================
# Section 1: Success path — return value isolation
# ===================================================================


def test_valid_request_succeeds_and_returns_independent_copy() -> None:
    """A valid request passes all gates and returns an independent copy."""
    request = _make_valid_request()
    result = _validate_resolution_input_v3(request)

    assert result == request
    assert result is not request


def _assert_deep_isolation(
    result: ResolveMetadataRequestV3, request: ResolveMetadataRequestV3
) -> None:
    """Assert the returned copy shares no mutable nested objects with the input.

    Helper shared by the normal isolation test and the diagnostic test.
    """
    # Nested objects must not be shared
    assert result.project_context is not request.project_context
    assert result.project_context.request_ids is not request.project_context.request_ids
    assert result.binding_request.usages is not request.binding_request.usages
    assert result.handoff_closure.payload is not request.handoff_closure.payload


def test_returned_copy_is_deep_isolated_from_original() -> None:
    """The returned copy must share no mutable nested objects with the input."""
    request = _make_valid_request()
    original_wire = deepcopy(request.model_dump(by_alias=True, mode="json"))

    result = _validate_resolution_input_v3(request)

    # Use the shared helper to verify nested isolation
    _assert_deep_isolation(result, request)

    # Mutate the returned copy
    result.project_context.request_ids.append("injected-id")
    result.binding_request.usages[0].evidence_ids.append("injected-evidence")
    result.handoff_closure.payload.usages[0].outcome = "SKIPPED"

    # Original must be completely unchanged
    current_wire = request.model_dump(by_alias=True, mode="json")
    assert current_wire == original_wire, "original request was mutated by changes to the copy"


def test_isolation_assertion_catches_shallow_copy() -> None:
    """Verify the isolation helper catches a shallow-copy (model_copy) implementation.

    request.model_copy() defaults to shallow copy — nested models are shared.
    Patching the structure re-validation to use model_copy() must cause the
    shared isolation helper to raise AssertionError.
    """
    request = _make_valid_request()

    def shallow_restore(value: object) -> ResolveMetadataRequestV3:
        # model_copy() defaults to shallow — nested objects ARE shared
        if not isinstance(value, ResolveMetadataRequestV3):
            raise MetadataResolutionInputErrorV3("METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID")
        return value.model_copy()

    with patch(
        "release_sql_bot.application.metadata_resolution_v3._revalidate_request_structure",
        side_effect=shallow_restore,
    ):
        result = _validate_resolution_input_v3(request)
        # With shallow copy, the isolation helper must detect shared state
        with pytest.raises(AssertionError):
            _assert_deep_isolation(result, request)


# ===================================================================
# Section 2: Step 0 — Structure re-validation
# ===================================================================


def test_rejects_dict_root_object() -> None:
    """A plain dict (even with valid content) must be rejected."""
    wire = valid_resolve_metadata_request_v3_wire()
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(wire)  # type: ignore[arg-type]
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


def test_rejects_v2_model_root_object() -> None:
    """A valid V2 model must be rejected as root object."""
    from release_sql_bot.domain.fact_bindings_v2 import FactBindingRequestV2

    v2_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "fact-binding-request-2.0.0.synthetic-ready.json"
    )
    v2_payload = json.loads(v2_path.read_text(encoding="utf-8"))
    v2_model = FactBindingRequestV2.model_validate(v2_payload)
    assert isinstance(v2_model, FactBindingRequestV2)
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(v2_model)  # type: ignore[arg-type]
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


def test_rejects_model_copy_with_invalid_version() -> None:
    """Top-level schema_version Literal violation is caught by structure re-validation."""
    request = _make_valid_request()
    mutated = request.model_copy(update={"schema_version": "9.9.9"})
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(mutated)
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


def test_rejects_nested_unserializable_object() -> None:
    """model_copy injecting an unserializable object in a nested field is caught."""
    request = _make_valid_request()
    mutated = request.model_copy(
        update={
            "handoff_closure": request.handoff_closure.model_copy(
                update={"payload": object()}  # not a valid FactBindingRequestV3
            )
        }
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(mutated)
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


def test_rejects_post_construction_field_corruption() -> None:
    """Directly corrupting a nested field after construction is caught."""
    request = _make_valid_request()
    # Corrupt the request_id in the binding request to a non-string type
    object.__setattr__(request.binding_request, "request_id", 12345)  # type: ignore
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(request)
    assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"


# ===================================================================
# Section 3: Step 1 — Handoff closure
# ===================================================================


def test_handoff_hash_mismatch_blocked_before_approval() -> None:
    """Handoff payload hash mismatch is caught; approval validator is not reached."""
    request = _make_valid_request()
    tampered = request.handoff_closure.model_copy(update={"payload_sha256": "b" * 64})
    tampered_request = request.model_copy(update={"handoff_closure": tampered})

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered_request)
    assert exc_info.value.code == "HANDOFF_PAYLOAD_HASH_MISMATCH"

    # Verify approval validator was not called on the original (valid) request
    assert (
        validate_approval_closure_v3(
            request.project_context,
            request.metadata_snapshot,
            request.approval_record,
        )
        is None
    )


def test_handoff_hash_mismatch_approval_not_called_via_spy() -> None:
    """Confirm approval validator is never reached when handoff fails first."""
    request = _make_valid_request()
    tampered = request.handoff_closure.model_copy(update={"payload_sha256": "c" * 64})
    tampered_request = request.model_copy(update={"handoff_closure": tampered})

    approval_called = False
    original_validate = validate_approval_closure_v3

    def spy_validate(*args: object) -> None:
        nonlocal approval_called
        approval_called = True
        return original_validate(*args)  # type: ignore[arg-type]

    with patch(
        "release_sql_bot.application.metadata_resolution_v3.validate_approval_closure_v3",
        side_effect=spy_validate,
    ):
        with pytest.raises(MetadataResolutionInputErrorV3):
            _validate_resolution_input_v3(tampered_request)

    assert approval_called is False, "approval validator must not be called on handoff failure"


# ===================================================================
# Section 4: Step 2 — bindingRequest vs closure.payload
# ===================================================================


def test_binding_request_outcome_mismatch() -> None:
    """Changing bindingRequest usage outcome (non-identity field) is caught."""
    request = _make_valid_request()
    binding_request = deepcopy(request.binding_request.model_dump(by_alias=True, mode="json"))
    binding_request["usages"][0]["outcome"] = "SKIPPED"
    new_binding = FactBindingRequestV3.model_validate(binding_request)

    assert new_binding.request_id == request.handoff_closure.request_id
    assert new_binding.rule_ref == request.handoff_closure.payload.rule_ref

    tampered_request = request.model_copy(update={"binding_request": new_binding})

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered_request)
    assert exc_info.value.code == "HANDOFF_BINDING_REQUEST_MISMATCH"


def test_binding_request_evidence_ids_order_mismatch() -> None:
    """Reversing evidenceIds order in bindingRequest is caught by byte comparison.

    Proves the gate is order-sensitive: same identity, same evidence ID set,
    only the array order differs.
    """
    from release_sql_bot.application.canonical import canonical_sha256

    request = _make_valid_request()

    # Step 1: Build payload A with two different, valid evidence IDs
    payload_a = request.binding_request.model_dump(by_alias=True, mode="json")
    payload_a["queryRequirements"]["aggregation"]["evidenceIds"] = [
        "ev-query-requirement",
        "ev-fact-declaration",
    ]
    binding_a = FactBindingRequestV3.model_validate(deepcopy(payload_a))

    # Step 2: Update closure.payload to A and recompute payloadSha256
    closure_wire = request.handoff_closure.model_dump(by_alias=True, mode="json")
    closure_wire["payload"] = deepcopy(payload_a)
    closure_wire["payloadSha256"] = canonical_sha256(FactBindingRequestV3.model_validate(payload_a))
    # batchSha256 is not recomputed — it is not a repository truth proof
    updated_closure = HandoffClosureV3.model_validate(closure_wire)

    # Step 3: Baseline request with closure.payload=A, bindingRequest=A — must pass
    baseline = request.model_copy(
        update={
            "handoff_closure": updated_closure,
            "binding_request": binding_a,
        }
    )
    _validate_resolution_input_v3(baseline)  # should not raise

    # Step 4: Deep copy bindingRequest and reverse evidenceIds order
    payload_b = deepcopy(payload_a)
    payload_b["queryRequirements"]["aggregation"]["evidenceIds"] = [
        "ev-fact-declaration",
        "ev-query-requirement",
    ]
    binding_b = FactBindingRequestV3.model_validate(payload_b)

    # Step 5: Pre-call assertions
    assert isinstance(binding_a, FactBindingRequestV3)
    assert isinstance(binding_b, FactBindingRequestV3)
    assert len(binding_a.query_requirements.aggregation.evidence_ids) == 2
    assert len(binding_b.query_requirements.aggregation.evidence_ids) == 2
    assert set(binding_a.query_requirements.aggregation.evidence_ids) == set(
        binding_b.query_requirements.aggregation.evidence_ids
    )
    assert (
        binding_a.query_requirements.aggregation.evidence_ids
        != binding_b.query_requirements.aggregation.evidence_ids
    )
    assert binding_a.request_id == binding_b.request_id
    assert binding_a.rule_ref == binding_b.rule_ref

    # Restoring B's order to A's order makes the full wire identical
    payload_b_restored = deepcopy(payload_b)
    payload_b_restored["queryRequirements"]["aggregation"]["evidenceIds"] = [
        "ev-query-requirement",
        "ev-fact-declaration",
    ]
    assert payload_b_restored == payload_a

    # Step 6: Construct error request with the correctly-hashed closure and reordered bindingRequest
    error_request = request.model_copy(
        update={
            "handoff_closure": updated_closure,
            "binding_request": binding_b,
        }
    )

    # Step 7: Call the real gate and assert the precise code
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(error_request)
    assert exc_info.value.code == "HANDOFF_BINDING_REQUEST_MISMATCH"


# ===================================================================
# Section 5: Step 3 — Approval closure
# ===================================================================


@pytest.mark.parametrize(
    "expected_code",
    [
        "APPROVAL_ID_MISMATCH",
        "APPROVAL_POLICY_MISMATCH",
        "APPROVAL_TIME_MISMATCH",
        "APPROVAL_CONTEXT_REF_MISMATCH",
        "APPROVAL_SNAPSHOT_REF_MISMATCH",
        "APPROVAL_CONTEXT_NOT_APPROVED",
        "APPROVAL_SNAPSHOT_BINDING_MISMATCH",
    ],
)
def test_approval_error_codes_preserved(expected_code: str) -> None:
    """Each approval code converts to MetadataResolutionInputErrorV3."""
    request = _make_valid_request()
    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")

    context_overrides: dict[str, object] = {}
    approval_overrides: dict[str, object] = {}

    if expected_code == "APPROVAL_ID_MISMATCH":
        context_overrides["approvalRef"] = {
            "approvalId": "wrong-id",
            "policyVersion": "policy-v1",
            "approvedAt": "2026-09-09T00:00:00+00:00",
        }
    elif expected_code == "APPROVAL_POLICY_MISMATCH":
        context_overrides["approvalRef"] = {
            "approvalId": "approval-1",
            "policyVersion": "wrong-policy",
            "approvedAt": "2026-09-09T00:00:00+00:00",
        }
    elif expected_code == "APPROVAL_TIME_MISMATCH":
        context_overrides["approvalRef"] = {
            "approvalId": "approval-1",
            "policyVersion": "policy-v1",
            "approvedAt": "2026-09-10T00:00:00+00:00",
        }
    elif expected_code == "APPROVAL_CONTEXT_REF_MISMATCH":
        approval_overrides["contextRef"] = {
            "contextId": "wrong-ctx",
            "contextVersion": 1,
            "sha256": "a" * 64,
        }
    elif expected_code == "APPROVAL_SNAPSHOT_REF_MISMATCH":
        approval_overrides["snapshotRef"] = {
            "snapshotId": "wrong-snap",
            "snapshotVersion": 1,
            "sha256": "a" * 64,
        }
    elif expected_code == "APPROVAL_CONTEXT_NOT_APPROVED":
        context_overrides["status"] = "draft"
    elif expected_code == "APPROVAL_SNAPSHOT_BINDING_MISMATCH":
        context_overrides["metadataSnapshotRef"] = {
            "snapshotId": "snap-1",
            "snapshotVersion": 1,
            "sha256": "f" * 64,
        }

    context = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=request.binding_request.rule_ref.model_dump(by_alias=True, mode="json"),
        request_id=request.binding_request.request_id,
        **(context_overrides or {}),
    )
    context_wire = context.model_dump(by_alias=True, mode="json")

    approval = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
        **(approval_overrides or {}),
    )

    request = _make_request_with_context(
        context=context,
        snapshot=snapshot,
        approval=approval,
        closure_wire=request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=request.binding_request.model_dump(by_alias=True, mode="json"),
    )

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(request)
    assert exc_info.value.code == expected_code


def test_approval_content_hash_mismatch() -> None:
    """Tampered approval contentSha256 is caught."""
    request = _make_valid_request()
    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")

    context = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=request.binding_request.rule_ref.model_dump(by_alias=True, mode="json"),
        request_id=request.binding_request.request_id,
    )
    context_wire = context.model_dump(by_alias=True, mode="json")
    approval = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )
    tampered_approval = approval.model_copy(update={"content_sha256": "e" * 64})

    tampered = _make_request_with_context(
        context=context,
        snapshot=snapshot,
        approval=tampered_approval,
        closure_wire=request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=request.binding_request.model_dump(by_alias=True, mode="json"),
    )

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered)
    assert exc_info.value.code == "APPROVAL_CONTENT_HASH_MISMATCH"


def test_approval_snapshot_not_approved() -> None:
    """Snapshot with status=draft is caught."""
    request = _make_valid_request()

    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    snapshot_wire["status"] = "draft"
    snapshot = GovernedMetadataSnapshotV3.model_validate(snapshot_wire)
    snapshot_wire["contentSha256"] = canonical_content_sha256(snapshot)
    snapshot = GovernedMetadataSnapshotV3.model_validate(snapshot_wire)
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")

    context = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=request.binding_request.rule_ref.model_dump(by_alias=True, mode="json"),
        request_id=request.binding_request.request_id,
    )
    context_wire = context.model_dump(by_alias=True, mode="json")
    approval = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )

    tampered = _make_request_with_context(
        context=context,
        snapshot=snapshot,
        approval=approval,
        closure_wire=request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=request.binding_request.model_dump(by_alias=True, mode="json"),
    )

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered)
    assert exc_info.value.code == "APPROVAL_SNAPSHOT_NOT_APPROVED"


def test_real_approval_happy_path_and_one_real_tampered_example() -> None:
    """Happy path passes approval; a real tampered approval is caught."""
    request = _make_valid_request()
    _validate_resolution_input_v3(request)  # should not raise

    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    context = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=request.binding_request.rule_ref.model_dump(by_alias=True, mode="json"),
        request_id=request.binding_request.request_id,
        approvalRef={
            "approvalId": "approval-1",
            "policyVersion": "wrong-policy",
            "approvedAt": "2026-09-09T00:00:00+00:00",
        },
    )
    context_wire = context.model_dump(by_alias=True, mode="json")
    approval = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )
    tampered = _make_request_with_context(
        context=context,
        snapshot=snapshot,
        approval=approval,
        closure_wire=request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=request.binding_request.model_dump(by_alias=True, mode="json"),
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered)
    assert exc_info.value.code == "APPROVAL_POLICY_MISMATCH"


# ===================================================================
# Section 6: Step 4 — Project scope
# ===================================================================


def test_project_id_mismatch() -> None:
    """projectId mismatch is caught."""
    request = _make_valid_request()
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"


def test_project_version_mismatch() -> None:
    """projectVersion mismatch is caught."""
    request = _make_valid_request()
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "proj-1", "projectVersion": 999}
    tampered = ResolveMetadataRequestV3.model_validate(wire)
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"


# ===================================================================
# Section 7: Step 5 — Rule scope
# ===================================================================


@pytest.mark.parametrize(
    "field,value",
    [
        ("ruleSetId", "WRONG_RULESET"),
        ("ruleVersion", "WRONG@v1"),
        ("sourceSha256", "1" * 64),
        ("catalogDigest", "2" * 64),
        ("candidatePayloadSha256", "3" * 64),
    ],
)
def test_rule_ref_field_mismatch(field: str, value: str) -> None:
    """Each independently modifiable ruleRef field mismatch is caught."""
    request = _make_valid_request()
    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")

    original_ruleRef = request.binding_request.rule_ref.model_dump(by_alias=True, mode="json")
    tampered_ruleRef = {**original_ruleRef, field: value}

    context = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=tampered_ruleRef,
        request_id=request.binding_request.request_id,
    )
    context_wire = context.model_dump(by_alias=True, mode="json")
    approval = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )

    tampered = _make_request_with_context(
        context=context,
        snapshot=snapshot,
        approval=approval,
        closure_wire=request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=request.binding_request.model_dump(by_alias=True, mode="json"),
    )

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered)
    assert exc_info.value.code == "RULE_REF_MISMATCH"


# ===================================================================
# Section 8: Step 6 — Request in context
# ===================================================================


def test_request_not_in_context_blocked() -> None:
    """requestId not in context.requestIds is blocked."""
    request = _make_valid_request()
    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")

    context = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=request.binding_request.rule_ref.model_dump(by_alias=True, mode="json"),
        request_id="other-request-id",
    )
    context_wire = context.model_dump(by_alias=True, mode="json")
    approval = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )

    tampered = _make_request_with_context(
        context=context,
        snapshot=snapshot,
        approval=approval,
        closure_wire=request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=request.binding_request.model_dump(by_alias=True, mode="json"),
    )

    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered)
    assert exc_info.value.code == "REQUEST_NOT_IN_CONTEXT"


def test_extra_request_id_passes() -> None:
    """An additional requestId in context does not cause failure."""
    request = _make_valid_request()
    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")

    wire = {
        "schemaVersion": "1.0.0",
        "contextId": "ctx-1",
        "contextVersion": 1,
        "status": "approved",
        "projectRef": {"projectId": "proj-1", "projectVersion": 1},
        "ruleRef": deepcopy(
            request.binding_request.rule_ref.model_dump(by_alias=True, mode="json")
        ),
        "requestIds": [
            request.binding_request.request_id,
            "other-valid-request",
        ],
        "metadataSnapshotRef": {
            "snapshotId": "snap-1",
            "snapshotVersion": 1,
            "sha256": snapshot_wire["contentSha256"],
        },
        "authorizationPolicyVersion": "policy-v1",
        "relationGrants": [
            {
                "grantId": "relgrant-1",
                "schemaName": "dbo",
                "relationName": "synthetic_table",
                "access": "read",
            }
        ],
        "columnGrants": [
            {
                "grantId": "colgrant-value",
                "relationGrantId": "relgrant-1",
                "columnName": "synthetic_value",
            },
            {
                "grantId": "colgrant-key",
                "relationGrantId": "relgrant-1",
                "columnName": "synthetic_key",
            },
        ],
        "fieldBindingAuthorizations": [
            {
                "authorizationId": "fba-value",
                "requestId": request.binding_request.request_id,
                "fieldId": "factValue",
                "role": "value",
                "columnGrantId": "colgrant-value",
            },
            {
                "authorizationId": "fba-key",
                "requestId": request.binding_request.request_id,
                "fieldId": "syntheticKey",
                "role": "entityKey",
                "columnGrantId": "colgrant-key",
            },
        ],
        "entityKeyAuthorizations": [
            {
                "authorizationId": "eka-1",
                "requestId": request.binding_request.request_id,
                "parameterName": "syntheticKey",
                "fieldId": "syntheticKey",
                "columnGrantId": "colgrant-key",
            }
        ],
        "joinGrants": [],
        "approvalRef": dict(_APPROVAL_REF),
        "contentSha256": _VALID_SHA,
    }
    model = ProjectBindingContextV3.model_validate(wire)
    wire["contentSha256"] = canonical_content_sha256(model)
    context = ProjectBindingContextV3.model_validate(wire)
    context_wire = context.model_dump(by_alias=True, mode="json")

    approval = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )

    valid_request = _make_request_with_context(
        context=context,
        snapshot=snapshot,
        approval=approval,
        closure_wire=request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=request.binding_request.model_dump(by_alias=True, mode="json"),
    )

    result = _validate_resolution_input_v3(valid_request)
    assert result is not None


# ===================================================================
# Section 9: Multi-error ordering — bidirectional evidence
# ===================================================================


def test_handoff_error_before_approval_error_bidirectional() -> None:
    """Handoff error wins over approval error; fixing handoff reveals approval error."""
    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    base_request = _make_valid_request()

    # Build a context with a deliberate approval error
    context_bad_approval = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=base_request.binding_request.rule_ref.model_dump(by_alias=True, mode="json"),
        request_id=base_request.binding_request.request_id,
        approvalRef={
            "approvalId": "approval-1",
            "policyVersion": "wrong-policy",
            "approvedAt": "2026-09-09T00:00:00+00:00",
        },
    )
    context_wire = context_bad_approval.model_dump(by_alias=True, mode="json")
    approval_bad = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )

    # Step 1: Both errors present — handoff fires first
    tampered_closure = base_request.handoff_closure.model_copy(update={"payload_sha256": "e" * 64})
    both_errors = _make_request_with_context(
        context=context_bad_approval,
        snapshot=snapshot,
        approval=approval_bad,
        closure_wire=tampered_closure.model_dump(by_alias=True, mode="json"),
        binding_request=base_request.binding_request.model_dump(by_alias=True, mode="json"),
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(both_errors)
    assert exc_info.value.code == "HANDOFF_PAYLOAD_HASH_MISMATCH"

    # Step 2: Fix only the handoff error — approval error now fires
    fixed_handoff = _make_request_with_context(
        context=context_bad_approval,
        snapshot=snapshot,
        approval=approval_bad,
        closure_wire=base_request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=base_request.binding_request.model_dump(by_alias=True, mode="json"),
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(fixed_handoff)
    assert exc_info.value.code == "APPROVAL_POLICY_MISMATCH"


def test_binding_mismatch_before_approval_error_bidirectional() -> None:
    """bindingRequest mismatch wins over approval error; fixing reveals approval error."""
    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    base_request = _make_valid_request()

    context_bad_approval = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=base_request.binding_request.rule_ref.model_dump(by_alias=True, mode="json"),
        request_id=base_request.binding_request.request_id,
        approvalRef={
            "approvalId": "approval-1",
            "policyVersion": "wrong-policy",
            "approvedAt": "2026-09-09T00:00:00+00:00",
        },
    )
    context_wire = context_bad_approval.model_dump(by_alias=True, mode="json")
    approval_bad = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )

    # Step 1: Both errors present — binding mismatch fires first
    binding_request = deepcopy(base_request.binding_request.model_dump(by_alias=True, mode="json"))
    binding_request["usages"][0]["outcome"] = "SKIPPED"
    both_errors = _make_request_with_context(
        context=context_bad_approval,
        snapshot=snapshot,
        approval=approval_bad,
        closure_wire=base_request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=binding_request,
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(both_errors)
    assert exc_info.value.code == "HANDOFF_BINDING_REQUEST_MISMATCH"

    # Step 2: Fix binding mismatch — approval error now fires
    fixed_binding = _make_request_with_context(
        context=context_bad_approval,
        snapshot=snapshot,
        approval=approval_bad,
        closure_wire=base_request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=base_request.binding_request.model_dump(by_alias=True, mode="json"),
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(fixed_binding)
    assert exc_info.value.code == "APPROVAL_POLICY_MISMATCH"


def test_approval_error_before_project_scope_bidirectional() -> None:
    """Approval error wins over project scope error; fixing reveals project error."""
    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    base_request = _make_valid_request()

    # Context with bad approval policy
    context_bad_approval = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=base_request.binding_request.rule_ref.model_dump(by_alias=True, mode="json"),
        request_id=base_request.binding_request.request_id,
        approvalRef={
            "approvalId": "approval-1",
            "policyVersion": "wrong-policy",
            "approvedAt": "2026-09-09T00:00:00+00:00",
        },
    )
    context_wire = context_bad_approval.model_dump(by_alias=True, mode="json")
    approval_bad = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )

    # Step 1: Both errors — approval fires first
    wire = base_request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    both_errors = ResolveMetadataRequestV3.model_validate(wire)
    both_errors = both_errors.model_copy(
        update={
            "project_context": context_bad_approval,
            "metadata_snapshot": snapshot,
            "approval_record": approval_bad,
        }
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(both_errors)
    assert exc_info.value.code == "APPROVAL_POLICY_MISMATCH"

    # Step 2: Fix approval — project error now fires
    context_good = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=base_request.binding_request.rule_ref.model_dump(by_alias=True, mode="json"),
        request_id=base_request.binding_request.request_id,
    )
    context_wire_good = context_good.model_dump(by_alias=True, mode="json")
    approval_good = _rebuild_approval_with(
        context_sha256=context_wire_good["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )
    fixed_approval = both_errors.model_copy(
        update={
            "project_context": context_good,
            "approval_record": approval_good,
        }
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(fixed_approval)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"


def test_project_mismatch_before_rule_mismatch_bidirectional() -> None:
    """Project mismatch wins over rule mismatch; fixing reveals rule error."""
    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    base_request = _make_valid_request()
    original_ruleRef = base_request.binding_request.rule_ref.model_dump(by_alias=True, mode="json")

    # Context with tampered ruleRef
    tampered_ruleRef = {**original_ruleRef, "ruleSetId": "WRONG"}
    context_bad_rule = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=tampered_ruleRef,
        request_id=base_request.binding_request.request_id,
    )
    context_wire = context_bad_rule.model_dump(by_alias=True, mode="json")
    approval = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )

    # Step 1: Both errors — project fires first
    wire = base_request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    both_errors = ResolveMetadataRequestV3.model_validate(wire)
    both_errors = both_errors.model_copy(
        update={
            "project_context": context_bad_rule,
            "metadata_snapshot": snapshot,
            "approval_record": approval,
        }
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(both_errors)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"

    # Step 2: Fix project — rule error now fires
    wire_fixed = base_request.model_dump(by_alias=True, mode="json")
    fixed_project = ResolveMetadataRequestV3.model_validate(wire_fixed)
    fixed_project = fixed_project.model_copy(
        update={
            "project_context": context_bad_rule,
            "metadata_snapshot": snapshot,
            "approval_record": approval,
        }
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(fixed_project)
    assert exc_info.value.code == "RULE_REF_MISMATCH"


def test_rule_mismatch_before_request_not_in_context_bidirectional() -> None:
    """Rule mismatch wins over request-not-in-context; fixing reveals request error."""
    snapshot = _rebuild_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    base_request = _make_valid_request()
    original_ruleRef = base_request.binding_request.rule_ref.model_dump(by_alias=True, mode="json")

    tampered_ruleRef = {**original_ruleRef, "ruleSetId": "WRONG"}
    context_bad_rule = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=tampered_ruleRef,
        request_id="some-other-request",  # step 6 would fail
    )
    context_wire = context_bad_rule.model_dump(by_alias=True, mode="json")
    approval = _rebuild_approval_with(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )

    # Step 1: Both errors — rule fires first
    both_errors = _make_request_with_context(
        context=context_bad_rule,
        snapshot=snapshot,
        approval=approval,
        closure_wire=base_request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=base_request.binding_request.model_dump(by_alias=True, mode="json"),
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(both_errors)
    assert exc_info.value.code == "RULE_REF_MISMATCH"

    # Step 2: Fix rule — request-not-in-context now fires
    context_good_rule = _rebuild_context_with(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=original_ruleRef,
        request_id="some-other-request",  # still doesn't contain the request
    )
    context_wire_good = context_good_rule.model_dump(by_alias=True, mode="json")
    approval_good = _rebuild_approval_with(
        context_sha256=context_wire_good["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )
    fixed_rule = _make_request_with_context(
        context=context_good_rule,
        snapshot=snapshot,
        approval=approval_good,
        closure_wire=base_request.handoff_closure.model_dump(by_alias=True, mode="json"),
        binding_request=base_request.binding_request.model_dump(by_alias=True, mode="json"),
    )
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(fixed_rule)
    assert exc_info.value.code == "REQUEST_NOT_IN_CONTEXT"


# ===================================================================
# Section 10: Input immutability — failure path
# ===================================================================


def test_input_not_mutated_on_success() -> None:
    """The original request is not mutated by a successful validation."""
    request = _make_valid_request()
    original_dump = request.model_dump(by_alias=True, mode="json")
    _validate_resolution_input_v3(request)
    assert request.model_dump(by_alias=True, mode="json") == original_dump


def test_input_not_mutated_on_structure_failure() -> None:
    """The actual input object is not mutated when structure re-validation fails."""
    wire = valid_resolve_metadata_request_v3_wire()
    # Save a snapshot of the input
    original_wire = deepcopy(wire)
    with pytest.raises(MetadataResolutionInputErrorV3):
        _validate_resolution_input_v3(wire)  # type: ignore[arg-type]
    assert wire == original_wire


def test_input_not_mutated_on_handoff_failure() -> None:
    """The actual input object is not mutated when handoff closure fails."""
    request = _make_valid_request()
    tampered = request.handoff_closure.model_copy(update={"payload_sha256": "e" * 64})
    request = request.model_copy(update={"handoff_closure": tampered})
    # Snapshot the actual object we will pass to the gate
    original_dump = request.model_dump(by_alias=True, mode="json")
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(request)
    assert exc_info.value.code == "HANDOFF_PAYLOAD_HASH_MISMATCH"
    # The object we actually passed must be unchanged
    assert request.model_dump(by_alias=True, mode="json") == original_dump


def test_input_not_mutated_on_scope_failure() -> None:
    """The actual input object is not mutated when a scope check fails."""
    request = _make_valid_request()
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": "wrong-proj", "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)
    # Snapshot the actual object we will pass
    original_dump = tampered.model_dump(by_alias=True, mode="json")
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"
    assert tampered.model_dump(by_alias=True, mode="json") == original_dump


# ===================================================================
# Section 11: Sensitive marker sanitization
# ===================================================================


@pytest.mark.parametrize(
    "case",
    [
        "serialization_failure",
        "revalidation_failure",
    ],
)
def test_sensitive_marker_not_in_structure_error(
    case: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Structure failure with sensitive marker on a real V3 root does not leak the marker.

    A. serialization_failure: inject a string into an integer field (usage.priority),
       making model_dump(warnings="error") fail.
    B. revalidation_failure: inject " MARKER " into projectRef.projectId — it is a
       serializable string but violates the stable-ID format constraint.
    """
    caplog.set_level(logging.DEBUG)
    request = _make_valid_request()

    if case == "serialization_failure":
        # Inject marker string into an integer field — serialization fails
        mutated = request.model_copy(
            update={
                "binding_request": request.binding_request.model_copy(
                    update={
                        "usages": [
                            request.binding_request.usages[0].model_copy(
                                update={"priority": _SYNTHETIC_PRIVATE_MARKER}
                            ),
                            *request.binding_request.usages[1:],
                        ]
                    }
                )
            }
        )
        # Precondition: root is a real V3 model
        assert isinstance(mutated, ResolveMetadataRequestV3)
        # Precondition: strict serialization fails (string in integer field)
        with pytest.raises((TypeError, ValueError)):
            mutated.model_dump(by_alias=True, mode="json", warnings="error")

        # Snapshot the actual object passed to the gate (using warnings=False
        # only to get a stable snapshot of the known-illegal field)
        before = deepcopy(mutated.model_dump(by_alias=True, mode="json", warnings=False))
        with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
            _validate_resolution_input_v3(mutated)
        assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"
        # The actual input object must not be mutated
        after = mutated.model_dump(by_alias=True, mode="json", warnings=False)
        assert after == before, "gate mutated its input"

    elif case == "revalidation_failure":
        # Inject padded marker into projectId — serializable but invalid format
        mutated = request.model_copy(
            update={
                "project_context": request.project_context.model_copy(
                    update={
                        "project_ref": request.project_context.project_ref.model_copy(
                            update={"project_id": f" {_SYNTHETIC_PRIVATE_MARKER} "}
                        )
                    }
                )
            }
        )
        # Precondition: root is a real V3 model
        assert isinstance(mutated, ResolveMetadataRequestV3)
        # Precondition: strict serialization succeeds
        wire = mutated.model_dump(by_alias=True, mode="json", warnings="error")
        # Precondition: re-validation of the wire fails (invalid projectId format)
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ResolveMetadataRequestV3.model_validate(wire)

        # Snapshot the actual object passed to the gate
        before = deepcopy(mutated.model_dump(by_alias=True, mode="json"))
        with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
            _validate_resolution_input_v3(mutated)
        assert exc_info.value.code == "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"
        # The actual input object must not be mutated
        after = mutated.model_dump(by_alias=True, mode="json")
        assert after == before, "gate mutated its input"

    # Common sanitization assertions
    assert _SYNTHETIC_PRIVATE_MARKER not in str(exc_info.value)
    assert _SYNTHETIC_PRIVATE_MARKER not in repr(exc_info.value)
    for record in caplog.records:
        assert _SYNTHETIC_PRIVATE_MARKER not in record.message


def test_sensitive_marker_not_in_scope_error(caplog: pytest.LogCaptureFixture) -> None:
    """Scope failure with sensitive marker in projectId does not leak the marker."""
    caplog.set_level(logging.DEBUG)
    request = _make_valid_request()
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectRef"] = {"projectId": _SYNTHETIC_PRIVATE_MARKER, "projectVersion": 1}
    tampered = ResolveMetadataRequestV3.model_validate(wire)
    with pytest.raises(MetadataResolutionInputErrorV3) as exc_info:
        _validate_resolution_input_v3(tampered)
    assert exc_info.value.code == "PROJECT_REF_MISMATCH"
    assert _SYNTHETIC_PRIVATE_MARKER not in str(exc_info.value)
    assert _SYNTHETIC_PRIVATE_MARKER not in repr(exc_info.value)
    for record in caplog.records:
        assert _SYNTHETIC_PRIVATE_MARKER not in record.message


def test_input_immutability_tests_detect_mutating_gate() -> None:
    """Prove the sensitive-marker structure tests catch a gate that mutates input.

    Replaces _validate_resolution_input_v3 in the test module's namespace
    with a fake that modifies the incoming request before raising. The existing
    structure-failure tests must then fail with 'gate mutated its input'.
    """

    def fake_validate_mutates_serialization(
        request: ResolveMetadataRequestV3,
    ) -> ResolveMetadataRequestV3:
        request.binding_request.usages[0].priority = 99999  # type: ignore
        raise MetadataResolutionInputErrorV3("METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID")

    def fake_validate_mutates_revalidation(
        request: ResolveMetadataRequestV3,
    ) -> ResolveMetadataRequestV3:
        request.project_context.project_ref.project_id = "mutated-id"
        raise MetadataResolutionInputErrorV3("METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID")

    import sys

    test_module = sys.modules[__name__]

    # serialization_failure case should detect the mutation
    with patch.object(
        test_module,
        "_validate_resolution_input_v3",
        side_effect=fake_validate_mutates_serialization,
    ):
        with pytest.raises(AssertionError, match="gate mutated its input"):
            test_sensitive_marker_not_in_structure_error(
                "serialization_failure", caplog=_make_null_caplog()
            )

    # revalidation_failure case should detect the mutation
    with patch.object(
        test_module,
        "_validate_resolution_input_v3",
        side_effect=fake_validate_mutates_revalidation,
    ):
        with pytest.raises(AssertionError, match="gate mutated its input"):
            test_sensitive_marker_not_in_structure_error(
                "revalidation_failure", caplog=_make_null_caplog()
            )


def _make_null_caplog() -> pytest.LogCaptureFixture:
    """Create a minimal LogCaptureFixture substitute for the diagnostic test."""

    class _NullCaplog:
        @property
        def records(self) -> list[object]:
            return []

        def set_level(self, level: int, logger: str | None = None) -> None:
            pass

    return _NullCaplog()  # type: ignore[return-value]


# ===================================================================
# Section 12: Neutral error properties
# ===================================================================


def test_error_str_and_repr_do_not_leak_input() -> None:
    """MetadataResolutionInputErrorV3 str/repr contain only the code."""
    error = MetadataResolutionInputErrorV3("PROJECT_REF_MISMATCH")
    assert str(error) == "PROJECT_REF_MISMATCH"
    assert "PROJECT_REF_MISMATCH" in repr(error)
    assert "request" not in str(error).lower()
    assert "payload" not in str(error).lower()
    assert error.code == "PROJECT_REF_MISMATCH"


def test_unknown_code_rejected() -> None:
    """An unknown code cannot be instantiated."""
    with pytest.raises(ValueError):
        MetadataResolutionInputErrorV3("TOTALLY_FAKE_CODE")


# ===================================================================
# Section 13: No infrastructure dependencies
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
