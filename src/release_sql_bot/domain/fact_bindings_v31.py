"""Independent FactBindingRequest 3.1.0 consumer.

Mirrors the frozen upstream Schema without importing RuleReader. Nested 3.0.0
field shapes that did not change are reused from the V3 consumer; this is not a
payload conversion path. V3 FactBindingRequestV3 still rejects 3.1.0 documents.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from release_sql_bot.domain.fact_bindings_v3 import (
    BindableFactV3,
    EvidenceV3,
    FactDataTypeV3,
    FactDataTypeWire,
    FactExampleV3,
    FactUsageV3,
    MappingCandidateV3,
    MappingStatusV3,
    NullPolicyWire,
    QueryRequirementsV3,
    UncertaintyV3,
    V3ConsumerModel,
    ValueSourceKindV3,
)

_SHA256_PATTERN = r"^[a-f0-9]{64}$"


class ResultRequirementV31(V3ConsumerModel):
    column_name: Literal["fact_value"]
    data_type: FactDataTypeWire
    cardinality: Literal["scalar", "set"]
    nullable: bool
    null_policy: NullPolicyWire
    unit: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_cardinality(self) -> ResultRequirementV31:
        if self.cardinality == "set" and self.data_type is not FactDataTypeV3.LIST:
            raise ValueError("set cardinality requires list dataType")
        if self.cardinality == "scalar" and self.data_type is FactDataTypeV3.LIST:
            raise ValueError("scalar cardinality cannot use list dataType")
        return self


class QueryRequirementsV31(QueryRequirementsV3):
    result: ResultRequirementV31  # type: ignore[assignment]


class RuleRefV31(V3ConsumerModel):
    rule_set_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    rule_version: str = Field(min_length=1, max_length=260)
    schema_version: Literal["3.1.0"]
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    parse_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    catalog_digest: str = Field(pattern=_SHA256_PATTERN)
    candidate_payload_sha256: str = Field(pattern=_SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_dual_source_hashes(self) -> RuleRefV31:
        if self.source_sha256 == self.parse_input_sha256:
            raise ValueError("source file hash must not equal parse input hash")
        return self


class ProvenanceV31(V3ConsumerModel):
    source_name: str = Field(min_length=1, max_length=255)
    relative_path: str | None = Field(default=None, max_length=1_000)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    parse_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    source_file_byte_length: int = Field(ge=1)
    parse_input_character_count: int = Field(ge=1)
    extractor_version: str = Field(min_length=1, max_length=80)
    extracted_sections: list[str] = Field(min_length=1)
    parser_version: str = Field(min_length=1, max_length=80)
    prompt_version: str = Field(min_length=1, max_length=120)
    provider: Literal["reviewed_import"]
    model: str = Field(min_length=1, max_length=160)


class FactBindingRequestV31(V3ConsumerModel):
    contract_version: Literal["3.1.0"]
    status: Literal["candidate"]
    executable: Literal[False]
    request_id: str = Field(min_length=3, max_length=420)
    rule_ref: RuleRefV31
    fact: BindableFactV3
    query_requirements: QueryRequirementsV31
    usages: list[FactUsageV3] = Field(min_length=1)
    examples: list[FactExampleV3] = Field(min_length=1)
    mapping_candidate: MappingCandidateV3
    provenance: ProvenanceV31
    evidence: list[EvidenceV3] = Field(min_length=1)
    uncertainties: list[UncertaintyV3]
    target_dialect: Literal["sqlserver"]
    requires_metadata_snapshot: Literal[True]
    temp_table_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_closure(self) -> FactBindingRequestV31:
        if self.request_id != f"{self.rule_ref.rule_version}#{self.fact.fact_code}":
            raise ValueError("requestId must equal ruleVersion#factCode")
        if self.mapping_candidate.fact_code != self.fact.fact_code:
            raise ValueError("mappingCandidate factCode must match fact")
        if self.query_requirements.result.data_type is not self.fact.data_type:
            raise ValueError("result dataType must match fact")
        if self.provenance.source_sha256 != self.rule_ref.source_sha256:
            raise ValueError("provenance sourceSha256 must match ruleRef")
        if self.provenance.parse_input_sha256 != self.rule_ref.parse_input_sha256:
            raise ValueError("provenance parseInputSha256 must match ruleRef")
        if not self.rule_ref.rule_version.startswith(f"{self.rule_ref.rule_set_id}@"):
            raise ValueError("ruleVersion must belong to ruleSetId")
        if self.query_requirements.entity.grain != self.fact.grain:
            raise ValueError("query entity grain must match fact grain")
        entity_parameters = {
            parameter.name for parameter in self.fact.parameters if parameter.role == "entityKey"
        }
        if set(self.query_requirements.entity.key_parameters) != entity_parameters:
            raise ValueError("query entity keys must match fact entityKey parameters")
        result = self.query_requirements.result
        if (
            result.nullable != self.fact.nullable
            or result.null_policy is not self.fact.null_policy
            or result.unit != self.fact.unit
        ):
            raise ValueError("result null and unit contract must match fact")
        if self.cardinality_from_fact() != result.cardinality:
            raise ValueError("result cardinality must follow fact dataType")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        known = set(evidence_ids)
        references = {
            *self.query_requirements.entity.evidence_ids,
            *(item for field in self.query_requirements.fields for item in field.evidence_ids),
            *self.query_requirements.filters.evidence_ids,
            *self.query_requirements.aggregation.evidence_ids,
            *self.query_requirements.time_range.evidence_ids,
            *(item for usage in self.usages for item in usage.evidence_ids),
            *(item for example in self.examples for item in example.evidence_ids),
            *(item for uncertainty in self.uncertainties for item in uncertainty.evidence_ids),
        }
        if unknown := sorted(references - known):
            raise ValueError(f"unknown evidence references: {unknown}")
        # Agent1 FactBindingRequest 3.1.0 does not reject extra evidence rows.
        parameters = {parameter.name for parameter in self.fact.parameters}
        for item in self.query_requirements.filters.items:
            if (
                item.value is not None
                and item.value.kind is ValueSourceKindV3.PARAMETER
                and item.value.parameter_name not in parameters
            ):
                raise ValueError("filter value references an unknown fact parameter")
        return self

    def cardinality_from_fact(self) -> str:
        return "set" if self.fact.data_type is FactDataTypeV3.LIST else "scalar"

    def mapping_is_unresolved(self) -> bool:
        return self.mapping_candidate.mapping_status is MappingStatusV3.UNRESOLVED
