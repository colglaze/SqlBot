from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from release_sql_bot.application.binding_intake_v2 import analyze_binding_gaps_v2
from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.application.metadata_resolution_v2 import resolve_metadata_v2
from release_sql_bot.domain.fact_bindings_v2 import FactBindingRequestV2
from release_sql_bot.domain.project_bindings_v2 import ResolveMetadataRequestV2

ROOT = Path(__file__).resolve().parents[1]
READY_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fact-binding-request-2.0.0.synthetic-ready.json"
BLOCKED_FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "fact-binding-request-2.0.0.synthetic-blocked.json"
)


def ready_binding_payload() -> dict[str, Any]:
    return json.loads(READY_FIXTURE_PATH.read_text(encoding="utf-8"))


def blocked_binding_payload() -> dict[str, Any]:
    return json.loads(BLOCKED_FIXTURE_PATH.read_text(encoding="utf-8"))


def _seal_content(payload: dict[str, Any]) -> dict[str, Any]:
    sealed = deepcopy(payload)
    sealed["contentSha256"] = canonical_content_sha256(sealed)
    return sealed


def metadata_snapshot_payload(
    *,
    second_relation: bool = False,
    case_sensitivity: str = "insensitive",
) -> dict[str, Any]:
    relations: list[dict[str, Any]] = [
        {
            "schemaName": "reporting",
            "relationName": "synthetic_report_amounts",
            "relationKind": "view",
            "columns": [
                {"columnName": "total_amount", "sqlType": "decimal(18,2)", "nullable": False},
                {"columnName": "project_id", "sqlType": "int", "nullable": False},
            ],
        }
    ]
    relationships: list[dict[str, Any]] = []
    if second_relation:
        relations.append(
            {
                "schemaName": "reporting",
                "relationName": "synthetic_projects",
                "relationKind": "view",
                "columns": [
                    {"columnName": "project_id", "sqlType": "int", "nullable": False},
                    {"columnName": "project_code", "sqlType": "nvarchar(40)", "nullable": False},
                ],
            }
        )
        relationships.append(
            {
                "relationshipId": "relationship.synthetic-project",
                "leftColumn": {
                    "schemaName": "reporting",
                    "relationName": "synthetic_report_amounts",
                    "columnName": "project_id",
                },
                "rightColumn": {
                    "schemaName": "reporting",
                    "relationName": "synthetic_projects",
                    "columnName": "project_id",
                },
            }
        )
    return _seal_content(
        {
            "schemaVersion": "1.0.0",
            "snapshotId": "snapshot.synthetic.001",
            "snapshotVersion": 1,
            "status": "approved",
            "dialect": "sqlserver",
            "identifierCaseSensitivity": case_sensitivity,
            "capturedAt": "2026-08-28T00:00:00Z",
            "sourceRef": {
                "sourceKind": "governedArtifact",
                "artifactId": "artifact.synthetic.metadata",
                "artifactVersion": "1.0.0",
                "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            },
            "relations": relations,
            "relationships": relationships,
            "approvalRef": {
                "approvalId": "approval.synthetic.snapshot.001",
                "policyVersion": "metadata-approval-v1",
                "approvedAt": "2026-08-28T00:05:00Z",
            },
            "contentSha256": "0" * 64,
        }
    )


def project_context_payload(
    snapshot: dict[str, Any],
    *,
    second_relation: bool = False,
    include_join: bool = True,
) -> dict[str, Any]:
    binding = ready_binding_payload()
    relation_grants: list[dict[str, Any]] = [
        {
            "grantId": "relation.synthetic.amounts",
            "schemaName": "reporting",
            "relationName": "synthetic_report_amounts",
            "access": "read",
        }
    ]
    column_grants: list[dict[str, Any]] = [
        {
            "grantId": "column.synthetic.total-amount",
            "relationGrantId": "relation.synthetic.amounts",
            "columnName": "total_amount",
        },
        {
            "grantId": "column.synthetic.amount-project-id",
            "relationGrantId": "relation.synthetic.amounts",
            "columnName": "project_id",
        },
    ]
    field_authorizations: list[dict[str, Any]] = [
        {
            "authorizationId": "field.synthetic.fact-value",
            "requestId": binding["requestId"],
            "fieldId": "factValue",
            "role": "value",
            "columnGrantId": "column.synthetic.total-amount",
        },
        {
            "authorizationId": "field.synthetic.entity-project-id",
            "requestId": binding["requestId"],
            "fieldId": "entity.projectId",
            "role": "entityKey",
            "columnGrantId": "column.synthetic.amount-project-id",
        },
        {
            "authorizationId": "field.synthetic.filter-project-id",
            "requestId": binding["requestId"],
            "fieldId": "filter.projectId",
            "role": "filter",
            "columnGrantId": "column.synthetic.amount-project-id",
        },
    ]
    join_grants: list[dict[str, Any]] = []
    if second_relation:
        relation_grants.append(
            {
                "grantId": "relation.synthetic.projects",
                "schemaName": "reporting",
                "relationName": "synthetic_projects",
                "access": "read",
            }
        )
        column_grants.extend(
            [
                {
                    "grantId": "column.synthetic.project-id",
                    "relationGrantId": "relation.synthetic.projects",
                    "columnName": "project_id",
                },
                {
                    "grantId": "column.synthetic.project-code",
                    "relationGrantId": "relation.synthetic.projects",
                    "columnName": "project_code",
                },
            ]
        )
        field_authorizations[-1]["columnGrantId"] = "column.synthetic.project-id"
        if include_join:
            join_grants.append(
                {
                    "grantId": "join.synthetic.amounts-projects",
                    "leftColumnGrantId": "column.synthetic.amount-project-id",
                    "rightColumnGrantId": "column.synthetic.project-id",
                    "joinType": "inner",
                }
            )
    return _seal_content(
        {
            "schemaVersion": "1.0.0",
            "contextId": "context.synthetic.001",
            "contextVersion": 1,
            "status": "approved",
            "projectRef": {"projectId": "project.synthetic", "projectVersion": 1},
            "ruleRef": deepcopy(binding["ruleRef"]),
            "requestIds": [binding["requestId"]],
            "metadataSnapshotRef": {
                "snapshotId": snapshot["snapshotId"],
                "snapshotVersion": snapshot["snapshotVersion"],
                "sha256": snapshot["contentSha256"],
            },
            "authorizationPolicyVersion": "physical-binding-policy-v1",
            "relationGrants": relation_grants,
            "columnGrants": column_grants,
            "fieldBindingAuthorizations": field_authorizations,
            "entityKeyAuthorizations": [
                {
                    "authorizationId": "entity.synthetic.project-id",
                    "requestId": binding["requestId"],
                    "parameterName": "projectId",
                    "fieldId": "entity.projectId",
                    "columnGrantId": "column.synthetic.amount-project-id",
                }
            ],
            "joinGrants": join_grants,
            "approvalRef": {
                "approvalId": "approval.synthetic.context.001",
                "policyVersion": "physical-binding-policy-v1",
                "approvedAt": "2026-08-28T00:10:00Z",
            },
            "contentSha256": "0" * 64,
        }
    )


def resolve_metadata_payload(
    *,
    blocked: bool = False,
    second_relation: bool = False,
    include_join: bool = True,
) -> dict[str, Any]:
    binding_payload = blocked_binding_payload() if blocked else ready_binding_payload()
    binding = FactBindingRequestV2.model_validate(binding_payload)
    gap = analyze_binding_gaps_v2(binding).model_dump(by_alias=True, mode="json")
    snapshot = metadata_snapshot_payload(second_relation=second_relation)
    context = project_context_payload(
        snapshot,
        second_relation=second_relation,
        include_join=include_join,
    )
    if blocked:
        context["ruleRef"] = deepcopy(binding_payload["ruleRef"])
        context["requestIds"] = [binding_payload["requestId"]]
        for authorization in context["fieldBindingAuthorizations"]:
            authorization["requestId"] = binding_payload["requestId"]
        for authorization in context["entityKeyAuthorizations"]:
            authorization["requestId"] = binding_payload["requestId"]
        context = _seal_content(context)
    return {
        "schemaVersion": "1.0.0",
        "projectRef": {"projectId": "project.synthetic", "projectVersion": 1},
        "bindingRequest": binding_payload,
        "bindingGapReport": gap,
        "projectContext": context,
        "metadataSnapshot": snapshot,
    }


def reseal_context(payload: dict[str, Any]) -> None:
    payload["projectContext"] = _seal_content(payload["projectContext"])


def reseal_snapshot_and_context(payload: dict[str, Any]) -> None:
    payload["metadataSnapshot"] = _seal_content(payload["metadataSnapshot"])
    payload["projectContext"]["metadataSnapshotRef"]["sha256"] = payload["metadataSnapshot"][
        "contentSha256"
    ]
    reseal_context(payload)


def refresh_gap_report(payload: dict[str, Any]) -> None:
    request = FactBindingRequestV2.model_validate(payload["bindingRequest"])
    payload["bindingGapReport"] = analyze_binding_gaps_v2(request).model_dump(
        by_alias=True,
        mode="json",
    )


def generate_candidate_request_payload(
    *,
    blocked: bool = False,
    second_relation: bool = False,
) -> dict[str, Any]:
    resolution_payload = resolve_metadata_payload(
        blocked=blocked,
        second_relation=second_relation,
    )
    resolution_request = ResolveMetadataRequestV2.model_validate(resolution_payload)
    resolution_report = resolve_metadata_v2(resolution_request)
    return {
        "schemaVersion": "1.0.0",
        "resolutionRequest": resolution_payload,
        "resolutionReport": resolution_report.model_dump(by_alias=True, mode="json"),
    }


def valid_generated_candidate_v2_payload(
    *,
    second_relation: bool = False,
) -> dict[str, Any]:
    objects = [
        {
            "schemaName": "reporting",
            "relationName": "synthetic_report_amounts",
        }
    ]
    if second_relation:
        objects.append(
            {
                "schemaName": "reporting",
                "relationName": "synthetic_projects",
            }
        )
    return {
        "templateCode": "SYNTHETIC_TOTAL_AMOUNT_V2",
        "sqlTemplate": (
            "SELECT amounts.total_amount AS fact_value "
            "FROM reporting.synthetic_report_amounts AS amounts "
            "WHERE amounts.project_id = :projectId"
        ),
        "parameters": [
            {
                "name": "projectId",
                "dataType": "integer",
                "required": True,
                "source": "fact.parameters.projectId",
            }
        ],
        "result": {
            "columnName": "fact_value",
            "dataType": "money",
            "cardinality": "scalar",
            "nullable": False,
            "nullPolicy": "error",
            "unit": "CNY",
        },
        "declaredObjects": objects,
        "declaredUsageCoverage": ["amount-positive"],
        "assumptions": [],
        "warnings": [],
    }


def valid_generated_candidate_v2_content() -> str:
    return json.dumps(
        valid_generated_candidate_v2_payload(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
