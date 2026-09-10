"""Independent V3 project authorization and governed metadata contracts.

V3 downstream chain: ProjectBindingContextV3, GovernedMetadataSnapshotV3,
ApprovalRecordV3, ResolveMetadataRequestV3, and
ApprovalClosureValidationErrorV3.

This module is structurally incompatible with the V2 project bindings by design:
no conversion, downgrade, or field-trimming path exists in either direction.
V3 request identifiers use a 420-character limit (V2 uses 384).
V3 rule references carry catalogDigest/candidatePayloadSha256 provenance.
ResolveMetadataRequestV3 只负责结构和类型契约；后续应用服务负责解析门禁。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from release_sql_bot.domain.fact_bindings_v3 import (
    FactBindingRequestV3,
    RuleRefV3,
    V3ConsumerModel,
    V3ReportModel,
)
from release_sql_bot.domain.handoff_closure_v3 import HandoffClosureV3

_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_STABLE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


def _validate_timestamp(value: str, field_name: str) -> str:
    from datetime import datetime

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value


def _reject_duplicate_ids(field_name: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} cannot contain duplicate IDs")


def _reject_snake_case_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and "_" in key:
                raise ValueError(f"snake_case key is not accepted: {key}")
            _reject_snake_case_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_snake_case_keys(nested)


class _V3Base(V3ConsumerModel):
    """V3 consumer base: strict camelCase, extra=forbid, snake_case rejected."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_nested_snake_case_fallback(cls, value: Any) -> Any:
        _reject_snake_case_keys(value)
        return value


class ContextStatusV3(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class SnapshotStatusV3(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class IdentifierCaseSensitivityV3(StrEnum):
    SENSITIVE = "sensitive"
    INSENSITIVE = "insensitive"


class RelationKindV3(StrEnum):
    TABLE = "table"
    VIEW = "view"


class JoinTypeV3(StrEnum):
    INNER = "inner"
    LEFT = "left"


ContextStatusWire = Annotated[ContextStatusV3, Field(strict=False)]
SnapshotStatusWire = Annotated[SnapshotStatusV3, Field(strict=False)]
IdentifierCaseWire = Annotated[IdentifierCaseSensitivityV3, Field(strict=False)]
RelationKindWire = Annotated[RelationKindV3, Field(strict=False)]
JoinTypeWire = Annotated[JoinTypeV3, Field(strict=False)]


class ProjectRefV3(_V3Base):
    project_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=160)
    project_version: int = Field(ge=1)


class ContextRefV3(_V3Base):
    context_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    context_version: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class MetadataSnapshotRefV3(_V3Base):
    snapshot_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    snapshot_version: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class ApprovalRefV3(_V3Base):
    approval_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    policy_version: str = Field(pattern=_STABLE_ID_PATTERN, max_length=160)
    approved_at: str = Field(min_length=1, max_length=80)

    @field_validator("approved_at")
    @classmethod
    def validate_approved_at(cls, value: str) -> str:
        return _validate_timestamp(value, "approvedAt")


class PhysicalIdentifierModel(_V3Base):
    """Shared validation for exact SQL Server identifier components."""

    @field_validator("schema_name", "relation_name", "column_name", check_fields=False)
    @classmethod
    def validate_identifier_component(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        if value != value.strip() or not value:
            raise ValueError("physical identifiers cannot be empty or padded")
        if any(token in value for token in ("*", ".", "#", "@", "[", "]")):
            raise ValueError("physical identifiers must be exact non-temporary components")
        return value


class PhysicalColumnRefV3(PhysicalIdentifierModel):
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)
    column_name: str = Field(min_length=1, max_length=128)


class GovernedColumnV3(PhysicalIdentifierModel):
    column_name: str = Field(min_length=1, max_length=128)
    sql_type: str = Field(min_length=1, max_length=120)
    nullable: bool

    @field_validator("sql_type")
    @classmethod
    def reject_non_scalar_sql_type(cls, value: str) -> str:
        if value != value.strip() or any(token in value for token in ("*", ";", "--", "/*")):
            raise ValueError("sqlType must be one exact scalar type declaration")
        return value


class GovernedRelationV3(PhysicalIdentifierModel):
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)
    relation_kind: RelationKindWire
    columns: list[GovernedColumnV3] = Field(min_length=1)


class GovernedRelationshipV3(_V3Base):
    relationship_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    left_column: PhysicalColumnRefV3
    right_column: PhysicalColumnRefV3

    @model_validator(mode="after")
    def reject_identical_endpoints(self) -> GovernedRelationshipV3:
        if self.left_column == self.right_column:
            raise ValueError("relationship endpoints must be different")
        return self


