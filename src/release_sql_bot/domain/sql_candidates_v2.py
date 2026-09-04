"""Independent V2 SQL candidate generation and audit contracts."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

from release_sql_bot.domain.fact_bindings_v2 import (
    FactDataTypeV2,
    FactKindV2,
    NullPolicyV2,
    ReportModel,
    V2ConsumerModel,
)
from release_sql_bot.domain.project_bindings_v2 import (
    BindingResolutionReportV2,
    ResolveMetadataRequestV2,
)

_SHA256_PATTERN = r"^[a-f0-9]{64}$"

FactDataTypeWire = Annotated[FactDataTypeV2, Field(strict=False)]
NullPolicyWire = Annotated[NullPolicyV2, Field(strict=False)]
FactKindWire = Annotated[FactKindV2, Field(strict=False)]


def _reject_snake_case_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and "_" in key:
                raise ValueError(f"snake_case key is not accepted: {key}")
            _reject_snake_case_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_snake_case_keys(nested)


def _validate_identifier(value: str) -> str:
    if value != value.strip() or not value:
        raise ValueError("physical identifier must be exact and non-empty")
    if any(token in value for token in ("*", ".", "#", "@", "[", "]")):
        raise ValueError("physical identifier must be one non-temporary component")
    return value


class GenerateSqlCandidateRequestV2(V2ConsumerModel):
    schema_version: Literal["1.0.0"]
    resolution_request: ResolveMetadataRequestV2
    resolution_report: BindingResolutionReportV2

    @model_validator(mode="before")
    @classmethod
    def reject_nested_snake_case_fallback(cls, value: Any) -> Any:
        _reject_snake_case_keys(value)
        return value


class GeneratedCandidateParameterV2(V2ConsumerModel):
    name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    data_type: FactDataTypeWire
    required: bool
    source: str = Field(pattern=r"^fact\.parameters\.[a-z][A-Za-z0-9]*$", max_length=240)


class GeneratedCandidateResultV2(V2ConsumerModel):
    column_name: Literal["fact_value"]
    data_type: FactDataTypeWire
    cardinality: Literal["scalar"]
    nullable: bool
    null_policy: NullPolicyWire
    unit: str | None = Field(max_length=80)


class DeclaredPhysicalRelationV2(V2ConsumerModel):
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)

    _validate_schema = field_validator("schema_name")(_validate_identifier)
    _validate_relation = field_validator("relation_name")(_validate_identifier)


class GeneratedCandidatePayloadV2(V2ConsumerModel):
    """Strict, untrusted JSON object that a model is allowed to propose."""

    template_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=160)
    sql_template: str = Field(min_length=1, max_length=100_000)
    parameters: list[GeneratedCandidateParameterV2] = Field(max_length=100)
    result: GeneratedCandidateResultV2
    declared_objects: list[DeclaredPhysicalRelationV2] = Field(min_length=1, max_length=100)
    declared_usage_coverage: list[str] = Field(min_length=1, max_length=1_000)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def reject_duplicate_declarations(self) -> GeneratedCandidatePayloadV2:
        parameter_names = [item.name for item in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("parameters cannot contain duplicate names")
        object_keys = [(item.schema_name, item.relation_name) for item in self.declared_objects]
        if len(object_keys) != len(set(object_keys)):
            raise ValueError("declaredObjects cannot contain duplicates")
        if len(self.declared_usage_coverage) != len(set(self.declared_usage_coverage)):
            raise ValueError("declaredUsageCoverage cannot contain duplicates")
        return self


class CandidateRequestRefV2(ReportModel):
    request_id: str = Field(min_length=3, max_length=384)
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)


class CandidateRuleRefV2(ReportModel):
    rule_id: str
    rule_version: str
    schema_version: str
    source_sha256: str = Field(pattern=_SHA256_PATTERN)


class CandidateProjectRefV2(ReportModel):
    project_id: str
    project_version: int = Field(ge=1)


class CandidateContextRefV2(ReportModel):
    context_id: str
    context_version: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class CandidateSnapshotRefV2(ReportModel):
    snapshot_id: str
    snapshot_version: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class CandidateResolutionRefV2(ReportModel):
    report_sha256: str = Field(pattern=_SHA256_PATTERN)
    context_ref: CandidateContextRefV2
    metadata_snapshot_ref: CandidateSnapshotRefV2
    authorization_policy_version: str


class CandidateFactRefV2(ReportModel):
    fact_code: str
    fact_kind: FactKindV2
    data_type: FactDataTypeV2
    grain: str


class CandidateParameterV2(ReportModel):
    name: str
    data_type: FactDataTypeV2
    required: bool
    source: str


class CandidateResultV2(ReportModel):
    column_name: Literal["fact_value"] = "fact_value"
    data_type: FactDataTypeV2
    cardinality: Literal["scalar"] = "scalar"
    nullable: bool
    null_policy: NullPolicyV2
    unit: str | None = None


class CandidateDeclaredRelationV2(ReportModel):
    schema_name: str
    relation_name: str


class CandidateProvenanceV2(ReportModel):
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    response_model: str = Field(min_length=1, max_length=160)
    prompt_version: Literal[
        "sqlserver-fact-candidate-v2",
        "sqlserver-fact-candidate-v2.1",
    ]
    provider_request_id: str = Field(min_length=1, max_length=240)
    system_fingerprint: str | None = Field(default=None, max_length=240)
    attempt_count: int = Field(ge=1, le=6)
    max_tokens: int = Field(ge=1)
    response_format: Literal["json_object"] = "json_object"


class SqlTemplateCandidateV2(ReportModel):
    schema_version: Literal["2.0.0"] = "2.0.0"
    template_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=160)
    status: Literal["candidate"] = "candidate"
    executable: Literal[False] = False
    review_status: Literal["pending"] = "pending"
    rule_ref: CandidateRuleRefV2
    request_ref: CandidateRequestRefV2
    project_ref: CandidateProjectRefV2
    resolution_ref: CandidateResolutionRefV2
    generation_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    fact_ref: CandidateFactRefV2
    dialect: Literal["sqlserver"] = "sqlserver"
    sql_template: str = Field(min_length=1, max_length=100_000)
    parameters: tuple[CandidateParameterV2, ...]
    result: CandidateResultV2
    declared_objects: tuple[CandidateDeclaredRelationV2, ...]
    declared_usage_coverage: tuple[str, ...]
    assumptions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    provenance: CandidateProvenanceV2
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def reject_duplicate_declarations(self) -> SqlTemplateCandidateV2:
        parameter_names = [item.name for item in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("parameters cannot contain duplicate names")
        object_keys = [(item.schema_name, item.relation_name) for item in self.declared_objects]
        if len(object_keys) != len(set(object_keys)):
            raise ValueError("declaredObjects cannot contain duplicates")
        if len(self.declared_usage_coverage) != len(set(self.declared_usage_coverage)):
            raise ValueError("declaredUsageCoverage cannot contain duplicates")
        return self
