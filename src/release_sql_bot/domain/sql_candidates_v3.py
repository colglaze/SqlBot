"""Independent V3 SQL candidate generation and audit contracts.

V3 downstream chain: GenerateSqlCandidateRequestV3,
GeneratedCandidatePayloadV3, SqlTemplateCandidateV3.

This module is structurally incompatible with the V2 candidate contracts by
design: no conversion, downgrade, or field-trimming path exists in either
direction. V3 candidates carry schemaVersion="3.0.0" (candidate contract
version, aligned with the generation pipeline generation, not mechanical
inheritance). V3 usage coverage preserves the full six-tuple per usage
(stage/ruleCode/priority/conditionId/conditionPath/outcome); the same
conditionId may appear in multiple usages and must never be merged.

M3 first delivery scope: source facts only, aggregation.mode=none,
timeRange.mode=none, filters.items empty, fields limited to factValue and
entity keys, all physical columns in one authorized relation, no JOINs,
parameters limited to declared entity-key parameters. Requests outside this
scope are rejected with a neutral scope error before any provider call.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from release_sql_bot.domain.fact_bindings_v3 import (
    FactDataTypeV3,
    FactKindV3,
    NullPolicyV3,
    RuleOutcomeV3,
    RuleStageNameV3,
    V3ConsumerModel,
    V3ReportModel,
)
from release_sql_bot.domain.project_bindings_v3 import (
    BindingResolutionReportV3,
    ResolveMetadataRequestV3,
)

_SHA256_PATTERN = r"^[a-f0-9]{64}$"

FactDataTypeWire = Annotated[FactDataTypeV3, Field(strict=False)]
NullPolicyWire = Annotated[NullPolicyV3, Field(strict=False)]
FactKindWire = Annotated[FactKindV3, Field(strict=False)]
RuleOutcomeWire = Annotated[RuleOutcomeV3, Field(strict=False)]
RuleStageWire = Annotated[RuleStageNameV3, Field(strict=False)]


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


class GenerateSqlCandidateRequestV3(_V3Base):
    """V3 candidate-generation request contract (M3).

    Carries the complete V3 resolution request and the corresponding
    resolution report. The generation service must independently re-run
    the full M2 resolution and compare against the carried report.

    This contract is structural + type only; it does NOT perform
    repository attestation, approval-truthiness verification, or
    resolution recomputation. Those checks belong to the application
    service before any provider call.
    """

    schema_version: Literal["1.0.0"]
    resolution_request: ResolveMetadataRequestV3
    resolution_report: BindingResolutionReportV3


# ---------------------------------------------------------------------------
# GeneratedCandidatePayloadV3 — untrusted model output contract
# ---------------------------------------------------------------------------


class GeneratedCandidateParameterV3(_V3Base):
    name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    data_type: FactDataTypeWire
    required: bool
    source: str = Field(pattern=r"^fact\.parameters\.[a-z][A-Za-z0-9]*$", max_length=240)


class GeneratedCandidateResultV3(_V3Base):
    column_name: Literal["fact_value"]
    data_type: FactDataTypeWire
    cardinality: Literal["scalar"]
    nullable: bool
    null_policy: NullPolicyWire
    unit: str | None = Field(default=None, max_length=80)


class DeclaredPhysicalRelationV3(_V3Base):
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)


class DeclaredUsageEntryV3(_V3Base):
    """Full six-tuple usage coverage entry.

    The same conditionId may appear in multiple entries with different
    stage/ruleCode/priority/conditionPath/outcome; all must be preserved.
    """

    stage: RuleStageWire
    rule_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    priority: int = Field(ge=1)
    condition_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=120)
    condition_path: str = Field(pattern=r"^/", max_length=1_000)
    outcome: RuleOutcomeWire


class GeneratedCandidatePayloadV3(_V3Base):
    """Strict, untrusted JSON object that a model is allowed to propose."""

    template_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=160)
    sql_template: str = Field(min_length=1, max_length=100_000)
    parameters: list[GeneratedCandidateParameterV3] = Field(max_length=100)
    result: GeneratedCandidateResultV3
    declared_objects: list[DeclaredPhysicalRelationV3] = Field(min_length=1, max_length=100)
    declared_usage_coverage: list[DeclaredUsageEntryV3] = Field(min_length=1, max_length=1_000)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def reject_duplicate_declarations(self) -> GeneratedCandidatePayloadV3:
        parameter_names = [item.name for item in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("parameters cannot contain duplicate names")
        object_keys = [(item.schema_name, item.relation_name) for item in self.declared_objects]
        if len(object_keys) != len(set(object_keys)):
            raise ValueError("declaredObjects cannot contain duplicates")
        usage_keys = [
            (
                item.stage,
                item.rule_code,
                item.priority,
                item.condition_id,
                item.condition_path,
                item.outcome,
            )
            for item in self.declared_usage_coverage
        ]
        if len(usage_keys) != len(set(usage_keys)):
            raise ValueError("declaredUsageCoverage cannot contain duplicate six-tuples")
        return self


# ---------------------------------------------------------------------------
# SqlTemplateCandidateV3 — application-assembled audit contract
# ---------------------------------------------------------------------------


class CandidateRuleRefV3(V3ReportModel):
    rule_set_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    rule_version: str = Field(min_length=1, max_length=260)
    schema_version: Literal["3.0.0"] = "3.0.0"
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    catalog_digest: str = Field(pattern=_SHA256_PATTERN)
    candidate_payload_sha256: str = Field(pattern=_SHA256_PATTERN)


class CandidateRequestRefV3(V3ReportModel):
    request_id: str = Field(min_length=3, max_length=420)
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)


class CandidateProjectRefV3(V3ReportModel):
    project_id: str
    project_version: int = Field(ge=1)


class CandidateContextRefV3(V3ReportModel):
    context_id: str
    context_version: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class CandidateSnapshotRefV3(V3ReportModel):
    snapshot_id: str
    snapshot_version: int = Field(ge=1)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class CandidateResolutionRefV3(V3ReportModel):
    report_sha256: str = Field(pattern=_SHA256_PATTERN)
    context_ref: CandidateContextRefV3
    metadata_snapshot_ref: CandidateSnapshotRefV3
    authorization_policy_version: str


class CandidateHandoffRefsV3(V3ReportModel):
    batch_sha256: str = Field(pattern=_SHA256_PATTERN)
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_schema_id: str = Field(min_length=1, max_length=200)
    contract_schema_sha256: str = Field(pattern=_SHA256_PATTERN)


class CandidateFactRefV3(V3ReportModel):
    fact_code: str
    fact_kind: FactKindWire
    data_type: FactDataTypeWire
    grain: str


class CandidateParameterV3(V3ReportModel):
    name: str
    data_type: FactDataTypeWire
    required: bool
    source: str


class CandidateResultV3(V3ReportModel):
    column_name: Literal["fact_value"] = "fact_value"
    data_type: FactDataTypeWire
    cardinality: Literal["scalar"] = "scalar"
    nullable: bool
    null_policy: NullPolicyV3
    unit: str | None = None


class CandidateDeclaredRelationV3(V3ReportModel):
    schema_name: str
    relation_name: str


class CandidateUsageEntryV3(V3ReportModel):
    stage: RuleStageWire
    rule_code: str
    priority: int = Field(ge=1)
    condition_id: str
    condition_path: str
    outcome: RuleOutcomeWire


class CandidateProvenanceV3(V3ReportModel):
    provider: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    response_model: str = Field(min_length=1, max_length=160)
    prompt_version: Literal["sqlserver-fact-candidate-v3.0"] = "sqlserver-fact-candidate-v3.0"
    provider_request_id: str = Field(min_length=1, max_length=240)
    system_fingerprint: str | None = Field(default=None, max_length=240)
    attempt_count: int = Field(ge=1, le=6)
    max_tokens: int = Field(ge=1)
    response_format: Literal["json_object"] = "json_object"


class SqlTemplateCandidateV3(V3ReportModel):
    """V3 SQL template candidate audit contract (application-assembled).

    Fixed status/audit fields are assembled by the application; the model
    may NOT provide or override them. contentSha256 uses the canonical
    self-hash excluding the contentSha256 field itself.

    M3 first-delivery scope: source facts, no aggregation, no time range,
    no filters, no joins, single authorized relation.
    """

    schema_version: Literal["3.0.0"] = "3.0.0"
    template_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=160)
    status: Literal["candidate"] = "candidate"
    executable: Literal[False] = False
    review_status: Literal["pending"] = "pending"
    rule_ref: CandidateRuleRefV3
    request_ref: CandidateRequestRefV3
    project_ref: CandidateProjectRefV3
    resolution_ref: CandidateResolutionRefV3
    handoff_refs: CandidateHandoffRefsV3
    generation_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    fact_ref: CandidateFactRefV3
    dialect: Literal["sqlserver"] = "sqlserver"
    sql_template: str = Field(min_length=1, max_length=100_000)
    parameters: tuple[CandidateParameterV3, ...]
    result: CandidateResultV3
    declared_objects: tuple[CandidateDeclaredRelationV3, ...]
    declared_usage_coverage: tuple[CandidateUsageEntryV3, ...]
    usage_traceability_sha256: str = Field(pattern=_SHA256_PATTERN)
    assumptions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    provenance: CandidateProvenanceV3
    content_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def reject_duplicate_declarations(self) -> SqlTemplateCandidateV3:
        parameter_names = [item.name for item in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("parameters cannot contain duplicate names")
        object_keys = [(item.schema_name, item.relation_name) for item in self.declared_objects]
        if len(object_keys) != len(set(object_keys)):
            raise ValueError("declaredObjects cannot contain duplicates")
        usage_keys = [
            (
                item.stage,
                item.rule_code,
                item.priority,
                item.condition_id,
                item.condition_path,
                item.outcome,
            )
            for item in self.declared_usage_coverage
        ]
        if len(usage_keys) != len(set(usage_keys)):
            raise ValueError("declaredUsageCoverage cannot contain duplicate six-tuples")
        return self