class GovernedSourceRefV3(_V3Base):
    source_kind: str = Field(pattern=_STABLE_ID_PATTERN, max_length=120)
    artifact_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    artifact_version: str = Field(pattern=_STABLE_ID_PATTERN, max_length=160)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class GovernedMetadataSnapshotV3(_V3Base):
    schema_version: Literal["1.0.0"]
    snapshot_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    snapshot_version: int = Field(ge=1)
    status: SnapshotStatusWire
    dialect: Literal["sqlserver"]
    identifier_case_sensitivity: IdentifierCaseWire
    captured_at: str = Field(min_length=1, max_length=80)
    source_ref: GovernedSourceRefV3
    relations: list[GovernedRelationV3] = Field(min_length=1)
    relationships: list[GovernedRelationshipV3]
    approval_ref: ApprovalRefV3
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: str) -> str:
        return _validate_timestamp(value, "capturedAt")

    @model_validator(mode="after")
    def reject_duplicate_relationship_ids(self) -> GovernedMetadataSnapshotV3:
        _reject_duplicate_ids(
            "relationships.relationshipId",
            [item.relationship_id for item in self.relationships],
        )
        return self


class RelationGrantV3(PhysicalIdentifierModel):
    grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)
    access: Literal["read"]


class ColumnGrantV3(PhysicalIdentifierModel):
    grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    relation_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    column_name: str = Field(min_length=1, max_length=128)


class FieldBindingAuthorizationV3(_V3Base):
    authorization_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    request_id: str = Field(min_length=3, max_length=420)
    field_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    role: Literal["value", "entityKey", "filter", "groupBy", "time"]
    column_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)


class EntityKeyAuthorizationV3(_V3Base):
    authorization_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    request_id: str = Field(min_length=3, max_length=420)
    parameter_name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    field_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    column_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)


class JoinGrantV3(_V3Base):
    grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    left_column_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    right_column_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    join_type: JoinTypeWire

    @model_validator(mode="after")
    def reject_self_join_grant(self) -> JoinGrantV3:
        if self.left_column_grant_id == self.right_column_grant_id:
            raise ValueError("join grant endpoints must be different column grants")
        return self


class ProjectBindingContextV3(_V3Base):
    schema_version: Literal["1.0.0"]
    context_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    context_version: int = Field(ge=1)
    status: ContextStatusWire
    project_ref: ProjectRefV3
    rule_ref: RuleRefV3
    request_ids: list[Annotated[str, Field(min_length=3, max_length=420)]] = Field(min_length=1)
    metadata_snapshot_ref: MetadataSnapshotRefV3
    authorization_policy_version: str = Field(pattern=_STABLE_ID_PATTERN, max_length=160)
    relation_grants: list[RelationGrantV3] = Field(min_length=1)
    column_grants: list[ColumnGrantV3] = Field(min_length=1)
    field_binding_authorizations: list[FieldBindingAuthorizationV3] = Field(min_length=1)
    entity_key_authorizations: list[EntityKeyAuthorizationV3]
    join_grants: list[JoinGrantV3]
    approval_ref: ApprovalRefV3
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def reject_duplicate_stable_ids(self) -> ProjectBindingContextV3:
        _reject_duplicate_ids("requestIds", self.request_ids)
        for field_name, values in (
            ("relationGrants.grantId", [item.grant_id for item in self.relation_grants]),
            ("columnGrants.grantId", [item.grant_id for item in self.column_grants]),
            (
                "fieldBindingAuthorizations.authorizationId",
                [item.authorization_id for item in self.field_binding_authorizations],
            ),
            (
                "entityKeyAuthorizations.authorizationId",
                [item.authorization_id for item in self.entity_key_authorizations],
            ),
            ("joinGrants.grantId", [item.grant_id for item in self.join_grants]),
        ):
            _reject_duplicate_ids(field_name, values)
        # Composite-key uniqueness for authorization identities
        fb_keys = [
            (item.request_id, item.field_id, item.role)
            for item in self.field_binding_authorizations
        ]
        if len(fb_keys) != len(set(fb_keys)):
            raise ValueError(
                "fieldBindingAuthorizations must have unique (requestId, fieldId, role)"
            )
        ek_keys = [
            (item.request_id, item.parameter_name, item.field_id)
            for item in self.entity_key_authorizations
        ]
        if len(ek_keys) != len(set(ek_keys)):
            raise ValueError(
                "entityKeyAuthorizations must have unique (requestId, parameterName, fieldId)"
            )
        return self


class ApprovalRecordV3(_V3Base):
    schema_version: Literal["1.0.0"]
    approval_id: str = Field(
        pattern=_STABLE_ID_PATTERN,
        max_length=200,
    )
    context_ref: ContextRefV3
    snapshot_ref: MetadataSnapshotRefV3
    policy_version: str = Field(pattern=_STABLE_ID_PATTERN, max_length=160)
    actor_ref: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    approved_at: str = Field(min_length=1, max_length=80)
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("approved_at")
    @classmethod
    def validate_approved_at(cls, value: str) -> str:
        return _validate_timestamp(value, "approvedAt")


