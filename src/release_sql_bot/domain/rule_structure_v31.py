"""Read-only 3.1.0 catalog, candidate tree, and parse-result consumers."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from release_sql_bot.domain.fact_bindings_v3 import (
    BindableFactV3,
    FactDataTypeWire,
    NullPolicyV3,
    NullPolicyWire,
    RuleOperatorV3,
    RuleOperatorWire,
    RuleOutcomeWire,
    RuleStageNameV3,
    V3ConsumerModel,
)
from release_sql_bot.domain.fact_bindings_v31 import ProvenanceV31, QueryRequirementsV31

_SHA256_PATTERN = r"^[a-f0-9]{64}$"
STAGE_ORDER_V31 = tuple(stage.value for stage in RuleStageNameV3)
EVALUATION_TIMEZONE_V31: Literal["Asia/Shanghai"] = "Asia/Shanghai"
ScalarV31 = str | int | float | bool


class V31StructureModel(V3ConsumerModel):
    """CamelCase consumer that still accepts JSON ISO datetimes."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra="forbid",
        strict=False,
    )


class ExpressionKindV31(StrEnum):
    FACT = "fact"
    LITERAL = "literal"
    PARAMETER = "parameter"
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    COALESCE = "coalesce"
    DATE_ADD = "dateAdd"


class ConditionKindV31(StrEnum):
    ALL = "all"
    ANY = "any"
    NOT = "not"
    COMPARE = "compare"
    ALL_MEMBERS = "allMembers"


