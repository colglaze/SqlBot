"""Versioned, authority-minimized prompt for V3 SQL candidates.

Independent V3 prompt namespace (sqlserver-fact-candidate-v3.x). Does NOT
reuse V2 prompt text or exactOutputDeclarations structure. Projects only
the information needed for this generation: authorized relations, columns,
entity-key bindings, factValue source, result contract, parameter
declarations (names/types/required/source only, never values), complete
usage six-tuples, and the single read-only SQL Server parameterized
query requirement.

Rule descriptions and evidence text are treated as untrusted data and are
NOT used as authorization instructions. Assumptions must NOT be used to
fill in missing production facts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from release_sql_bot.domain.sql_candidates_v3 import (
    GeneratedCandidatePayloadV3,
    GenerateSqlCandidateRequestV3,
)

SQLSERVER_CANDIDATE_PROMPT_VERSION_V3 = "sqlserver-fact-candidate-v3.1"
SQLSERVER_CANDIDATE_MAX_TOKENS_V3 = 4_096

_SYSTEM_PROMPT_V3 = """You generate exactly one untrusted SQL Server fact candidate as JSON.
Return one JSON object conforming to the supplied schema. Do not return Markdown, code fences,
prose outside JSON, lifecycle status, approval claims, authorization claims, or execution claims.

Use only the supplied authoritative authorized relations, columns, entity-key bindings, and
result contract. Generate one read-only SELECT candidate that returns exactly one scalar column
named fact_value. Use :name placeholders for runtime fact parameters and never invent or embed
runtime parameter values. Never invent relations, columns, joins, aggregation semantics,
time semantics, or business rules. Do not use temporary objects, DDL, DML, EXEC, dynamic SQL,
external access, or multiple statements.

When filterConstraints are supplied, each is a strict entity-key equality
filter that MUST be satisfied by the WHERE clause using the exact same
column = :parameter comparison that the entity-key binding already requires.
Do not duplicate an existing predicate, do not add extra conditions, and do not
alter the operator, column, or parameter name. Filters whose parameter does not
appear in exactOutputDeclarations.parameters must not be materialized.

