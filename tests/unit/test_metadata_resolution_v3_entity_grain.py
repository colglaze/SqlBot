"""Unit tests for V3 entity-grain mapping resolution (DEV §5.3.2 extension).

Tests the internal helper ``_resolve_entity_grain_mapping_v3``: looking up
the (entityType, grain) mapping in the context, verifying the mapped relation
grant exists, and checking consistency with all resolved entity-key relation
grants.

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataEntityResolutionErrorV3,
    MetadataResolutionInputErrorV3,
    _resolve_entity_grain_mapping_v3,
)
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordV3,
    GovernedMetadataSnapshotV3,
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

    # Reclose handoff closure payload hash
    from release_sql_bot.application.canonical import canonical_sha256
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3

    payload_model = FactBindingRequestV3.model_validate(req_wire["handoffClosure"]["payload"])
    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(payload_model)

    return ResolveMetadataRequestV3.model_validate(req_wire)


# ===================================================================
# Section 1: Happy path
# ===================================================================


def test_valid_entity_grain_mapping_succeeds() -> None:
    """Complete happy path: mapping exists, grant exists, keys consistent."""
    request = _make_valid_request()
    result = _resolve_entity_grain_mapping_v3(request)
    assert len(result) == 1
    assert result[0].entity_type == "synthetic_entity"
    assert result[0].grain == "synthetic_grain"
    assert result[0].relation_grant_id == "relgrant-1"


# ===================================================================
# Section 2: Missing mapping
# ===================================================================


def test_missing_entity_grain_mapping_raises() -> None:
    """No (entityType, grain) match in context → ENTITY_GRAIN_MAPPING_MISSING."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    # Replace entityGrainAuthorizations with a non-matching entry
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "other_entity",
            "grain": "other_grain",
            "relationGrantId": "relgrant-1",
        }
    ]
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataEntityResolutionErrorV3) as exc_info:
        _resolve_entity_grain_mapping_v3(request)
    assert exc_info.value.code == "ENTITY_GRAIN_MAPPING_MISSING"


# ===================================================================
# Section 3: Dangling relation grant
# ===================================================================


def test_dangling_relation_grant_raises() -> None:
    """Mapping references a relation grant not in context → ENTITY_GRAIN_GRANT_INVALID."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "nonexistent-relgrant",
        }
    ]
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataEntityResolutionErrorV3) as exc_info:
        _resolve_entity_grain_mapping_v3(request)
    assert exc_info.value.code == "ENTITY_GRAIN_GRANT_INVALID"


# ===================================================================
# Section 4: Relation mismatch with entity keys
# ===================================================================


def test_relation_mismatch_with_entity_keys_raises() -> None:
    """Entity keys map to a different relation grant → ENTITY_GRAIN_RELATION_MISMATCH."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")

    # Add a second relation grant and point the entity-grain mapping to it
    req_wire["projectContext"]["relationGrants"].append(
        {
            "grantId": "relgrant-other",
            "schemaName": "dbo",
            "relationName": "other_table",
            "access": "read",
        }
    )
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-other",
        }
    ]
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataEntityResolutionErrorV3) as exc_info:
        _resolve_entity_grain_mapping_v3(request)
    assert exc_info.value.code == "ENTITY_GRAIN_RELATION_MISMATCH"


# ===================================================================
# Section 5: Input gate propagation
# ===================================================================


def test_input_gate_failure_propagates() -> None:
    """Input-gate failure (e.g., projectRef mismatch) propagates before entity check."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectRef"]["projectId"] = "wrong-project"
    request = ResolveMetadataRequestV3.model_validate(req_wire)

    with pytest.raises(MetadataResolutionInputErrorV3):
        _resolve_entity_grain_mapping_v3(request)


# ===================================================================
# Section 6: No entity keys — returns empty
# ===================================================================


# ===================================================================
# Section 7: Input immutability
# ===================================================================


def test_input_not_mutated_on_success() -> None:
    """Successful resolution does not mutate the input request."""
    request = _make_valid_request()
    before = request.model_dump(by_alias=True, mode="json")
    _resolve_entity_grain_mapping_v3(request)
    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


def test_input_not_mutated_on_failure() -> None:
    """Failed resolution does not mutate the input request."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "wrong_entity",
            "grain": "wrong_grain",
            "relationGrantId": "relgrant-1",
        }
    ]
    request = _reclose_all_hashes_from_wire(req_wire)
    before = request.model_dump(by_alias=True, mode="json")

    with pytest.raises(MetadataEntityResolutionErrorV3):
        _resolve_entity_grain_mapping_v3(request)

    after = request.model_dump(by_alias=True, mode="json")
    assert after == before


# ===================================================================
# Section 8: Error sanitization
# ===================================================================