class EmptyCollectionPolicyV31(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INDETERMINATE = "indeterminate"


class DuplicateMemberPolicyV31(StrEnum):
    UNIQUE_PRESERVE_ORDER = "uniquePreserveOrder"


class MissingMemberPolicyV31(StrEnum):
    FAIL = "fail"
    INDETERMINATE = "indeterminate"


class StageHitPolicyV31(StrEnum):
    TERMINATE = "terminate"
    FIRST_MATCH_THEN_CONTINUE = "firstMatchThenContinue"


class RuntimeParameterRoleV31(StrEnum):
    EVALUATION_CLOCK = "evaluationClock"
    CUTOFF = "cutoff"
    EFFECTIVE_FROM = "effectiveFrom"


class DateUnitV31(StrEnum):
    DAY = "day"
    HOUR = "hour"
    MINUTE = "minute"


class RuleNodeStatusV31(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"


ExpressionKindWire = Annotated[ExpressionKindV31, Field(strict=False)]
ConditionKindWire = Annotated[ConditionKindV31, Field(strict=False)]
EmptyCollectionPolicyWire = Annotated[EmptyCollectionPolicyV31, Field(strict=False)]
DuplicateMemberPolicyWire = Annotated[DuplicateMemberPolicyV31, Field(strict=False)]
MissingMemberPolicyWire = Annotated[MissingMemberPolicyV31, Field(strict=False)]
StageHitPolicyWire = Annotated[StageHitPolicyV31, Field(strict=False)]
RuntimeParameterRoleWire = Annotated[RuntimeParameterRoleV31, Field(strict=False)]
DateUnitWire = Annotated[DateUnitV31, Field(strict=False)]
RuleNodeStatusWire = Annotated[RuleNodeStatusV31, Field(strict=False)]


class ExpressionNodeV31(V31StructureModel):
    kind: ExpressionKindWire
    fact_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    parameter_name: str | None = Field(
        default=None,
        pattern=r"^[a-z][A-Za-z0-9]*$",
        max_length=100,
    )
    value: ScalarV31 | list[ScalarV31] | None = None
    children: list[ExpressionNodeV31] = Field(default_factory=list)
    unit: DateUnitWire | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ExpressionNodeV31:
        if self.kind is ExpressionKindV31.FACT:
            if (
                self.fact_code is None
                or self.parameter_name is not None
                or self.value is not None
                or self.children
                or self.unit
            ):
                raise ValueError("fact expressions require only factCode")
        elif self.kind is ExpressionKindV31.LITERAL:
            if (
                self.value is None
                or self.fact_code is not None
                or self.parameter_name is not None
                or self.children
                or self.unit
            ):
                raise ValueError("literal expressions require only a non-null value")
        elif self.kind is ExpressionKindV31.PARAMETER:
            if (
                self.parameter_name is None
                or self.fact_code is not None
                or self.value is not None
                or self.children
                or self.unit
            ):
                raise ValueError("parameter expressions require only parameterName")
        elif self.kind in {ExpressionKindV31.ADD, ExpressionKindV31.MULTIPLY}:
            if (
                len(self.children) < 2
                or self.fact_code is not None
                or self.parameter_name is not None
                or self.value is not None
                or self.unit is not None
            ):
                raise ValueError("add/multiply expressions require at least two children")
        elif self.kind in {ExpressionKindV31.SUBTRACT, ExpressionKindV31.DIVIDE}:
            if (
                len(self.children) != 2
                or self.fact_code is not None
                or self.parameter_name is not None
                or self.value is not None
                or self.unit is not None
            ):
                raise ValueError("subtract/divide expressions require exactly two children")
        elif self.kind is ExpressionKindV31.COALESCE:
            if (
                len(self.children) < 2
                or self.fact_code is not None
                or self.parameter_name is not None
                or self.value is not None
                or self.unit is not None
            ):
                raise ValueError("coalesce expressions require at least two children")
        elif self.kind is ExpressionKindV31.DATE_ADD:
            if (
                len(self.children) != 2
                or self.unit is None
                or self.fact_code is not None
                or self.parameter_name is not None
                or self.value is not None
            ):
                raise ValueError("dateAdd expressions require two children and unit")
        return self

    def referenced_fact_codes(self) -> set[str]:
        codes = {self.fact_code} if self.fact_code else set()
        for child in self.children:
            codes |= child.referenced_fact_codes()
        return codes

    def referenced_parameter_names(self) -> set[str]:
        names = {self.parameter_name} if self.parameter_name else set()
        for child in self.children:
            names |= child.referenced_parameter_names()
        return names

    def uses_arithmetic(self) -> bool:
        if self.kind in {
            ExpressionKindV31.ADD,
            ExpressionKindV31.SUBTRACT,
            ExpressionKindV31.MULTIPLY,
            ExpressionKindV31.DIVIDE,
            ExpressionKindV31.DATE_ADD,
        }:
            return True
        return any(child.uses_arithmetic() for child in self.children)


class ConditionNodeV31(V31StructureModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=160)
    kind: ConditionKindWire
    description: str = Field(min_length=1, max_length=2_000)
    enabled: bool = True
    children: list[ConditionNodeV31] = Field(default_factory=list)
    left: ExpressionNodeV31 | None = None
    operator: RuleOperatorWire | None = None
    right: ExpressionNodeV31 | None = None
    null_policy: NullPolicyWire = NullPolicyV3.INDETERMINATE
    collection_fact_code: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    empty_collection_policy: EmptyCollectionPolicyWire | None = None
    duplicate_member_policy: DuplicateMemberPolicyWire | None = None
    missing_member_policy: MissingMemberPolicyWire | None = None
    already_satisfied: ConditionNodeV31 | None = None
    member_predicate: ConditionNodeV31 | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> ConditionNodeV31:
        comparison_present = any((self.left is not None, self.operator is not None, self.right))
        member_present = any(
            (
                self.collection_fact_code is not None,
                self.empty_collection_policy is not None,
                self.duplicate_member_policy is not None,
                self.missing_member_policy is not None,
                self.already_satisfied is not None,
                self.member_predicate is not None,
            )
        )
        if self.kind in {ConditionKindV31.ALL, ConditionKindV31.ANY}:
            if not self.children or comparison_present or member_present:
                raise ValueError("all/any nodes require children and no comparison/member fields")
        elif self.kind is ConditionKindV31.NOT:
            if len(self.children) != 1 or comparison_present or member_present:
                raise ValueError(
                    "not nodes require exactly one child and no comparison/member fields"
                )
        elif self.kind is ConditionKindV31.COMPARE:
            if self.children or member_present or self.left is None or self.operator is None:
                raise ValueError(
                    "compare nodes require left/operator and no children/member fields"
                )
            unary = {
                RuleOperatorV3.IS_NULL,
                RuleOperatorV3.IS_NOT_NULL,
                RuleOperatorV3.IS_BLANK,
                RuleOperatorV3.IS_NOT_BLANK,
            }
            if self.operator in unary and self.right is not None:
                raise ValueError("null/blank operators cannot contain right")
            if self.operator not in unary and self.right is None:
                raise ValueError("binary compare operators require right")
        else:
            if (
                self.children
                or comparison_present
                or self.collection_fact_code is None
                or self.empty_collection_policy is None
                or self.duplicate_member_policy is None
                or self.missing_member_policy is None
                or self.member_predicate is None
            ):
                raise ValueError(
                    "allMembers nodes require collection, policies, memberPredicate, "
                    "and no children"
                )
        return self

    def referenced_fact_codes(self) -> set[str]:
        codes: set[str] = set()
        if self.collection_fact_code:
            codes.add(self.collection_fact_code)
        for expression in (self.left, self.right):
            if expression is not None:
                codes |= expression.referenced_fact_codes()
        for child in self.children:
            codes |= child.referenced_fact_codes()
        if self.already_satisfied is not None:
            codes |= self.already_satisfied.referenced_fact_codes()
        if self.member_predicate is not None:
            codes |= self.member_predicate.referenced_fact_codes()
        return codes

    def referenced_parameter_names(self) -> set[str]:
        names: set[str] = set()
        for expression in (self.left, self.right):
            if expression is not None:
                names |= expression.referenced_parameter_names()
        for child in self.children:
            names |= child.referenced_parameter_names()
        if self.already_satisfied is not None:
            names |= self.already_satisfied.referenced_parameter_names()
        if self.member_predicate is not None:
            names |= self.member_predicate.referenced_parameter_names()
        return names

    def uses_any_or_not(self) -> bool:
        if self.kind in {ConditionKindV31.ANY, ConditionKindV31.NOT}:
            return True
        nodes = [*self.children]
        if self.already_satisfied is not None:
            nodes.append(self.already_satisfied)
        if self.member_predicate is not None:
            nodes.append(self.member_predicate)
        return any(node.uses_any_or_not() for node in nodes)

    def uses_arithmetic(self) -> bool:
        expressions = [item for item in (self.left, self.right) if item is not None]
        if any(item.uses_arithmetic() for item in expressions):
            return True
        nodes = [*self.children]
        if self.already_satisfied is not None:
            nodes.append(self.already_satisfied)
        if self.member_predicate is not None:
            nodes.append(self.member_predicate)
        return any(node.uses_arithmetic() for node in nodes)

    def collection_fact_codes(self) -> set[str]:
        codes = {self.collection_fact_code} if self.collection_fact_code else set()
        for child in self.children:
            codes |= child.collection_fact_codes()
        if self.already_satisfied is not None:
            codes |= self.already_satisfied.collection_fact_codes()
        if self.member_predicate is not None:
            codes |= self.member_predicate.collection_fact_codes()
        return codes


class RuntimeParameterV31(V31StructureModel):
    name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    data_type: FactDataTypeWire
    role: RuntimeParameterRoleWire
    required: bool = True
    bound_value: ScalarV31 | None = None
    inclusive: bool = True
    description: str = Field(min_length=1, max_length=1_000)


class StageSemanticsV31(V31StructureModel):
    on_hit: StageHitPolicyWire
    unknown: Literal["indeterminate"] = "indeterminate"


class RuleNodeV31(V31StructureModel):
    rule_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    priority: int = Field(ge=1, le=10_000)
    title: str = Field(min_length=1, max_length=300)
    status: RuleNodeStatusWire
    when: ConditionNodeV31 | None = None
    outcome: RuleOutcomeWire | None = None
    reason_code: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    failure_reason: str = Field(min_length=1, max_length=2_000)
    recommendations: list[str] = Field(min_length=1)
    blocking_issue_ids: list[str] = Field(default_factory=list)


class RuleStageV31(V31StructureModel):
    stage: Literal[
        "stateGuards",
        "prerequisites",
        "eligibility",
        "postGates",
        "exclusions",
    ]
    rules: list[RuleNodeV31] = Field(default_factory=list)
    empty_stage_reason: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def validate_empty_stage(self) -> RuleStageV31:
        if self.rules:
            if self.empty_stage_reason is not None:
                raise ValueError("non-empty stages cannot declare emptyStageReason")
        elif self.empty_stage_reason is None:
            raise ValueError("empty stages require emptyStageReason")
        return self


class SourceIdentityV31(V31StructureModel):
    source_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    parse_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    extractor_version: str = Field(min_length=1, max_length=80)
    extracted_sections: list[str] = Field(min_length=1)
    source_file_byte_length: int = Field(ge=1)
    parse_input_character_count: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_dual_hashes(self) -> SourceIdentityV31:
        if self.source_file_sha256 == self.parse_input_sha256:
            raise ValueError("source file hash must not equal parse input hash")
        return self


class BlockingIssueV31(V31StructureModel):
    issue_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$", max_length=160)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    message: str = Field(min_length=1, max_length=2_000)
    fact_codes: list[str] = Field(default_factory=list)
    resolution_hint: str = Field(min_length=1, max_length=2_000)


class ProposedFactV31(V31StructureModel):
    fact_code: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    name: str = Field(min_length=1, max_length=200)
    data_type_hint: str | None = Field(default=None, max_length=80)
    reason: str = Field(min_length=1, max_length=2_000)
    source_locator: str = Field(min_length=1, max_length=300)


class RuleStructureCandidateV31(V31StructureModel):
    contract_version: Literal["3.1.0"]
    rule_set_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    title: str = Field(min_length=1, max_length=300)
    scope: str = Field(min_length=1, max_length=4_000)
    catalog_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    catalog_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=120)
    catalog_digest: str = Field(pattern=_SHA256_PATTERN)
    source_views: list[str] = Field(min_length=1)
    source_identity: SourceIdentityV31
    runtime_parameters: list[RuntimeParameterV31] = Field(min_length=1)
    evaluation_timezone: Literal["Asia/Shanghai"] = EVALUATION_TIMEZONE_V31
    required_fact_codes: list[str] = Field(min_length=1)
    stages: list[RuleStageV31] = Field(min_length=5, max_length=5)
    stage_semantics: dict[str, StageSemanticsV31]
    default_outcome: RuleOutcomeWire
    default_reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    blocking_issues: list[BlockingIssueV31] = Field(default_factory=list)
    proposed_facts: list[ProposedFactV31] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_stage_semantics(self) -> RuleStructureCandidateV31:
        names = [stage.stage for stage in self.stages]
        if names != list(STAGE_ORDER_V31):
            raise ValueError("stages must appear in frozen V3 order")
        expected = set(STAGE_ORDER_V31)
        if set(self.stage_semantics) != expected:
            raise ValueError("stageSemantics must declare every frozen V3 stage exactly once")
        eligibility = self.stage_semantics[RuleStageNameV3.ELIGIBILITY.value]
        if eligibility.on_hit is not StageHitPolicyV31.FIRST_MATCH_THEN_CONTINUE:
            raise ValueError("eligibility onHit must be firstMatchThenContinue")
        for name, semantics in self.stage_semantics.items():
            if name != RuleStageNameV3.ELIGIBILITY.value and semantics.on_hit is not (
                StageHitPolicyV31.TERMINATE
            ):
                raise ValueError(f"{name} onHit must terminate")
        parameter_names = [parameter.name for parameter in self.runtime_parameters]
        if len(parameter_names) != len(set(parameter_names)):
            raise ValueError("runtime parameter names must be unique")
        return self

    def stage_names(self) -> tuple[str, ...]:
        return tuple(stage.stage for stage in self.stages)


class CatalogEvidenceV31(V31StructureModel):
    evidence_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$", max_length=160)
    source_kind: Literal["businessConfirmation", "ruleText", "viewDefinition"]
    source_id: str = Field(min_length=1, max_length=240)
    source_sha256: str = Field(pattern=_SHA256_PATTERN)
    locator: str = Field(min_length=1, max_length=300)
    note: str = Field(min_length=1, max_length=1_000)


class CatalogParameterV31(V31StructureModel):
    name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    role: Literal["entityKey", "filter", "timeAnchor"]
    data_type: FactDataTypeWire
    required: bool = True
    description: str = Field(min_length=1, max_length=1_000)


class ConfirmedFactV31(V31StructureModel):
    fact_code: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    data_type: FactDataTypeWire
    nullable: bool
    null_policy: NullPolicyWire
    grain: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=100)
    parameters: list[CatalogParameterV31] = Field(min_length=1)
    allowed_values: list[ScalarV31] = Field(default_factory=list)
    unit: str | None = Field(default=None, max_length=80)
    evidence_refs: list[str] = Field(min_length=1)
    binding_profile_ref: str | None = Field(default=None, max_length=240)
    binding_issues: list[str] = Field(default_factory=list)


