"""Strict Phase 5A contracts for restricted SQL Server description validation.

The request/report pair in this module is the wire boundary of the describe-only
validation slice. Reports never carry SQL text, parameter values, connection
targets, credentials, raw driver errors, or business results, and they always
stay ``executable=false``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from release_sql_bot.domain.fact_bindings_v2 import (
    FactDataTypeV2,
    ReportModel,
    V2ConsumerModel,
)
from release_sql_bot.domain.sql_validation import (
    SqlStaticValidationReportV2,
    ValidateSqlCandidateRequestV2,
)

_SHA256_PATTERN = r"^[a-f0-9]{64}$"

SCHEMA_VERSION = "1.0.0"
VALIDATION_POLICY_VERSION = "sqlserver-validation-v1"
BINDER_VERSION = "sqlserver-token-binder-v1"
RESULT_COLUMN_NAME = "fact_value"

ValidationMode = Literal["describeOnly"]
DataClassification = Literal["synthetic", "maskedNonProduction"]
ValidationStatus = Literal["passed", "blocked", "inconclusive"]
EnvironmentClass = Literal["development", "staging", "production"]

FactDataTypeWire = Annotated[FactDataTypeV2, Field(strict=False)]

# Stable stage ordering for issues; every stage below the database boundary
# must keep the adapter call count at zero.
STAGE_CONFIG = 1
STAGE_PROFILE = 2
STAGE_REFERENCE = 3
STAGE_PARAMETER = 4
STAGE_BINDER = 5
STAGE_CONNECTION = 6
STAGE_SESSION_POLICY = 7
STAGE_TARGET = 8
STAGE_PERMISSION = 9
STAGE_SNAPSHOT = 10
STAGE_DESCRIBE = 11
STAGE_INTERNAL = 20

ISSUE_CODES: tuple[str, ...] = (
    "INPUT_REFERENCE_MISMATCH",
    "STATIC_REPORT_MISMATCH",
    "STATIC_GATE_NOT_PASSED",
    "VALIDATION_DISABLED",
    "PROFILE_NOT_FOUND",
    "MODE_NOT_ALLOWED",
    "PRODUCTION_TARGET_FORBIDDEN",
    "TLS_POLICY_INVALID",
    "REQUESTED_LIMIT_VIOLATION",
    "DRIVER_UNAVAILABLE",
    "CONNECTION_UNAVAILABLE",
    "TARGET_IDENTITY_MISMATCH",
    "SERVER_VERSION_UNSUPPORTED",
    "TARGET_ATTESTATION_FAILED",
    "SESSION_POLICY_FAILED",
    "SELECT_PERMISSION_MISSING",
    "WRITE_PERMISSION_PRESENT",
    "PERMISSION_UNDETERMINED",
    "SNAPSHOT_OBJECT_MISSING",
    "SNAPSHOT_COLUMN_MISSING",
    "SNAPSHOT_TYPE_DRIFT",
    "SNAPSHOT_NULLABILITY_DRIFT",
    "SNAPSHOT_DEFINITION_DRIFT",
    "SNAPSHOT_EXTRA_COLUMN",
    "SNAPSHOT_PROBE_FAILED",
    "PARAMETER_MISSING",
    "PARAMETER_UNDECLARED",
    "PARAMETER_TYPE_MISMATCH",
    "PARAMETER_VALUE_REJECTED",
    "PARAMETER_BIND_FAILED",
    "DESCRIBE_REJECTED",
    "DESCRIBE_SIZE_LIMIT",
    "RESULT_SHAPE_MISMATCH",
    "RESULT_TYPE_MISMATCH",
    "RESULT_NULLABILITY_MISMATCH",
    "INTERNAL_VALIDATION_FAILED",
)

# Neutral capability names used by permission evidence; they never contain
# object, database, or principal names.
_FORBIDDEN_OBJECT_PERMISSIONS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "ALTER",
    "CONTROL",
    "TAKE OWNERSHIP",
    "IMPERSONATE",
)
_FORBIDDEN_ROLES = ("db_owner", "db_datawriter", "sysadmin")


def _reject_snake_case_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and "_" in key:
                raise ValueError(f"snake_case key is not accepted: {key}")
            _reject_snake_case_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_snake_case_keys(nested)


def _same_wire_shape(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_wire_shape(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_wire_shape(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def _require_exact_wire_shape(value: Any, field: str, model: Any) -> None:
    if isinstance(value, dict) and field in value:
        parsed = model.model_validate(value[field])
        if not _same_wire_shape(parsed.model_dump(by_alias=True, mode="json"), value[field]):
            raise ValueError(f"{field} must be complete and require no coercion")


def _utc_now_text(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class ParameterBindingV2(V2ConsumerModel):
    """One controlled parameter value that only exists in caller memory."""

    name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    data_type: FactDataTypeWire
    value: str | int | float | bool | None
    source: str = Field(
        pattern=r"^validationCase\.[A-Za-z0-9][A-Za-z0-9._-]{0,198}$",
        max_length=240,
    )


class RequestedValidationLimitsV2(V2ConsumerModel):
    """Optional caller limits that may only tighten the configured profile."""

    lock_timeout_milliseconds: int | None = Field(default=None, ge=1)
    command_timeout_seconds: int | None = Field(default=None, ge=1, le=300)
    max_describe_rows: int | None = Field(default=None, ge=1)
    max_describe_bytes: int | None = Field(default=None, ge=1024)


class ValidationCaseV2(V2ConsumerModel):
    case_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
    data_classification: DataClassification
    parameter_bindings: list[ParameterBindingV2] = Field(min_length=1, max_length=100)
    requested_limits: RequestedValidationLimitsV2 | None = None

    @model_validator(mode="after")
    def reject_duplicate_parameter_names(self) -> ValidationCaseV2:
        names = [item.name for item in self.parameter_bindings]
        if len(names) != len(set(names)):
            raise ValueError("parameterBindings cannot contain duplicate names")
        return self


class ValidateSqlServerRequestV2(V2ConsumerModel):
    """Complete Phase 5A request; nothing may be resolved by ID at run time."""

    schema_version: Literal["1.0.0"]
    mode: Literal["describeOnly"]
    validation_profile_id: str = Field(min_length=1, max_length=64)
    static_validation_request: ValidateSqlCandidateRequestV2
    static_validation_report: SqlStaticValidationReportV2
    validation_case: ValidationCaseV2

    @model_validator(mode="before")
    @classmethod
    def reject_nested_snake_case_fallback(cls, value: Any) -> Any:
        _reject_snake_case_keys(value)
        if isinstance(value, dict):
            _require_exact_wire_shape(
                value, "staticValidationRequest", ValidateSqlCandidateRequestV2
            )
            _require_exact_wire_shape(value, "staticValidationReport", SqlStaticValidationReportV2)
        return value


class SqlServerValidationIssueV2(ReportModel):
    stage_order: int = Field(ge=1, le=20)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    field_path: str = Field(pattern=r"^/", max_length=1_000)
    message: str = Field(min_length=1, max_length=500)
    safe_identifier: str | None = Field(default=None, max_length=500)


class ValidationCandidateRefV2(ReportModel):
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    sql_template_sha256: str = Field(pattern=_SHA256_PATTERN)
    generation_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    context_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)


class TargetEvidenceV2(ReportModel):
    profile_id: str
    environment_class: str
    server_major_version: int
    engine_edition: int
    compatibility_level: int
    target_identity_fingerprint: str = Field(min_length=16, max_length=256)
    identity_matched: bool
    version_supported: bool


class PermissionEvidenceV2(ReportModel):
    determined: bool
    objects_checked: int
    select_granted_objects: int
    write_capability_detected: bool
    forbidden_capabilities: tuple[str, ...] = ()
    server_role_write_detected: bool = False
    database_role_write_detected: bool = False


class SnapshotDriftItemV2(ReportModel):
    drift_type: Literal[
        "objectMissing",
        "objectTypeDrift",
        "columnMissing",
        "typeDrift",
        "nullabilityDrift",
        "definitionDrift",
        "extraColumn",
        "extraObject",
        "computedColumn",
    ]
    safe_identifier: str = Field(min_length=1, max_length=500)


class SnapshotDriftEvidenceV2(ReportModel):
    status: Literal["matched", "drifted", "notCompared"]
    relations_checked: int = Field(ge=0)
    columns_checked: int = Field(ge=0)
    drifts: tuple[SnapshotDriftItemV2, ...] = ()


class DescribeEvidenceV2(ReportModel):
    attempted: bool = False
    described: bool = False
    result_column_count: int | None = Field(default=None, ge=0)
    column_name: str | None = Field(default=None, max_length=128)
    sql_server_type_family: str | None = Field(default=None, max_length=40)
    nullable: bool | None = None
    source_within_closure: bool | None = None
    response_rows: int | None = Field(default=None, ge=0)
    response_bytes: int | None = Field(default=None, ge=0)


class BoundSqlRefV2(ReportModel):
    binder_version: str = Field(min_length=1, max_length=80)
    parameter_style: Literal["namedSqlServer"] = "namedSqlServer"
    source_sql_sha256: str = Field(pattern=_SHA256_PATTERN)
    bound_sql_sha256: str = Field(pattern=_SHA256_PATTERN)
    ordered_parameter_names: tuple[str, ...] = Field(max_length=100)
    parameter_count: int = Field(ge=0)


class ParameterEvidenceV2(ReportModel):
    name: str
    data_type: FactDataTypeV2
    source: str
    data_classification: DataClassification
    value_hmac_sha256: str = Field(pattern=_SHA256_PATTERN)
    bound: bool


class SqlServerValidationReportV2(ReportModel):
    schema_version: Literal["1.0.0"] = SCHEMA_VERSION
    validation_policy_version: Literal["sqlserver-validation-v1"] = VALIDATION_POLICY_VERSION
    mode: ValidationMode
    status: ValidationStatus
    executable: Literal[False] = False
    validation_run_id: str = Field(min_length=8, max_length=128)
    started_at: str = Field(min_length=20, max_length=80)
    completed_at: str = Field(min_length=20, max_length=80)
    duration_ms: int = Field(ge=0)
    candidate_ref: ValidationCandidateRefV2
    static_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    target_evidence: TargetEvidenceV2 | None = None
    permission_evidence: PermissionEvidenceV2 | None = None
    snapshot_drift_evidence: SnapshotDriftEvidenceV2 | None = None
    bound_sql_ref: BoundSqlRefV2 | None = None
    parameter_evidence: tuple[ParameterEvidenceV2, ...] = ()
    describe_evidence: DescribeEvidenceV2 | None = None
    issues: tuple[SqlServerValidationIssueV2, ...]
    warnings: tuple[SqlServerValidationIssueV2, ...] = ()
    report_sha256: str = Field(pattern=_SHA256_PATTERN)


# ---------------------------------------------------------------------------
# sqlserver-type-policy-v1: versioned SQL Server type normalization rules.
# ---------------------------------------------------------------------------

CHAR_FAMILIES = ("char", "varchar", "nchar", "nvarchar")
INTEGER_FAMILIES = ("tinyint", "smallint", "int", "bigint")
EXACT_FAMILIES = (
    "tinyint",
    "smallint",
    "int",
    "bigint",
    "bit",
    "date",
    "real",
    "money",
    "smallmoney",
    "uniqueidentifier",
    "rowversion",
    "binary",
    "varbinary",
    "image",
    "text",
    "ntext",
    "xml",
    "sql_variant",
    "time",
    "datetimeoffset",
)
DEPRECATED_LOB_FAMILIES = ("text", "ntext", "image")

_RESULT_TYPE_FAMILIES: dict[FactDataTypeV2, tuple[str, ...]] = {
    FactDataTypeV2.STRING: ("char", "varchar", "nchar", "nvarchar"),
    FactDataTypeV2.ENUM: ("char", "varchar", "nchar", "nvarchar"),
    FactDataTypeV2.INTEGER: ("tinyint", "smallint", "int", "bigint"),
    FactDataTypeV2.NUMBER: ("decimal", "numeric", "float", "real"),
    FactDataTypeV2.MONEY: ("decimal", "numeric", "money", "smallmoney"),
    FactDataTypeV2.BOOLEAN: ("bit",),
    FactDataTypeV2.DATE: ("date",),
    FactDataTypeV2.DATETIME: ("datetime2", "datetime", "smalldatetime"),
}


class SqlServerTypeSpec:
    """Normalized view of one SQL Server scalar type declaration."""

    __slots__ = ("family", "length", "precision", "scale", "is_max")

    def __init__(
        self,
        family: str,
        *,
        length: int | None = None,
        precision: int | None = None,
        scale: int | None = None,
        is_max: bool = False,
    ) -> None:
        self.family = family
        self.length = length
        self.precision = precision
        self.scale = scale
        self.is_max = is_max

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, SqlServerTypeSpec)
            and self.family == other.family
            and self.length == other.length
            and self.precision == other.precision
            and self.scale == other.scale
            and self.is_max == other.is_max
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return (
            f"SqlServerTypeSpec(family={self.family!r}, length={self.length!r}, "
            f"precision={self.precision!r}, scale={self.scale!r}, is_max={self.is_max!r})"
        )


def parse_sql_server_type(declaration: str) -> SqlServerTypeSpec | None:
    """Parse a SQL Server scalar type declaration such as ``decimal(18,2)``.

    Returns ``None`` when the declaration cannot be interpreted exactly; callers
    must fail closed on ``None``.
    """

    text = declaration.strip()
    if not text or any(token in text for token in (";", "--", "/*", "*", '"')):
        return None
    paren = text.find("(")
    base = text[:paren].strip().casefold() if paren >= 0 else text.strip().casefold()
    arguments: list[int] = []
    if paren >= 0:
        if not text.endswith(")"):
            return None
        inner = text[paren + 1 : -1].strip()
        if not inner:
            return None
        for part in inner.split(","):
            part = part.strip().casefold()
            if part == "max":
                arguments.append(-1)
                continue
            if not part.isdigit():
                return None
            arguments.append(int(part))
    if base in CHAR_FAMILIES:
        if not arguments:
            return SqlServerTypeSpec(base)
        if len(arguments) != 1:
            return None
        if arguments[0] == -1:
            return SqlServerTypeSpec(base, is_max=True)
        return SqlServerTypeSpec(base, length=arguments[0])
    if base in (
        "decimal",
        "numeric",
        "datetime2",
        "time",
        "datetimeoffset",
        "float",
        "varbinary",
        "binary",
    ):
        if not arguments:
            return SqlServerTypeSpec(base)
        if len(arguments) == 1:
            return SqlServerTypeSpec(base, precision=arguments[0])
        if len(arguments) == 2:
            return SqlServerTypeSpec(base, precision=arguments[0], scale=arguments[1])
        return None
    if base in EXACT_FAMILIES:
        return SqlServerTypeSpec(base) if not arguments else None
    return None


def described_type_family(system_type_name: str) -> str | None:
    spec = parse_sql_server_type(system_type_name)
    return spec.family if spec is not None else None


def result_family_allowed(data_type: FactDataTypeV2, family: str) -> bool:
    allowed = _RESULT_TYPE_FAMILIES.get(data_type)
    return family in allowed if allowed is not None else False


def parameter_declaration_is_bounded(spec: SqlServerTypeSpec) -> bool:
    """Parameter declarations must not use deprecated LOB or unbounded types."""

    if spec.family in DEPRECATED_LOB_FAMILIES:
        return False
    if spec.family in CHAR_FAMILIES and spec.is_max:
        return False
    return True


def live_column_compatible(
    approved_family: str,
    approved_spec: SqlServerTypeSpec,
    *,
    live_family: str | None,
    live_max_length: int,
    live_precision: int,
    live_scale: int,
) -> Literal["compatible", "typeDrift"] | None:
    """Compare one approved column type against live catalog facts.

    ``None`` means the live facts cannot be interpreted (alias/CLR type) and the
    caller must fail closed. Nullability is compared by the application layer.
    """

    if live_family is None:
        return None
    if live_family != approved_family:
        return "typeDrift"
    if approved_family in CHAR_FAMILIES:
        unit = 2 if approved_family in ("nchar", "nvarchar") else 1
        live_chars = live_max_length // unit
        if approved_spec.is_max:
            return "compatible"
        if approved_spec.length is None or live_chars < approved_spec.length:
            return "typeDrift"
        return "compatible"
    if approved_family in ("decimal", "numeric"):
        if live_precision < (approved_spec.precision or 0) or live_scale != (
            approved_spec.scale or 0
        ):
            return "typeDrift"
        return "compatible"
    if approved_family == "float":
        if approved_spec.precision is not None and live_precision != approved_spec.precision:
            return "typeDrift"
        return "compatible"
    if approved_family in ("datetime2", "time", "datetimeoffset"):
        if approved_spec.precision is not None and live_scale != approved_spec.precision:
            return "typeDrift"
        return "compatible"
    return "compatible"


def forbidden_object_permissions() -> tuple[str, ...]:
    return _FORBIDDEN_OBJECT_PERMISSIONS


def forbidden_roles() -> tuple[str, ...]:
    return _FORBIDDEN_ROLES
