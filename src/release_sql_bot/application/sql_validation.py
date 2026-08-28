"""Pure orchestration for the V2 offline SQL AST safety gate."""

from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
from typing import Protocol

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.metadata_resolution_v2 import resolve_metadata_v2
from release_sql_bot.application.ports.sql_ast import (
    OfflineColumn,
    OfflineRelation,
    SqlDialectInspector,
    SqlGatePolicy,
    SqlInspectionRequest,
)
from release_sql_bot.domain.fact_bindings_v2 import (
    ParameterFilterValueV2,
    UncertaintyImpactV2,
)
from release_sql_bot.domain.project_bindings_v2 import BindingResolutionReportV2
from release_sql_bot.domain.sql_candidates_v2 import SqlTemplateCandidateV2
from release_sql_bot.domain.sql_validation import (
    SqlCandidateValidationRefV2,
    SqlStaticValidationReportV2,
    SqlUsageCoverageEvidenceV2,
    SqlValidationIssueV2,
    ValidateSqlCandidateRequestV2,
)


def _issue(
    gate_order: int,
    code: str,
    field_path: str,
    message: str,
    normalized_identifier: str | None = None,
) -> SqlValidationIssueV2:
    return SqlValidationIssueV2(
        gate_order=gate_order,
        code=code,
        field_path=field_path,
        message=message,
        normalized_identifier=normalized_identifier,
    )


def _sort_issues(
    issues: Iterable[SqlValidationIssueV2],
) -> tuple[SqlValidationIssueV2, ...]:
    unique = {
        (
            item.gate_order,
            item.code,
            item.field_path,
            item.message,
            item.normalized_identifier,
        ): item
        for item in issues
    }
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.gate_order,
                item.code,
                item.field_path,
                item.normalized_identifier or "",
            ),
        )
    )


def _candidate_ref(payload: ValidateSqlCandidateRequestV2) -> SqlCandidateValidationRefV2:
    request = payload.generation_request.resolution_request
    return SqlCandidateValidationRefV2(
        candidate_content_sha256=canonical_content_sha256(payload.candidate),
        sql_template_sha256=sha256(payload.candidate.sql_template.encode("utf-8")).hexdigest(),
        generation_input_sha256=canonical_sha256(payload.generation_request),
        resolution_report_sha256=canonical_sha256(payload.generation_request.resolution_report),
        context_sha256=canonical_content_sha256(request.project_context),
        snapshot_sha256=canonical_content_sha256(request.metadata_snapshot),
    )


class _ColumnLike(Protocol):
    schema_name: str
    relation_name: str
    column_name: str


def _column_key(column: _ColumnLike) -> tuple[str, str, str]:
    return (
        column.schema_name,
        column.relation_name,
        column.column_name,
    )


def _expected_relations(
    report: BindingResolutionReportV2,
) -> set[tuple[str, str]]:
    relations = {
        (
            item.physical_column.schema_name,
            item.physical_column.relation_name,
        )
        for item in report.resolved_bindings
    }
    if report.result_source is not None:
        relations.update(
            (item.schema_name, item.relation_name) for item in report.result_source.physical_columns
        )
    for item in report.authorized_joins:
        relations.add((item.left_column.schema_name, item.left_column.relation_name))
        relations.add((item.right_column.schema_name, item.right_column.relation_name))
    return relations


def _authorized_columns(
    report: BindingResolutionReportV2,
) -> set[tuple[str, str, str]]:
    columns = {_column_key(item.physical_column) for item in report.resolved_bindings}
    if report.result_source is not None:
        columns.update(_column_key(item) for item in report.result_source.physical_columns)
    for item in report.authorized_joins:
        columns.add(_column_key(item.left_column))
        columns.add(_column_key(item.right_column))
    return columns


def _candidate_claims_match(
    candidate: SqlTemplateCandidateV2,
    report: BindingResolutionReportV2,
    payload: ValidateSqlCandidateRequestV2,
) -> bool:
    binding = payload.generation_request.resolution_request.binding_request
    expected_parameters = {
        (item.name, item.data_type, item.required, f"fact.parameters.{item.name}")
        for item in binding.fact.parameters
    }
    actual_parameters = {
        (item.name, item.data_type, item.required, item.source) for item in candidate.parameters
    }
    expected_result = binding.query_requirements.result
    result_matches = (
        candidate.result.column_name == expected_result.column_name
        and candidate.result.data_type is expected_result.data_type
        and candidate.result.cardinality == expected_result.cardinality
        and candidate.result.nullable is expected_result.nullable
        and candidate.result.null_policy is expected_result.null_policy
        and candidate.result.unit == expected_result.unit
    )
    declared_relations = {
        (item.schema_name, item.relation_name) for item in candidate.declared_objects
    }
    return (
        expected_parameters == actual_parameters
        and result_matches
        and declared_relations == _expected_relations(report)
    )