_APPROVAL_CLOSURE_CODES: frozenset[str] = frozenset(
    {
        "APPROVAL_ID_MISMATCH",
        "APPROVAL_POLICY_MISMATCH",
        "APPROVAL_TIME_MISMATCH",
        "APPROVAL_CONTEXT_REF_MISMATCH",
        "APPROVAL_SNAPSHOT_REF_MISMATCH",
        "APPROVAL_CONTENT_HASH_MISMATCH",
        "APPROVAL_CONTEXT_NOT_APPROVED",
        "APPROVAL_SNAPSHOT_NOT_APPROVED",
        "APPROVAL_SNAPSHOT_BINDING_MISMATCH",
    }
)


class ApprovalClosureValidationErrorV3(Exception):
    """Stable neutral error for V3 approval closure validation.

    Carries only the issue code from DEV §3.5. Never carries raw IDs,
    hashes, private objects, or original payloads.
    """

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in _APPROVAL_CLOSURE_CODES:
            raise ValueError("unknown approval-closure issue code")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class ResolveMetadataRequestV3(_V3Base):
    """V3 metadata-resolution request contract (M2 第三子任务).

    Pure structural and type contract. Carries the handoff closure,
    binding request, project context, metadata snapshot and approval
    record for subsequent offline resolution.

    M1/M2 职责边界：本类只负责结构和类型契约。不在 Pydantic
    validator 中执行：
    - payloadSha256 重算；
    - Schema 来源核验；
    - closure 与 bindingRequest 跨字段一致性；
    - context/snapshot/approval 九组校验；
    - context、snapshot 自哈希校验；
    - projectRef、ruleRef、requestId 范围一致性；
    - relation/column/grant 解析；
    - usage 摘要重算。

    这些检查属于后续 resolve_metadata_v3 应用服务。
    因此只要各自结构合法，本契约必须允许构造；后续解析器负责阻断。

    不构成仓储真实性证明；不证明真实 batch 已验证。
    """

    schema_version: Literal["1.0.0"]
    project_ref: ProjectRefV3
    handoff_closure: HandoffClosureV3
    binding_request: FactBindingRequestV3
    project_context: ProjectBindingContextV3
    metadata_snapshot: GovernedMetadataSnapshotV3
    approval_record: ApprovalRecordV3


# ---------------------------------------------------------------------------
# BindingResolutionReportV3 and nested output contracts (M2 第四子任务)
# ---------------------------------------------------------------------------


class ResolutionStatusV3(StrEnum):
    BLOCKED = "blocked"
    METADATA_RESOLVED = "metadataResolved"


ResolutionStatusWire = Annotated[ResolutionStatusV3, Field(strict=False)]


class ResolvedFieldV3(_V3Base):
    field_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    role: Literal["value", "entityKey", "filter", "groupBy", "time"]
    authorization_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    column_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)
    column_name: str = Field(min_length=1, max_length=128)
    evidence_ids: list[str] = Field(min_length=1)


class ResolvedEntityKeyV3(_V3Base):
    parameter_name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    field_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    authorization_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    column_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)
    column_name: str = Field(min_length=1, max_length=128)
    evidence_ids: list[str] = Field(min_length=1)


class ResolvedFilterV3(_V3Base):
    filter_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    field_id: str = Field(min_length=1, max_length=160)
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)
    column_name: str = Field(min_length=1, max_length=128)
    evidence_ids: list[str] = Field(min_length=1)


class ResolvedAggregationV3(_V3Base):
    mode: Literal["none", "precomputed", "compute", "exists"]
    function: str | None = Field(default=None, max_length=80)
    input_field_ids: list[str]
    group_by_field_ids: list[str]
    distinct: bool | None = None
    evidence_ids: list[str] = Field(min_length=1)


class ResolvedTimeRangeV3(_V3Base):
    mode: Literal["none", "asOf", "between"]
    time_field_id: str | None = Field(default=None, max_length=160)
    time_schema_name: str | None = Field(default=None, max_length=128)
    time_relation_name: str | None = Field(default=None, max_length=128)
    time_column_name: str | None = Field(default=None, max_length=128)
    evidence_ids: list[str] = Field(min_length=1)


class ResolvedJoinV3(_V3Base):
    join_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    left_schema_name: str = Field(min_length=1, max_length=128)
    left_relation_name: str = Field(min_length=1, max_length=128)
    left_column_name: str = Field(min_length=1, max_length=128)
    right_schema_name: str = Field(min_length=1, max_length=128)
    right_relation_name: str = Field(min_length=1, max_length=128)
    right_column_name: str = Field(min_length=1, max_length=128)
    join_type: JoinTypeWire
    evidence_ids: list[str] = Field(min_length=1)


