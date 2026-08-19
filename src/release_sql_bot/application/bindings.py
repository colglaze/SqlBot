"""Deterministic readiness gate before Agent 2 SQL generation."""

from __future__ import annotations

from release_sql_bot.domain.fact_bindings import (
    BindingReadiness,
    BlockingIssue,
    FactKind,
    MappingStatus,
    ValidateFactBindingRequest,
)


def _issue(code: str, message: str, field_path: str) -> BlockingIssue:
    return BlockingIssue(code=code, message=message, field_path=field_path)


def validate_binding_readiness(payload: ValidateFactBindingRequest) -> BindingReadiness:
    request = payload.binding_request
    context = payload.context
    issues: list[BlockingIssue] = []

    if request.contract_version != "1.0.0":
        issues.append(
            _issue(
                "BINDING_CONTRACT_UNSUPPORTED",
                "Only FactBindingRequest contract 1.0.0 is supported",
                "bindingRequest.contractVersion",
            )
        )
    if request.rule_ref.schema_version != "2.0.0":
        issues.append(
            _issue(
                "RULE_SCHEMA_UNSUPPORTED",
                "Only RuleReader schema 2.0.0 can enter Agent 2",
                "bindingRequest.ruleRef.schemaVersion",
            )
        )
    if request.status != "candidate":
        issues.append(
            _issue(
                "BINDING_STATUS_INVALID",
                "Fact binding input must remain a candidate",
                "bindingRequest.status",
            )
        )
    if request.fact.fact_kind is FactKind.DERIVED:
        issues.append(
            _issue(
                "DERIVED_FACT_NOT_SQL_BOUND",
                "Derived facts are evaluated deterministically and do not receive SQL templates",
                "bindingRequest.fact.factKind",
            )
        )
    if not request.fact.fact_code:
        issues.append(
            _issue(
                "FACT_CODE_MISSING",
                "A stable factCode is required",
                "bindingRequest.fact.factCode",
            )
        )
    if not request.fact.grain:
        issues.append(
            _issue(
                "FACT_GRAIN_MISSING",
                "Fact grain is required before SQL generation",
                "bindingRequest.fact.grain",
            )
        )
    if not request.fact.parameters:
        issues.append(
            _issue(
                "FACT_PARAMETERS_MISSING",
                "At least one business parameter is required",
                "bindingRequest.fact.parameters",
            )
        )
    if not request.usages:
        issues.append(
            _issue(
                "FACT_USAGE_MISSING",
                "At least one rule condition usage is required",
                "bindingRequest.usages",
            )
        )
    if request.target_dialect != "sqlserver" or context.dialect.name != "sqlserver":
        issues.append(
            _issue(
                "DIALECT_UNSUPPORTED",
                "The first supported target dialect is sqlserver",
                "context.dialect.name",
            )
        )
    if not request.requires_metadata_snapshot:
        issues.append(
            _issue(
                "METADATA_SNAPSHOT_REQUIRED",
                "Agent 2 cannot infer production metadata without a snapshot",
                "bindingRequest.requiresMetadataSnapshot",
            )
        )
    if not context.entity_keys:
        issues.append(
            _issue(
                "ENTITY_KEY_MISSING",
                "SQL Server context requires at least one entity key",
                "context.entityKeys",
            )
        )
    if not context.allowed_relations:
        issues.append(
            _issue(
                "ALLOWED_RELATIONS_MISSING",
                "SQL Server context requires an explicit relation allowlist",
                "context.allowedRelations",
            )
        )
    allowed_relations = {relation.qualified_name for relation in context.allowed_relations}
    key_parameters = {key.parameter_name for key in context.entity_keys}
    required_parameters = {
        parameter.name for parameter in request.fact.parameters if parameter.required
    }
    if missing_parameters := sorted(required_parameters - key_parameters):
        issues.append(
            _issue(
                "PARAMETER_SOURCE_MISSING",
                f"Required fact parameters have no entity key source: {missing_parameters}",
                "context.entityKeys",
            )
        )
    for key in context.entity_keys:
        key_relation = f"{key.schema_name}.{key.relation_name}"
        if key_relation not in allowed_relations:
            issues.append(
                _issue(
                    "ENTITY_KEY_RELATION_NOT_ALLOWED",
                    f"Entity key relation is outside the allowlist: {key_relation}",
                    "context.entityKeys",
                )
            )
    mapping = request.mapping_candidate
    if (
        context.allowed_relations
        and mapping.mapping_status is MappingStatus.MAPPED
        and mapping.view_name
    ):
        if not any(
            relation.relation_name == mapping.view_name for relation in context.allowed_relations
        ):
            issues.append(
                _issue(
                    "MAPPING_RELATION_NOT_ALLOWED",
                    f"Mapped relation is outside the allowlist: {mapping.view_name}",
                    "bindingRequest.mappingCandidate.viewName",
                )
            )
    if request.temp_table_allowed or context.temp_table_allowed:
        issues.append(
            _issue(
                "TEMP_TABLE_DISABLED",
                "Session temporary tables are disabled for the first SQL Server slice",
                "context.tempTableAllowed",
            )
        )

    return BindingReadiness(
        status="blocked" if issues else "ready",
        fact_code=request.fact.fact_code,
        context_id=context.context_id,
        issues=issues,
    )
