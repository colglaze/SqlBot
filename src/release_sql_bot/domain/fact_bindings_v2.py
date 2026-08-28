"""Independent RuleReader FactBindingRequest 2.0.0 consumer contracts."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

ScalarValue = str | int | float | bool
ScalarOrList = ScalarValue | list[ScalarValue]


class V2ConsumerModel(BaseModel):
    """Strict wire model: aliases are accepted, Python field names are not."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class ReportModel(BaseModel):
    """Immutable camelCase report model assembled by trusted application code."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class FactKindV2(StrEnum):
    SOURCE = "source"
    AGGREGATE = "aggregate"
    EXISTS = "exists"
    DERIVED = "derived"


class FactDataTypeV2(StrEnum):
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


class NullPolicyV2(StrEnum):
    FAIL = "fail"
    PASS = "pass"
    INDETERMINATE = "indeterminate"
    ERROR = "error"


class ResolutionStatusV2(StrEnum):
    DECLARED = "declared"
    CANDIDATE = "candidate"
    UNRESOLVED = "unresolved"
    NOT_APPLICABLE = "notApplicable"


class MappingStatusV2(StrEnum):
    MAPPED = "mapped"
    UNRESOLVED = "unresolved"


class FieldRoleV2(StrEnum):
    VALUE = "value"
    ENTITY_KEY = "entityKey"
    FILTER = "filter"
    GROUP_BY = "groupBy"
    TIME = "time"


class RuleOperatorV2(StrEnum):
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


class AggregationModeV2(StrEnum):
    NONE = "none"
    PRECOMPUTED = "precomputed"
    COMPUTE = "compute"
    EXISTS = "exists"
    UNRESOLVED = "unresolved"


class AggregationFunctionV2(StrEnum):
    SUM = "sum"
    COUNT = "count"
    COUNT_DISTINCT = "countDistinct"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class TimeRangeModeV2(StrEnum):
    NONE = "none"
    AS_OF = "asOf"
    BETWEEN = "between"
    UNRESOLVED = "unresolved"


class EvidenceKindV2(StrEnum):
    FACT_DECLARATION = "factDeclaration"
    CONDITION_USAGE = "conditionUsage"
    MAPPING_CANDIDATE = "mappingCandidate"
    TEST_CASE = "testCase"


class ParserProviderV2(StrEnum):
    DEEPSEEK = "deepseek"
    REVIEWED_IMPORT = "reviewed_import"


class UncertaintyCategoryV2(StrEnum):
    ENTITY = "entity"
    FIELD = "field"
    FILTER = "filter"
    AGGREGATION = "aggregation"
    TIME_RANGE = "timeRange"
    SOURCE = "source"


class UncertaintyImpactV2(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


FactKindWire = Annotated[FactKindV2, Field(strict=False)]
FactDataTypeWire = Annotated[FactDataTypeV2, Field(strict=False)]
NullPolicyWire = Annotated[NullPolicyV2, Field(strict=False)]
MappingStatusWire = Annotated[MappingStatusV2, Field(strict=False)]
FieldRoleWire = Annotated[FieldRoleV2, Field(strict=False)]
RuleOperatorWire = Annotated[RuleOperatorV2, Field(strict=False)]
AggregationModeWire = Annotated[AggregationModeV2, Field(strict=False)]
AggregationFunctionWire = Annotated[AggregationFunctionV2, Field(strict=False)]
ResolutionStatusWire = Annotated[ResolutionStatusV2, Field(strict=False)]
TimeRangeModeWire = Annotated[TimeRangeModeV2, Field(strict=False)]
EvidenceKindWire = Annotated[EvidenceKindV2, Field(strict=False)]
ParserProviderWire = Annotated[ParserProviderV2, Field(strict=False)]
UncertaintyCategoryWire = Annotated[UncertaintyCategoryV2, Field(strict=False)]
UncertaintyImpactWire = Annotated[UncertaintyImpactV2, Field(strict=False)]


class BindingFactParameterV2(V2ConsumerModel):
    name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    data_type: FactDataTypeWire
    description: str = Field(min_length=1, max_length=1_000)
    required: bool


class BindableFactV2(V2ConsumerModel):
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
    parameters: list[BindingFactParameterV2] = Field(min_length=1)
    unit: str | None = Field(max_length=80)
    allowed_values: list[ScalarOrList]
    default_value: ScalarValue | None
    derivation: None


class BindingRuleRefV2(V2ConsumerModel):
    rule_id: str = Field(min_length=1, max_length=120)
    rule_version: str = Field(min_length=1, max_length=220)
    schema_version: str = Field(min_length=1, max_length=40)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class FactUsageV2(V2ConsumerModel):
    condition_id: str = Field(min_length=1, max_length=120)
    condition_path: str = Field(min_length=1, max_length=1_000)
    operator: RuleOperatorWire
    expression_side: Literal["left", "right", "leftDerivation", "rightDerivation"]
    evidence_ids: list[str] = Field(min_length=1)


class FactExampleV2(V2ConsumerModel):
    test_case_id: str = Field(min_length=1, max_length=120)
    value: ScalarOrList | None
    expected_rule_result: Literal["pass", "fail"]
    evidence_ids: list[str] = Field(min_length=1)


class BindingMappingCandidateV2(V2ConsumerModel):
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
    def validate_mapping_shape(self) -> BindingMappingCandidateV2:
        if self.mapping_status is MappingStatusV2.MAPPED:
            if self.view_name is None or self.view_field is None:
                raise ValueError("mapped candidate requires viewName and viewField")
        elif any(
            value is not None for value in (self.view_name, self.view_field, self.view_active)
        ):
            raise ValueError("unresolved candidate cannot declare physical source hints")
        return self


class BindingSourceProvenanceV2(V2ConsumerModel):
    source_name: str = Field(min_length=1, max_length=255)
    relative_path: str | None = Field(max_length=1_000)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    character_count: int = Field(ge=1)


class BindingParserProvenanceV2(V2ConsumerModel):
    parser_version: str = Field(min_length=1, max_length=80)
    prompt_version: str = Field(min_length=1, max_length=120)
    provider: ParserProviderWire
    model: str = Field(min_length=1, max_length=160)


class BindingEvidenceV2(V2ConsumerModel):
    evidence_id: str = Field(
        pattern=r"^[a-z][A-Za-z0-9_.:-]*$",
        max_length=200,
    )
    kind: EvidenceKindWire
    source_path: str = Field(pattern=r"^/", max_length=1_000)


class BindingProvenanceV2(V2ConsumerModel):
    source: BindingSourceProvenanceV2
    parser: BindingParserProvenanceV2
    generated_at: str = Field(min_length=1, max_length=80)
    evidence: list[BindingEvidenceV2] = Field(min_length=2)

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("generatedAt must be an ISO-8601 datetime") from exc
        if parsed.tzinfo is None:
            raise ValueError("generatedAt must include a timezone")
        return value


class SourceFieldCandidateV2(V2ConsumerModel):
    relation_name: str = Field(min_length=1, max_length=150)
    field_name: str = Field(min_length=1, max_length=150)
    relation_active: bool | None
    review_status: Literal["candidate"]


class EntityRequirementV2(V2ConsumerModel):
    entity_type: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    grain: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    key_parameters: list[str]
    resolution_status: Literal[ResolutionStatusV2.DECLARED]
    key_resolution_status: Literal[
        ResolutionStatusV2.CANDIDATE,
        ResolutionStatusV2.UNRESOLVED,
    ]
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_key_shape(self) -> EntityRequirementV2:
        if self.key_resolution_status is ResolutionStatusV2.CANDIDATE:
            if not self.key_parameters:
                raise ValueError("candidate entity keys require keyParameters")
        elif self.key_parameters:
            raise ValueError("unresolved entity keys cannot declare keyParameters")
        return self


class FieldRequirementV2(V2ConsumerModel):
    field_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    role: FieldRoleWire
    logical_name: str = Field(min_length=1, max_length=200)
    data_type: FactDataTypeWire
    required: bool
    source_candidate: SourceFieldCandidateV2 | None
    resolution_status: Literal[
        ResolutionStatusV2.CANDIDATE,
        ResolutionStatusV2.UNRESOLVED,
    ]
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_resolution_shape(self) -> FieldRequirementV2:
        if self.resolution_status is ResolutionStatusV2.CANDIDATE:
            if self.source_candidate is None:
                raise ValueError("candidate field requires sourceCandidate")
        elif self.source_candidate is not None:
            raise ValueError("unresolved field cannot declare sourceCandidate")
        return self


class ParameterFilterValueV2(V2ConsumerModel):
    kind: Literal["parameter"]
    parameter_name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)


class LiteralFilterValueV2(V2ConsumerModel):
    kind: Literal["literal"]
    value: ScalarOrList


FilterValueV2 = Annotated[
    ParameterFilterValueV2 | LiteralFilterValueV2,
    Field(discriminator="kind"),
]


class FilterRequirementV2(V2ConsumerModel):
    filter_id: str = Field(pattern=r"^[a-z][A-Za-z0-9_.-]*$", max_length=160)
    field_id: str = Field(min_length=1, max_length=160)
    operator: RuleOperatorWire
    value: FilterValueV2 | None
    required: bool
    resolution_status: Literal[
        ResolutionStatusV2.CANDIDATE,
        ResolutionStatusV2.UNRESOLVED,
    ]
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_value_shape(self) -> FilterRequirementV2:
        unary = {
            RuleOperatorV2.IS_NULL,
            RuleOperatorV2.IS_NOT_NULL,
            RuleOperatorV2.IS_BLANK,
            RuleOperatorV2.IS_NOT_BLANK,
        }
        if self.operator in unary and self.value is not None:
            raise ValueError("unary filter operator cannot declare value")
        if self.operator not in unary and self.value is None:
            raise ValueError("non-unary filter operator requires value")
        return self


class FilterSetV2(V2ConsumerModel):
    items: list[FilterRequirementV2]
    completeness: Literal["complete", "unresolved"]
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_complete_filters(self) -> FilterSetV2:
        if self.completeness == "complete" and any(
            item.resolution_status is not ResolutionStatusV2.CANDIDATE for item in self.items
        ):
            raise ValueError("complete filter set cannot contain unresolved items")
        return self


class AggregationRequirementV2(V2ConsumerModel):
    mode: AggregationModeWire
    function: AggregationFunctionWire | None
    input_field_ids: list[str]
    group_by_field_ids: list[str]
    distinct: bool | None
    resolution_status: ResolutionStatusWire
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_aggregation_shape(self) -> AggregationRequirementV2:
        expected_status = {
            AggregationModeV2.NONE: ResolutionStatusV2.DECLARED,
            AggregationModeV2.PRECOMPUTED: ResolutionStatusV2.CANDIDATE,
            AggregationModeV2.EXISTS: ResolutionStatusV2.DECLARED,
            AggregationModeV2.UNRESOLVED: ResolutionStatusV2.UNRESOLVED,
        }
        if self.mode is AggregationModeV2.COMPUTE:
            if (
                self.function is None
                or not self.input_field_ids
                or self.distinct is None
                or self.resolution_status
                not in {ResolutionStatusV2.DECLARED, ResolutionStatusV2.CANDIDATE}
            ):
                raise ValueError("computed aggregation shape is incomplete")
        else:
            if (
                self.function is not None
                or self.input_field_ids
                or self.group_by_field_ids
                or self.distinct is not None
            ):
                raise ValueError("non-computed aggregation cannot declare compute fields")
            if self.resolution_status is not expected_status[self.mode]:
                raise ValueError("aggregation resolutionStatus does not match mode")
        return self


class TimeBoundaryV2(V2ConsumerModel):
    kind: Literal["parameter", "literal"]
    parameter_name: str | None = Field(max_length=100)
    value: str | None = Field(max_length=80)
    inclusive: bool

    @model_validator(mode="after")
    def validate_boundary_shape(self) -> TimeBoundaryV2:
        if self.kind == "parameter":
            if self.parameter_name is None or self.value is not None:
                raise ValueError("parameter boundary requires only parameterName")
        elif self.parameter_name is not None or self.value is None:
            raise ValueError("literal boundary requires only value")
        return self


class TimeRangeRequirementV2(V2ConsumerModel):
    mode: TimeRangeModeWire
    time_field_id: str | None = Field(max_length=160)
    start: TimeBoundaryV2 | None
    end: TimeBoundaryV2 | None
    timezone: str | None = Field(max_length=80)
    resolution_status: ResolutionStatusWire
    evidence_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_time_range_shape(self) -> TimeRangeRequirementV2:
        if self.mode is TimeRangeModeV2.NONE:
            expected = (None, None, None, None, ResolutionStatusV2.NOT_APPLICABLE)
        elif self.mode is TimeRangeModeV2.UNRESOLVED:
            expected = (None, None, None, None, ResolutionStatusV2.UNRESOLVED)
        else:
            if self.time_field_id is None or self.end is None:
                raise ValueError("resolved time range requires timeFieldId and end")
            if self.mode is TimeRangeModeV2.AS_OF and self.start is not None:
                raise ValueError("asOf time range cannot declare start")
            if self.mode is TimeRangeModeV2.BETWEEN and self.start is None:
                raise ValueError("between time range requires start")
            if self.resolution_status not in {
                ResolutionStatusV2.DECLARED,
                ResolutionStatusV2.CANDIDATE,
            }:
                raise ValueError("resolved time range has invalid resolutionStatus")
            return self
        actual = (
            self.time_field_id,
            self.start,
            self.end,
            self.timezone,
            self.resolution_status,
        )
        if actual != expected:
            raise ValueError("unresolved/not-applicable time range has unexpected fields")
        return self


class FactResultRequirementV2(V2ConsumerModel):
    column_name: Literal["fact_value"]
    data_type: FactDataTypeWire
    cardinality: Literal["scalar"]
    nullable: bool
    null_policy: NullPolicyWire
    unit: str | None = Field(max_length=80)


class QueryRequirementsV2(V2ConsumerModel):
    entity: EntityRequirementV2
    fields: list[FieldRequirementV2] = Field(min_length=1)
    filters: FilterSetV2
    aggregation: AggregationRequirementV2
    time_range: TimeRangeRequirementV2
    result: FactResultRequirementV2


class BindingUncertaintyV2(V2ConsumerModel):
    uncertainty_id: str = Field(
        pattern=r"^[a-z][A-Za-z0-9_.:-]*$",
        max_length=200,
    )
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    category: UncertaintyCategoryWire
    field_path: str = Field(pattern=r"^/", max_length=1_000)
    impact: UncertaintyImpactWire
    reason: str = Field(min_length=1, max_length=2_000)
    resolution_hint: str | None = Field(max_length=2_000)
    evidence_ids: list[str] = Field(min_length=1)


class FactBindingRequestV2(V2ConsumerModel):
    """SqlBot-owned consumer model for the complete V2 handoff payload."""

    contract_version: str = Field(min_length=1, max_length=40)
    status: str = Field(min_length=1, max_length=40)
    request_id: str = Field(min_length=3, max_length=384)
    rule_ref: BindingRuleRefV2
    fact: BindableFactV2
    query_requirements: QueryRequirementsV2
    usages: list[FactUsageV2] = Field(min_length=1)
    mapping_candidate: BindingMappingCandidateV2
    examples: list[FactExampleV2]
    provenance: BindingProvenanceV2
    uncertainties: list[BindingUncertaintyV2]
    target_dialect: str = Field(min_length=1, max_length=40)
    requires_metadata_snapshot: bool
    temp_table_allowed: bool

    @model_validator(mode="after")
    def reject_duplicate_local_ids(self) -> FactBindingRequestV2:
        identifiers = {
            "usages.conditionId": [item.condition_id for item in self.usages],
            "queryRequirements.fields.fieldId": [
                item.field_id for item in self.query_requirements.fields
            ],
            "queryRequirements.filters.items.filterId": [
                item.filter_id for item in self.query_requirements.filters.items
            ],
            "provenance.evidence.evidenceId": [
                item.evidence_id for item in self.provenance.evidence
            ],
            "uncertainties.uncertaintyId": [item.uncertainty_id for item in self.uncertainties],
        }
        for field_name, values in identifiers.items():
            if len(values) != len(set(values)):
                raise ValueError(f"{field_name} cannot contain duplicates")
        return self


class BindingGapOwner(StrEnum):
    BUSINESS_RULE_REVIEW = "businessRuleReview"
    METADATA_REVIEW = "metadataReview"
    SQL_BOT = "sqlBot"


class BindingGapIssue(ReportModel):
    code: str = Field(min_length=1, max_length=120)
    owner: BindingGapOwner
    field_path: str = Field(min_length=1, max_length=1_000)
    message: str = Field(min_length=1, max_length=2_000)
    evidence_ids: tuple[str, ...] = ()
    uncertainty_id: str | None = Field(default=None, max_length=200)


class BindingGapHashes(ReportModel):
    request_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    rule_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class BindingGapReport(ReportModel):
    status: Literal["blocked", "readyForMetadataResolution"]
    executable: Literal[False] = False
    request_id: str = Field(min_length=3, max_length=384)
    hashes: BindingGapHashes
    blocking_issues: tuple[BindingGapIssue, ...]
    warnings: tuple[BindingGapIssue, ...]