class HandoffRefsV3(_V3Base):
    batch_sha256: str = Field(pattern=_SHA256_PATTERN)
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_schema_id: str = Field(min_length=1, max_length=200)
    contract_schema_sha256: str = Field(pattern=_SHA256_PATTERN)


class ResolutionHashesV3(_V3Base):
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    context_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)


class MetadataResolutionIssueOwnerV3(StrEnum):
    BUSINESS_RULE_REVIEW = "businessRuleReview"
    METADATA_REVIEW = "metadataReview"
    SQL_BOT = "sqlBot"


MetadataOwnerWire = Annotated[MetadataResolutionIssueOwnerV3, Field(strict=False)]


class MetadataResolutionIssueV3(_V3Base):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    owner: MetadataOwnerWire
    impact: Literal["blocker", "warning"]
    message: str = Field(min_length=1, max_length=2_000)


def _validate_report_consistency(value: Any) -> Any:
    """Post-build validation for report status/output consistency.

    When status=blocked:
    - four list fields must be empty
    - resolvedAggregation must be None
    - resolvedTimeRange must be None
    - at least one blocker issue is required
    - executable is always False (enforced by Literal)

    Explicit rejection: even mode="none" objects are non-None resolution
    results and must be rejected, not silently cleared or repaired.
    """
    if not isinstance(value, BindingResolutionReportV3):
        return value
    status = value.status
    issues = value.issues
    blockers = [i for i in issues if i.impact == "blocker"]
    if status is ResolutionStatusV3.BLOCKED:
        if not blockers:
            raise ValueError("blocked report must contain at least one blocker issue")
        if value.resolved_fields or value.resolved_entity_keys:
            raise ValueError("blocked report must not carry resolved field/entity-key output")
        if value.resolved_filters:
            raise ValueError("blocked report must not carry resolved filters")
        if value.resolved_joins:
            raise ValueError("blocked report must not carry resolved joins")
        if value.resolved_aggregation is not None:
            raise ValueError("blocked report must not carry resolved aggregation")
        if value.resolved_time_range is not None:
            raise ValueError("blocked report must not carry resolved time range")
    elif blockers:
        raise ValueError("metadataResolved report must not contain blocker issues")
    return value


class BindingResolutionReportV3(V3ReportModel):
    """V3 metadata-resolution output report (M2 第四子任务).

    Defines the output contract only. Does NOT compute hashes or
    perform resolution. Future application service must independently
    recompute and verify.

    Internal consistency:
    - blocked requires at least one blocker issue
    - blocked must not carry resolvable output fields
    - metadataResolved must not contain blocker issues
    - metadataResolved must carry complete references and summaries
    - executable is always false

    Input format constraints (DEV §5.4 补全):
    - strict=True: 禁止类型强转（如 tuple→list、int→str），确保
      报告输入格式精确符合契约，避免静默规范化；
    - str_strip_whitespace=False: 禁止自动去除字符串两端空白，
      确保 SHA-256 等精确字段的格式约束有效。
    仅在本类覆盖，不影响共享 V3ReportModel 及既有 intake 报告。
    """

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=False,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_nested_snake_case_fallback(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for key in value:
                if isinstance(key, str) and "_" in key:
                    raise ValueError(f"snake_case key is not accepted: {key}")
        return value

    schema_version: Literal["1.0.0"]
    status: ResolutionStatusWire
    executable: Literal[False] = False
    request_ref: RequestRefV3
    project_ref: ProjectRefV3
    context_ref: ContextRefV3
    snapshot_ref: MetadataSnapshotRefV3
    handoff_refs: HandoffRefsV3
    resolution_hashes: ResolutionHashesV3
    resolved_fields: list[ResolvedFieldV3] = Field(default_factory=list)
    resolved_entity_keys: list[ResolvedEntityKeyV3] = Field(default_factory=list)
    resolved_filters: list[ResolvedFilterV3] = Field(default_factory=list)
    resolved_aggregation: ResolvedAggregationV3 | None = None
    resolved_time_range: ResolvedTimeRangeV3 | None = None
    resolved_joins: list[ResolvedJoinV3] = Field(default_factory=list)
    usage_traceability_sha256: str = Field(pattern=_SHA256_PATTERN)
    issues: list[MetadataResolutionIssueV3] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_report_consistency(self) -> BindingResolutionReportV3:
        _validate_report_consistency(self)
        return self


class RequestRefV3(_V3Base):
    request_id: str = Field(min_length=3, max_length=420)
    rule_ref: RuleRefV3
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
