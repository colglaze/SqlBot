"""Reusable synthetic fixtures for V3 metadata resolution tests.

All data is synthetic and offline. No repository, provider, SQL or
environment access. Fixtures return new objects on each call; caller
mutations do not pollute subsequent calls.

These fixtures are for offline testing only and do NOT constitute real
approval or repository attestation.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.handoff_intake_v3 import (
    FACT_BINDING_SCHEMA_ID_V3,
    FACT_BINDING_SCHEMA_SHA256_V3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordV3,
    GovernedMetadataSnapshotV3,
    ProjectBindingContextV3,
    ResolveMetadataRequestV3,
)

ROOT = Path(__file__).resolve().parents[1]
V3_PAYLOAD_PATH = ROOT / "tests" / "fixtures" / "fact-binding-request-3.0.0.synthetic-ready.json"
_APPROVAL_REF = {
    "approvalId": "approval-1",
    "policyVersion": "policy-v1",
    "approvedAt": "2026-09-09T00:00:00+00:00",
}

_VALID_SHA = "a" * 64


def _load_v3_payload() -> dict[str, object]:
    return json.loads(V3_PAYLOAD_PATH.read_text(encoding="utf-8"))


def _derive_local_payload() -> dict[str, object]:
    """Derive a local payload copy with entity-key field added.

    The shared fixture only has a factValue field but requires
    entity.keyParameters=["syntheticKey"]. We add a synthetic entity-key
    field and evidence reference so the payload is self-consistent.
    """
    pl = deepcopy(_load_v3_payload())
    # Add entity-key field alongside the existing factValue field
    pl["queryRequirements"]["fields"].append(
        {
            "fieldId": "syntheticKey",
            "role": "entityKey",
            "logicalName": "synthetic_entity_key",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        }
    )
    return pl


def valid_handoff_closure_v3_wire() -> dict[str, object]:
    """Build a valid HandoffClosureV3 wire with real canonical payload hash."""
    pl = _derive_local_payload()
    rule_ref = pl["ruleRef"]
    rv = rule_ref["ruleVersion"]
    fc = pl["fact"]["factCode"]
    rid = pl["requestId"]
    real_hash = canonical_sha256(FactBindingRequestV3.model_validate(pl))
    return {
        "schemaVersion": "1.0.0",
        "ruleVersion": rv,
        "requestId": rid,
        "factCode": fc,
        "payloadSha256": real_hash,
        "batchSha256": _VALID_SHA,
        "contractSchemaId": FACT_BINDING_SCHEMA_ID_V3,
        "contractSchemaSha256": FACT_BINDING_SCHEMA_SHA256_V3,
        "intakeStatus": "readyForMetadataResolution",
        "payload": pl,
    }


def _build_snapshot_wire() -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "snapshotId": "snap-1",
        "snapshotVersion": 1,
        "status": "approved",
        "dialect": "sqlserver",
        "identifierCaseSensitivity": "insensitive",
        "capturedAt": "2026-09-08T00:00:00+00:00",
        "sourceRef": {
            "sourceKind": "metadataReview",
            "artifactId": "artifact-1",
            "artifactVersion": "v1",
            "sha256": _VALID_SHA,
        },
        "relations": [
            {
                "schemaName": "dbo",
                "relationName": "synthetic_table",
                "relationKind": "table",
                "columns": [
                    {
                        "columnName": "synthetic_value",
                        "sqlType": "int",
                        "nullable": False,
                    },
                    {
                        "columnName": "synthetic_key",
                        "sqlType": "nvarchar(100)",
                        "nullable": False,
                    },
                ],
            }
        ],
        "relationships": [],
        "approvalRef": dict(_APPROVAL_REF),
        "contentSha256": _VALID_SHA,
    }


def _build_context_wire(
    *,
    snapshot_sha256: str,
    rule_ref: dict[str, object],
    request_id: str,
) -> dict[str, object]:
    return {
        "schemaVersion": "1.1.0",
        "contextId": "ctx-1",
        "contextVersion": 2,
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
        "entityGrainAuthorizations": [
            {
                "entityType": "synthetic_entity",
                "grain": "synthetic_grain",
                "relationGrantId": "relgrant-1",
            }
        ],
        "joinAuthorizationEvidence": [],
        "approvalRef": dict(_APPROVAL_REF),
        "contentSha256": _VALID_SHA,
    }


def _build_approval_wire(
    *,
    context_sha256: str,
    snapshot_sha256: str,
) -> dict[str, object]:
    return {
        "schemaVersion": "1.0.0",
        "approvalId": "approval-1",
        "contextRef": {
            "contextId": "ctx-1",
            "contextVersion": 2,
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


def _make_snapshot() -> GovernedMetadataSnapshotV3:
    wire = _build_snapshot_wire()
    model = GovernedMetadataSnapshotV3.model_validate(wire)
    wire["contentSha256"] = canonical_content_sha256(model)
    return GovernedMetadataSnapshotV3.model_validate(wire)


def _make_context(
    *,
    snapshot_sha256: str,
    rule_ref: dict[str, object],
    request_id: str,
) -> ProjectBindingContextV3:
    wire = _build_context_wire(
        snapshot_sha256=snapshot_sha256,
        rule_ref=rule_ref,
        request_id=request_id,
    )
    model = ProjectBindingContextV3.model_validate(wire)
    wire["contentSha256"] = canonical_content_sha256(model)
    return ProjectBindingContextV3.model_validate(wire)


def _make_approval(
    *,
    context_sha256: str,
    snapshot_sha256: str,
) -> ApprovalRecordV3:
    wire = _build_approval_wire(
        context_sha256=context_sha256,
        snapshot_sha256=snapshot_sha256,
    )
    model = ApprovalRecordV3.model_validate(wire)
    wire["contentSha256"] = canonical_content_sha256(model)
    return ApprovalRecordV3.model_validate(wire)


def valid_resolve_metadata_request_v3_wire() -> dict[str, object]:
    """Build a valid ResolveMetadataRequestV3 wire with self-consistent references.

    All identity fields (ruleRef, requestId, factCode) are derived from the
    actual V3 fixture. context/snapshot/approval form a self-consistent
    approval-closure package. Entity-key field and authorizations are included.
    """
    closure_wire = valid_handoff_closure_v3_wire()
    binding_request = deepcopy(closure_wire["payload"])
    rule_ref = binding_request["ruleRef"]
    request_id = binding_request["requestId"]

    snapshot = _make_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    context = _make_context(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=rule_ref,
        request_id=request_id,
    )
    context_wire = context.model_dump(by_alias=True, mode="json")
    approval = _make_approval(
        context_sha256=context_wire["contentSha256"],
        snapshot_sha256=snapshot_wire["contentSha256"],
    )
    return {
        "schemaVersion": "1.0.0",
        "projectRef": {
            "projectId": "proj-1",
            "projectVersion": 1,
        },
        "handoffClosure": closure_wire,
        "bindingRequest": binding_request,
        "projectContext": context_wire,
        "metadataSnapshot": snapshot_wire,
        "approvalRecord": approval.model_dump(by_alias=True, mode="json"),
    }


def _build_report_refs(
    *,
    closure_wire: dict[str, object],
    binding_request: dict[str, object],
    context_wire: dict[str, object],
    snapshot_wire: dict[str, object],
) -> dict[str, object]:
    """Build the common reference/handoff/hashes section of a report."""
    return {
        "requestRef": {
            "requestId": binding_request["requestId"],
            "ruleRef": deepcopy(binding_request["ruleRef"]),
            "payloadSha256": closure_wire["payloadSha256"],
        },
        "projectRef": {"projectId": "proj-1", "projectVersion": 1},
        "contextRef": {
            "contextId": "ctx-1",
            "contextVersion": 1,
            "sha256": context_wire["contentSha256"],
        },
        "snapshotRef": {
            "snapshotId": "snap-1",
            "snapshotVersion": 1,
            "sha256": snapshot_wire["contentSha256"],
        },
        "handoffRefs": {
            "batchSha256": closure_wire["batchSha256"],
            "payloadSha256": closure_wire["payloadSha256"],
            "contractSchemaId": closure_wire["contractSchemaId"],
            "contractSchemaSha256": closure_wire["contractSchemaSha256"],
        },
        "resolutionHashes": {
            "payloadSha256": closure_wire["payloadSha256"],
            "contextSha256": context_wire["contentSha256"],
            "snapshotSha256": snapshot_wire["contentSha256"],
        },
    }


def valid_metadata_resolved_report_v3_wire() -> dict[str, object]:
    """Build a valid metadataResolved report wire (synthetic, offline)."""
    closure_wire = valid_handoff_closure_v3_wire()
    binding_request = deepcopy(closure_wire["payload"])
    snapshot = _make_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    context = _make_context(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=binding_request["ruleRef"],
        request_id=binding_request["requestId"],
    )
    context_wire = context.model_dump(by_alias=True, mode="json")
    refs = _build_report_refs(
        closure_wire=closure_wire,
        binding_request=binding_request,
        context_wire=context_wire,
        snapshot_wire=snapshot_wire,
    )
    return {
        "schemaVersion": "1.0.0",
        "status": "metadataResolved",
        "executable": False,
        **refs,
        "resolvedFields": [
            {
                "fieldId": "factValue",
                "role": "value",
                "authorizationId": "fba-value",
                "columnGrantId": "colgrant-value",
                "schemaName": "dbo",
                "relationName": "synthetic_table",
                "columnName": "synthetic_value",
                "evidenceIds": ["ev-fact-declaration"],
            },
        ],
        "resolvedEntityKeys": [
            {
                "parameterName": "syntheticKey",
                "fieldId": "syntheticKey",
                "authorizationId": "eka-1",
                "columnGrantId": "colgrant-key",
                "schemaName": "dbo",
                "relationName": "synthetic_table",
                "columnName": "synthetic_key",
                "evidenceIds": ["ev-fact-declaration"],
            },
        ],
        "resolvedFilters": [],
        "resolvedAggregation": {
            "mode": "none",
            "function": None,
            "inputFieldIds": [],
            "groupByFieldIds": [],
            "distinct": None,
            "evidenceIds": ["ev-query-requirement"],
        },
        "resolvedTimeRange": {
            "mode": "none",
            "timeFieldId": None,
            "timeSchemaName": None,
            "timeRelationName": None,
            "timeColumnName": None,
            "evidenceIds": ["ev-query-requirement"],
        },
        "resolvedJoins": [],
        "usageTraceabilitySha256": "e" * 64,
        "issues": [],
    }


def _reclose_context_approval_from_wire(
    req_wire: dict[str, object],
) -> None:
    """Reclose context and approval hashes in-place (snapshot already closed)."""
    from release_sql_bot.domain.project_bindings_v3 import (
        ApprovalRecordV3,
        ProjectBindingContextV3,
    )

    context = ProjectBindingContextV3.model_validate(req_wire["projectContext"])
    req_wire["projectContext"]["contentSha256"] = canonical_content_sha256(context)

    req_wire["approvalRecord"]["contextRef"]["sha256"] = req_wire["projectContext"]["contentSha256"]
    req_wire["approvalRecord"]["snapshotRef"]["sha256"] = req_wire["metadataSnapshot"][
        "contentSha256"
    ]
    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    req_wire["approvalRecord"]["contentSha256"] = canonical_content_sha256(approval)


def _reclose_all_hashes_from_wire(
    req_wire: dict[str, object],
) -> ResolveMetadataRequestV3:
    """Reclose snapshot/context/approval/payload hashes from a modified wire."""
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
    from release_sql_bot.domain.project_bindings_v3 import GovernedMetadataSnapshotV3

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

    req_wire["handoffClosure"]["payload"] = req_wire["bindingRequest"]
    req_wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(req_wire["bindingRequest"])
    )
    return ResolveMetadataRequestV3.model_validate(req_wire)


def valid_blocked_report_v3_wire() -> dict[str, object]:
    """Build a valid blocked report wire (synthetic, offline)."""
    closure_wire = valid_handoff_closure_v3_wire()
    binding_request = deepcopy(closure_wire["payload"])
    snapshot = _make_snapshot()
    snapshot_wire = snapshot.model_dump(by_alias=True, mode="json")
    context = _make_context(
        snapshot_sha256=snapshot_wire["contentSha256"],
        rule_ref=binding_request["ruleRef"],
        request_id=binding_request["requestId"],
    )
    context_wire = context.model_dump(by_alias=True, mode="json")
    refs = _build_report_refs(
        closure_wire=closure_wire,
        binding_request=binding_request,
        context_wire=context_wire,
        snapshot_wire=snapshot_wire,
    )
    return {
        "schemaVersion": "1.0.0",
        "status": "blocked",
        "executable": False,
        **refs,
        "resolvedFields": [],
        "resolvedEntityKeys": [],
        "resolvedFilters": [],
        "resolvedAggregation": None,
        "resolvedTimeRange": None,
        "resolvedJoins": [],
        "usageTraceabilitySha256": "e" * 64,
        "issues": [
            {
                "code": "ENTITY_KEY_NOT_AUTHORIZED",
                "owner": "metadataReview",
                "impact": "blocker",
                "message": "Synthetic entity-key authorization is unresolved.",
            },
        ],
    }
