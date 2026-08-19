"""Candidate-only SQL template contract for one business fact."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from release_sql_bot.domain.fact_bindings import ContractModel, FactDataType, RuleRef


class CandidateFactRef(ContractModel):
    fact_code: str
    fact_kind: Literal["source", "aggregate", "exists"]
    data_type: FactDataType
    grain: str


class CandidateContextRef(ContractModel):
    context_id: str
    context_version: int
    metadata_snapshot_id: str
    metadata_snapshot_version: int
    metadata_snapshot_sha256: str


class SqlParameter(ContractModel):
    name: str
    data_type: FactDataType
    required: bool
    source: str


class SqlResultContract(ContractModel):
    column_name: Literal["fact_value"] = "fact_value"
    data_type: FactDataType
    cardinality: Literal["scalar"] = "scalar"
    nullable: bool


class CandidateProvenance(ContractModel):
    provider: Literal["deepseek"] = "deepseek"
    model: str
    prompt_version: str


class SqlTemplateCandidate(ContractModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    template_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=160)
    status: Literal["candidate"] = "candidate"
    executable: Literal[False] = False
    review_status: Literal["pending"] = "pending"
    rule_ref: RuleRef
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
