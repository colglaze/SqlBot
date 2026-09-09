"""Independent V3 project authorization and governed metadata contracts.

V3 downstream chain: ProjectBindingContextV3, GovernedMetadataSnapshotV3,
ApprovalRecordV3, and ApprovalClosureValidationErrorV3.

This module is structurally incompatible with the V2 project bindings by design:
no conversion, downgrade, or field-trimming path exists in either direction.
V3 request identifiers use a 420-character limit (V2 uses 384).
V3 rule references carry catalogDigest/candidatePayloadSha256 provenance.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from release_sql_bot.domain.fact_bindings_v3 import RuleRefV3, V3ConsumerModel

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