class BusinessConfirmedFactCatalogV31(V31StructureModel):
    contract_version: Literal["3.0.0"]
    catalog_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    catalog_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=120)
    catalog_digest: str = Field(pattern=_SHA256_PATTERN)
    facts: list[ConfirmedFactV31] = Field(min_length=1)
    evidence: list[CatalogEvidenceV31] = Field(min_length=1)


class CatalogRefV31(V31StructureModel):
    catalog_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    catalog_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=120)
    catalog_digest: str = Field(pattern=_SHA256_PATTERN)
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)


class CandidateRefV31(V31StructureModel):
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    parse_input_sha256: str = Field(pattern=_SHA256_PATTERN)


class CompleteDeliveryRefV31(V31StructureModel):
    catalog_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    result_payload_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    purpose: Literal["optimization-plan-generation"]


class ParserProvenanceV31(V31StructureModel):
    parser_version: str = Field(min_length=1, max_length=80)
    prompt_version: str = Field(min_length=1, max_length=120)
    provider: Literal["reviewed_import"]
    model: str = Field(min_length=1, max_length=160)


class FactDeclarationV31(V31StructureModel):
    fact: BindableFactV3
    query: QueryRequirementsV31
    uncertainties: list[dict[str, object]] = Field(default_factory=list)


class MemberSnapshotV31(V31StructureModel):
    member_key: str = Field(min_length=1, max_length=160)
    facts: dict[str, ScalarV31 | list[ScalarV31] | None]


class TestCaseV31(V31StructureModel):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$", max_length=120)
    description: str = Field(min_length=1, max_length=500)
    given: dict[str, ScalarV31 | list[ScalarV31] | None]
    runtime: dict[str, ScalarV31] = Field(default_factory=dict)
    members: list[MemberSnapshotV31] = Field(default_factory=list)
    expected_outcome: RuleOutcomeWire
    expected_reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    expected_matched_rule_codes: list[str]


class RuleParseResultV31(V31StructureModel):
    schema_version: Literal["3.1.0"]
    rule_version: str = Field(min_length=1, max_length=260)
    rule_set_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    generated_at: datetime
    status: Literal["draft"]
    executable: Literal[False]
    source: ProvenanceV31
    parser: ParserProvenanceV31
    catalog_ref: CatalogRefV31
    candidate_ref: CandidateRefV31
    delivery_ref: CompleteDeliveryRefV31
    fact_declarations: list[FactDeclarationV31] = Field(min_length=1)
    test_cases: list[TestCaseV31] = Field(min_length=1)
    agent2_readiness_ready: bool

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("generatedAt requires a timezone")
        return value


ExpressionNodeV31.model_rebuild()
ConditionNodeV31.model_rebuild()
