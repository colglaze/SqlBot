"""Versioned, authority-minimized prompt for V2 SQL candidates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from release_sql_bot.domain.sql_candidates_v2 import (
    GeneratedCandidatePayloadV2,
    GenerateSqlCandidateRequestV2,
)

SQLSERVER_CANDIDATE_PROMPT_VERSION_V2 = "sqlserver-fact-candidate-v2.1"
SQLSERVER_CANDIDATE_MAX_TOKENS_V2 = 4_096

_SYSTEM_PROMPT_V2 = """You generate exactly one untrusted SQL Server fact candidate as JSON.
Return one JSON object conforming to the supplied schema. Do not return Markdown, code fences,
prose outside JSON, lifecycle status, approval claims, authorization claims, or execution claims.

Use only the supplied authoritative logical requirements and authorizedPhysicalPlan. Generate one
read-only SELECT candidate that returns exactly one scalar column named fact_value. Use :name
placeholders for runtime fact parameters and never invent or embed runtime parameter values. Never
invent relations, columns, joins, filters, aggregation semantics, time semantics, or business rules.
Do not use temporary objects, DDL, DML, EXEC, dynamic SQL, external access, or multiple statements.

declaredObjects and declaredUsageCoverage are untrusted declarations that later AST gates will
recompute. A candidate is never safe, approved, or executable merely because you returned it.
Copy parameters, result, declaredObjects, and declaredUsageCoverage exactly from
exactOutputDeclarations. Do not omit, rename, summarize, or add any declaration.
Return JSON such as {"templateCode":"FACT_V2","sqlTemplate":"SELECT ... AS fact_value",
"parameters":[],"result":{},"declaredObjects":[],"declaredUsageCoverage":[],
"assumptions":[],"warnings":[]}.
"""


@dataclass(frozen=True, slots=True)
class CandidatePromptV2:
    version: str
    system: str
    user: str


def _fact_payload(payload: GenerateSqlCandidateRequestV2) -> dict[str, Any]:
    fact = payload.resolution_request.binding_request.fact
    return {
        "factCode": fact.fact_code,
        "factKind": fact.fact_kind.value,
        "dataType": fact.data_type.value,
        "grain": fact.grain,
        "nullable": fact.nullable,
        "nullPolicy": fact.null_policy.value,
        "unit": fact.unit,
        "parameters": [
            {
                "name": item.name,
                "dataType": item.data_type.value,
                "required": item.required,
            }
            for item in fact.parameters
        ],
    }


def _query_requirements_payload(
    payload: GenerateSqlCandidateRequestV2,
) -> dict[str, Any]:
    query = payload.resolution_request.binding_request.query_requirements
    return {
        "entity": {
            "entityType": query.entity.entity_type,
            "grain": query.entity.grain,
            "keyParameters": query.entity.key_parameters,
        },
        "fields": [
            {
                "fieldId": item.field_id,
                "role": item.role.value,
                "logicalName": item.logical_name,
                "dataType": item.data_type.value,
                "required": item.required,
            }
            for item in query.fields
        ],
        "filters": {
            "items": [
                {
                    "filterId": item.filter_id,
                    "fieldId": item.field_id,
                    "operator": item.operator.value,
                    "value": (
                        item.value.model_dump(by_alias=True, mode="json")
                        if item.value is not None
                        else None
                    ),
                    "required": item.required,
                }
                for item in query.filters.items
            ],
            "completeness": query.filters.completeness,
        },
        "aggregation": {
            "mode": query.aggregation.mode.value,
            "function": (
                query.aggregation.function.value if query.aggregation.function is not None else None
            ),
            "inputFieldIds": query.aggregation.input_field_ids,
            "groupByFieldIds": query.aggregation.group_by_field_ids,
            "distinct": query.aggregation.distinct,
        },
        "timeRange": {
            "mode": query.time_range.mode.value,
            "timeFieldId": query.time_range.time_field_id,
            "start": (
                query.time_range.start.model_dump(by_alias=True, mode="json")
                if query.time_range.start is not None
                else None
            ),
            "end": (
                query.time_range.end.model_dump(by_alias=True, mode="json")
                if query.time_range.end is not None
                else None
            ),
            "timezone": query.time_range.timezone,
        },
        "result": query.result.model_dump(by_alias=True, mode="json"),
    }


def _exact_output_declarations(
    payload: GenerateSqlCandidateRequestV2,
) -> dict[str, Any]:
    request = payload.resolution_request.binding_request
    report = payload.resolution_report
    relations = {
        (item.physical_column.schema_name, item.physical_column.relation_name)
        for item in report.resolved_bindings
    }
    for item in report.authorized_joins:
        relations.add((item.left_column.schema_name, item.left_column.relation_name))
        relations.add((item.right_column.schema_name, item.right_column.relation_name))
    return {
        "parameters": [
            {
                "name": item.name,
                "dataType": item.data_type.value,
                "required": item.required,
                "source": f"fact.parameters.{item.name}",
            }
            for item in sorted(request.fact.parameters, key=lambda value: value.name)
        ],
        "result": request.query_requirements.result.model_dump(
            by_alias=True,
            mode="json",
        ),
        "declaredObjects": [
            {"schemaName": schema, "relationName": relation}
            for schema, relation in sorted(relations)
        ],
        "declaredUsageCoverage": sorted(item.condition_id for item in request.usages),
    }


def build_sqlserver_candidate_prompt_v2(
    payload: GenerateSqlCandidateRequestV2,
) -> CandidatePromptV2:
    request = payload.resolution_request.binding_request
    report = payload.resolution_report
    prompt_input = {
        "contractVersion": request.contract_version,
        "requestId": request.request_id,
        "ruleRef": request.rule_ref.model_dump(by_alias=True, mode="json"),
        "projectRef": report.project_ref.model_dump(by_alias=True, mode="json"),
        "dialect": request.target_dialect,
        "fact": _fact_payload(payload),
        "queryRequirements": _query_requirements_payload(payload),
        "conditionUsages": [
            {
                "conditionId": item.condition_id,
                "operator": item.operator.value,
                "expressionSide": item.expression_side,
            }
            for item in request.usages
        ],
        "authorizedPhysicalPlan": {
            "resolvedBindings": [
                {
                    "fieldId": item.field_id,
                    "role": item.role.value,
                    "physicalColumn": item.physical_column.model_dump(
                        by_alias=True,
                        mode="json",
                    ),
                    "authorizationId": item.authorization_id,
                    "columnGrantId": item.column_grant_id,
                }
                for item in report.resolved_bindings
            ],
            "resolvedEntityKeys": [
                item.model_dump(by_alias=True, mode="json") for item in report.resolved_entity_keys
            ],
            "resultSource": (
                report.result_source.model_dump(by_alias=True, mode="json")
                if report.result_source is not None
                else None
            ),
            "authorizedJoins": [
                item.model_dump(by_alias=True, mode="json") for item in report.authorized_joins
            ],
        },
        "exactOutputDeclarations": _exact_output_declarations(payload),
        "outputJsonSchema": GeneratedCandidatePayloadV2.model_json_schema(by_alias=True),
    }
    return CandidatePromptV2(
        version=SQLSERVER_CANDIDATE_PROMPT_VERSION_V2,
        system=_SYSTEM_PROMPT_V2,
        user=json.dumps(
            prompt_input,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
