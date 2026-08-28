"""Candidate-only SQL template contracts for one business fact."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from release_sql_bot.domain.fact_bindings import ContractModel, FactDataType, RuleRef


class CandidateFactRef(ContractModel):
    fact_code: str
    fact_kind: Literal["source", "aggregate", "exists"]
    data_type: FactDataType
    grain: str


class CandidateBindingRef(ContractModel):
    contract_version: str = Field(min_length=1, max_length=40)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class CandidateContextRef(ContractModel):
    context_id: str
    context_version: int
    metadata_snapshot_id: str
    metadata_snapshot_version: int
    metadata_snapshot_sha256: str


class SqlParameter(ContractModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]*$", max_length=100)
    data_type: FactDataType
    required: bool
    source: str = Field(min_length=1, max_length=240)


class SqlResultContract(ContractModel):
    column_name: Literal["fact_value"] = "fact_value"
    data_type: FactDataType
    cardinality: Literal["scalar"] = "scalar"
    nullable: bool


class CandidateProvenance(ContractModel):
    provider: Literal["deepseek"] = "deepseek"
    model: str = Field(min_length=1, max_length=160)
    response_model: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]*$", max_length=160)
    provider_request_id: str = Field(min_length=1, max_length=240)
    system_fingerprint: str | None = Field(default=None, max_length=240)
    attempt_count: int = Field(ge=1, le=6)
    max_tokens: int = Field(ge=1)
    response_format: Literal["json_object"] = "json_object"


class GeneratedCandidatePayload(ContractModel):
    """Strict untrusted payload that the model is allowed to produce."""

    template_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=160)
    sql_template: str = Field(min_length=1, max_length=100_000)
    parameters: list[SqlParameter] = Field(min_length=1, max_length=100)
    result: SqlResultContract
    allowed_objects: list[str] = Field(min_length=1, max_length=100)
    usage_coverage: list[str] = Field(min_length=1, max_length=1_000)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    warnings: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def reject_duplicate_references(self) -> GeneratedCandidatePayload:
        parameter_names = [parameter.name for parameter in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("parameters cannot contain duplicate names")
        if len(self.allowed_objects) != len(set(self.allowed_objects)):
            raise ValueError("allowedObjects cannot contain duplicates")
        if len(self.usage_coverage) != len(set(self.usage_coverage)):
            raise ValueError("usageCoverage cannot contain duplicates")
        return self


class SqlTemplateCandidate(ContractModel):
    schema_version: Literal["1.1.0"] = "1.1.0"
    template_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=160)
    status: Literal["candidate"] = "candidate"
    executable: Literal[False] = False
    review_status: Literal["pending"] = "pending"
    rule_ref: RuleRef
    binding_ref: CandidateBindingRef
    fact_ref: CandidateFactRef
    context_ref: CandidateContextRef
    dialect: Literal["sqlserver"] = "sqlserver"
    sql_template: str = Field(min_length=1, max_length=100_000)
    parameters: list[SqlParameter] = Field(min_length=1)
    result: SqlResultContract
    allowed_objects: list[str] = Field(min_length=1)
    usage_coverage: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    provenance: CandidateProvenance

    @model_validator(mode="after")
    def reject_duplicate_references(self) -> SqlTemplateCandidate:
        parameter_names = [parameter.name for parameter in self.parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("parameters cannot contain duplicate names")
        if len(self.allowed_objects) != len(set(self.allowed_objects)):
            raise ValueError("allowedObjects cannot contain duplicates")
        if len(self.usage_coverage) != len(set(self.usage_coverage)):
            raise ValueError("usageCoverage cannot contain duplicates")
        return self