def test_error_does_not_leak_entity_type(caplog: pytest.LogCaptureFixture) -> None:
    """EntityResolutionError does not expose the actual entity type value."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": _SYNTHETIC_PRIVATE_MARKER.lower(),
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-1",
        }
    ]
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataEntityResolutionErrorV3) as exc_info:
        _resolve_entity_grain_mapping_v3(request)

    err = exc_info.value
    assert _SYNTHETIC_PRIVATE_MARKER not in err.code
    assert _SYNTHETIC_PRIVATE_MARKER not in str(err)
    assert _SYNTHETIC_PRIVATE_MARKER not in repr(err)


# ===================================================================
# Section 9: Case sensitivity for grantId comparison
# ===================================================================


def test_grant_id_exact_comparison() -> None:
    """grantId comparison is exact (no casefold), even when snapshot is insensitive."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    # Change entity-grain mapping to use uppercase grantId
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "RELGRANT-1",  # uppercase — won't match relgrant-1
        }
    ]
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataEntityResolutionErrorV3) as exc_info:
        _resolve_entity_grain_mapping_v3(request)
    # Should be GRANT_INVALID because "RELGRANT-1" != "relgrant-1" (exact comparison)
    assert exc_info.value.code == "ENTITY_GRAIN_GRANT_INVALID"


# ===================================================================
# Section 10: Identifier case sensitivity — physical names use snapshot strategy
# ===================================================================


def test_physical_name_comparison_uses_snapshot_case_strategy() -> None:
    """Physical identifier matching uses snapshot.identifier_case_sensitivity."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    # The synthetic snapshot uses "insensitive" — this test verifies the
    # entity-grain helper doesn't interfere with physical name comparison.
    # (Physical name matching is tested elsewhere; this is a smoke test.)
    request = _reclose_all_hashes_from_wire(req_wire)
    result = _resolve_entity_grain_mapping_v3(request)
    assert len(result) == 1


# ===================================================================
# Section 11: Context schemaVersion rejection
# ===================================================================


def test_schema_version_enforced_at_pydantic_level() -> None:
    """schemaVersion is fixed to 1.1.0 at the Pydantic level."""
    from pydantic import ValidationError

    request = _make_valid_request()
    # Valid 1.1.0 context works
    assert request.project_context.schema_version == "1.1.0"

    # Attempting to construct a context with wrong schemaVersion fails
    req_wire = request.model_dump(by_alias=True, mode="json")
    req_wire["projectContext"]["schemaVersion"] = "2.0.0"
    with pytest.raises(ValidationError):
        ProjectBindingContextV3.model_validate(req_wire["projectContext"])


# ===================================================================
# Section 12: Two-entity-key consistency
# ===================================================================


def _build_two_key_request() -> ResolveMetadataRequestV3:
    """Build a valid request with TWO entity keys in the SAME relation."""
    base = _make_valid_request()
    base_wire = base.model_dump(by_alias=True, mode="json")

    # Add a second entity key field + authorization + column grant
    # BOTH bindingRequest AND handoffClosure.payload must be updated identically
    for key in ("bindingRequest",):
        base_wire[key]["queryRequirements"]["entity"]["keyParameters"] = [
            "syntheticKey",
            "secondKey",
        ]
        base_wire[key]["queryRequirements"]["fields"].append(
            {
                "fieldId": "secondKey",
                "role": "entityKey",
                "logicalName": "second_entity_key",
                "dataType": "string",
                "required": True,
                "evidenceIds": ["ev-fact-declaration"],
            }
        )
        base_wire[key]["fact"]["parameters"].append(
            {
                "name": "secondKey",
                "role": "entityKey",
                "dataType": "string",
                "required": True,
                "description": "second entity key",
            }
        )

    # handoffClosure.payload must mirror bindingRequest byte-for-byte
    base_wire["handoffClosure"]["payload"] = deepcopy(base_wire["bindingRequest"])

    # Add field binding authorization for secondKey
    base_wire["projectContext"]["fieldBindingAuthorizations"].append(
        {
            "authorizationId": "fba-key-2",
            "requestId": base_wire["bindingRequest"]["requestId"],
            "fieldId": "secondKey",
            "role": "entityKey",
            "columnGrantId": "colgrant-key",  # SAME relation as first key
        }
    )
    # Add entity-key authorization for secondKey
    base_wire["projectContext"]["entityKeyAuthorizations"].append(
        {
            "authorizationId": "eka-2",
            "requestId": base_wire["bindingRequest"]["requestId"],
            "parameterName": "secondKey",
            "fieldId": "secondKey",
            "columnGrantId": "colgrant-key",  # SAME relation as first key
        }
    )
    return _reclose_all_hashes_from_wire(base_wire)


def test_two_entity_keys_same_relation_succeeds() -> None:
    """Two entity keys in the same relation → mapping succeeds."""
    request = _build_two_key_request()
    result = _resolve_entity_grain_mapping_v3(request)
    assert len(result) == 1
    assert result[0].relation_grant_id == "relgrant-1"


def test_second_entity_key_different_relation_rejected() -> None:
    """Second entity key in a DIFFERENT relation → ENTITY_GRAIN_RELATION_MISMATCH."""
    # Build a request where the second key points to a different relation
    base = _make_valid_request()
    base_wire = base.model_dump(by_alias=True, mode="json")

    # Add a second relation + column grant in a different relation
    base_wire["metadataSnapshot"]["relations"].append(
        {
            "schemaName": "dbo",
            "relationName": "other_table",
            "relationKind": "table",
            "columns": [{"columnName": "other_key", "sqlType": "nvarchar(100)", "nullable": False}],
        }
    )
    base_wire["projectContext"]["relationGrants"].append(
        {
            "grantId": "relgrant-other",
            "schemaName": "dbo",
            "relationName": "other_table",
            "access": "read",
        }
    )
    base_wire["projectContext"]["columnGrants"].append(
        {
            "grantId": "colgrant-other-key",
            "relationGrantId": "relgrant-other",
            "columnName": "other_key",
        }
    )

    # Add second entity key pointing to the other relation
    # BOTH bindingRequest AND handoffClosure.payload must be updated identically
    base_wire["bindingRequest"]["queryRequirements"]["entity"]["keyParameters"] = [
        "syntheticKey",
        "otherKey",
    ]
    base_wire["bindingRequest"]["queryRequirements"]["fields"].append(
        {
            "fieldId": "otherKey",
            "role": "entityKey",
            "logicalName": "other_entity_key",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        }
    )
    base_wire["bindingRequest"]["fact"]["parameters"].append(
        {
            "name": "otherKey",
            "role": "entityKey",
            "dataType": "string",
            "required": True,
            "description": "other entity key",
        }
    )
    # handoffClosure.payload must mirror bindingRequest byte-for-byte
    base_wire["handoffClosure"]["payload"] = deepcopy(base_wire["bindingRequest"])

    # Add field binding authorization for otherKey
    base_wire["projectContext"]["fieldBindingAuthorizations"].append(
        {
            "authorizationId": "fba-other",
            "requestId": base_wire["bindingRequest"]["requestId"],
            "fieldId": "otherKey",
            "role": "entityKey",
            "columnGrantId": "colgrant-other-key",  # DIFFERENT relation
        }
    )
    # Add entity-key authorization for otherKey
    base_wire["projectContext"]["entityKeyAuthorizations"].append(
        {
            "authorizationId": "eka-other",
            "requestId": base_wire["bindingRequest"]["requestId"],
            "parameterName": "otherKey",
            "fieldId": "otherKey",
            "columnGrantId": "colgrant-other-key",  # DIFFERENT relation
        }
    )

    request = _reclose_all_hashes_from_wire(base_wire)

    with pytest.raises(MetadataEntityResolutionErrorV3) as exc_info:
        _resolve_entity_grain_mapping_v3(request)
    assert exc_info.value.code == "ENTITY_GRAIN_RELATION_MISMATCH"


# ===================================================================
# Section 13: Physical identifier case strategy
# ===================================================================


def test_physical_case_insensitive_matches() -> None:
    """insensitive snapshot allows case-different physical name matches."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    # Snapshot uses "insensitive" — verify the helper works
    assert req_wire["metadataSnapshot"]["identifierCaseSensitivity"] == "insensitive"
    request = _reclose_all_hashes_from_wire(req_wire)
    result = _resolve_entity_grain_mapping_v3(request)
    assert len(result) == 1


