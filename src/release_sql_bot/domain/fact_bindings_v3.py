"""Independent RuleReader FactBindingRequest 3.0.0 consumer contracts.

Mirrors the frozen upstream Schema (``contracts/fact-binding-request-3.0.0.schema.json``)
and the upstream domain validators without importing RuleReader. This module is
structurally incompatible with the V2 consumer by design: no conversion, downgrade,
 or field-trimming path exists in either direction.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic.alias_generators import to_camel

ScalarValueV3 = str | int | float | bool
ScalarOrListV3 = ScalarValueV3 | list[ScalarValueV3]


class V3ConsumerModel(BaseModel):
    """Strict wire model: aliases are accepted, Python field names are not."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class V3ReportModel(BaseModel):
    """Immutable camelCase model assembled by trusted application code."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class FactKindV3(StrEnum):
    SOURCE = "source"
    AGGREGATE = "aggregate"
    EXISTS = "exists"


class FactDataTypeV3(StrEnum):
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


class NullPolicyV3(StrEnum):
    FAIL = "fail"
    PASS = "pass"
    INDETERMINATE = "indeterminate"
    ERROR = "error"


class MappingStatusV3(StrEnum):
    MAPPED = "mapped"
    UNRESOLVED = "unresolved"


class FieldRoleV3(StrEnum):
    VALUE = "value"
    ENTITY_KEY = "entityKey"
    FILTER = "filter"
    GROUP_BY = "groupBy"
    TIME = "time"


class RuleOperatorV3(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
    IS_BLANK = "is_blank"
    IS_NOT_BLANK = "is_not_blank"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"


class AggregationModeV3(StrEnum):
    NONE = "none"
    PRECOMPUTED = "precomputed"
    COMPUTE = "compute"
    EXISTS = "exists"


class AggregationFunctionV3(StrEnum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "countDistinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class TimeRangeModeV3(StrEnum):
    NONE = "none"
    AS_OF = "asOf"
    BETWEEN = "between"


class ValueSourceKindV3(StrEnum):
    PARAMETER = "parameter"
    LITERAL = "literal"


class EvidenceKindV3(StrEnum):
    FACT_DECLARATION = "factDeclaration"
    CONDITION_USAGE = "conditionUsage"
    QUERY_REQUIREMENT = "queryRequirement"
    SOURCE_PROVENANCE = "sourceProvenance"
    EXAMPLE = "example"


class RuleOutcomeV3(StrEnum):
    READY = "READY"
    WAITING_COMPLETION = "WAITING_COMPLETION"
    WAITING_CONDITIONS = "WAITING_CONDITIONS"
    NO_RELEASE_REQUIRED = "NO_RELEASE_REQUIRED"
    ALREADY_RELEASED = "ALREADY_RELEASED"
    SKIPPED = "SKIPPED"
    INDETERMINATE = "INDETERMINATE"


class RuleStageNameV3(StrEnum):
    STATE_GUARDS = "stateGuards"
    PREREQUISITES = "prerequisites"
    ELIGIBILITY = "eligibility"
    POST_GATES = "postGates"
    EXCLUSIONS = "exclusions"


FactKindWire = Annotated[FactKindV3, Field(strict=False)]
FactDataTypeWire = Annotated[FactDataTypeV3, Field(strict=False)]
NullPolicyWire = Annotated[NullPolicyV3, Field(strict=False)]
MappingStatusWire = Annotated[MappingStatusV3, Field(strict=False)]
FieldRoleWire = Annotated[FieldRoleV3, Field(strict=False)]
RuleOperatorWire = Annotated[RuleOperatorV3, Field(strict=False)]
AggregationModeWire = Annotated[AggregationModeV3, Field(strict=False)]
AggregationFunctionWire = Annotated[AggregationFunctionV3, Field(strict=False)]
TimeRangeModeWire = Annotated[TimeRangeModeV3, Field(strict=False)]
ValueSourceKindWire = Annotated[ValueSourceKindV3, Field(strict=False)]
EvidenceKindWire = Annotated[EvidenceKindV3, Field(strict=False)]
RuleOutcomeWire = Annotated[RuleOutcomeV3, Field(strict=False)]
RuleStageWire = Annotated[RuleStageNameV3, Field(strict=False)]


class FactParameterV3(V3ConsumerModel):
    name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    role: Literal["entityKey", "filter", "timeAnchor"]
    data_type: FactDataTypeWire
    required: bool
    description: str = Field(min_length=1, max_length=1_000)


class BindableFactV3(V3ConsumerModel):
    fact_code: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    name: str = Field(min_length=1, max_length=200)
    fact_kind: FactKindWire
    data_type: FactDataTypeWire
    description: str = Field(min_length=1, max_length=2_000)
    nullable: bool
    null_policy: NullPolicyWire
    grain: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    parameters: list[FactParameterV3] = Field(min_length=1)
    unit: str | None = Field(default=None, max_length=80)
    allowed_values: list[ScalarOrListV3] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_parameters(self) -> BindableFactV3:
        names = [item.name for item in self.parameters]
        if len(names) != len(set(names)):
            raise ValueError("fact parameters must be unique")
        return self


class RuleRefV3(V3ConsumerModel):
    rule_set_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    rule_version: str = Field(min_length=1, max_length=260)
    schema_version: Literal["3.0.0"]
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    catalog_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    candidate_payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class EntityRequirementV3(V3ConsumerModel):
    entity_type: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    grain: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    key_parameters: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class FieldRequirementV3(V3ConsumerModel):
    field_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    role: FieldRoleWire
    logical_name: str = Field(min_length=1, max_length=200)
    data_type: FactDataTypeWire
    required: bool
    evidence_ids: list[str] = Field(min_length=1)


class FilterValueV3(V3ConsumerModel):
    kind: ValueSourceKindWire
    parameter_name: str | None = Field(default=None, max_length=100)
    literal: ScalarOrListV3 | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> FilterValueV3:
        if self.kind is ValueSourceKindV3.PARAMETER:
            if self.parameter_name is None or self.literal is not None:
                raise ValueError("parameter filter values require only parameterName")
        elif self.literal is None or self.parameter_name is not None:
            raise ValueError("literal filter values require only literal")
        return self


class FilterRequirementV3(V3ConsumerModel):
    filter_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    field_id: str = Field(min_length=1, max_length=160)
    operator: RuleOperatorWire
    value: FilterValueV3 | None
    null_policy: NullPolicyWire
    required: bool
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_operator_value(self) -> FilterRequirementV3:
        unary = {
            RuleOperatorV3.IS_NULL,
            RuleOperatorV3.IS_NOT_NULL,
            RuleOperatorV3.IS_BLANK,
            RuleOperatorV3.IS_NOT_BLANK,
        }
        if self.operator in unary and self.value is not None:
            raise ValueError("unary null/blank filters cannot contain value")
        if self.operator not in unary and self.value is None:
            raise ValueError("binary filters require value")
        return self


class FilterSetV3(V3ConsumerModel):
    items: list[FilterRequirementV3]
    completeness: Literal["complete"]
    evidence_ids: list[str] = Field(min_length=1)


class AggregationRequirementV3(V3ConsumerModel):
    mode: AggregationModeWire
    function: AggregationFunctionWire | None
    input_field_ids: list[str]
    group_by_field_ids: list[str]
    distinct: bool | None
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_shape(self) -> AggregationRequirementV3:
        if self.mode is AggregationModeV3.COMPUTE:
            if self.function is None or not self.input_field_ids or self.distinct is None:
                raise ValueError("computed aggregation requires function, inputs, and distinct")
        elif self.function is not None or self.input_field_ids or self.group_by_field_ids:
            raise ValueError("non-computed aggregation cannot contain compute fields")
        elif self.distinct is not None:
            raise ValueError("non-computed aggregation requires distinct=null")
        return self


class TimeBoundaryV3(V3ConsumerModel):
    kind: ValueSourceKindWire
    parameter_name: str | None = Field(default=None, max_length=100)
    value: str | None = Field(default=None, max_length=80)
    inclusive: bool

    @model_validator(mode="after")
    def validate_kind(self) -> TimeBoundaryV3:
        if self.kind is ValueSourceKindV3.PARAMETER:
            if self.parameter_name is None or self.value is not None:
                raise ValueError("parameter boundaries require only parameterName")
        elif self.value is None or self.parameter_name is not None:
            raise ValueError("literal boundaries require only value")
        return self


class TimeRangeRequirementV3(V3ConsumerModel):
    mode: TimeRangeModeWire
    time_field_id: str | None = Field(default=None, max_length=160)
    start: TimeBoundaryV3 | None = None
    end: TimeBoundaryV3 | None = None
    timezone: str | None = Field(default=None, max_length=80)
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_shape(self) -> TimeRangeRequirementV3:
        if self.mode is TimeRangeModeV3.NONE:
            if any((self.time_field_id, self.start, self.end, self.timezone)):
                raise ValueError("none time range cannot contain temporal details")
        elif self.mode is TimeRangeModeV3.AS_OF:
            if self.time_field_id is None or self.start is not None or self.end is None:
                raise ValueError("asOf requires timeFieldId and only end")
        elif self.time_field_id is None or self.start is None or self.end is None:
            raise ValueError("between requires timeFieldId and both boundaries")
        return self


class ResultRequirementV3(V3ConsumerModel):
    column_name: Literal["fact_value"]
    data_type: FactDataTypeWire
    cardinality: Literal["scalar"]
    nullable: bool
    null_policy: NullPolicyWire
    unit: str | None = Field(default=None, max_length=80)


class QueryRequirementsV3(V3ConsumerModel):
    entity: EntityRequirementV3
    fields: list[FieldRequirementV3] = Field(min_length=1)
    filters: FilterSetV3
    aggregation: AggregationRequirementV3
    time_range: TimeRangeRequirementV3
    result: ResultRequirementV3

    @model_validator(mode="after")
    def validate_references(self) -> QueryRequirementsV3:
        field_ids = [field.field_id for field in self.fields]
        if len(field_ids) != len(set(field_ids)) or field_ids.count("factValue") != 1:
            raise ValueError("fields require unique IDs and exactly one factValue")
        known = set(field_ids)
        referenced = {
            *(item.field_id for item in self.filters.items),
            *self.aggregation.input_field_ids,
            *self.aggregation.group_by_field_ids,
        }
        if self.time_range.time_field_id is not None:
            referenced.add(self.time_range.time_field_id)
        if unknown := sorted(referenced - known):
            raise ValueError(f"query requirements reference unknown fields: {unknown}")
        return self


class FactUsageV3(V3ConsumerModel):
    stage: RuleStageWire
    rule_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    priority: int = Field(ge=1)
    condition_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=120)
    condition_path: str = Field(pattern=r"^/", max_length=1_000)
    outcome: RuleOutcomeWire
    evidence_ids: list[str] = Field(min_length=1)


class FactExampleV3(V3ConsumerModel):
    example_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=120)
    value: ScalarOrListV3 | None
    expected_outcome: RuleOutcomeWire
    evidence_ids: list[str] = Field(min_length=1)


class MappingCandidateV3(V3ConsumerModel):
    fact_code: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    mapping_status: MappingStatusWire
    view_name: str | None = Field(max_length=150)
    view_field: str | None = Field(max_length=150)
    view_active: bool | None
    review_status: Literal["candidate"]
    note: str = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def validate_mapping_shape(self) -> MappingCandidateV3:
        if self.mapping_status is MappingStatusV3.MAPPED:
            if self.view_name is None or self.view_field is None:
                raise ValueError("mapped candidate requires viewName and viewField")
        elif any(
            value is not None for value in (self.view_name, self.view_field, self.view_active)
        ):
            raise ValueError("unresolved candidate cannot declare physical source hints")
        return self


class ProvenanceV3(V3ConsumerModel):
    source_name: str = Field(min_length=1, max_length=255)
    relative_path: str | None = Field(default=None, max_length=1_000)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_character_count: int = Field(ge=1)
    parser_version: str = Field(min_length=1, max_length=80)
    prompt_version: str = Field(min_length=1, max_length=120)
    provider: Literal["deepseek", "reviewed_import"]
    model: str = Field(min_length=1, max_length=160)


class EvidenceV3(V3ConsumerModel):
    evidence_id: str = Field(
        pattern=r"^[a-z][A-Za-z0-9_.:-]*$",
        max_length=200,
    )
    kind: EvidenceKindWire
    source_document: Literal["ruleResult", "candidate", "catalog"]
    source_path: str = Field(pattern=r"^/", max_length=1_000)


class UncertaintyV3(V3ConsumerModel):
    uncertainty_id: str = Field(
        pattern=r"^[a-z][A-Za-z0-9_.:-]*$",
        max_length=200,
    )
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    impact: Literal["warning"]
    reason: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[str] = Field(min_length=1)


class FactBindingRequestV3(V3ConsumerModel):
    """SqlBot-owned consumer model for the complete V3 handoff payload."""

    contract_version: Literal["3.0.0"]
    status: Literal["candidate"]
    executable: Literal[False]
    request_id: str = Field(min_length=3, max_length=420)
    rule_ref: RuleRefV3
    fact: BindableFactV3
    query_requirements: QueryRequirementsV3
    usages: list[FactUsageV3] = Field(min_length=1)
    examples: list[FactExampleV3] = Field(min_length=1)
    mapping_candidate: MappingCandidateV3
    provenance: ProvenanceV3
    evidence: list[EvidenceV3] = Field(min_length=1)
    uncertainties: list[UncertaintyV3]
    target_dialect: Literal["sqlserver"]
    requires_metadata_snapshot: Literal[True]
    temp_table_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_closure(self) -> FactBindingRequestV3:
        if self.request_id != f"{self.rule_ref.rule_version}#{self.fact.fact_code}":
            raise ValueError("requestId must equal ruleVersion#factCode")
        if self.mapping_candidate.fact_code != self.fact.fact_code:
            raise ValueError("mappingCandidate factCode must match fact")
        if self.query_requirements.result.data_type is not self.fact.data_type:
            raise ValueError("result dataType must match fact")
        if self.provenance.source_sha256 != self.rule_ref.source_sha256:
            raise ValueError("provenance sourceSha256 must match ruleRef")
        if not self.rule_ref.rule_version.startswith(f"{self.rule_ref.rule_set_id}@"):
            raise ValueError("ruleVersion must belong to ruleSetId")
        if self.query_requirements.entity.grain != self.fact.grain:
            raise ValueError("query entity grain must match fact grain")
        entity_parameters = {
            parameter.name for parameter in self.fact.parameters if parameter.role == "entityKey"
        }
        if set(self.query_requirements.entity.key_parameters) != entity_parameters:
            raise ValueError("query entity keys must match fact entityKey parameters")
        value_field = next(
            field for field in self.query_requirements.fields if field.field_id == "factValue"
        )
        if (
            value_field.role is not FieldRoleV3.VALUE
            or value_field.logical_name != self.fact.fact_code
        ):
            raise ValueError("factValue field must identify the request fact")
        parameters = {parameter.name for parameter in self.fact.parameters}
        for item in self.query_requirements.filters.items:
            if (
                item.value is not None
                and item.value.kind is ValueSourceKindV3.PARAMETER
                and item.value.parameter_name not in parameters
            ):
                raise ValueError("filter value references an unknown fact parameter")
        for boundary in (
            self.query_requirements.time_range.start,
            self.query_requirements.time_range.end,
        ):
            if (
                boundary is not None
                and boundary.kind is ValueSourceKindV3.PARAMETER
                and boundary.parameter_name not in parameters
            ):
                raise ValueError("time boundary references an unknown fact parameter")
        result = self.query_requirements.result
        if (
            result.nullable != self.fact.nullable
            or result.null_policy is not self.fact.null_policy
            or result.unit != self.fact.unit
        ):
            raise ValueError("result null and unit contract must match fact")
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence IDs must be unique")
        known = set(evidence_ids)
        references = {
            *(item for item in self.query_requirements.entity.evidence_ids),
            *(item for field in self.query_requirements.fields for item in field.evidence_ids),
            *(item for item in self.query_requirements.filters.evidence_ids),
            *(item for item in self.query_requirements.aggregation.evidence_ids),
            *(item for item in self.query_requirements.time_range.evidence_ids),
            *(item for usage in self.usages for item in usage.evidence_ids),
            *(item for example in self.examples for item in example.evidence_ids),
            *(item for uncertainty in self.uncertainties for item in uncertainty.evidence_ids),
        }
        if unknown := sorted(references - known):
            raise ValueError(f"unknown evidence references: {unknown}")
        if unused := sorted(known - references):
            raise ValueError(f"unreferenced evidence entries: {unused}")
        usage_ids = [
            (item.stage, item.rule_code, item.condition_id, item.condition_path)
            for item in self.usages
        ]
        if len(usage_ids) != len(set(usage_ids)):
            raise ValueError("fact usages must be unique")
        example_ids = [item.example_id for item in self.examples]
        if len(example_ids) != len(set(example_ids)):
            raise ValueError("fact examples must be unique")
        uncertainty_ids = [item.uncertainty_id for item in self.uncertainties]
        if len(uncertainty_ids) != len(set(uncertainty_ids)):
            raise ValueError("fact uncertainties must be unique")
        return self