def _reference_issues(
    payload: ValidateSqlCandidateRequestV2,
    recomputed: BindingResolutionReportV2,
) -> list[SqlValidationIssueV2]:
    generation = payload.generation_request
    resolution_request = generation.resolution_request
    carried = generation.resolution_report
    candidate = payload.candidate
    binding = resolution_request.binding_request
    issues: list[SqlValidationIssueV2] = []

    has_blocking_uncertainty = any(
        item.impact is UncertaintyImpactV2.BLOCKING for item in binding.uncertainties
    )
    if (
        recomputed.status != "metadataResolved"
        or recomputed.executable is not False
        or recomputed.blocking_issues
        or recomputed.result_source is None
        or has_blocking_uncertainty
    ):
        issues.append(
            _issue(
                2,
                "RESOLUTION_NOT_READY",
                "/generationRequest/resolutionReport",
                "Phase 2G 重算结果不是无阻断的 metadataResolved 闭包。",
            )
        )

    carried_hash = canonical_sha256(carried)
    recomputed_hash = canonical_sha256(recomputed)
    generation_hash = canonical_sha256(generation)
    if carried_hash != recomputed_hash:
        issues.append(
            _issue(
                2,
                "REFERENCE_MISMATCH",
                "/generationRequest/resolutionReport",
                "携带的 Phase 2G 报告与当前完整输入重算结果不一致。",
            )
        )

    actual_content_hash = canonical_content_sha256(candidate)
    if candidate.content_sha256 != actual_content_hash:
        issues.append(
            _issue(
                2,
                "CANDIDATE_HASH_MISMATCH",
                "/candidate/contentSha256",
                "候选自哈希与候选内容不一致。",
            )
        )

    reference_match = (
        candidate.generation_input_sha256 == generation_hash
        and candidate.resolution_ref.report_sha256 == recomputed_hash
        and candidate.request_ref.request_id == binding.request_id
        and candidate.request_ref.payload_sha256 == recomputed.hashes.payload_sha256
        and candidate.rule_ref.rule_id == binding.rule_ref.rule_id
        and candidate.rule_ref.rule_version == binding.rule_ref.rule_version
        and candidate.rule_ref.schema_version == binding.rule_ref.schema_version
        and candidate.rule_ref.source_sha256 == binding.rule_ref.source_sha256
        and candidate.project_ref.project_id == recomputed.project_ref.project_id
        and candidate.project_ref.project_version == recomputed.project_ref.project_version
        and candidate.resolution_ref.context_ref.context_id == recomputed.context_ref.context_id
        and candidate.resolution_ref.context_ref.context_version
        == recomputed.context_ref.context_version
        and candidate.resolution_ref.context_ref.sha256 == recomputed.context_ref.sha256
        and candidate.resolution_ref.metadata_snapshot_ref.snapshot_id
        == recomputed.metadata_snapshot_ref.snapshot_id
        and candidate.resolution_ref.metadata_snapshot_ref.snapshot_version
        == recomputed.metadata_snapshot_ref.snapshot_version
        and candidate.resolution_ref.metadata_snapshot_ref.sha256
        == recomputed.metadata_snapshot_ref.sha256
        and candidate.resolution_ref.authorization_policy_version
        == recomputed.authorization_policy_version
        and candidate.fact_ref.fact_code == binding.fact.fact_code
        and candidate.fact_ref.fact_kind is binding.fact.fact_kind
        and candidate.fact_ref.data_type is binding.fact.data_type
        and candidate.fact_ref.grain == binding.fact.grain
        and candidate.status == "candidate"
        and candidate.executable is False
        and candidate.review_status == "pending"
        and candidate.dialect == "sqlserver"
        and _candidate_claims_match(candidate, recomputed, payload)
    )
    if not reference_match:
        issues.append(
            _issue(
                2,
                "REFERENCE_MISMATCH",
                "/candidate",
                "候选引用或声明与 V2 生成输入闭包不一致。",
            )
        )

    expected_usage_ids = {item.condition_id for item in binding.usages}
    if set(candidate.declared_usage_coverage) != expected_usage_ids:
        issues.append(
            _issue(
                10,
                "SQL_USAGE_COVERAGE_MISMATCH",
                "/candidate/declaredUsageCoverage",
                "候选 usage 声明与稳定 conditionId 集合不一致。",
            )
        )
    return issues


