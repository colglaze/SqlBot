"""Shared synthetic fixtures for V3 filter-support tests.

Provides helpers to build complete, internally-consistent V3 requests
with entity-key eq filters. Every helper rebuilds the full hash chain
(handoff → approval → M2 report) so the request passes all M3/M4 gates.

These fixtures are for offline testing only and do NOT constitute real
approval or repository attestation.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3
from release_sql_bot.domain.fact_binding_handoffs_v3 import (
    StoredFactBindingHandoffBatchV3,
)
from release_sql_bot.domain.fact_bindings_v3 import (
    FactBindingRequestV3,
)
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordV3,
    GovernedMetadataSnapshotV3,
    ProjectBindingContextV3,
    ResolveMetadataRequestV3,
)
from release_sql_bot.domain.sql_candidates_v3 import (
    GenerateSqlCandidateRequestV3,
)
from tests.unit.test_candidates_v3 import _InMemoryHandoffRepository
from tests.v3_metadata_support import valid_resolve_metadata_request_v3_wire


def _base_request_wire() -> dict[str, Any]:
    """Return a fresh copy of the shared fixture wire."""
    return copy.deepcopy(valid_resolve_metadata_request_v3_wire())


def _rebuild_snapshot_and_approval(
    wire: dict[str, Any],
) -> tuple[GovernedMetadataSnapshotV3, ProjectBindingContextV3, ApprovalRecordV3]:
    """Rebuild snapshot/context/approval hashes after wire mutation."""
    snapshot = GovernedMetadataSnapshotV3.model_validate(wire["metadataSnapshot"])
    wire["metadataSnapshot"]["contentSha256"] = canonical_content_sha256(snapshot)
    snapshot = GovernedMetadataSnapshotV3.model_validate(wire["metadataSnapshot"])
    snapshot_sha = wire["metadataSnapshot"]["contentSha256"]

    context = ProjectBindingContextV3.model_validate(wire["projectContext"])
    wire["projectContext"]["metadataSnapshotRef"]["sha256"] = snapshot_sha
    context = ProjectBindingContextV3.model_validate(wire["projectContext"])
    wire["projectContext"]["contentSha256"] = canonical_content_sha256(context)
    context_sha = wire["projectContext"]["contentSha256"]

    approval = ApprovalRecordV3.model_validate(wire["approvalRecord"])
    wire["approvalRecord"]["contextRef"]["sha256"] = context_sha
    wire["approvalRecord"]["snapshotRef"]["sha256"] = snapshot_sha
    approval = ApprovalRecordV3.model_validate(wire["approvalRecord"])
    wire["approvalRecord"]["contentSha256"] = canonical_content_sha256(approval)

    return snapshot, context, approval


def build_filter_request(
    filter_items: list[dict[str, Any]],
    *,
    extra_entity_keys: list[dict[str, Any]] | None = None,
    make_nullable: set[str] | None = None,
) -> tuple[GenerateSqlCandidateRequestV3, Any]:
    """Build a complete V3 generation request with the given filter items.

    Returns ``(payload, handoff_repository)`` ready for M3/M4/M6 testing.

    The request is fully self-consistent: handoff hashes, approval
    references, and M2 resolution are all recomputed from the filter-equipped
    binding request.

    Args:
        filter_items: The filter items to attach.
        extra_entity_keys: Additional entity-key definitions, each a dict
            with ``parameter_name``, ``field_id``, ``column_name``.
        make_nullable: Set of column names to mark nullable=true.
    """
    wire = _base_request_wire()

    # 1. Set filter items on the binding request.
    wire["bindingRequest"]["queryRequirements"]["filters"]["items"] = filter_items

    # 2. Add extra entity keys if requested.
    if extra_entity_keys:
        _add_extra_entity_keys(wire, extra_entity_keys)

    # 3. Mark columns nullable if requested.
    if make_nullable:
        for rel in wire["metadataSnapshot"]["relations"]:
            for col in rel["columns"]:
                if col["columnName"] in make_nullable:
                    col["nullable"] = True

    # 4. Rebuild snapshot/context/approval hashes.
    _rebuild_snapshot_and_approval(wire)

    # 5. Close the handoff payload hash.
    wire["handoffClosure"]["payload"] = wire["bindingRequest"]
    wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire["bindingRequest"])
    )

    # 6. Rebuild the handoff batch.
    closure = wire["handoffClosure"]
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)
    batch_request_id = closure["requestId"]
    batch_rule_version = closure["ruleVersion"]
    request_dict = {
        "request_id": batch_request_id,
        "rule_version": batch_rule_version,
        "fact_code": closure["factCode"],
        "contract_version": "3.0.0",
        "payload_sha256": closure["payloadSha256"],
        "created_at": now,
        "payload": wire["bindingRequest"],
    }
    batch_sha256 = canonical_sha256(
        [{"requestId": batch_request_id, "payloadSha256": closure["payloadSha256"]}]
    )
    batch = StoredFactBindingHandoffBatchV3.model_validate(
        {
            "_id": batch_rule_version,
            "rule_version": batch_rule_version,
            "contract_version": "3.0.0",
            "request_count": 1,
            "request_ids": [batch_request_id],
            "batch_sha256": batch_sha256,
            "created_at": now,
            "requests": [request_dict],
        }
    )
    handoff_repo = _InMemoryHandoffRepository(batch)

    # 7. Close the closure's batchSha256.
    wire["handoffClosure"]["batchSha256"] = batch_sha256

    # 8. Run real M2 resolution.
    request = ResolveMetadataRequestV3.model_validate(wire)
    report = resolve_metadata_v3(request)

    # 9. Build the generation request.
    payload = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": wire,
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    return payload, handoff_repo


def _add_extra_entity_keys(
    wire: dict[str, Any],
    extra_keys: list[dict[str, Any]],
) -> None:
    """Add extra entity-key parameters, authorizations, fields, and snapshot cols."""
    binding = wire["bindingRequest"]
    fact = binding["fact"]
    qc = binding["queryRequirements"]
    snapshot = wire["metadataSnapshot"]
    context = wire["projectContext"]
    request_id = binding["requestId"]

    for key in extra_keys:
        param_name = key["parameter_name"]
        field_id = key["field_id"]
        column_name = key["column_name"]
        col_grant_id = f"colgrant-{param_name}"

        # Add fact parameter.
        fact["parameters"].append(
            {
                "name": param_name,
                "role": key.get("role", "entityKey"),
                "dataType": key.get("data_type", "string"),
                "required": key.get("required", True),
                "description": key.get("description", f"Synthetic {param_name} parameter."),
            }
        )

        # Add query requirement field.
        qc["fields"].append(
            {
                "fieldId": field_id,
                "role": "entityKey",
                "logicalName": f"synthetic_{param_name}",
                "dataType": key.get("data_type", "string"),
                "required": key.get("required", True),
                "evidenceIds": ["ev-fact-declaration"],
            }
        )

        # Add to entity key parameters.
        qc["entity"]["keyParameters"].append(param_name)

        # Add column grant.
        context["columnGrants"].append(
            {
                "grantId": col_grant_id,
                "relationGrantId": "relgrant-1",
                "columnName": column_name,
            }
        )

        # Add field binding authorization.
        context["fieldBindingAuthorizations"].append(
            {
                "authorizationId": f"fba-{param_name}",
                "requestId": request_id,
                "fieldId": field_id,
                "role": "entityKey",
                "columnGrantId": col_grant_id,
            }
        )

        # Add entity-key authorization.
        context["entityKeyAuthorizations"].append(
            {
                "authorizationId": f"eka-{param_name}",
                "requestId": request_id,
                "parameterName": param_name,
                "fieldId": field_id,
                "columnGrantId": col_grant_id,
            }
        )

        # Add snapshot column to the first relation.
        snapshot["relations"][0]["columns"].append(
            {
                "columnName": column_name,
                "sqlType": key.get("sql_type", "nvarchar(100)"),
                "nullable": key.get("nullable", False),
            }
        )


def make_filter_item(
    *,
    filter_id: str = "filter-1",
    field_id: str = "syntheticKey",
    parameter_name: str = "syntheticKey",
    operator: str = "eq",
    value_kind: str = "parameter",
    null_policy: str = "error",
    required: bool = True,
    evidence_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Build a filter item dict."""
    value: dict[str, Any] = {"kind": value_kind}
    if value_kind == "parameter":
        value["parameterName"] = parameter_name
    else:
        value["literal"] = "100"
    return {
        "filterId": filter_id,
        "fieldId": field_id,
        "operator": operator,
        "value": value,
        "nullPolicy": null_policy,
        "required": required,
        "evidenceIds": evidence_ids or ["ev-query-requirement"],
    }
