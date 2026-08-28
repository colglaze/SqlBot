"""Deterministic offline authorization resolution for V2 fact bindings."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from hashlib import sha256
from typing import TypeVar

from release_sql_bot.application.binding_intake_v2 import analyze_binding_gaps_v2
from release_sql_bot.application.canonical import canonical_content_sha256, canonical_sha256
from release_sql_bot.domain.fact_bindings_v2 import (
    AggregationModeV2,
    BindingGapOwner,
    FactDataTypeV2,
    FieldRequirementV2,
    FieldRoleV2,
    UncertaintyImpactV2,
)
from release_sql_bot.domain.project_bindings_v2 import (
    AuthorizedJoinV2,
    BindingResolutionHashes,
    BindingResolutionIssue,
    BindingResolutionReportV2,
    CandidateEvidenceDispositionV2,
    ColumnGrantV2,
    ContextRefV2,
    ContextStatusV2,
    GovernedColumnV2,
    GovernedMetadataSnapshotV2,
    GovernedRelationV2,
    IdentifierCaseSensitivityV2,
    MetadataSnapshotRefV2,
    PhysicalColumnRefV2,
    ProjectBindingContextV2,
    RelationGrantV2,
    ResolvedEntityKeyV2,
    ResolvedFieldBindingV2,
    ResolvedResultSourceV2,
    ResolveMetadataRequestV2,
    SnapshotStatusV2,
)

_T = TypeVar("_T")
_RelationKey = tuple[str, str]
_ColumnKey = tuple[str, str, str]
_RelationshipKey = tuple[_ColumnKey, _ColumnKey]

_GATE_ORDER: dict[str, int] = {
    "REQUEST_VERSION_UNSUPPORTED": 10,
    "CONTEXT_VERSION_UNSUPPORTED": 10,
    "SNAPSHOT_VERSION_UNSUPPORTED": 10,
    "REQUEST_REF_MISMATCH": 20,
    "GAP_REPORT_MISMATCH": 20,
    "DIALECT_MISMATCH": 20,
    "CONTEXT_HASH_MISMATCH": 30,
    "SNAPSHOT_HASH_MISMATCH": 30,
    "CONTEXT_SCOPE_MISMATCH": 40,
    "SNAPSHOT_REF_MISMATCH": 40,
    "CONTEXT_NOT_APPROVED": 50,
    "SNAPSHOT_NOT_APPROVED": 50,
    "UPSTREAM_BINDING_BLOCKED": 60,
    "SNAPSHOT_IDENTIFIER_AMBIGUOUS": 70,
    "RELATION_NOT_IN_SNAPSHOT": 80,
    "COLUMN_NOT_IN_SNAPSHOT": 80,
    "RELATION_NOT_GRANTED": 90,
    "COLUMN_NOT_GRANTED": 90,
    "FIELD_AUTHORIZATION_MISSING": 100,
    "FIELD_AUTHORIZATION_AMBIGUOUS": 100,
    "FIELD_ROLE_MISMATCH": 100,
    "ENTITY_KEY_AUTHORIZATION_MISSING": 110,
    "SQL_TYPE_INCOMPATIBLE": 120,
    "RESULT_SOURCE_UNRESOLVED": 130,
    "JOIN_PATH_UNRESOLVED": 140,
    "JOIN_PATH_NOT_GRANTED": 140,
    "CANDIDATE_EVIDENCE_ONLY": 150,
    "CANDIDATE_EVIDENCE_CONFLICT": 150,
}

_SQL_TYPE_BASE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)(?:\s*\([^()]+\))?$")
_LOGICAL_SQL_TYPES: dict[FactDataTypeV2, frozenset[str]] = {
    FactDataTypeV2.STRING: frozenset(
        {"char", "varchar", "nchar", "nvarchar", "text", "ntext", "sysname", "uniqueidentifier"}
    ),
    FactDataTypeV2.INTEGER: frozenset({"tinyint", "smallint", "int", "bigint"}),
    FactDataTypeV2.NUMBER: frozenset({"decimal", "numeric", "float", "real"}),
    FactDataTypeV2.BOOLEAN: frozenset({"bit"}),
    FactDataTypeV2.DATE: frozenset({"date"}),
    FactDataTypeV2.DATETIME: frozenset(
        {"datetime", "datetime2", "smalldatetime", "datetimeoffset"}
    ),
    FactDataTypeV2.ENUM: frozenset({"char", "varchar", "nchar", "nvarchar"}),
    FactDataTypeV2.MONEY: frozenset({"money", "smallmoney", "decimal", "numeric"}),
    FactDataTypeV2.LIST: frozenset(),
    FactDataTypeV2.UNKNOWN: frozenset(),
}


def _issue(
    code: str,
    owner: BindingGapOwner,
    field_path: str,
    message: str,
    *,
    normalized_identifier: str | None = None,
    evidence_ids: Iterable[str] = (),
    uncertainty_id: str | None = None,
) -> BindingResolutionIssue:
    return BindingResolutionIssue(
        code=code,
        owner=owner,
        field_path=field_path,
        message=message,
        normalized_identifier=normalized_identifier,
        evidence_ids=tuple(evidence_ids),
        uncertainty_id=uncertainty_id,
    )


def _sort_issues(
    issues: Iterable[BindingResolutionIssue],
) -> tuple[BindingResolutionIssue, ...]:
    unique: dict[tuple[object, ...], BindingResolutionIssue] = {}
    for issue in issues:
        key = (
            issue.code,
            issue.owner.value,
            issue.field_path,
            issue.normalized_identifier,
            issue.evidence_ids,
            issue.uncertainty_id,
            issue.message,
        )
        unique[key] = issue
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                _GATE_ORDER.get(item.code, 1_000),
                item.code,
                item.field_path,
                item.normalized_identifier or "",
                item.owner.value,
                item.evidence_ids,
            ),
        )
    )


def _normalizer(
    snapshot: GovernedMetadataSnapshotV2,
) -> Callable[[str], str]:
    if snapshot.identifier_case_sensitivity is IdentifierCaseSensitivityV2.INSENSITIVE:
        return str.casefold
    return lambda value: value


def _physical_ref(schema: str, relation: str, column: str) -> PhysicalColumnRefV2:
    return PhysicalColumnRefV2.model_validate(
        {
            "schemaName": schema,
            "relationName": relation,
            "columnName": column,
        }
    )


def _physical_key(
    value: PhysicalColumnRefV2,
    normalize: Callable[[str], str],
) -> _ColumnKey:
    return (
        normalize(value.schema_name),
        normalize(value.relation_name),
        normalize(value.column_name),
    )


def _relationship_key(left: _ColumnKey, right: _ColumnKey) -> _RelationshipKey:
    return (left, right) if left <= right else (right, left)


def _one_or_none(values: list[_T]) -> _T | None:
    return values[0] if len(values) == 1 else None


@dataclass(frozen=True, slots=True)
class _MetadataIndex:
    normalize: Callable[[str], str]
    relations: dict[_RelationKey, list[GovernedRelationV2]]
    columns: dict[_ColumnKey, list[GovernedColumnV2]]
    relationships: dict[_RelationshipKey, list[str]]


@dataclass(frozen=True, slots=True)
class _GrantIndex:
    relation_grants: dict[str, RelationGrantV2]
    column_grants: dict[str, ColumnGrantV2]
    physical_by_column_grant: dict[str, PhysicalColumnRefV2]


def _metadata_index(
    snapshot: GovernedMetadataSnapshotV2,
) -> tuple[_MetadataIndex, list[BindingResolutionIssue]]:
    normalize = _normalizer(snapshot)
    relations: dict[_RelationKey, list[GovernedRelationV2]] = defaultdict(list)
    columns: dict[_ColumnKey, list[GovernedColumnV2]] = defaultdict(list)
    relationships: dict[_RelationshipKey, list[str]] = defaultdict(list)
    issues: list[BindingResolutionIssue] = []

    for relation in snapshot.relations:
        relation_key = (normalize(relation.schema_name), normalize(relation.relation_name))
        relations[relation_key].append(relation)
        for column in relation.columns:
            column_key = (*relation_key, normalize(column.column_name))
            columns[column_key].append(column)

    for key, values in relations.items():
        if len(values) > 1:
            issues.append(
                _issue(
                    "SNAPSHOT_IDENTIFIER_AMBIGUOUS",
                    BindingGapOwner.METADATA_REVIEW,
                    "/metadataSnapshot/relations",
                    "元数据快照包含按声明大小写策略无法区分的重复 relation。",
                    normalized_identifier=".".join(key),
                )
            )
    for key, values in columns.items():
        if len(values) > 1:
            issues.append(
                _issue(
                    "SNAPSHOT_IDENTIFIER_AMBIGUOUS",
                    BindingGapOwner.METADATA_REVIEW,
                    "/metadataSnapshot/relations",
                    "元数据快照包含按声明大小写策略无法区分的重复 column。",
                    normalized_identifier=".".join(key),
                )
            )

    for index, relationship in enumerate(snapshot.relationships):
        left = _physical_key(relationship.left_column, normalize)
        right = _physical_key(relationship.right_column, normalize)
        for side, key in (("leftColumn", left), ("rightColumn", right)):
            if len(columns.get(key, [])) != 1:
                issues.append(
                    _issue(
                        "COLUMN_NOT_IN_SNAPSHOT",
                        BindingGapOwner.METADATA_REVIEW,
                        f"/metadataSnapshot/relationships/{index}/{side}",
                        "relationship 端点没有命中快照中的唯一物理列。",
                        normalized_identifier=".".join(key),
                    )
                )
        relationships[_relationship_key(left, right)].append(relationship.relationship_id)

    for key, values in relationships.items():
        if len(values) > 1:
            issues.append(
                _issue(
                    "SNAPSHOT_IDENTIFIER_AMBIGUOUS",
                    BindingGapOwner.METADATA_REVIEW,
                    "/metadataSnapshot/relationships",
                    "元数据快照包含重复的 relationship edge。",
                    normalized_identifier=f"{'.'.join(key[0])}<->{'.'.join(key[1])}",
                )
            )
    return _MetadataIndex(normalize, dict(relations), dict(columns), dict(relationships)), issues


def _grant_index(
    context: ProjectBindingContextV2,
    metadata: _MetadataIndex,
    payload: ResolveMetadataRequestV2,
) -> tuple[_GrantIndex, list[BindingResolutionIssue]]:
    relation_grants = {grant.grant_id: grant for grant in context.relation_grants}
    column_grants = {grant.grant_id: grant for grant in context.column_grants}
    physical_by_column_grant: dict[str, PhysicalColumnRefV2] = {}
    issues: list[BindingResolutionIssue] = []
    relation_keys_by_grant: dict[str, _RelationKey] = {}

    for index, grant in enumerate(context.relation_grants):
        key = (metadata.normalize(grant.schema_name), metadata.normalize(grant.relation_name))
        relation_keys_by_grant[grant.grant_id] = key
        matches = metadata.relations.get(key, [])
        if len(matches) != 1:
            issues.append(
                _issue(
                    "RELATION_NOT_IN_SNAPSHOT",
                    BindingGapOwner.METADATA_REVIEW,
                    f"/projectContext/relationGrants/{index}",
                    "relation grant 没有命中快照中的唯一 relation。",
                    normalized_identifier=".".join(key),
                )
            )

    for index, grant in enumerate(context.column_grants):
        relation_key = relation_keys_by_grant.get(grant.relation_grant_id)
        if relation_key is None:
            issues.append(
                _issue(
                    "RELATION_NOT_GRANTED",
                    BindingGapOwner.METADATA_REVIEW,
                    f"/projectContext/columnGrants/{index}/relationGrantId",
                    "column grant 引用了不存在的 relation grant。",
                    normalized_identifier=grant.relation_grant_id,
                )
            )
            continue
        column_key = (*relation_key, metadata.normalize(grant.column_name))
        matches = metadata.columns.get(column_key, [])
        relation_match = metadata.relations.get(relation_key, [])
        if len(relation_match) != 1:
            continue
        if len(matches) != 1:
            issues.append(
                _issue(
                    "COLUMN_NOT_IN_SNAPSHOT",
                    BindingGapOwner.METADATA_REVIEW,
                    f"/projectContext/columnGrants/{index}/columnName",
                    "column grant 没有命中快照中的唯一 column。",
                    normalized_identifier=".".join(column_key),
                )
            )
            continue
        approved_relation = relation_match[0]
        approved_column = matches[0]
        physical_by_column_grant[grant.grant_id] = _physical_ref(
            approved_relation.schema_name,
            approved_relation.relation_name,
            approved_column.column_name,
        )

    for collection_name, authorizations in (
        ("fieldBindingAuthorizations", context.field_binding_authorizations),
        ("entityKeyAuthorizations", context.entity_key_authorizations),
    ):
        for index, authorization in enumerate(authorizations):
            if authorization.column_grant_id not in column_grants:
                issues.append(
                    _issue(
                        "COLUMN_NOT_GRANTED",
                        BindingGapOwner.METADATA_REVIEW,
                        f"/projectContext/{collection_name}/{index}/columnGrantId",
                        "authorization 引用了不存在的 column grant。",
                        normalized_identifier=authorization.column_grant_id,
                    )
                )
            if authorization.request_id not in context.request_ids:
                issues.append(
                    _issue(
                        "CONTEXT_SCOPE_MISMATCH",
                        BindingGapOwner.SQL_BOT,
                        f"/projectContext/{collection_name}/{index}/requestId",
                        "authorization 的 requestId 不在上下文批准范围内。",
                        normalized_identifier=authorization.request_id,
                    )
                )
            if authorization.request_id == payload.binding_request.request_id:
                field_ids = {
                    field.field_id for field in payload.binding_request.query_requirements.fields
                }
                if authorization.field_id not in field_ids:
                    issues.append(
                        _issue(
                            "CONTEXT_SCOPE_MISMATCH",
                            BindingGapOwner.SQL_BOT,
                            f"/projectContext/{collection_name}/{index}/fieldId",
                            "authorization 引用了当前 V2 请求中不存在的 fieldId。",
                            normalized_identifier=authorization.field_id,
                        )
                    )

    for index, grant in enumerate(context.join_grants):
        for side, grant_id in (
            ("leftColumnGrantId", grant.left_column_grant_id),
            ("rightColumnGrantId", grant.right_column_grant_id),
        ):
            if grant_id not in column_grants:
                issues.append(
                    _issue(
                        "COLUMN_NOT_GRANTED",
                        BindingGapOwner.METADATA_REVIEW,
                        f"/projectContext/joinGrants/{index}/{side}",
                        "join grant 引用了不存在的 column grant。",
                        normalized_identifier=grant_id,
                    )
                )
    return (
        _GrantIndex(relation_grants, column_grants, physical_by_column_grant),
        issues,
    )


def _preflight_issues(
    payload: ResolveMetadataRequestV2,
    *,
    actual_context_hash: str,
    actual_snapshot_hash: str,
) -> list[BindingResolutionIssue]:
    request = payload.binding_request
    supplied_gap = payload.binding_gap_report
    context = payload.project_context
    snapshot = payload.metadata_snapshot
    recomputed_gap = analyze_binding_gaps_v2(request)
    issues: list[BindingResolutionIssue] = []

    if payload.schema_version != "1.0.0":
        issues.append(
            _issue(
                "REQUEST_VERSION_UNSUPPORTED",
                BindingGapOwner.SQL_BOT,
                "/schemaVersion",
                "ResolveMetadataRequestV2 schemaVersion 必须为 1.0.0。",
            )
        )
    if context.schema_version != "1.0.0":
        issues.append(
            _issue(
                "CONTEXT_VERSION_UNSUPPORTED",
                BindingGapOwner.SQL_BOT,
                "/projectContext/schemaVersion",
                "ProjectBindingContextV2 schemaVersion 必须为 1.0.0。",
            )
        )
    if snapshot.schema_version != "1.0.0":
        issues.append(
            _issue(
                "SNAPSHOT_VERSION_UNSUPPORTED",
                BindingGapOwner.SQL_BOT,
                "/metadataSnapshot/schemaVersion",
                "GovernedMetadataSnapshot schemaVersion 必须为 1.0.0。",
            )
        )
    if supplied_gap.request_id != request.request_id:
        issues.append(
            _issue(
                "REQUEST_REF_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/bindingGapReport/requestId",
                "Phase 2F report 与 binding request 的 requestId 不一致。",
            )
        )
    if canonical_sha256(supplied_gap) != canonical_sha256(recomputed_gap):
        issues.append(
            _issue(
                "GAP_REPORT_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/bindingGapReport",
                "Phase 2F report 与当前 V2 请求的确定性重算结果不一致。",
            )
        )
    if payload.project_ref != context.project_ref:
        issues.append(
            _issue(
                "CONTEXT_SCOPE_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/projectContext/projectRef",
                "项目上下文没有精确覆盖请求中的 projectRef。",
            )
        )
    if context.rule_ref != request.rule_ref:
        issues.append(
            _issue(
                "CONTEXT_SCOPE_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/projectContext/ruleRef",
                "项目上下文没有精确覆盖 V2 ruleRef。",
            )
        )
    if request.request_id not in context.request_ids:
        issues.append(
            _issue(
                "CONTEXT_SCOPE_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/projectContext/requestIds",
                "项目上下文没有显式批准当前 requestId。",
            )
        )
    if context.approval_ref.policy_version != context.authorization_policy_version:
        issues.append(
            _issue(
                "CONTEXT_SCOPE_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/projectContext/approvalRef/policyVersion",
                "上下文批准策略版本与 authorizationPolicyVersion 不一致。",
            )
        )
    expected_snapshot_ref = (
        snapshot.snapshot_id,
        snapshot.snapshot_version,
        snapshot.content_sha256,
    )
    actual_snapshot_ref = (
        context.metadata_snapshot_ref.snapshot_id,
        context.metadata_snapshot_ref.snapshot_version,
        context.metadata_snapshot_ref.sha256,
    )
    if actual_snapshot_ref != expected_snapshot_ref:
        issues.append(
            _issue(
                "SNAPSHOT_REF_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/projectContext/metadataSnapshotRef",
                "上下文引用的快照 ID、版本或声明哈希与请求快照不一致。",
            )
        )
    if request.target_dialect != "sqlserver" or snapshot.dialect != "sqlserver":
        issues.append(
            _issue(
                "DIALECT_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/metadataSnapshot/dialect",
                "V2 请求与快照必须精确使用 sqlserver dialect。",
            )
        )
    if context.content_sha256 != actual_context_hash:
        issues.append(
            _issue(
                "CONTEXT_HASH_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/projectContext/contentSha256",
                "项目上下文 canonical 内容哈希不匹配。",
            )
        )
    if snapshot.content_sha256 != actual_snapshot_hash:
        issues.append(
            _issue(
                "SNAPSHOT_HASH_MISMATCH",
                BindingGapOwner.SQL_BOT,
                "/metadataSnapshot/contentSha256",
                "元数据快照 canonical 内容哈希不匹配。",
            )
        )
    if context.status is not ContextStatusV2.APPROVED:
        issues.append(
            _issue(
                "CONTEXT_NOT_APPROVED",
                BindingGapOwner.METADATA_REVIEW,
                "/projectContext/status",
                "resolver 只消费 approved 项目上下文。",
            )
        )
    if snapshot.status is not SnapshotStatusV2.APPROVED:
        issues.append(
            _issue(
                "SNAPSHOT_NOT_APPROVED",
                BindingGapOwner.METADATA_REVIEW,
                "/metadataSnapshot/status",
                "resolver 只消费 approved 元数据快照。",
            )
        )
    return issues


def _sql_type_is_compatible(logical_type: FactDataTypeV2, sql_type: str) -> bool:
    match = _SQL_TYPE_BASE.fullmatch(sql_type)
    if match is None:
        return False
    return match.group(1).casefold() in _LOGICAL_SQL_TYPES[logical_type]


def _mandatory_fields(payload: ResolveMetadataRequestV2) -> dict[str, FieldRequirementV2]:
    query = payload.binding_request.query_requirements
    registry = {field.field_id: field for field in query.fields}
    required_ids = {field.field_id for field in query.fields if field.required}
    required_ids.update(item.field_id for item in query.filters.items)
    required_ids.update(query.aggregation.input_field_ids)
    required_ids.update(query.aggregation.group_by_field_ids)
    if query.time_range.time_field_id is not None:
        required_ids.add(query.time_range.time_field_id)
    required_ids.update(
        authorization.field_id
        for authorization in payload.project_context.entity_key_authorizations
        if authorization.request_id == payload.binding_request.request_id
        and authorization.parameter_name in query.entity.key_parameters
    )
    return {
        field_id: registry[field_id] for field_id in sorted(required_ids) if field_id in registry
    }


def _resolve_fields(
    payload: ResolveMetadataRequestV2,
    grants: _GrantIndex,
    metadata: _MetadataIndex,
) -> tuple[list[ResolvedFieldBindingV2], list[BindingResolutionIssue]]:
    request = payload.binding_request
    context = payload.project_context
    fields = _mandatory_fields(payload)
    issues: list[BindingResolutionIssue] = []
    resolved: list[ResolvedFieldBindingV2] = []

    grouped: dict[tuple[str, str, FieldRoleV2], list[object]] = defaultdict(list)
    for authorization in context.field_binding_authorizations:
        grouped[(authorization.request_id, authorization.field_id, authorization.role)].append(
            authorization
        )

    for field_id, field in fields.items():
        key = (request.request_id, field_id, field.role)
        matches = grouped.get(key, [])
        path = f"/projectContext/fieldBindingAuthorizations/{field_id}"
        if not matches:
            same_field = [
                item
                for item in context.field_binding_authorizations
                if item.request_id == request.request_id and item.field_id == field_id
            ]
            if same_field:
                issues.append(
                    _issue(
                        "FIELD_ROLE_MISMATCH",
                        BindingGapOwner.METADATA_REVIEW,
                        path,
                        "field authorization 的 role 与 V2 field role 不一致。",
                        normalized_identifier=field_id,
                        evidence_ids=field.evidence_ids,
                    )
                )
            else:
                issues.append(
                    _issue(
                        "FIELD_AUTHORIZATION_MISSING",
                        BindingGapOwner.METADATA_REVIEW,
                        path,
                        "必需逻辑字段缺少精确 field authorization。",
                        normalized_identifier=field_id,
                        evidence_ids=field.evidence_ids,
                    )
                )
            continue
        if len(matches) > 1:
            issues.append(
                _issue(
                    "FIELD_AUTHORIZATION_AMBIGUOUS",
                    BindingGapOwner.METADATA_REVIEW,
                    path,
                    "必需逻辑字段命中了多个 field authorization。",
                    normalized_identifier=field_id,
                    evidence_ids=field.evidence_ids,
                )
            )
            continue
        authorization = matches[0]
        physical = grants.physical_by_column_grant.get(authorization.column_grant_id)
        if physical is None:
            issues.append(
                _issue(
                    "COLUMN_NOT_GRANTED",
                    BindingGapOwner.METADATA_REVIEW,
                    path,
                    "field authorization 没有命中已授权且存在于快照的 column grant。",
                    normalized_identifier=authorization.column_grant_id,
                    evidence_ids=field.evidence_ids,
                )
            )
            continue
        column_key = _physical_key(physical, metadata.normalize)
        column = _one_or_none(metadata.columns.get(column_key, []))
        if column is None or not _sql_type_is_compatible(field.data_type, column.sql_type):
            issues.append(
                _issue(
                    "SQL_TYPE_INCOMPATIBLE",
                    BindingGapOwner.METADATA_REVIEW,
                    path,
                    "逻辑字段类型与授权物理列的 SQL 类型不兼容，resolver 不会插入转换。",
                    normalized_identifier=".".join(column_key),
                    evidence_ids=field.evidence_ids,
                )
            )
            continue
        resolved.append(
            ResolvedFieldBindingV2(
                field_id=field_id,
                role=field.role,
                physical_column=physical,
                authorization_id=authorization.authorization_id,
                column_grant_id=authorization.column_grant_id,
                evidence_ids=tuple(field.evidence_ids),
            )
        )
    return sorted(resolved, key=lambda item: (item.field_id, item.role.value)), issues


def _resolve_entity_keys(
    payload: ResolveMetadataRequestV2,
    resolved_fields: list[ResolvedFieldBindingV2],
) -> tuple[list[ResolvedEntityKeyV2], list[BindingResolutionIssue]]:
    request = payload.binding_request
    context = payload.project_context
    query = request.query_requirements
    fields = {field.field_id: field for field in query.fields}
    parameters = {parameter.name: parameter for parameter in request.fact.parameters}
    resolved_by_id = {item.field_id: item for item in resolved_fields}
    issues: list[BindingResolutionIssue] = []
    resolved: list[ResolvedEntityKeyV2] = []

    for parameter_name in sorted(query.entity.key_parameters):
        path = f"/projectContext/entityKeyAuthorizations/{parameter_name}"
        matches = [
            item
            for item in context.entity_key_authorizations
            if item.request_id == request.request_id and item.parameter_name == parameter_name
        ]
        if len(matches) != 1:
            issues.append(
                _issue(
                    "ENTITY_KEY_AUTHORIZATION_MISSING",
                    BindingGapOwner.METADATA_REVIEW,
                    path,
                    "每个 entity key parameter 必须命中唯一的 entity-key authorization。",
                    normalized_identifier=parameter_name,
                )
            )
            continue
        authorization = matches[0]
        field = fields.get(authorization.field_id)
        field_binding = resolved_by_id.get(authorization.field_id)
        if field is None or field.role is not FieldRoleV2.ENTITY_KEY:
            issues.append(
                _issue(
                    "FIELD_ROLE_MISMATCH",
                    BindingGapOwner.METADATA_REVIEW,
                    path,
                    "entity-key authorization 必须引用 role=entityKey 的 V2 field。",
                    normalized_identifier=authorization.field_id,
                )
            )
            continue
        if field_binding is None or field_binding.column_grant_id != authorization.column_grant_id:
            issues.append(
                _issue(
                    "ENTITY_KEY_AUTHORIZATION_MISSING",
                    BindingGapOwner.METADATA_REVIEW,
                    path,
                    "entity-key authorization 与字段物理列授权不一致。",
                    normalized_identifier=authorization.field_id,
                )
            )
            continue
        parameter = parameters.get(parameter_name)
        if parameter is None or parameter.data_type is not field.data_type:
            issues.append(
                _issue(
                    "SQL_TYPE_INCOMPATIBLE",
                    BindingGapOwner.METADATA_REVIEW,
                    path,
                    "entity key parameter 类型与 entityKey field 类型不一致。",
                    normalized_identifier=parameter_name,
                )
            )
            continue
        resolved.append(
            ResolvedEntityKeyV2(
                parameter_name=parameter_name,
                field_id=authorization.field_id,
                physical_column=field_binding.physical_column,
                authorization_id=authorization.authorization_id,
            )
        )
    return resolved, issues


def _resolve_result_source(
    payload: ResolveMetadataRequestV2,
    resolved_fields: list[ResolvedFieldBindingV2],
) -> tuple[ResolvedResultSourceV2 | None, list[BindingResolutionIssue]]:
    query = payload.binding_request.query_requirements
    resolved_by_id = {item.field_id: item for item in resolved_fields}
    issues: list[BindingResolutionIssue] = []

    if query.aggregation.mode is AggregationModeV2.COMPUTE:
        field_ids = tuple(query.aggregation.input_field_ids)
        bindings = [resolved_by_id.get(field_id) for field_id in field_ids]
        if not field_ids or any(item is None for item in bindings):
            issues.append(
                _issue(
                    "RESULT_SOURCE_UNRESOLVED",
                    BindingGapOwner.BUSINESS_RULE_REVIEW,
                    "/bindingRequest/queryRequirements/aggregation/inputFieldIds",
                    "computed aggregation 的全部输入字段尚未形成唯一授权绑定。",
                )
            )
            return None, issues
        return (
            ResolvedResultSourceV2(
                mode="aggregation",
                field_ids=field_ids,
                physical_columns=tuple(item.physical_column for item in bindings if item),
            ),
            issues,
        )
    if query.aggregation.mode is AggregationModeV2.EXISTS:
        return ResolvedResultSourceV2(mode="exists", field_ids=(), physical_columns=()), issues
    if query.aggregation.mode in {AggregationModeV2.NONE, AggregationModeV2.PRECOMPUTED}:
        values = [
            item
            for item in resolved_fields
            if item.role is FieldRoleV2.VALUE
            and next(field.required for field in query.fields if field.field_id == item.field_id)
        ]
        if len(values) != 1:
            issues.append(
                _issue(
                    "RESULT_SOURCE_UNRESOLVED",
                    BindingGapOwner.BUSINESS_RULE_REVIEW,
                    "/bindingRequest/queryRequirements/result",
                    "column/precomputed 结果必须命中唯一的 required role=value field。",
                )
            )
            return None, issues
        value = values[0]
        return (
            ResolvedResultSourceV2(
                mode="column",
                field_ids=(value.field_id,),
                physical_columns=(value.physical_column,),
            ),
            issues,
        )
    issues.append(
        _issue(
            "RESULT_SOURCE_UNRESOLVED",
            BindingGapOwner.BUSINESS_RULE_REVIEW,
            "/bindingRequest/queryRequirements/aggregation",
            "未决 aggregation mode 不能形成物理结果来源。",
        )
    )
    return None, issues


def _authorized_join_candidates(
    payload: ResolveMetadataRequestV2,
    metadata: _MetadataIndex,
    grants: _GrantIndex,
) -> tuple[list[AuthorizedJoinV2], list[BindingResolutionIssue]]:
    authorized: list[AuthorizedJoinV2] = []
    issues: list[BindingResolutionIssue] = []
    for index, grant in enumerate(payload.project_context.join_grants):
        left = grants.physical_by_column_grant.get(grant.left_column_grant_id)
        right = grants.physical_by_column_grant.get(grant.right_column_grant_id)
        if left is None or right is None:
            continue
        relationship = _relationship_key(
            _physical_key(left, metadata.normalize),
            _physical_key(right, metadata.normalize),
        )
        matches = metadata.relationships.get(relationship, [])
        if len(matches) != 1:
            issues.append(
                _issue(
                    "JOIN_PATH_UNRESOLVED",
                    BindingGapOwner.METADATA_REVIEW,
                    f"/projectContext/joinGrants/{index}",
                    "join grant 没有命中快照中的唯一 relationship edge。",
                    normalized_identifier=grant.grant_id,
                )
            )
            continue
        authorized.append(
            AuthorizedJoinV2(
                grant_id=grant.grant_id,
                left_column=left,
                right_column=right,
                join_type=grant.join_type,
            )
        )
    return authorized, issues


def _select_join_closure(
    metadata: _MetadataIndex,
    resolved_fields: list[ResolvedFieldBindingV2],
    candidates: list[AuthorizedJoinV2],
) -> tuple[list[AuthorizedJoinV2], list[BindingResolutionIssue]]:
    relations = {
        (
            metadata.normalize(item.physical_column.schema_name),
            metadata.normalize(item.physical_column.relation_name),
        )
        for item in resolved_fields
    }
    if len(relations) <= 1:
        return [], []

    usable: list[AuthorizedJoinV2] = []
    for item in candidates:
        left_relation = (
            metadata.normalize(item.left_column.schema_name),
            metadata.normalize(item.left_column.relation_name),
        )
        right_relation = (
            metadata.normalize(item.right_column.schema_name),
            metadata.normalize(item.right_column.relation_name),
        )
        if (
            left_relation in relations
            and right_relation in relations
            and left_relation != right_relation
        ):
            usable.append(item)

    adjacency: dict[_RelationKey, set[_RelationKey]] = {relation: set() for relation in relations}
    for item in usable:
        left = (
            metadata.normalize(item.left_column.schema_name),
            metadata.normalize(item.left_column.relation_name),
        )
        right = (
            metadata.normalize(item.right_column.schema_name),
            metadata.normalize(item.right_column.relation_name),
        )
        adjacency[left].add(right)
        adjacency[right].add(left)

    seen: set[_RelationKey] = set()
    pending = [min(relations)]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(sorted(adjacency[current] - seen))

    path = "/projectContext/joinGrants"
    if seen != relations:
        snapshot_relations = {
            frozenset((edge[0][:2], edge[1][:2])) for edge in metadata.relationships
        }
        unresolved_pairs = {
            frozenset((left, right))
            for left in relations
            for right in relations
            if left < right and frozenset((left, right)) in snapshot_relations
        }
        code = "JOIN_PATH_NOT_GRANTED" if unresolved_pairs else "JOIN_PATH_UNRESOLVED"
        message = (
            "快照存在关系边，但项目上下文没有形成唯一批准的 join 闭包。"
            if code == "JOIN_PATH_NOT_GRANTED"
            else "快照与上下文无法连接全部已解析 relation。"
        )
        return [], [_issue(code, BindingGapOwner.METADATA_REVIEW, path, message)]
    if len(usable) != len(relations) - 1:
        return [], [
            _issue(
                "JOIN_PATH_UNRESOLVED",
                BindingGapOwner.METADATA_REVIEW,
                path,
                "多个已批准 join 路径形成歧义，resolver 不会自动选择。",
            )
        ]
    return sorted(usable, key=lambda item: item.grant_id), []


def _parse_candidate_column(
    relation_name: str | None,
    column_name: str | None,
) -> tuple[str, str, str] | None:
    if relation_name is None or column_name is None or relation_name.count(".") != 1:
        return None
    schema, relation = relation_name.split(".", 1)
    components = (schema, relation, column_name)
    if any(
        not item
        or item != item.strip()
        or any(token in item for token in ("*", ".", "#", "@", "[", "]"))
        for item in components
    ):
        return None
    return components


def _candidate_dispositions(
    payload: ResolveMetadataRequestV2,
    metadata: _MetadataIndex,
    resolved_fields: list[ResolvedFieldBindingV2],
) -> tuple[list[CandidateEvidenceDispositionV2], list[BindingResolutionIssue]]:
    resolved = {item.field_id: item.physical_column for item in resolved_fields}
    resolved_values = [
        item.physical_column for item in resolved_fields if item.role is FieldRoleV2.VALUE
    ]
    mapped_result = resolved_values[0] if len(resolved_values) == 1 else None
    candidates: list[tuple[str, str | None, str | None, PhysicalColumnRefV2 | None]] = []
    mapping = payload.binding_request.mapping_candidate
    candidates.append(
        (
            "/bindingRequest/mappingCandidate",
            mapping.view_name,
            mapping.view_field,
            mapped_result,
        )
    )
    for index, field in enumerate(payload.binding_request.query_requirements.fields):
        candidate = field.source_candidate
        candidates.append(
            (
                f"/bindingRequest/queryRequirements/fields/{index}/sourceCandidate",
                candidate.relation_name if candidate else None,
                candidate.field_name if candidate else None,
                resolved.get(field.field_id),
            )
        )
    candidates.append(("/bindingRequest/provenance/parser", None, None, None))

    dispositions: list[CandidateEvidenceDispositionV2] = []
    warnings: list[BindingResolutionIssue] = []
    for path, relation_name, column_name, authorized in candidates:
        parsed = _parse_candidate_column(relation_name, column_name)
        if parsed is None or authorized is None:
            disposition = "notUsed"
        else:
            candidate_key = tuple(metadata.normalize(item) for item in parsed)
            authorized_key = _physical_key(authorized, metadata.normalize)
            disposition = "consistent" if candidate_key == authorized_key else "conflict"
        dispositions.append(
            CandidateEvidenceDispositionV2(
                evidence_path=path,
                disposition=disposition,
                resolved_column=authorized,
            )
        )
        code = (
            "CANDIDATE_EVIDENCE_CONFLICT"
            if disposition == "conflict"
            else "CANDIDATE_EVIDENCE_ONLY"
        )
        warnings.append(
            _issue(
                code,
                BindingGapOwner.METADATA_REVIEW,
                path,
                "候选证据与明确授权冲突；授权结果保持不变。"
                if disposition == "conflict"
                else "候选、Prompt 和模型来源只作复核证据，不授予物理访问权限。",
            )
        )
    return dispositions, warnings


def _context_ref(context: ProjectBindingContextV2) -> ContextRefV2:
    return ContextRefV2.model_validate(
        {
            "contextId": context.context_id,
            "contextVersion": context.context_version,
            "sha256": context.content_sha256,
        }
    )


def _snapshot_ref(snapshot: GovernedMetadataSnapshotV2) -> MetadataSnapshotRefV2:
    return MetadataSnapshotRefV2.model_validate(
        {
            "snapshotId": snapshot.snapshot_id,
            "snapshotVersion": snapshot.snapshot_version,
            "sha256": snapshot.content_sha256,
        }
    )


def _build_report(
    payload: ResolveMetadataRequestV2,
    *,
    actual_context_hash: str,
    actual_snapshot_hash: str,
    blocking_issues: Iterable[BindingResolutionIssue],
    warnings: Iterable[BindingResolutionIssue],
    resolved_bindings: Iterable[ResolvedFieldBindingV2] = (),
    resolved_entity_keys: Iterable[ResolvedEntityKeyV2] = (),
    result_source: ResolvedResultSourceV2 | None = None,
    authorized_joins: Iterable[AuthorizedJoinV2] = (),
    dispositions: Iterable[CandidateEvidenceDispositionV2] = (),
) -> BindingResolutionReportV2:
    request = payload.binding_request
    issues = _sort_issues(blocking_issues)
    gap = payload.binding_gap_report.model_copy(deep=True)
    return BindingResolutionReportV2(
        status="blocked" if issues else "metadataResolved",
        executable=False,
        request_id=request.request_id,
        project_ref=payload.project_ref.model_copy(deep=True),
        rule_ref=request.rule_ref.model_copy(deep=True),
        context_ref=_context_ref(payload.project_context),
        metadata_snapshot_ref=_snapshot_ref(payload.metadata_snapshot),
        hashes=BindingResolutionHashes(
            request_sha256=sha256(request.request_id.encode("utf-8")).hexdigest(),
            payload_sha256=canonical_sha256(request),
            rule_sha256=canonical_sha256(request.rule_ref),
            source_sha256=request.provenance.source.sha256,
            gap_report_sha256=canonical_sha256(payload.binding_gap_report),
            context_sha256=actual_context_hash,
            snapshot_sha256=actual_snapshot_hash,
        ),
        authorization_policy_version=payload.project_context.authorization_policy_version,
        binding_gap_report=gap,
        uncertainties=tuple(item.model_copy(deep=True) for item in request.uncertainties),
        resolved_bindings=tuple(resolved_bindings),
        resolved_entity_keys=tuple(resolved_entity_keys),
        result_source=result_source,
        authorized_joins=tuple(authorized_joins),
        candidate_evidence_dispositions=tuple(dispositions),
        blocking_issues=issues,
        warnings=_sort_issues(warnings),
    )


def resolve_metadata_v2(payload: ResolveMetadataRequestV2) -> BindingResolutionReportV2:
    """Resolve V2 physical bindings without I/O, model calls, SQL, or mutations."""

    request = payload.binding_request
    supplied_gap = payload.binding_gap_report
    recomputed_gap = analyze_binding_gaps_v2(request)
    actual_context_hash = canonical_content_sha256(payload.project_context)
    actual_snapshot_hash = canonical_content_sha256(payload.metadata_snapshot)
    blocking = _preflight_issues(
        payload,
        actual_context_hash=actual_context_hash,
        actual_snapshot_hash=actual_snapshot_hash,
    )
    warnings = [BindingResolutionIssue.from_gap_issue(item) for item in recomputed_gap.warnings]

    upstream_blocked = (
        recomputed_gap.status == "blocked"
        or supplied_gap.status == "blocked"
        or any(
            uncertainty.impact is UncertaintyImpactV2.BLOCKING
            for uncertainty in request.uncertainties
        )
    )
    if upstream_blocked:
        blocking.append(
            _issue(
                "UPSTREAM_BINDING_BLOCKED",
                BindingGapOwner.BUSINESS_RULE_REVIEW,
                "/bindingGapReport",
                "Phase 2F 或原始 V2 uncertainty 仍为 blocking，物理解析未开始。",
            )
        )
        blocking.extend(
            BindingResolutionIssue.from_gap_issue(item) for item in recomputed_gap.blocking_issues
        )
        return _build_report(
            payload,
            actual_context_hash=actual_context_hash,
            actual_snapshot_hash=actual_snapshot_hash,
            blocking_issues=blocking,
            warnings=warnings,
        )

    if blocking:
        return _build_report(
            payload,
            actual_context_hash=actual_context_hash,
            actual_snapshot_hash=actual_snapshot_hash,
            blocking_issues=blocking,
            warnings=warnings,
        )

    metadata, metadata_issues = _metadata_index(payload.metadata_snapshot)
    grants, grant_issues = _grant_index(payload.project_context, metadata, payload)
    blocking.extend(metadata_issues)
    blocking.extend(grant_issues)
    if blocking:
        return _build_report(
            payload,
            actual_context_hash=actual_context_hash,
            actual_snapshot_hash=actual_snapshot_hash,
            blocking_issues=blocking,
            warnings=warnings,
        )

    resolved_fields, field_issues = _resolve_fields(payload, grants, metadata)
    resolved_entity_keys, entity_issues = _resolve_entity_keys(payload, resolved_fields)
    result_source, result_issues = _resolve_result_source(payload, resolved_fields)
    join_candidates, join_candidate_issues = _authorized_join_candidates(
        payload,
        metadata,
        grants,
    )
    authorized_joins, join_closure_issues = _select_join_closure(
        metadata,
        resolved_fields,
        join_candidates,
    )
    dispositions, candidate_warnings = _candidate_dispositions(
        payload,
        metadata,
        resolved_fields,
    )
    blocking.extend(field_issues)
    blocking.extend(entity_issues)
    blocking.extend(result_issues)
    blocking.extend(join_candidate_issues)
    blocking.extend(join_closure_issues)
    warnings.extend(candidate_warnings)

    return _build_report(
        payload,
        actual_context_hash=actual_context_hash,
        actual_snapshot_hash=actual_snapshot_hash,
        blocking_issues=blocking,
        warnings=warnings,
        resolved_bindings=resolved_fields,
        resolved_entity_keys=resolved_entity_keys,
        result_source=result_source,
        authorized_joins=authorized_joins,
        dispositions=dispositions,
    )
