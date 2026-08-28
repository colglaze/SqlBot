"""Independent V2 project authorization and governed metadata contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from release_sql_bot.domain.fact_bindings_v2 import (
    BindingGapIssue,
    BindingGapOwner,
    BindingGapReport,
    BindingRuleRefV2,
    BindingUncertaintyV2,
    FactBindingRequestV2,
    FieldRoleV2,
    ReportModel,
    V2ConsumerModel,
)

_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_STABLE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


def _validate_timestamp(value: str, field_name: str) -> str:
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


class ContextStatusV2(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class SnapshotStatusV2(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class IdentifierCaseSensitivityV2(StrEnum):
    SENSITIVE = "sensitive"
    INSENSITIVE = "insensitive"


class RelationKindV2(StrEnum):
    TABLE = "table"
    VIEW = "view"


class JoinTypeV2(StrEnum):
    INNER = "inner"
    LEFT = "left"


ContextStatusWire = Annotated[ContextStatusV2, Field(strict=False)]
SnapshotStatusWire = Annotated[SnapshotStatusV2, Field(strict=False)]
IdentifierCaseSensitivityWire = Annotated[
    IdentifierCaseSensitivityV2,
    Field(strict=False),
]
RelationKindWire = Annotated[RelationKindV2, Field(strict=False)]
JoinTypeWire = Annotated[JoinTypeV2, Field(strict=False)]
FieldRoleWire = Annotated[FieldRoleV2, Field(strict=False)]


class ProjectRefV2(V2ConsumerModel):
    project_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=160)
    project_version: int = Field(ge=1)


class ContextRefV2(V2ConsumerModel):
    context_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    context_version: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class MetadataSnapshotRefV2(V2ConsumerModel):
    snapshot_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    snapshot_version: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class ApprovalRefV2(V2ConsumerModel):
    approval_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    policy_version: str = Field(pattern=_STABLE_ID_PATTERN, max_length=160)
    approved_at: str = Field(min_length=1, max_length=80)

    @field_validator("approved_at")
    @classmethod
    def validate_approved_at(cls, value: str) -> str:
        return _validate_timestamp(value, "approvedAt")


class PhysicalIdentifierModel(V2ConsumerModel):
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


class PhysicalColumnRefV2(PhysicalIdentifierModel):
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)
    column_name: str = Field(min_length=1, max_length=128)


class RelationGrantV2(PhysicalIdentifierModel):
    grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)
    access: Literal["read"]


class ColumnGrantV2(PhysicalIdentifierModel):
    grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    relation_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    column_name: str = Field(min_length=1, max_length=128)


class FieldBindingAuthorizationV2(V2ConsumerModel):
    authorization_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    request_id: str = Field(min_length=3, max_length=384)
    field_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    role: FieldRoleWire
    column_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)


class EntityKeyAuthorizationV2(V2ConsumerModel):
    authorization_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    request_id: str = Field(min_length=3, max_length=384)
    parameter_name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    field_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    column_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)


class JoinGrantV2(V2ConsumerModel):
    grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    left_column_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    right_column_grant_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    join_type: JoinTypeWire

    @model_validator(mode="after")
    def reject_self_join_grant(self) -> JoinGrantV2:
        if self.left_column_grant_id == self.right_column_grant_id:
            raise ValueError("join grant endpoints must be different column grants")
        return self


class ProjectBindingContextV2(V2ConsumerModel):
    schema_version: Literal["1.0.0"]
    context_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    context_version: int = Field(ge=1)
    status: ContextStatusWire
    project_ref: ProjectRefV2
    rule_ref: BindingRuleRefV2
    request_ids: list[str] = Field(min_length=1)
    metadata_snapshot_ref: MetadataSnapshotRefV2
    authorization_policy_version: str = Field(pattern=_STABLE_ID_PATTERN, max_length=160)
    relation_grants: list[RelationGrantV2] = Field(min_length=1)
    column_grants: list[ColumnGrantV2] = Field(min_length=1)
    field_binding_authorizations: list[FieldBindingAuthorizationV2] = Field(min_length=1)
    entity_key_authorizations: list[EntityKeyAuthorizationV2]
    join_grants: list[JoinGrantV2]
    approval_ref: ApprovalRefV2
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def reject_duplicate_stable_ids(self) -> ProjectBindingContextV2:
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
        return self


class GovernedSourceRefV2(V2ConsumerModel):
    source_kind: str = Field(pattern=_STABLE_ID_PATTERN, max_length=120)
    artifact_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    artifact_version: str = Field(pattern=_STABLE_ID_PATTERN, max_length=160)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class GovernedColumnV2(PhysicalIdentifierModel):
    column_name: str = Field(min_length=1, max_length=128)
    sql_type: str = Field(min_length=1, max_length=120)
    nullable: bool

    @field_validator("sql_type")
    @classmethod
    def reject_non_scalar_sql_type(cls, value: str) -> str:
        if value != value.strip() or any(token in value for token in ("*", ";", "--", "/*")):
            raise ValueError("sqlType must be one exact scalar type declaration")
        return value


class GovernedRelationV2(PhysicalIdentifierModel):
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)
    relation_kind: RelationKindWire
    columns: list[GovernedColumnV2] = Field(min_length=1)


class GovernedRelationshipV2(V2ConsumerModel):
    relationship_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    left_column: PhysicalColumnRefV2
    right_column: PhysicalColumnRefV2

    @model_validator(mode="after")
    def reject_identical_endpoints(self) -> GovernedRelationshipV2:
        if self.left_column == self.right_column:
            raise ValueError("relationship endpoints must be different")
        return self


class GovernedMetadataSnapshotV2(V2ConsumerModel):
    schema_version: Literal["1.0.0"]
    snapshot_id: str = Field(pattern=_STABLE_ID_PATTERN, max_length=200)
    snapshot_version: int = Field(ge=1)
    status: SnapshotStatusWire
    dialect: Literal["sqlserver"]
    identifier_case_sensitivity: IdentifierCaseSensitivityWire
    captured_at: str = Field(min_length=1, max_length=80)
    source_ref: GovernedSourceRefV2
    relations: list[GovernedRelationV2] = Field(min_length=1)
    relationships: list[GovernedRelationshipV2]
    approval_ref: ApprovalRefV2
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("captured_at")
    @classmethod
    def validate_captured_at(cls, value: str) -> str:
        return _validate_timestamp(value, "capturedAt")

    @model_validator(mode="after")
    def reject_duplicate_relationship_ids(self) -> GovernedMetadataSnapshotV2:
        _reject_duplicate_ids(
            "relationships.relationshipId",
            [item.relationship_id for item in self.relationships],
        )
        return self


def _reject_snake_case_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and "_" in key:
                raise ValueError(f"snake_case key is not accepted: {key}")
            _reject_snake_case_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_snake_case_keys(nested)


class ResolveMetadataRequestV2(V2ConsumerModel):
    schema_version: Literal["1.0.0"]
    project_ref: ProjectRefV2
    binding_request: FactBindingRequestV2
    binding_gap_report: BindingGapReport
    project_context: ProjectBindingContextV2
    metadata_snapshot: GovernedMetadataSnapshotV2

    @model_validator(mode="before")
    @classmethod
    def reject_nested_snake_case_fallback(cls, value: Any) -> Any:
        _reject_snake_case_keys(value)
        return value


class BindingResolutionHashes(ReportModel):
    request_sha256: str = Field(pattern=_SHA256_PATTERN)
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    rule_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    gap_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    context_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)


class BindingResolutionIssue(ReportModel):
    code: str = Field(min_length=1, max_length=120)
    owner: BindingGapOwner
    field_path: str = Field(min_length=1, max_length=1_000)
    message: str = Field(min_length=1, max_length=2_000)
    normalized_identifier: str | None = Field(default=None, max_length=500)
    evidence_ids: tuple[str, ...] = ()
    uncertainty_id: str | None = Field(default=None, max_length=200)

    @classmethod
    def from_gap_issue(cls, issue: BindingGapIssue) -> BindingResolutionIssue:
        return cls(
            code=issue.code,
            owner=issue.owner,
            field_path=issue.field_path,
            message=issue.message,
            evidence_ids=issue.evidence_ids,
            uncertainty_id=issue.uncertainty_id,
        )


class ResolvedFieldBindingV2(ReportModel):
    field_id: str
    role: FieldRoleV2
    physical_column: PhysicalColumnRefV2
    authorization_id: str
    column_grant_id: str
    evidence_ids: tuple[str, ...]


class ResolvedEntityKeyV2(ReportModel):
    parameter_name: str
    field_id: str
    physical_column: PhysicalColumnRefV2
    authorization_id: str


class ResolvedResultSourceV2(ReportModel):
    mode: Literal["column", "aggregation", "exists"]
    field_ids: tuple[str, ...]
    physical_columns: tuple[PhysicalColumnRefV2, ...]


class AuthorizedJoinV2(ReportModel):
    grant_id: str
    left_column: PhysicalColumnRefV2
    right_column: PhysicalColumnRefV2
    join_type: JoinTypeV2


class CandidateEvidenceDispositionV2(ReportModel):
    evidence_path: str
    disposition: Literal["consistent", "conflict", "notUsed"]
    resolved_column: PhysicalColumnRefV2 | None = None


class BindingResolutionReportV2(ReportModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    status: Literal["blocked", "metadataResolved"]
    executable: Literal[False] = False
    request_id: str = Field(min_length=3, max_length=384)
    project_ref: ProjectRefV2
    rule_ref: BindingRuleRefV2
    context_ref: ContextRefV2
    metadata_snapshot_ref: MetadataSnapshotRefV2
    hashes: BindingResolutionHashes
    authorization_policy_version: str
    binding_gap_report: BindingGapReport
    uncertainties: tuple[BindingUncertaintyV2, ...]
    resolved_bindings: tuple[ResolvedFieldBindingV2, ...]
    resolved_entity_keys: tuple[ResolvedEntityKeyV2, ...]
    result_source: ResolvedResultSourceV2 | None
    authorized_joins: tuple[AuthorizedJoinV2, ...]
    candidate_evidence_dispositions: tuple[CandidateEvidenceDispositionV2, ...]
    blocking_issues: tuple[BindingResolutionIssue, ...]
    warnings: tuple[BindingResolutionIssue, ...]
