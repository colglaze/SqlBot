"""Fact-level handoff contracts owned by the Agent 2 domain."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel


class ContractModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class FactKind(StrEnum):
    SOURCE = "source"
    AGGREGATE = "aggregate"
    EXISTS = "exists"
    DERIVED = "derived"


class FactDataType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    ENUM = "enum"
    MONEY = "money"
    LIST = "list"
    UNKNOWN = "unknown"


class NullPolicy(StrEnum):
    FAIL = "fail"
    PASS = "pass"
    INDETERMINATE = "indeterminate"
    ERROR = "error"


class MappingStatus(StrEnum):
    MAPPED = "mapped"
    UNRESOLVED = "unresolved"


class FactParameter(ContractModel):
    name: str = Field(max_length=100)
    data_type: FactDataType
    description: str = Field(max_length=1_000)
    required: bool = True


class FactContract(ContractModel):
    fact_code: str = Field(max_length=160)
    name: str = Field(max_length=200)
    fact_kind: FactKind
    data_type: FactDataType
    description: str = Field(max_length=2_000)
    nullable: bool
    null_policy: NullPolicy
    grain: str = Field(max_length=100)
    parameters: list[FactParameter] = Field(default_factory=list)
    unit: str | None = Field(default=None, max_length=80)
    allowed_values: list[Any] = Field(default_factory=list)
    default_value: str | int | float | bool | None = None
    derivation: dict[str, Any] | None = None


class RuleRef(ContractModel):
    rule_id: str
    rule_version: str
    schema_version: str
    source_sha256: str


class FactUsage(ContractModel):
    condition_id: str
    condition_path: str
    operator: str
    expression_side: str


class FactExample(ContractModel):
    test_case_id: str
    value: Any
    expected_rule_result: str


class MappingCandidate(ContractModel):
    fact_code: str
    mapping_status: MappingStatus
    view_name: str | None = None
    view_field: str | None = None
    view_active: bool | None = None
    review_status: str
    note: str


class FactBindingRequest(ContractModel):
    contract_version: str
    status: str
    rule_ref: RuleRef
    fact: FactContract
    usages: list[FactUsage] = Field(default_factory=list)
    mapping_candidate: MappingCandidate
    examples: list[FactExample] = Field(default_factory=list)
    target_dialect: str
    requires_metadata_snapshot: bool
    temp_table_allowed: bool


class MetadataSnapshotRef(ContractModel):
    snapshot_id: str = Field(min_length=1, max_length=160)
    version: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class SqlServerDialect(ContractModel):
    name: str
    version: str = Field(min_length=1, max_length=80)


class AllowedRelation(ContractModel):
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)

    @property
    def qualified_name(self) -> str:
        return f"{self.schema_name}.{self.relation_name}"


class EntityKey(ContractModel):
    parameter_name: str = Field(min_length=1, max_length=100)
    schema_name: str = Field(min_length=1, max_length=128)
    relation_name: str = Field(min_length=1, max_length=128)
    column_name: str = Field(min_length=1, max_length=128)


class SqlServerCapabilities(ContractModel):
    explain: bool = False
    session_temp_table: bool = False


class SqlServerBindingContext(ContractModel):
    context_id: str = Field(min_length=1, max_length=160)
    context_version: int = Field(ge=1)
    dialect: SqlServerDialect
    metadata_snapshot: MetadataSnapshotRef
    entity_keys: list[EntityKey] = Field(default_factory=list)
    allowed_relations: list[AllowedRelation] = Field(default_factory=list)
    capabilities: SqlServerCapabilities = Field(default_factory=SqlServerCapabilities)
    temp_table_allowed: bool = False

    @model_validator(mode="after")
    def reject_duplicate_relations(self) -> SqlServerBindingContext:
        names = [relation.qualified_name for relation in self.allowed_relations]
        if len(names) != len(set(names)):
            raise ValueError("allowedRelations cannot contain duplicates")
        parameter_names = [key.parameter_name for key in self.entity_keys]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("entityKeys cannot contain duplicate parameterName values")
        return self


class ValidateFactBindingRequest(ContractModel):
    binding_request: FactBindingRequest
    context: SqlServerBindingContext


class BlockingIssue(ContractModel):
    code: str
    message: str
    field_path: str


class BindingReadiness(ContractModel):
    status: str
    fact_code: str
    context_id: str
    issues: list[BlockingIssue]
