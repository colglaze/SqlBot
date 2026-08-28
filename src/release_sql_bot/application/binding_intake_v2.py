"""Deterministic intake and gap analysis for FactBindingRequest 2.0.0."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from hashlib import sha256

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.domain.fact_bindings_v2 import (
    AggregationModeV2,
    BindingGapHashes,
    BindingGapIssue,
    BindingGapOwner,
    BindingGapReport,
    BindingUncertaintyV2,
    FactBindingRequestV2,
    FactKindV2,
    FieldRoleV2,
    ParameterFilterValueV2,
    ResolutionStatusV2,
    TimeRangeModeV2,
    UncertaintyCategoryV2,
    UncertaintyImpactV2,
)

_EXPECTED_UNCERTAINTY_OWNERS: dict[str, BindingGapOwner] = {
    "ENTITY_KEY_UNRESOLVED": BindingGapOwner.BUSINESS_RULE_REVIEW,
    "VALUE_FIELD_UNRESOLVED": BindingGapOwner.METADATA_REVIEW,
    "FILTER_FIELD_UNRESOLVED": BindingGapOwner.METADATA_REVIEW,
    "FILTER_SET_INCOMPLETE": BindingGapOwner.BUSINESS_RULE_REVIEW,
    "AGGREGATION_UNRESOLVED": BindingGapOwner.BUSINESS_RULE_REVIEW,
    "TIME_RANGE_UNRESOLVED": BindingGapOwner.BUSINESS_RULE_REVIEW,
}

_CATEGORY_OWNERS: dict[UncertaintyCategoryV2, BindingGapOwner] = {
    UncertaintyCategoryV2.ENTITY: BindingGapOwner.BUSINESS_RULE_REVIEW,
    UncertaintyCategoryV2.FIELD: BindingGapOwner.METADATA_REVIEW,
    UncertaintyCategoryV2.FILTER: BindingGapOwner.BUSINESS_RULE_REVIEW,
    UncertaintyCategoryV2.AGGREGATION: BindingGapOwner.BUSINESS_RULE_REVIEW,
    UncertaintyCategoryV2.TIME_RANGE: BindingGapOwner.BUSINESS_RULE_REVIEW,
    UncertaintyCategoryV2.SOURCE: BindingGapOwner.METADATA_REVIEW,
}


def _issue(
    code: str,
    owner: BindingGapOwner,
    field_path: str,
    message: str,
    *,
    evidence_ids: Iterable[str] = (),
    uncertainty_id: str | None = None,
) -> BindingGapIssue:
    return BindingGapIssue(
        code=code,
        owner=owner,
        field_path=field_path,
        message=message,
        evidence_ids=tuple(evidence_ids),
        uncertainty_id=uncertainty_id,
    )


def _issue_from_uncertainty(uncertainty: BindingUncertaintyV2) -> BindingGapIssue:
    return _issue(
        uncertainty.code,
        _EXPECTED_UNCERTAINTY_OWNERS.get(
            uncertainty.code,
            _CATEGORY_OWNERS[uncertainty.category],
        ),
        uncertainty.field_path,
        uncertainty.reason,
        evidence_ids=uncertainty.evidence_ids,
        uncertainty_id=uncertainty.uncertainty_id,
    )


def _evidence_references(
    request: FactBindingRequestV2,
) -> Iterator[tuple[str, list[str]]]:
    query = request.query_requirements
    yield "/queryRequirements/entity/evidenceIds", query.entity.evidence_ids
    for index, field in enumerate(query.fields):
        yield f"/queryRequirements/fields/{index}/evidenceIds", field.evidence_ids
    yield "/queryRequirements/filters/evidenceIds", query.filters.evidence_ids
    for index, item in enumerate(query.filters.items):
        yield f"/queryRequirements/filters/items/{index}/evidenceIds", item.evidence_ids
    yield "/queryRequirements/aggregation/evidenceIds", query.aggregation.evidence_ids
    yield "/queryRequirements/timeRange/evidenceIds", query.time_range.evidence_ids
    for index, usage in enumerate(request.usages):
        yield f"/usages/{index}/evidenceIds", usage.evidence_ids
    for index, example in enumerate(request.examples):
        yield f"/examples/{index}/evidenceIds", example.evidence_ids
    for index, uncertainty in enumerate(request.uncertainties):
        yield f"/uncertainties/{index}/evidenceIds", uncertainty.evidence_ids


def _integrity_issues(request: FactBindingRequestV2) -> list[BindingGapIssue]:
    issues: list[BindingGapIssue] = []
    query = request.query_requirements

    if request.contract_version != "2.0.0":
        issues.append(
            _issue(
                "BINDING_CONTRACT_UNSUPPORTED",
                BindingGapOwner.SQL_BOT,
                "/contractVersion",
                "FactBindingRequest contractVersion 必须为 2.0.0。",
            )
        )
    if request.rule_ref.schema_version != "2.0.0":
        issues.append(
            _issue(
                "RULE_SCHEMA_UNSUPPORTED",
                BindingGapOwner.SQL_BOT,
                "/ruleRef/schemaVersion",
                "RuleReader rule schemaVersion 必须为 2.0.0。",
            )
        )
    if request.status != "candidate":
        issues.append(
            _issue(
                "BINDING_STATUS_INVALID",
                BindingGapOwner.SQL_BOT,
                "/status",
                "事实交接必须保持 candidate 状态。",
            )
        )

    expected_request_id = f"{request.rule_ref.rule_version}#{request.fact.fact_code}"
    if request.request_id != expected_request_id:
        issues.append(
            _issue(
                "REQUEST_ID_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/requestId",
                "requestId 必须精确等于 <ruleVersion>#<factCode>。",
            )
        )
    if request.fact.fact_kind is FactKindV2.DERIVED:
        issues.append(
            _issue(
                "DERIVED_FACT_NOT_SQL_BOUND",
                BindingGapOwner.SQL_BOT,
                "/fact/factKind",
                "derived 事实由确定性规则计算，不能进入 SQL 绑定。",
            )
        )
    if request.mapping_candidate.fact_code != request.fact.fact_code:
        issues.append(
            _issue(
                "MAPPING_FACT_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/mappingCandidate/factCode",
                "mappingCandidate.factCode 与事实标识不一致。",
            )
        )
    if request.provenance.source.sha256 != request.rule_ref.source_sha256:
        issues.append(
            _issue(
                "SOURCE_HASH_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/provenance/source/sha256",
                "provenance source 哈希与 ruleRef.sourceSha256 不一致。",
            )
        )
    if request.target_dialect != "sqlserver":
        issues.append(
            _issue(
                "DIALECT_UNSUPPORTED",
                BindingGapOwner.SQL_BOT,
                "/targetDialect",
                "当前只接受 targetDialect=sqlserver。",
            )
        )
    if not request.requires_metadata_snapshot:
        issues.append(
            _issue(
                "METADATA_SNAPSHOT_REQUIRED",
                BindingGapOwner.SQL_BOT,
                "/requiresMetadataSnapshot",
                "V2 接入必须声明 requiresMetadataSnapshot=true。",
            )
        )
    if request.temp_table_allowed:
        issues.append(
            _issue(
                "TEMP_TABLE_DISABLED",
                BindingGapOwner.SQL_BOT,
                "/tempTableAllowed",
                "首个 SQL Server 切片必须保持 tempTableAllowed=false。",
            )
        )

    if query.entity.grain != request.fact.grain:
        issues.append(
            _issue(
                "ENTITY_GRAIN_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/queryRequirements/entity/grain",
                "queryRequirements.entity.grain 与 fact.grain 不一致。",
            )
        )
    result = query.result
    if (
        result.data_type is not request.fact.data_type
        or result.nullable is not request.fact.nullable
        or result.null_policy is not request.fact.null_policy
        or result.unit != request.fact.unit
    ):
        issues.append(
            _issue(
                "RESULT_CONTRACT_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/queryRequirements/result",
                "result 的类型、空值或单位契约与 fact 不一致。",
            )
        )

    evidence_ids = {item.evidence_id for item in request.provenance.evidence}
    for field_path, referenced_ids in _evidence_references(request):
        unknown = sorted(set(referenced_ids) - evidence_ids)
        if unknown:
            issues.append(
                _issue(
                    "EVIDENCE_ID_UNKNOWN",
                    BindingGapOwner.SQL_BOT,
                    field_path,
                    f"evidenceIds 引用了未注册证据：{unknown}。",
                    evidence_ids=unknown,
                )
            )

    parameter_names = {parameter.name for parameter in request.fact.parameters}
    field_ids = {field.field_id for field in query.fields}

    unknown_key_parameters = sorted(set(query.entity.key_parameters) - parameter_names)
    if unknown_key_parameters:
        issues.append(
            _issue(
                "PARAMETER_REFERENCE_UNKNOWN",
                BindingGapOwner.SQL_BOT,
                "/queryRequirements/entity/keyParameters",
                f"entity keyParameters 引用了未声明参数：{unknown_key_parameters}。",
            )
        )

    for index, item in enumerate(query.filters.items):
        if item.field_id not in field_ids:
            issues.append(
                _issue(
                    "FIELD_REFERENCE_UNKNOWN",
                    BindingGapOwner.SQL_BOT,
                    f"/queryRequirements/filters/items/{index}/fieldId",
                    f"filter 引用了不存在的 fieldId：{item.field_id}。",
                )
            )
        if (
            isinstance(item.value, ParameterFilterValueV2)
            and item.value.parameter_name not in parameter_names
        ):
            issues.append(
                _issue(
                    "PARAMETER_REFERENCE_UNKNOWN",
                    BindingGapOwner.SQL_BOT,
                    f"/queryRequirements/filters/items/{index}/value/parameterName",
                    f"filter 引用了未声明参数：{item.value.parameter_name}。",
                )
            )

    aggregation_refs = query.aggregation.input_field_ids + query.aggregation.group_by_field_ids
    unknown_aggregation_fields = sorted(set(aggregation_refs) - field_ids)
    if unknown_aggregation_fields:
        issues.append(
            _issue(
                "FIELD_REFERENCE_UNKNOWN",
                BindingGapOwner.SQL_BOT,
                "/queryRequirements/aggregation",
                f"aggregation 引用了不存在的 fieldId：{unknown_aggregation_fields}。",
            )
        )

    if query.time_range.time_field_id is not None:
        if query.time_range.time_field_id not in field_ids:
            issues.append(
                _issue(
                    "FIELD_REFERENCE_UNKNOWN",
                    BindingGapOwner.SQL_BOT,
                    "/queryRequirements/timeRange/timeFieldId",
                    f"timeRange 引用了不存在的 fieldId：{query.time_range.time_field_id}。",
                )
            )
    for boundary_name, boundary in (
        ("start", query.time_range.start),
        ("end", query.time_range.end),
    ):
        if (
            boundary is not None
            and boundary.kind == "parameter"
            and boundary.parameter_name not in parameter_names
        ):
            issues.append(
                _issue(
                    "PARAMETER_REFERENCE_UNKNOWN",
                    BindingGapOwner.SQL_BOT,
                    f"/queryRequirements/timeRange/{boundary_name}/parameterName",
                    f"timeRange 引用了未声明参数：{boundary.parameter_name}。",
                )
            )
    return issues


def _required_blocking_uncertainties(
    request: FactBindingRequestV2,
) -> dict[str, list[str]]:
    query = request.query_requirements
    required: dict[str, list[str]] = {}

    def require(code: str, field_path: str) -> None:
        required.setdefault(code, []).append(field_path)

    if query.entity.key_resolution_status is ResolutionStatusV2.UNRESOLVED:
        require("ENTITY_KEY_UNRESOLVED", "/queryRequirements/entity/keyParameters")

    for index, field in enumerate(query.fields):
        if not field.required or field.resolution_status is not ResolutionStatusV2.UNRESOLVED:
            continue
        field_path = f"/queryRequirements/fields/{index}/sourceCandidate"
        if field.role is FieldRoleV2.VALUE:
            require("VALUE_FIELD_UNRESOLVED", field_path)
        elif field.role in {FieldRoleV2.FILTER, FieldRoleV2.ENTITY_KEY}:
            require("FILTER_FIELD_UNRESOLVED", field_path)
        elif field.role is FieldRoleV2.GROUP_BY:
            require("AGGREGATION_UNRESOLVED", field_path)
        elif field.role is FieldRoleV2.TIME:
            require("TIME_RANGE_UNRESOLVED", field_path)

    for index, item in enumerate(query.filters.items):
        if item.resolution_status is ResolutionStatusV2.UNRESOLVED:
            require(
                "FILTER_FIELD_UNRESOLVED",
                f"/queryRequirements/filters/items/{index}",
            )
    if query.filters.completeness == "unresolved":
        require("FILTER_SET_INCOMPLETE", "/queryRequirements/filters/completeness")
    if (
        query.aggregation.mode is AggregationModeV2.UNRESOLVED
        or query.aggregation.resolution_status is ResolutionStatusV2.UNRESOLVED
    ):
        require("AGGREGATION_UNRESOLVED", "/queryRequirements/aggregation")
    if (
        query.time_range.mode is TimeRangeModeV2.UNRESOLVED
        or query.time_range.resolution_status is ResolutionStatusV2.UNRESOLVED
    ):
        require("TIME_RANGE_UNRESOLVED", "/queryRequirements/timeRange")
    return required


def _missing_uncertainty_issues(
    request: FactBindingRequestV2,
) -> list[BindingGapIssue]:
    provided_blocking_codes = {
        uncertainty.code
        for uncertainty in request.uncertainties
        if uncertainty.impact is UncertaintyImpactV2.BLOCKING
    }
    issues: list[BindingGapIssue] = []
    for expected_code, paths in _required_blocking_uncertainties(request).items():
        if expected_code in provided_blocking_codes:
            continue
        issues.append(
            _issue(
                "BLOCKING_UNCERTAINTY_MISSING",
                _EXPECTED_UNCERTAINTY_OWNERS[expected_code],
                paths[0],
                f"未决 requirement 缺少 impact=blocking 的 {expected_code} uncertainty；"
                f"受影响路径：{sorted(paths)}。",
            )
        )
    return issues


def _candidate_evidence_warnings(
    request: FactBindingRequestV2,
) -> list[BindingGapIssue]:
    warnings = [
        _issue(
            "MAPPING_CANDIDATE_NOT_AUTHORIZATION",
            BindingGapOwner.METADATA_REVIEW,
            "/mappingCandidate",
            "mappingCandidate 只是上游候选证据，不授予物理表列权限。",
        ),
        _issue(
            "PARSER_PROVENANCE_NOT_AUTHORIZATION",
            BindingGapOwner.SQL_BOT,
            "/provenance/parser",
            "Prompt、provider 和 model 声明只用于追溯，不授予物理表列权限。",
        ),
    ]
    for index, field in enumerate(request.query_requirements.fields):
        if field.source_candidate is None:
            continue
        warnings.append(
            _issue(
                "SOURCE_CANDIDATE_NOT_AUTHORIZATION",
                BindingGapOwner.METADATA_REVIEW,
                f"/queryRequirements/fields/{index}/sourceCandidate",
                "sourceCandidate 必须经过项目上下文、元数据快照和物理表列授权复核。",
                evidence_ids=field.evidence_ids,
            )
        )
    return warnings


def _sort_issues(issues: Iterable[BindingGapIssue]) -> tuple[BindingGapIssue, ...]:
    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                issue.code,
                issue.field_path,
                issue.uncertainty_id or "",
                issue.owner.value,
                issue.evidence_ids,
            ),
        )
    )


def analyze_binding_gaps_v2(request: FactBindingRequestV2) -> BindingGapReport:
    """Return a deterministic, non-executable report without resolving any gap."""

    blocking_issues = _integrity_issues(request)
    blocking_issues.extend(_missing_uncertainty_issues(request))
    warnings = _candidate_evidence_warnings(request)

    for uncertainty in request.uncertainties:
        target = blocking_issues if uncertainty.impact is UncertaintyImpactV2.BLOCKING else warnings
        target.append(_issue_from_uncertainty(uncertainty))

    sorted_blocking = _sort_issues(blocking_issues)
    sorted_warnings = _sort_issues(warnings)
    return BindingGapReport(
        status="blocked" if sorted_blocking else "readyForMetadataResolution",
        executable=False,
        request_id=request.request_id,
        hashes=BindingGapHashes(
            request_sha256=sha256(request.request_id.encode("utf-8")).hexdigest(),
            payload_sha256=canonical_sha256(request),
            rule_sha256=canonical_sha256(request.rule_ref),
            source_sha256=request.provenance.source.sha256,
        ),
        blocking_issues=sorted_blocking,
        warnings=sorted_warnings,
    )