def _normalize_key(
    value: tuple[str, ...],
    sensitivity: str,
) -> tuple[str, ...]:
    if sensitivity == "sensitive":
        return value
    return tuple(item.casefold() for item in value)


def _join_key(
    join_type: str,
    left: tuple[str, str, str],
    right: tuple[str, str, str],
    sensitivity: str,
) -> tuple[str, tuple[str, str, str], tuple[str, str, str]]:
    normalized_left = _normalize_key(left, sensitivity)
    normalized_right = _normalize_key(right, sensitivity)
    if join_type == "inner" and normalized_right < normalized_left:
        normalized_left, normalized_right = normalized_right, normalized_left
    return (join_type, normalized_left, normalized_right)


def _logical_parameter_names(payload: ValidateSqlCandidateRequestV2) -> set[str]:
    requirements = payload.generation_request.resolution_request.binding_request.query_requirements
    names = set(requirements.entity.key_parameters)
    names.update(
        item.value.parameter_name
        for item in requirements.filters.items
        if isinstance(item.value, ParameterFilterValueV2)
    )
    for boundary in (requirements.time_range.start, requirements.time_range.end):
        if boundary is not None and boundary.kind == "parameter":
            assert boundary.parameter_name is not None
            names.add(boundary.parameter_name)
    return names


def _offline_schema(payload: ValidateSqlCandidateRequestV2) -> tuple[OfflineRelation, ...]:
    snapshot = payload.generation_request.resolution_request.metadata_snapshot
    return tuple(
        OfflineRelation(
            schema_name=relation.schema_name,
            relation_name=relation.relation_name,
            columns=tuple(
                OfflineColumn(name=column.column_name, sql_type=column.sql_type)
                for column in relation.columns
            ),
        )
        for relation in snapshot.relations
    )