declaredObjects, declaredUsageCoverage, parameters, and result are untrusted declarations that
later AST gates will recompute. A candidate is never safe, approved, or executable merely because
you returned it. Copy parameters, result, declaredObjects, and declaredUsageCoverage exactly from
exactOutputDeclarations. Do not omit, rename, summarize, merge same-conditionId usages, or add
any declaration. Preserve every usage six-tuple
(stage/ruleCode/priority/conditionId/conditionPath/outcome) exactly as supplied.
"""


@dataclass(frozen=True, slots=True)
class CandidatePromptV3:
    version: str
    system: str
    user: str


def _fact_payload(payload: GenerateSqlCandidateRequestV3) -> dict[str, Any]:
    fact = payload.resolution_request.binding_request.fact
    return {
        "factCode": fact.fact_code,
        "factKind": str(fact.fact_kind),
        "dataType": str(fact.data_type),
        "grain": fact.grain,
        "nullable": fact.nullable,
        "nullPolicy": str(fact.null_policy),
        "unit": fact.unit,
        "parameters": [
            {
                "name": item.name,
                "dataType": str(item.data_type),
                "required": item.required,
            }
            for item in fact.parameters
        ],
    }


def _authorized_physical_plan(
    payload: GenerateSqlCandidateRequestV3,
) -> dict[str, Any]:
    report = payload.resolution_report
    return {
        "resolvedFields": [
            item.model_dump(by_alias=True, mode="json") for item in report.resolved_fields
        ],
        "resolvedEntityKeys": [
            item.model_dump(by_alias=True, mode="json") for item in report.resolved_entity_keys
        ],
    }


def _exact_output_declarations(
    payload: GenerateSqlCandidateRequestV3,
) -> dict[str, Any]:
    request = payload.resolution_request.binding_request
    report = payload.resolution_report

    # Authorized relations from resolved fields + entity keys (single relation in M3 scope)
    relations: dict[tuple[str, str], bool] = {}
    for rf in report.resolved_fields:
        relations[(rf.schema_name, rf.relation_name)] = True
    for re in report.resolved_entity_keys:
        relations[(re.schema_name, re.relation_name)] = True

    # Parameters: names/types/required/source only — never values
    parameters = [
        {
            "name": item.name,
            "dataType": str(item.data_type),
            "required": item.required,
            "source": f"fact.parameters.{item.name}",
        }
        for item in sorted(request.fact.parameters, key=lambda p: p.name)
    ]

    # Result contract from the authoritative request
    result = request.query_requirements.result.model_dump(by_alias=True, mode="json")

    # Declared objects: sorted relations
    declared_objects = [
        {"schemaName": schema, "relationName": relation} for schema, relation in sorted(relations)
    ]

    # Declared usage coverage: full six-tuples, original order
    declared_usage_coverage = [
        {
            "stage": str(item.stage),
            "ruleCode": item.rule_code,
            "priority": item.priority,
            "conditionId": item.condition_id,
            "conditionPath": item.condition_path,
            "outcome": str(item.outcome),
        }
        for item in request.usages
    ]

    return {
        "parameters": parameters,
        "result": result,
        "declaredObjects": declared_objects,
        "declaredUsageCoverage": declared_usage_coverage,
    }


def _filter_constraints(
    payload: GenerateSqlCandidateRequestV3,
) -> list[dict[str, Any]]:
    """Build the deterministic filter-constraint list for the prompt.

    Only emitted when the filters are strict entity-key eq filters that
    passed :func:`qualified_filter_param_names`. Each entry identifies the
    parameter, its physical column, and the evidence trail.
    """
    from release_sql_bot.application.filter_constraints_v3 import (
        qualified_filter_param_names,
    )

    request = payload.resolution_request.binding_request
    report = payload.resolution_report

    if not request.query_requirements.filters.items:
        return []

    qualified = qualified_filter_param_names(payload)
    if not qualified:
        return []

    # Map parameter → physical column from resolved entity keys.
    col_map = {
        ek.parameter_name: {
            "schemaName": ek.schema_name,
            "relationName": ek.relation_name,
            "columnName": ek.column_name,
        }
        for ek in report.resolved_entity_keys
    }

    constraints = []
    for filt in request.query_requirements.filters.items:
        if filt.value is None or str(filt.value.kind) != "parameter":
            continue
        pname = filt.value.parameter_name
        if pname not in qualified:
            continue
        col = col_map.get(pname)
        if col is None:
            continue
        constraints.append(
            {
                "filterId": filt.filter_id,
                "parameterName": pname,
                "schemaName": col["schemaName"],
                "relationName": col["relationName"],
                "columnName": col["columnName"],
                "evidenceIds": list(filt.evidence_ids),
            }
        )
    return constraints


def build_sqlserver_candidate_prompt_v3(
    payload: GenerateSqlCandidateRequestV3,
) -> CandidatePromptV3:
    """Build the V3 candidate prompt with authority-minimized projection."""
    request = payload.resolution_request.binding_request
    report = payload.resolution_report
    prompt_input = {
        "contractVersion": request.contract_version,
        "requestId": request.request_id,
        "ruleRef": request.rule_ref.model_dump(by_alias=True, mode="json"),
        "projectRef": report.project_ref.model_dump(by_alias=True, mode="json"),
        "dialect": request.target_dialect,
        "fact": _fact_payload(payload),
        "authorizedPhysicalPlan": _authorized_physical_plan(payload),
        "exactOutputDeclarations": _exact_output_declarations(payload),
        "filterConstraints": _filter_constraints(payload),
        "outputJsonSchema": GeneratedCandidatePayloadV3.model_json_schema(by_alias=True),
    }
    return CandidatePromptV3(
        version=SQLSERVER_CANDIDATE_PROMPT_VERSION_V3,
        system=_SYSTEM_PROMPT_V3,
        user=json.dumps(
            prompt_input,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
    )
