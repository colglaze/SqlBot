"""Phase 5A orchestration: preflight gates, then restricted describe-only probes.

Every database-adapter call happens only after the complete Phase 2G/Phase 4
closure has been recomputed from the full input, the carried static report has
been proven canonical-equal to the recomputation, the candidate lifecycle is
intact, the profile is safe, and the parameter set matches the candidate
exactly. Any preflight failure keeps the adapter call count at zero.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.application.ports.sql_ast import SqlDialectInspector
from release_sql_bot.application.ports.sql_parameter_binding import (
    BoundSqlV2,
    SqlParameterBinder,
    SqlParameterBindingError,
)
from release_sql_bot.application.ports.sqlserver_validation import (
    CatalogFactsRequest,
    DescribeRequest,
    ObjectRef,
    PermissionAttestationRequest,
    SessionLimits,
    SessionPolicy,
    SqlServerDescribeRejectedError,
    SqlServerProbeUndeterminedError,
    SqlServerTimeoutError,
    SqlServerUnavailableError,
    SqlServerValidationPort,
    TargetAttestationExpectation,
)
from release_sql_bot.application.sql_validation import validate_sql_candidate_v2
from release_sql_bot.domain.fact_bindings_v2 import FactDataTypeV2, ParameterFilterValueV2
from release_sql_bot.domain.project_bindings_v2 import (
    BindingResolutionReportV2,
    GovernedMetadataSnapshotV2,
    PhysicalColumnRefV2,
)
from release_sql_bot.domain.sql_validation import SqlStaticValidationReportV2
from release_sql_bot.domain.sqlserver_validation import (
    RESULT_COLUMN_NAME,
    STAGE_BINDER,
    STAGE_CONFIG,
    STAGE_CONNECTION,
    STAGE_DESCRIBE,
    STAGE_INTERNAL,
    STAGE_PARAMETER,
    STAGE_PERMISSION,
    STAGE_PROFILE,
    STAGE_REFERENCE,
    STAGE_SESSION_POLICY,
    STAGE_SNAPSHOT,
    STAGE_TARGET,
    BoundSqlRefV2,
    DescribeEvidenceV2,
    ParameterEvidenceV2,
    PermissionEvidenceV2,
    RequestedValidationLimitsV2,
    SnapshotDriftEvidenceV2,
    SnapshotDriftItemV2,
    SqlServerValidationIssueV2,
    SqlServerValidationReportV2,
    TargetEvidenceV2,
    ValidationCandidateRefV2,
    described_type_family,
    live_column_compatible,
    parameter_declaration_is_bounded,
    parse_sql_server_type,
    result_family_allowed,
)

_UTC = UTC

_PARAM_TYPE_FAMILIES: dict[FactDataTypeV2, tuple[str, ...]] = {
    FactDataTypeV2.STRING: ("char", "varchar", "nchar", "nvarchar"),
    FactDataTypeV2.ENUM: ("char", "varchar", "nchar", "nvarchar"),
    FactDataTypeV2.INTEGER: ("tinyint", "smallint", "int", "bigint"),
    FactDataTypeV2.NUMBER: ("decimal", "numeric", "float", "real"),
    FactDataTypeV2.MONEY: ("decimal", "numeric", "money", "smallmoney"),
    FactDataTypeV2.BOOLEAN: ("bit",),
    FactDataTypeV2.DATE: ("date",),
    FactDataTypeV2.DATETIME: ("datetime2", "datetime", "smalldatetime"),
}

_OBJECT_TYPE_CODES: dict[str, str] = {"table": "U", "view": "V"}

_BIGINT_MIN = -(2**63)
_BIGINT_MAX = 2**63 - 1
_MAX_STRING_VALUE_LENGTH = 4000


@dataclass(frozen=True, slots=True)
class SqlServerValidationProfile:
    """Resolved, pre-approved validation profile assembled from configuration."""

    profile_id: str
    enabled: bool
    environment_class: str
    allowed_modes: tuple[str, ...]
    connect_timeout_seconds: int
    command_timeout_seconds: int
    lock_timeout_milliseconds: int
    max_describe_rows: int
    max_describe_bytes: int
    supported_major_versions: tuple[int, ...]
    encrypt: bool
    trust_server_certificate: bool
    read_only: bool
    application_intent: str
    host: str
    database: str


class _Stage:
    """Mutable per-run issue collector with a fixed outcome."""

    def __init__(self) -> None:
        self.issues: list[SqlServerValidationIssueV2] = []
        self.warnings: list[SqlServerValidationIssueV2] = []
        self.inconclusive = False

    def add(
        self,
        stage: int,
        code: str,
        field_path: str,
        message: str,
        safe_identifier: str | None = None,
    ) -> None:
        self.issues.append(
            SqlServerValidationIssueV2(
                stage_order=stage,
                code=code,
                field_path=field_path,
                message=message,
                safe_identifier=safe_identifier,
            )
        )

    def warn(
        self,
        stage: int,
        code: str,
        field_path: str,
        message: str,
        safe_identifier: str | None = None,
    ) -> None:
        self.warnings.append(
            SqlServerValidationIssueV2(
                stage_order=stage,
                code=code,
                field_path=field_path,
                message=message,
                safe_identifier=safe_identifier,
            )
        )

    @property
    def blocked(self) -> bool:
        return bool(self.issues)

    def status(self) -> str:
        if not self.issues:
            return "passed"
        return "inconclusive" if self.inconclusive else "blocked"


def _sort_issues(
    issues: list[SqlServerValidationIssueV2],
) -> tuple[SqlServerValidationIssueV2, ...]:
    unique: dict[tuple[str, str, str, str], SqlServerValidationIssueV2] = {}
    for issue in issues:
        key = (
            str(issue.stage_order),
            issue.code,
            issue.field_path,
            issue.safe_identifier or "",
        )
        unique.setdefault(key, issue)
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.stage_order,
                item.code,
                item.field_path,
                item.safe_identifier or "",
            ),
        )
    )


def _utc_text(moment: datetime) -> str:
    return moment.astimezone(_UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _sha256_hex(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _case_key(
    schema_name: str,
    relation_name: str,
    sensitivity: str,
) -> tuple[str, str]:
    if sensitivity == "sensitive":
        return (schema_name, relation_name)
    return (schema_name.casefold(), relation_name.casefold())


def _column_key(
    schema_name: str,
    relation_name: str,
    column_name: str,
    sensitivity: str,
) -> tuple[str, str, str]:
    if sensitivity == "sensitive":
        return (schema_name, relation_name, column_name)
    return (schema_name.casefold(), relation_name.casefold(), column_name.casefold())


# ---------------------------------------------------------------------------
# Preflight helpers (no adapter interaction)
# ---------------------------------------------------------------------------


def _profile_issues(
    stage: _Stage,
    request,
    profile: SqlServerValidationProfile,
) -> bool:
    """Validate the profile boundary; returns True when the run must abort."""

    if not profile.enabled:
        stage.add(
            STAGE_CONFIG,
            "VALIDATION_DISABLED",
            "/validationProfileId",
            "SQL Server 验证开关处于关闭状态。",
            profile.profile_id,
        )
        return True
    if request.validation_profile_id != profile.profile_id:
        stage.add(
            STAGE_PROFILE,
            "PROFILE_NOT_FOUND",
            "/validationProfileId",
            "请求引用的 validation profile 不存在或不匹配本机配置。",
        )
        return True
    if request.mode not in profile.allowed_modes or request.mode != "describeOnly":
        stage.add(
            STAGE_PROFILE,
            "MODE_NOT_ALLOWED",
            "/mode",
            "当前 profile 未启用请求的验证模式；Phase 5A 只允许 describeOnly。",
        )
        return True
    if profile.environment_class == "production":
        stage.add(
            STAGE_PROFILE,
            "PRODUCTION_TARGET_FORBIDDEN",
            "/validationProfileId",
            "验证 profile 指向 production 环境，永远拒绝。",
        )
        return True
    if (
        not profile.encrypt
        or profile.trust_server_certificate
        or not profile.read_only
        or profile.application_intent != "ReadOnly"
    ):
        stage.add(
            STAGE_PROFILE,
            "TLS_POLICY_INVALID",
            "/validationProfileId",
            "验证 profile 的 TLS 或只读意图策略不满足固定安全规则。",
        )
        return True

    limits = request.validation_case.requested_limits
    if limits is not None:
        _check_requested_limits(stage, limits, profile)
    return stage.blocked


def _check_requested_limits(
    stage: _Stage,
    limits: RequestedValidationLimitsV2,
    profile: SqlServerValidationProfile,
) -> None:
    requested: list[tuple[str, int | None, int]] = [
        (
            "/validationCase/requestedLimits/lockTimeoutMilliseconds",
            limits.lock_timeout_milliseconds,
            profile.lock_timeout_milliseconds,
        ),
        (
            "/validationCase/requestedLimits/commandTimeoutSeconds",
            limits.command_timeout_seconds,
            profile.command_timeout_seconds,
        ),
        (
            "/validationCase/requestedLimits/maxDescribeRows",
            limits.max_describe_rows,
            profile.max_describe_rows,
        ),
        (
            "/validationCase/requestedLimits/maxDescribeBytes",
            limits.max_describe_bytes,
            profile.max_describe_bytes,
        ),
    ]
    for field_path, value, ceiling in requested:
        if value is not None and value > ceiling:
            stage.add(
                STAGE_PROFILE,
                "REQUESTED_LIMIT_VIOLATION",
                field_path,
                "requestedLimits 只能收紧 profile 上限，不能放宽。",
            )


def _recompute_reference_closure(
    stage: _Stage,
    inspector: SqlDialectInspector,
    request,
) -> SqlStaticValidationReportV2 | None:
    """Recompute Phase 2G + Phase 4 and compare the carried static report."""

    recomputed = validate_sql_candidate_v2(inspector, request.static_validation_request)
    carried = request.static_validation_report
    if canonical_sha256(carried) != canonical_sha256(recomputed):
        stage.add(
            STAGE_REFERENCE,
            "STATIC_REPORT_MISMATCH",
            "/staticValidationReport",
            "携带的静态验证报告与完整输入重算结果不一致，可能被伪造或已陈旧。",
        )
    if recomputed.status != "passed":
        stage.add(
            STAGE_REFERENCE,
            "STATIC_GATE_NOT_PASSED",
            "/staticValidationReport/status",
            "Phase 4 静态门禁重算结果不是 passed。",
        )
    if any(issue.gate_order <= 3 for issue in recomputed.issues):
        stage.add(
            STAGE_REFERENCE,
            "INPUT_REFERENCE_MISMATCH",
            "/staticValidationRequest",
            "Phase 2G/候选引用闭包重算发现不一致或存在 blocking uncertainty。",
        )
    return recomputed


def _value_rejection(
    data_type: FactDataTypeV2,
    value: str | int | float | bool | None,
) -> str | None:
    if value is None:
        return None
    if data_type in (FactDataTypeV2.LIST, FactDataTypeV2.UNKNOWN):
        return "list/unknown 参数类型在首版不受支持"
    if data_type in (FactDataTypeV2.STRING, FactDataTypeV2.ENUM):
        if not isinstance(value, str):
            return "string 参数值必须是字符串"
        if len(value) > _MAX_STRING_VALUE_LENGTH:
            return "string 参数值超过长度上限"
        return None
    if data_type == FactDataTypeV2.INTEGER:
        if isinstance(value, bool) or not isinstance(value, int):
            return "integer 参数值必须是整数"
        if value < _BIGINT_MIN or value > _BIGINT_MAX:
            return "integer 参数值超出 bigint 范围"
        return None
    if data_type in (FactDataTypeV2.NUMBER, FactDataTypeV2.MONEY):
        if isinstance(value, bool):
            return "数值参数值不能是布尔"
        if isinstance(value, int):
            return None
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                return "NaN/Infinity 参数值被拒绝"
            return None
        if isinstance(value, str):
            try:
                parsed = Decimal(value)
            except InvalidOperation:
                return "decimal 字符串无法解析"
            if not parsed.is_finite():
                return "非有限 decimal 参数值被拒绝"
            return None
        return "数值参数值类型不受支持"
    if data_type == FactDataTypeV2.BOOLEAN:
        return None if isinstance(value, bool) else "boolean 参数值必须是布尔"
    if data_type == FactDataTypeV2.DATE:
        if not isinstance(value, str):
            return "date 参数值必须是 ISO 字符串"
        try:
            date.fromisoformat(value)
        except ValueError:
            return "date 参数值无法按 ISO 日期解析"
        return None
    if data_type == FactDataTypeV2.DATETIME:
        if not isinstance(value, str):
            return "datetime 参数值必须是 ISO 字符串"
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "datetime 参数值无法按 ISO 解析"
        if parsed.tzinfo is None:
            return "datetime 参数值缺少显式时区策略"
        return None
    return "参数值类型不受支持"


def _parameter_column_map(
    recomputed_resolution: BindingResolutionReportV2,
    payload,
) -> dict[str, PhysicalColumnRefV2 | None]:
    requirements = payload.generation_request.resolution_request.binding_request.query_requirements
    binding_by_field: dict[str, PhysicalColumnRefV2] = {
        item.field_id: item.physical_column for item in recomputed_resolution.resolved_bindings
    }
    entity_by_parameter: dict[str, PhysicalColumnRefV2] = {
        item.parameter_name: item.physical_column
        for item in recomputed_resolution.resolved_entity_keys
    }
    mapping: dict[str, PhysicalColumnRefV2 | None] = {}
    for name in requirements.entity.key_parameters:
        mapping[name] = entity_by_parameter.get(name)
    for item in requirements.filters.items:
        value = item.value
        if isinstance(value, ParameterFilterValueV2):
            mapping.setdefault(value.parameter_name, binding_by_field.get(item.field_id))
    time_range = requirements.time_range
    if time_range.time_field_id:
        for boundary in (time_range.start, time_range.end):
            if boundary is not None and boundary.kind == "parameter" and boundary.parameter_name:
                mapping.setdefault(
                    boundary.parameter_name,
                    binding_by_field.get(time_range.time_field_id),
                )
    return mapping


def _snapshot_column_types(
    snapshot: GovernedMetadataSnapshotV2,
    sensitivity: str,
) -> dict[tuple[str, str, str], str]:
    columns: dict[tuple[str, str, str], str] = {}
    for relation in snapshot.relations:
        for column in relation.columns:
            columns[
                _column_key(
                    relation.schema_name,
                    relation.relation_name,
                    column.column_name,
                    sensitivity,
                )
            ] = column.sql_type
    return columns


def _parameter_issues(
    stage: _Stage,
    request,
    recomputed_resolution: BindingResolutionReportV2,
    snapshot: GovernedMetadataSnapshotV2,
) -> None:
    sensitivity = snapshot.identifier_case_sensitivity.value
    case = request.validation_case
    candidate = request.static_validation_request.candidate
    expected = {item.name: item for item in candidate.parameters}
    actual = {item.name: item for item in case.parameter_bindings}
    for name in sorted(set(expected) - set(actual)):
        stage.add(
            STAGE_PARAMETER,
            "PARAMETER_MISSING",
            "/validationCase/parameterBindings",
            "候选参数缺少对应的绑定。",
            name,
        )
    for name in sorted(set(actual) - set(expected)):
        stage.add(
            STAGE_PARAMETER,
            "PARAMETER_UNDECLARED",
            "/validationCase/parameterBindings",
            "绑定引用了候选未声明的参数。",
            name,
        )

    case_prefix = f"validationCase.{case.case_id}"
    column_map = _parameter_column_map(recomputed_resolution, request.static_validation_request)
    snapshot_columns = _snapshot_column_types(snapshot, sensitivity)

    for name in sorted(set(expected) & set(actual)):
        binding = actual[name]
        declared = expected[name]
        if binding.data_type != declared.data_type:
            stage.add(
                STAGE_PARAMETER,
                "PARAMETER_TYPE_MISMATCH",
                "/validationCase/parameterBindings",
                "绑定数据类型与候选参数声明不一致。",
                name,
            )
            continue
        if binding.source != case_prefix:
            stage.add(
                STAGE_PARAMETER,
                "PARAMETER_VALUE_REJECTED",
                "/validationCase/parameterBindings/source",
                "参数来源必须指向当前 validation case。",
                name,
            )
            continue
        rejection = _value_rejection(binding.data_type, binding.value)
        if rejection is not None:
            stage.add(
                STAGE_PARAMETER,
                "PARAMETER_VALUE_REJECTED",
                "/validationCase/parameterBindings/value",
                rejection,
                name,
            )
            continue
        if declared.required and binding.value is None:
            stage.add(
                STAGE_PARAMETER,
                "PARAMETER_VALUE_REJECTED",
                "/validationCase/parameterBindings/value",
                "必填参数缺少值。",
                name,
            )
            continue
        column = column_map.get(name)
        if column is None:
            stage.add(
                STAGE_PARAMETER,
                "PARAMETER_TYPE_MISMATCH",
                "/validationCase/parameterBindings",
                "参数无法从 Phase 2G 解析结果唯一映射到物理列。",
                name,
            )
            continue
        sql_type = snapshot_columns.get(
            _column_key(column.schema_name, column.relation_name, column.column_name, sensitivity)
        )
        spec = parse_sql_server_type(sql_type or "")
        if spec is None:
            stage.add(
                STAGE_PARAMETER,
                "PARAMETER_TYPE_MISMATCH",
                "/validationCase/parameterBindings",
                "批准快照列类型无法按版本化类型策略解析。",
                name,
            )
            continue
        if spec.family not in _PARAM_TYPE_FAMILIES[declared.data_type] or not (
            parameter_declaration_is_bounded(spec)
        ):
            stage.add(
                STAGE_PARAMETER,
                "PARAMETER_TYPE_MISMATCH",
                "/validationCase/parameterBindings",
                "参数声明类型族与绑定类型不兼容或没有长度上限。",
                name,
            )


def _binder_issues(
    stage: _Stage,
    request,
    recomputed: SqlStaticValidationReportV2,
    binder: SqlParameterBinder,
) -> tuple[BoundSqlV2 | None, dict[str, str]]:
    """Bind the candidate SQL; returns bound result and declaration types."""

    if stage.blocked:
        return None, {}
    candidate = request.static_validation_request.candidate
    try:
        bound = binder.bind(candidate.sql_template)
    except SqlParameterBindingError:
        stage.add(
            STAGE_BINDER,
            "PARAMETER_BIND_FAILED",
            "/candidate/sqlTemplate",
            "参数 binder 无法确定性绑定候选 SQL，已 fail closed。",
        )
        return None, {}

    inspection = recomputed.inspection
    assert inspection is not None
    inspected_names = sorted(
        item.name
        for item in inspection.placeholders
        if item.raw_kind == "colonNamed" and item.name is not None
    )
    if inspected_names != sorted(bound.occurrence_names):
        stage.add(
            STAGE_BINDER,
            "PARAMETER_BIND_FAILED",
            "/candidate/sqlTemplate",
            "binder 占位符结果与 Phase 4 AST 检查不一致。",
        )
        return None, {}
    if sorted(bound.ordered_parameter_names) != sorted(item.name for item in candidate.parameters):
        stage.add(
            STAGE_BINDER,
            "PARAMETER_BIND_FAILED",
            "/candidate/sqlTemplate",
            "binder 参数集合与候选参数声明不一致。",
        )
        return None, {}
    return bound, {}


def _parameter_declaration(
    request,
    bound: BoundSqlV2,
    snapshot: GovernedMetadataSnapshotV2,
    recomputed_resolution: BindingResolutionReportV2,
) -> str | None:
    """Build the ``@pN <type>`` declaration string from the approved snapshot."""

    sensitivity = snapshot.identifier_case_sensitivity.value
    payload = request.static_validation_request
    column_map = _parameter_column_map(recomputed_resolution, payload)
    snapshot_columns = _snapshot_column_types(snapshot, sensitivity)
    parts: list[str] = []
    for slot, name in enumerate(bound.ordered_parameter_names):
        column = column_map.get(name)
        if column is None:
            return None
        sql_type = snapshot_columns.get(
            _column_key(column.schema_name, column.relation_name, column.column_name, sensitivity)
        )
        if not sql_type:
            return None
        spec = parse_sql_server_type(sql_type)
        if spec is None or not parameter_declaration_is_bounded(spec):
            return None
        parts.append(f"@p{slot} {sql_type}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Database-stage comparison helpers
# ---------------------------------------------------------------------------


def _compare_snapshot(
    stage: _Stage,
    request,
    recomputed: SqlStaticValidationReportV2,
    facts,
) -> SnapshotDriftEvidenceV2:
    """Compare approved snapshot subset against live catalog facts."""

    snapshot = (
        request.static_validation_request.generation_request.resolution_request.metadata_snapshot
    )
    sensitivity = snapshot.identifier_case_sensitivity.value
    inspection = recomputed.inspection
    objects: list[ObjectRef] = (
        [ObjectRef(item.schema_name, item.relation_name) for item in inspection.physical_objects]
        if inspection is not None
        else []
    )

    snapshot_relations: dict[tuple[str, str], object] = {
        _case_key(relation.schema_name, relation.relation_name, sensitivity): relation
        for relation in snapshot.relations
    }
    live_relations: dict[tuple[str, str], object] = {
        _case_key(facts_item.schema_name, facts_item.relation_name, sensitivity): facts_item
        for facts_item in facts.relations
    }
    drifts: list[SnapshotDriftItemV2] = []
    relations_checked = 0
    columns_checked = 0

    phase4_keys = {_case_key(item.schema_name, item.relation_name, sensitivity) for item in objects}
    ordered_live_keys = sorted(live_relations)
    for position, key in enumerate(ordered_live_keys):
        if key not in phase4_keys:
            drifts.append(
                SnapshotDriftItemV2(
                    drift_type="extraObject",
                    safe_identifier=f"liveObjects/{position}",
                )
            )

    for index, obj in enumerate(objects):
        key = _case_key(obj.schema_name, obj.relation_name, sensitivity)
        approved = snapshot_relations.get(key)
        live = live_relations.get(key)
        if approved is None or live is None:
            if approved is not None and live is None:
                stage.add(
                    STAGE_SNAPSHOT,
                    "SNAPSHOT_OBJECT_MISSING",
                    "/snapshotDriftEvidence",
                    "实时 catalog 中找不到批准快照中的对象。",
                    f"objects/{index}",
                )
                drifts.append(
                    SnapshotDriftItemV2(
                        drift_type="objectMissing",
                        safe_identifier=f"objects/{index}",
                    )
                )
            continue
        relations_checked += 1
        relation_kind = getattr(approved, "relation_kind", None)
        expected_code = _OBJECT_TYPE_CODES.get(getattr(relation_kind, "value", str(relation_kind)))
        if live.object_type is None or (expected_code and live.object_type != expected_code):
            stage.add(
                STAGE_SNAPSHOT,
                "SNAPSHOT_TYPE_DRIFT",
                "/snapshotDriftEvidence",
                "实时对象类型与批准快照不一致。",
                f"objects/{index}",
            )
            drifts.append(
                SnapshotDriftItemV2(
                    drift_type="objectTypeDrift",
                    safe_identifier=f"objects/{index}",
                )
            )
        if live.modified_after_capture:
            stage.warn(
                STAGE_SNAPSHOT,
                "SNAPSHOT_DEFINITION_DRIFT",
                "/snapshotDriftEvidence",
                "实时对象在快照捕获后发生过修改。",
                f"objects/{index}",
            )
            drifts.append(
                SnapshotDriftItemV2(
                    drift_type="definitionDrift",
                    safe_identifier=f"objects/{index}",
                )
            )

        approved_columns = list(approved.columns)
        live_columns: dict[str, object] = {}
        for live_column in live.columns:
            name = live_column.column_name
            live_columns[name if sensitivity == "sensitive" else name.casefold()] = live_column
        approved_names: set[str] = set()
        for column_index, approved_column in enumerate(approved_columns):
            columns_checked += 1
            lookup = (
                approved_column.column_name
                if sensitivity == "sensitive"
                else approved_column.column_name.casefold()
            )
            approved_names.add(lookup)
            column_id = f"objects/{index}/columns/{column_index}"
            live_column = live_columns.get(lookup)
            if live_column is None:
                stage.add(
                    STAGE_SNAPSHOT,
                    "SNAPSHOT_COLUMN_MISSING",
                    "/snapshotDriftEvidence",
                    "实时 catalog 中缺少批准快照声明的列。",
                    column_id,
                )
                drifts.append(
                    SnapshotDriftItemV2(
                        drift_type="columnMissing",
                        safe_identifier=column_id,
                    )
                )
                continue
            if live_column.is_user_defined_type:
                stage.add(
                    STAGE_SNAPSHOT,
                    "SNAPSHOT_TYPE_DRIFT",
                    "/snapshotDriftEvidence",
                    "实时列使用 alias/CLR 类型，首版阻断。",
                    column_id,
                )
                drifts.append(
                    SnapshotDriftItemV2(
                        drift_type="typeDrift",
                        safe_identifier=column_id,
                    )
                )
                continue
            approved_spec = parse_sql_server_type(approved_column.sql_type)
            live_family = (
                described_type_family(live_column.system_type_name)
                if live_column.system_type_name
                else None
            )
            if approved_spec is None or live_family is None:
                stage.add(
                    STAGE_SNAPSHOT,
                    "SNAPSHOT_TYPE_DRIFT",
                    "/snapshotDriftEvidence",
                    "列类型无法按版本化类型策略解析，fail closed。",
                    column_id,
                )
                drifts.append(
                    SnapshotDriftItemV2(
                        drift_type="typeDrift",
                        safe_identifier=column_id,
                    )
                )
                continue
            compatibility = live_column_compatible(
                approved_spec.family,
                approved_spec,
                live_family=live_family,
                live_max_length=live_column.max_length,
                live_precision=live_column.precision,
                live_scale=live_column.scale,
            )
            if compatibility == "typeDrift":
                stage.add(
                    STAGE_SNAPSHOT,
                    "SNAPSHOT_TYPE_DRIFT",
                    "/snapshotDriftEvidence",
                    "实时列类型、长度或精度与批准快照不兼容。",
                    column_id,
                )
                drifts.append(
                    SnapshotDriftItemV2(
                        drift_type="typeDrift",
                        safe_identifier=column_id,
                    )
                )
            if live_column.is_nullable and not approved_column.nullable:
                stage.add(
                    STAGE_SNAPSHOT,
                    "SNAPSHOT_NULLABILITY_DRIFT",
                    "/snapshotDriftEvidence",
                    "实时列空值性比批准契约更宽。",
                    column_id,
                )
                drifts.append(
                    SnapshotDriftItemV2(
                        drift_type="nullabilityDrift",
                        safe_identifier=column_id,
                    )
                )
            if live_column.is_computed:
                stage.warn(
                    STAGE_SNAPSHOT,
                    "SNAPSHOT_DEFINITION_DRIFT",
                    "/snapshotDriftEvidence",
                    "实时列是 computed 列，批准快照未声明该状态。",
                    column_id,
                )
                drifts.append(
                    SnapshotDriftItemV2(
                        drift_type="computedColumn",
                        safe_identifier=column_id,
                    )
                )
        extra_live = [
            lookup for lookup, _ in sorted(live_columns.items()) if lookup not in approved_names
        ]
        for position in range(len(extra_live)):
            drifts.append(
                SnapshotDriftItemV2(
                    drift_type="extraColumn",
                    safe_identifier=f"objects/{index}/liveColumns/{position}",
                )
            )

    return SnapshotDriftEvidenceV2(
        status="drifted" if drifts else "matched",
        relations_checked=relations_checked,
        columns_checked=columns_checked,
        drifts=tuple(drifts),
    )


def _describe_issues(
    stage: _Stage,
    describe,
    max_rows: int,
    max_bytes: int,
    result_data_type: FactDataTypeV2,
    result_nullable: bool,
) -> DescribeEvidenceV2:
    evidence = DescribeEvidenceV2(
        attempted=True,
        described=False,
        response_rows=describe.response_rows,
        response_bytes=describe.response_bytes,
        result_column_count=len(describe.columns),
        source_within_closure=True,
    )

    def update(**changes: object) -> DescribeEvidenceV2:
        return evidence.model_copy(update=changes)

    if describe.response_rows is not None and describe.response_rows > max_rows:
        stage.add(
            STAGE_DESCRIBE,
            "DESCRIBE_SIZE_LIMIT",
            "/describeEvidence",
            "描述结果行数超过配置上限。",
        )
        return evidence
    if describe.response_bytes is not None and describe.response_bytes > max_bytes:
        stage.add(
            STAGE_DESCRIBE,
            "DESCRIBE_SIZE_LIMIT",
            "/describeEvidence",
            "描述结果字节数超过配置上限。",
        )
        return evidence
    if len(describe.columns) != 1:
        stage.add(
            STAGE_DESCRIBE,
            "RESULT_SHAPE_MISMATCH",
            "/describeEvidence",
            "首个结果集必须恰好返回一个结果列。",
        )
        return evidence
    column = describe.columns[0]
    evidence = update(column_name=column.name, nullable=column.is_nullable)
    if column.name != RESULT_COLUMN_NAME:
        stage.add(
            STAGE_DESCRIBE,
            "RESULT_SHAPE_MISMATCH",
            "/describeEvidence",
            "结果列名必须精确为 fact_value。",
        )
        return evidence
    if column.system_type_name is None:
        stage.add(
            STAGE_DESCRIBE,
            "RESULT_TYPE_MISMATCH",
            "/describeEvidence",
            "SQL Server 未返回可解释的结果类型。",
        )
        return evidence
    family = described_type_family(column.system_type_name)
    evidence = update(sql_server_type_family=family)
    if family is None or not result_family_allowed(result_data_type, family):
        stage.add(
            STAGE_DESCRIBE,
            "RESULT_TYPE_MISMATCH",
            "/describeEvidence",
            "结果类型族与 V2 结果类型矩阵不兼容。",
        )
        return evidence
    if result_nullable is False and column.is_nullable is not False:
        stage.add(
            STAGE_DESCRIBE,
            "RESULT_NULLABILITY_MISMATCH",
            "/describeEvidence",
            "结果空值性比契约更宽或无法确定。",
        )
        return evidence
    return update(described=True)


def _permission_issues(
    stage: _Stage,
    permissions,
    objects: list[ObjectRef],
) -> PermissionEvidenceV2 | None:
    def _safe_object(ref: ObjectRef) -> str:
        for index, item in enumerate(objects):
            if item == ref:
                return f"objects/{index}"
        return "objects/unknown"

    evidence = PermissionEvidenceV2(
        determined=permissions.determined,
        objects_checked=len(objects),
        select_granted_objects=len(permissions.select_granted),
        write_capability_detected=bool(
            permissions.forbidden_capabilities
            or permissions.server_role_write_detected
            or permissions.database_role_write_detected
        ),
        forbidden_capabilities=tuple(sorted(set(permissions.forbidden_capabilities))),
        server_role_write_detected=permissions.server_role_write_detected,
        database_role_write_detected=permissions.database_role_write_detected,
    )
    if not permissions.determined:
        stage.add(
            STAGE_PERMISSION,
            "PERMISSION_UNDETERMINED",
            "/permissionEvidence",
            "有效权限无法确定，不能假定只读。",
        )
        return evidence
    for ref in permissions.select_denied:
        stage.add(
            STAGE_PERMISSION,
            "SELECT_PERMISSION_MISSING",
            "/permissionEvidence",
            "对象缺少 SELECT 有效权限。",
            _safe_object(ref),
        )
    granted = set(permissions.select_granted)
    for ref in objects:
        if ref not in granted:
            stage.add(
                STAGE_PERMISSION,
                "SELECT_PERMISSION_MISSING",
                "/permissionEvidence",
                "对象缺少 SELECT 有效权限。",
                _safe_object(ref),
            )
    for capability in sorted(set(permissions.forbidden_capabilities)):
        stage.add(
            STAGE_PERMISSION,
            "WRITE_PERMISSION_PRESENT",
            "/permissionEvidence",
            "检测到写入、控制或模拟能力。",
            capability,
        )
    if permissions.server_role_write_detected or permissions.database_role_write_detected:
        stage.add(
            STAGE_PERMISSION,
            "WRITE_PERMISSION_PRESENT",
            "/permissionEvidence",
            "当前账号具有高权限数据库或服务器角色。",
        )
    return evidence


def _candidate_ref(request) -> ValidationCandidateRefV2:
    carried = request.static_validation_report.candidate_ref
    return ValidationCandidateRefV2(
        content_sha256=carried.candidate_content_sha256,
        sql_template_sha256=carried.sql_template_sha256,
        generation_input_sha256=carried.generation_input_sha256,
        resolution_report_sha256=carried.resolution_report_sha256,
        context_sha256=carried.context_sha256,
        snapshot_sha256=carried.snapshot_sha256,
    )


def validate_sqlserver_candidate_v2(
    *,
    request,
    profile: SqlServerValidationProfile,
    inspector: SqlDialectInspector,
    binder: SqlParameterBinder,
    validator_port: SqlServerValidationPort,
    value_fingerprinter: Callable[[str], str],
    identity_fingerprinter: Callable[[str], str],
    run_id: str,
    started_at: datetime,
) -> SqlServerValidationReportV2:
    """Run one describe-only validation; adapter calls stay at zero on preflight failure."""

    stage = _Stage()
    target_evidence: TargetEvidenceV2 | None = None
    permission_evidence: PermissionEvidenceV2 | None = None
    drift_evidence: SnapshotDriftEvidenceV2 | None = None
    describe_evidence: DescribeEvidenceV2 | None = None
    bound: BoundSqlV2 | None = None
    parameter_evidence: tuple[ParameterEvidenceV2, ...] = ()
    recomputed: SqlStaticValidationReportV2 | None = None
    max_describe_rows = profile.max_describe_rows
    max_describe_bytes = profile.max_describe_bytes
    lock_timeout = profile.lock_timeout_milliseconds
    command_timeout = profile.command_timeout_seconds

    if _profile_issues(stage, request, profile):
        recomputed = None
    else:
        recomputed = _recompute_reference_closure(stage, inspector, request)

    limits = request.validation_case.requested_limits
    if limits is not None:
        lock_timeout = limits.lock_timeout_milliseconds or lock_timeout
        command_timeout = limits.command_timeout_seconds or command_timeout
        max_describe_rows = limits.max_describe_rows or max_describe_rows
        max_describe_bytes = limits.max_describe_bytes or max_describe_bytes

    if recomputed is not None and not stage.blocked:
        _parameter_issues(
            stage,
            request,
            request.static_validation_request.generation_request.resolution_report,
            request.static_validation_request.generation_request.resolution_request.metadata_snapshot,
        )

    bound_sql_ref: BoundSqlRefV2 | None = None
    declaration: str | None = None
    if recomputed is not None and not stage.blocked:
        resolution_report = request.static_validation_request.generation_request.resolution_report
        bound, _ = _binder_issues(stage, request, recomputed, binder)
        if bound is not None:
            declaration = _parameter_declaration(
                request,
                bound,
                request.static_validation_request.generation_request.resolution_request.metadata_snapshot,
                resolution_report,
            )
            if declaration is None:
                stage.add(
                    STAGE_BINDER,
                    "PARAMETER_TYPE_MISMATCH",
                    "/validationCase/parameterBindings",
                    "参数声明类型无法从批准快照确定性派生。",
                )
        if bound is not None and not stage.blocked:
            bound_sql_ref = BoundSqlRefV2(
                binder_version=bound.binder_version,
                source_sql_sha256=bound.source_sql_sha256,
                bound_sql_sha256=bound.describe_sql_sha256,
                ordered_parameter_names=bound.ordered_parameter_names,
                parameter_count=len(bound.ordered_parameter_names),
            )
            case = request.validation_case
            parameter_evidence = tuple(
                ParameterEvidenceV2(
                    name=item.name,
                    data_type=item.data_type,
                    source=item.source,
                    data_classification=case.data_classification,
                    value_hmac_sha256=value_fingerprinter(
                        json.dumps(
                            item.value,
                            ensure_ascii=False,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                    ),
                    bound=True,
                )
                for item in case.parameter_bindings
            )

    if not stage.blocked and bound is not None and declaration is not None:
        holder = _TargetHolder()
        _run_database_stage(
            stage,
            request,
            profile,
            recomputed,
            validator_port,
            identity_fingerprinter,
            bound,
            declaration,
            lock_timeout,
            command_timeout,
            max_describe_rows,
            max_describe_bytes,
            holder,
        )
        target_evidence = holder.target_evidence
        permission_evidence = holder.permission_evidence
        drift_evidence = holder.drift_evidence
        describe_evidence = holder.describe_evidence

    completed_at = datetime.now(_UTC)
    duration_ms = max(0, int((completed_at - started_at).total_seconds() * 1000))
    report = SqlServerValidationReportV2(
        mode=request.mode,
        status=stage.status(),
        validation_run_id=run_id,
        started_at=_utc_text(started_at),
        completed_at=_utc_text(completed_at),
        duration_ms=duration_ms,
        candidate_ref=_candidate_ref(request),
        static_report_sha256=(
            canonical_sha256(recomputed)
            if recomputed is not None
            else canonical_sha256(request.static_validation_report)
        ),
        target_evidence=target_evidence,
        permission_evidence=permission_evidence,
        snapshot_drift_evidence=drift_evidence,
        bound_sql_ref=bound_sql_ref,
        parameter_evidence=parameter_evidence,
        describe_evidence=describe_evidence,
        issues=_sort_issues(stage.issues),
        warnings=_sort_issues(stage.warnings),
        report_sha256="0" * 64,
    )
    payload = report.model_dump(by_alias=True, mode="json")
    payload.pop("reportSha256", None)
    report = report.model_copy(update={"report_sha256": canonical_sha256(payload)})
    return report


class _TargetHolder:
    def __init__(self) -> None:
        self.target_evidence: TargetEvidenceV2 | None = None
        self.permission_evidence: PermissionEvidenceV2 | None = None
        self.drift_evidence: SnapshotDriftEvidenceV2 | None = None
        self.describe_evidence: DescribeEvidenceV2 | None = None


def _run_database_stage(
    stage: _Stage,
    request,
    profile: SqlServerValidationProfile,
    recomputed: SqlStaticValidationReportV2,
    validator_port: SqlServerValidationPort,
    identity_fingerprinter: Callable[[str], str],
    bound: BoundSqlV2,
    declaration: str,
    lock_timeout: int,
    command_timeout: int,
    max_describe_rows: int,
    max_describe_bytes: int,
    holder: _TargetHolder,
) -> None:
    session = None
    try:
        session = validator_port.open_session(
            SessionLimits(
                connect_timeout_seconds=profile.connect_timeout_seconds,
                command_timeout_seconds=command_timeout,
                lock_timeout_milliseconds=lock_timeout,
            )
        )
        session.apply_session_policy(
            SessionPolicy(
                lock_timeout_milliseconds=lock_timeout,
                command_timeout_seconds=command_timeout,
            )
        )
        identity = session.attest_target(
            TargetAttestationExpectation(
                expected_server_fingerprint=identity_fingerprinter(f"server:{profile.host}"),
                expected_database_fingerprint=identity_fingerprinter(
                    f"database:{profile.database}"
                ),
                supported_major_versions=profile.supported_major_versions,
            )
        )
        holder.target_evidence = TargetEvidenceV2(
            profile_id=profile.profile_id,
            environment_class=profile.environment_class,
            server_major_version=identity.server_major_version,
            engine_edition=identity.engine_edition,
            compatibility_level=identity.compatibility_level,
            target_identity_fingerprint=(
                f"{identity.server_fingerprint}:{identity.database_fingerprint}"
            ),
            identity_matched=identity.identity_matched,
            version_supported=identity.version_supported,
        )
        if not identity.identity_matched:
            stage.add(
                STAGE_TARGET,
                "TARGET_IDENTITY_MISMATCH",
                "/targetEvidence",
                "实时目标身份与 validation profile 不一致。",
            )
            return
        if not identity.version_supported:
            stage.add(
                STAGE_TARGET,
                "SERVER_VERSION_UNSUPPORTED",
                "/targetEvidence",
                "SQL Server 主版本不在 profile 支持范围内。",
            )
            return

        inspection = recomputed.inspection
        objects = (
            [
                ObjectRef(item.schema_name, item.relation_name)
                for item in inspection.physical_objects
            ]
            if inspection is not None
            else []
        )
        permissions = session.attest_permissions(
            PermissionAttestationRequest(objects=tuple(objects))
        )
        holder.permission_evidence = _permission_issues(stage, permissions, objects)
        if stage.blocked:
            return

        resolution_request = request.static_validation_request.generation_request.resolution_request
        captured_at = _parse_utc(resolution_request.metadata_snapshot.captured_at)
        facts = session.read_catalog_facts(
            CatalogFactsRequest(objects=tuple(objects), captured_at=captured_at)
        )
        if facts.undetermined:
            for ref in facts.undetermined:
                stage.add(
                    STAGE_SNAPSHOT,
                    "SNAPSHOT_PROBE_FAILED",
                    "/snapshotDriftEvidence",
                    "实时 catalog 探测无法完成，不能证明快照一致。",
                    f"objects/{objects.index(ref)}" if ref in objects else "objects/unknown",
                )
            return
        drift_evidence = _compare_snapshot(stage, request, recomputed, facts)
        holder.drift_evidence = drift_evidence
        if stage.blocked:
            return

        candidate = request.static_validation_request.candidate
        describe = session.describe_first_result_set(
            DescribeRequest(
                tsql=bound.describe_sql,
                parameter_declaration=declaration,
                max_rows=max_describe_rows,
                max_bytes=max_describe_bytes,
            )
        )
        holder.describe_evidence = _describe_issues(
            stage,
            describe,
            max_describe_rows,
            max_describe_bytes,
            candidate.result.data_type,
            candidate.result.nullable,
        )
    except SqlServerTimeoutError as exc:
        stage.inconclusive = True
        stage.add(
            STAGE_CONNECTION,
            "CONNECTION_UNAVAILABLE",
            "/adapter",
            "SQL Server 操作超时，结果不可判定。",
            exc.sqlstate,
        )
    except SqlServerUnavailableError as exc:
        stage.inconclusive = True
        sqlstate = exc.sqlstate or ""
        code = "DRIVER_UNAVAILABLE" if sqlstate.startswith("IM") else "CONNECTION_UNAVAILABLE"
        stage.add(
            STAGE_CONNECTION,
            code,
            "/adapter",
            "SQL Server 连接或驱动不可用。",
            exc.sqlstate,
        )
    except SqlServerDescribeRejectedError as exc:
        stage.add(
            STAGE_DESCRIBE,
            "DESCRIBE_REJECTED",
            "/describeEvidence",
            "SQL Server 拒绝对该 batch 做结果描述。",
            exc.sqlstate,
        )
    except SqlServerProbeUndeterminedError as exc:
        operation = exc.operation
        if operation.startswith("permission"):
            stage.add(
                STAGE_PERMISSION,
                "PERMISSION_UNDETERMINED",
                "/adapter",
                "SQL Server 权限探测无法完成，不能假定只读。",
                exc.sqlstate,
            )
        elif operation.startswith("sessionPolicy"):
            stage.add(
                STAGE_SESSION_POLICY,
                "SESSION_POLICY_FAILED",
                "/adapter",
                "固定 session policy 应用或回读失败。",
                exc.sqlstate,
            )
        elif operation.startswith("target"):
            stage.add(
                STAGE_TARGET,
                "TARGET_ATTESTATION_FAILED",
                "/adapter",
                "目标身份探测无法完成，不能证明目标一致。",
                exc.sqlstate,
            )
        else:
            stage.add(
                STAGE_SNAPSHOT,
                "SNAPSHOT_PROBE_FAILED",
                "/adapter",
                "实时 catalog 探测无法完成，不能证明快照一致。",
                exc.sqlstate,
            )
    except Exception:  # noqa: BLE001 - unexpected failures must stay inconclusive
        stage.inconclusive = True
        stage.add(
            STAGE_INTERNAL,
            "INTERNAL_VALIDATION_FAILED",
            "/adapter",
            "验证过程出现未预期错误，结果不可判定。",
        )
    finally:
        if session is not None:
            session.rollback_safely()
            session.close()