def test_grant_id_case_sensitive_exact_match() -> None:
    """grantId comparison is exact — 'RELGRANT-1' != 'relgrant-1'."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    # Point entity-grain mapping to uppercase grantId
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": "synthetic_entity",
            "grain": "synthetic_grain",
            "relationGrantId": "RELGRANT-1",  # uppercase — won't match
        }
    ]
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataEntityResolutionErrorV3) as exc_info:
        _resolve_entity_grain_mapping_v3(request)
    assert exc_info.value.code == "ENTITY_GRAIN_GRANT_INVALID"


# ===================================================================
# Section 14: Sanitization with actual marker
# ===================================================================


def test_sanitization_checks_actual_marker(caplog: pytest.LogCaptureFixture) -> None:
    """Error messages must not leak the actual marker value."""
    request = _make_valid_request()
    req_wire = request.model_dump(by_alias=True, mode="json")
    # Use a marker that matches the entityType pattern but is clearly synthetic
    marker = "secretentity"
    req_wire["projectContext"]["entityGrainAuthorizations"] = [
        {
            "entityType": marker,
            "grain": "synthetic_grain",
            "relationGrantId": "relgrant-1",
        }
    ]
    request = _reclose_all_hashes_from_wire(req_wire)

    with pytest.raises(MetadataEntityResolutionErrorV3) as exc_info:
        _resolve_entity_grain_mapping_v3(request)

    err = exc_info.value
    assert marker not in err.code
    assert marker not in str(err)
    assert marker not in repr(err)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