def validate_sql_candidate_v2(
    inspector: SqlDialectInspector,
    payload: ValidateSqlCandidateRequestV2,
) -> SqlStaticValidationReportV2:
    """Validate one candidate without database, repository, model, or file access."""

    generation = payload.generation_request
    resolution_request = generation.resolution_request
    binding = resolution_request.binding_request
    recomputed = resolve_metadata_v2(resolution_request)
    candidate_ref = _candidate_ref(payload)
    issues = _reference_issues(payload, recomputed)
    if issues:
        return SqlStaticValidationReportV2(
            status="blocked",
            candidate_ref=candidate_ref,
            parser_ref=inspector.parser_ref,
            issues=_sort_issues(issues),
            inspection=None,
            usage_coverage=(),
        )

    sensitivity = resolution_request.metadata_snapshot.identifier_case_sensitivity.value
    inspection_result = inspector.inspect(
        SqlInspectionRequest(
            sql=payload.candidate.sql_template,
            dialect="tsql",
            identifier_case_sensitivity=sensitivity,
            offline_schema=_offline_schema(payload),
            gate_policy=SqlGatePolicy(),
        )
    )
    inspection = inspection_result.summary
    issues.extend(inspection_result.issues)

    allowed_relations = {
        _normalize_key(item, sensitivity) for item in _expected_relations(recomputed)
    }
    actual_relations = {
        _normalize_key((item.schema_name, item.relation_name), sensitivity)
        for item in inspection.physical_objects
    }
    declared_relations = {
        _normalize_key((item.schema_name, item.relation_name), sensitivity)
        for item in payload.candidate.declared_objects
    }
    for relation in sorted(actual_relations - allowed_relations):
        issues.append(
            _issue(
                6,
                "SQL_OBJECT_NOT_ALLOWED",
                "/inspection/physicalObjects",
                "AST 物理对象不在 Phase 2G 精确授权闭包中。",
                ".".join(relation),
            )
        )
    if actual_relations != declared_relations:
        issues.append(
            _issue(
                6,
                "SQL_OBJECT_CLAIM_MISMATCH",
                "/candidate/declaredObjects",
                "候选对象声明与 AST 实际物理对象集合不一致。",
            )
        )

    authorized_joins = {
        _join_key(
            item.join_type.value,
            _column_key(item.left_column),
            _column_key(item.right_column),
            sensitivity,
        )
        for item in recomputed.authorized_joins
    }
    actual_joins = {
        _join_key(
            item.join_type,
            _column_key(item.left_column),
            _column_key(item.right_column),
            sensitivity,
        )
        for item in inspection.joins
    }
    if actual_joins != authorized_joins:
        issues.append(
            _issue(
                6,
                "SQL_JOIN_NOT_ALLOWED",
                "/inspection/joins",
                "AST join 类型或基础列端点与 Phase 2G 授权 join 集合不一致。",
            )
        )

    allowed_columns = {
        _normalize_key(item, sensitivity) for item in _authorized_columns(recomputed)
    }
    actual_columns = {
        _normalize_key(
            (item.schema_name, item.relation_name, item.column_name),
            sensitivity,
        )
        for item in inspection.base_columns
    }
    for column in sorted(actual_columns - allowed_columns):
        issues.append(
            _issue(
                7,
                "SQL_COLUMN_NOT_ALLOWED",
                "/inspection/baseColumns",
                "AST 基础列不在 Phase 2G 精确授权列集合中。",
                ".".join(column),
            )
        )

    expected_parameters = {item.name for item in binding.fact.parameters}
    actual_parameters = {
        item.name
        for item in inspection.placeholders
        if item.raw_kind == "colonNamed" and item.name is not None
    }
    for name in sorted(expected_parameters - actual_parameters):
        issues.append(
            _issue(
                8,
                "SQL_PARAMETER_MISSING",
                "/inspection/placeholders",
                "事实声明参数未出现在 AST 命名占位符中。",
                name,
            )
        )
    for name in sorted(actual_parameters - expected_parameters):
        issues.append(
            _issue(
                8,
                "SQL_PARAMETER_UNDECLARED",
                "/inspection/placeholders",
                "AST 命名占位符未由 V2 事实参数声明。",
                name,
            )
        )

    logical_parameters = _logical_parameter_names(payload)
    for name in sorted(expected_parameters - logical_parameters):
        issues.append(
            _issue(
                8,
                "SQL_PARAMETER_LOGIC_UNBOUND",
                "/generationRequest/resolutionRequest/bindingRequest/queryRequirements",
                "事实参数未被 entity、filter 或 time 逻辑要求引用。",
                name,
            )
        )
    entity_parameters = set(binding.query_requirements.entity.key_parameters)
    resolved_entity_parameters = {item.parameter_name for item in recomputed.resolved_entity_keys}
    for name in sorted(entity_parameters - resolved_entity_parameters):
        issues.append(
            _issue(
                8,
                "SQL_PARAMETER_ENTITY_KEY_MISSING",
                "/generationRequest/resolutionReport/resolvedEntityKeys",
                "entity key 参数未命中 Phase 2G 已解析实体键。",
                name,
            )
        )

    result_valid = len(inspection.result_columns) == 1
    if result_valid:
        result = inspection.result_columns[0]
        result_valid = result.alias == "fact_value"
        actual_result_sources = {
            _normalize_key(_column_key(item), sensitivity) for item in result.source_columns
        }
        expected_result_sources = {
            _normalize_key(_column_key(item), sensitivity)
            for item in recomputed.result_source.physical_columns
        }
        if recomputed.result_source.mode == "column":
            source_valid = actual_result_sources == expected_result_sources
        else:
            source_valid = bool(actual_result_sources) and expected_result_sources.issubset(
                actual_result_sources
            )
        if not source_valid:
            issues.append(
                _issue(
                    9,
                    "SQL_RESULT_SOURCE_UNPROVEN",
                    result.expression_path,
                    "fact_value 投影未证明依赖 Phase 2G 指定结果来源。",
                )
            )
            result_valid = False
    else:
        issues.append(
            _issue(
                9,
                "SQL_RESULT_SHAPE",
                "/inspection/resultColumns",
                "AST 未形成唯一 fact_value 投影证据。",
            )
        )

    semantic_issues = [item for item in issues if item.gate_order <= 9]
    usage_coverage: tuple[SqlUsageCoverageEvidenceV2, ...] = ()
    if semantic_issues or not result_valid:
        issues.append(
            _issue(
                10,
                "SQL_USAGE_SOURCE_UNPROVEN",
                "/usageCoverage",
                "存在 AST 门禁问题，不能生成稳定 condition usage 覆盖证据。",
            )
        )
    else:
        result_path = inspection.result_columns[0].expression_path
        usage_coverage = tuple(
            SqlUsageCoverageEvidenceV2(
                condition_id=usage.condition_id,
                condition_path=usage.condition_path,
                fact_code=binding.fact.fact_code,
                result_expression_path=result_path,
            )
            for usage in sorted(binding.usages, key=lambda item: item.condition_id)
        )

    sorted_issues = _sort_issues(issues)
    return SqlStaticValidationReportV2(
        status="blocked" if sorted_issues else "passed",
        candidate_ref=candidate_ref,
        parser_ref=inspection.parser_ref,
        issues=sorted_issues,
        inspection=inspection,
        usage_coverage=usage_coverage,
    )
