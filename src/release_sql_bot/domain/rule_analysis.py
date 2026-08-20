"""确定性的释放规则规范化与变更分析。"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator


class RuleModel(BaseModel):
    """释放规则领域使用的封闭且不依赖框架的模型。"""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RuleTarget(StrEnum):
    PROJECT_REPORT = "project_report"
    RAW_DATA = "raw_data"


class RuleStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class Combinator(StrEnum):
    ALL = "all"
    ANY = "any"


class RuleOperator(StrEnum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    BETWEEN = "between"
    LIKE = "like"
    IS_NULL = "is_null"
    NOT_NULL = "not_null"


class RuleValueType(StrEnum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"


class RuleNullPolicy(StrEnum):
    FAIL = "fail"
    PASS = "pass"
    EXCLUDE = "exclude"
    ERROR = "error"


class RulePredicate(RuleModel):
    condition_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    field: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$")
    operator: RuleOperator
    value: Any = None
    value_type: RuleValueType
    null_policy: RuleNullPolicy
    description: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def validate_operator_value(self) -> RulePredicate:
        has_value = "value" in self.model_fields_set
        null_operators = {RuleOperator.IS_NULL, RuleOperator.NOT_NULL}

        if self.operator in null_operators:
            if has_value:
                raise ValueError(f"{self.operator.value} 不能定义 value")
            return self
        if not has_value:
            raise ValueError(f"{self.operator.value} 必须定义 value")

        values = self.value if isinstance(self.value, list) else [self.value]
        if self.operator in {RuleOperator.IN, RuleOperator.NOT_IN}:
            if not isinstance(self.value, list) or not self.value:
                raise ValueError(f"{self.operator.value} 必须使用非空数组")
            canonical_items = [_canonical_json_value(item) for item in self.value]
            if len(canonical_items) != len(set(canonical_items)):
                raise ValueError(f"{self.operator.value} 的数组值必须唯一")
        elif self.operator is RuleOperator.BETWEEN:
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError("between 必须包含两个边界")
            if self.value_type is RuleValueType.BOOLEAN:
                raise ValueError("between 与 boolean 不兼容")
        elif isinstance(self.value, list):
            raise ValueError(f"{self.operator.value} 必须使用标量值")

        if self.operator is RuleOperator.LIKE and self.value_type is not RuleValueType.STRING:
            raise ValueError("like 只与 string 兼容")
        for value in values:
            _validate_typed_value(value, self.value_type)
        if self.operator is RuleOperator.BETWEEN:
            _validate_ordered_boundaries(self.value, self.value_type)
        return self


class RuleConditionGroup(RuleModel):
    group_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    combinator: Combinator
    negate: StrictBool = False
    conditions: tuple[RuleConditionGroup | RulePredicate, ...] = Field(min_length=1)


class RuleMetadata(RuleModel):
    created_by: str = Field(min_length=1, max_length=256)
    change_reason: str = Field(min_length=1, max_length=2_000)
    source_ticket: str | None = Field(default=None, max_length=256)


class ReleaseRule(RuleModel):
    schema_version: Literal["1.0"]
    rule_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    project_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    version: StrictInt = Field(ge=1)
    target: RuleTarget
    status: RuleStatus
    effective_from: datetime
    effective_to: datetime | None = None
    condition: RuleConditionGroup
    metadata: RuleMetadata

    @model_validator(mode="after")
    def validate_effective_window(self) -> ReleaseRule:
        if self.effective_from.tzinfo is None:
            raise ValueError("effective_from 必须包含时区偏移")
        if self.effective_to is not None:
            if self.effective_to.tzinfo is None:
                raise ValueError("effective_to 必须包含时区偏移")
            if self.effective_to <= self.effective_from:
                raise ValueError("effective_to 必须晚于 effective_from")
        return self


class DuplicateConditionIdError(ValueError):
    """条件身份不唯一时拒绝分析，禁止静默覆盖。"""

    def __init__(self, condition_ids: tuple[str, ...]) -> None:
        self.condition_ids = condition_ids
        joined = ", ".join(condition_ids)
        super().__init__(f"condition_id 重复：{joined}")


class ConditionSnapshot(RuleModel):
    condition_id: str
    path: str
    predicate: dict[str, Any]


class CanonicalRuleArtifact(RuleModel):
    rule_id: str
    project_id: str
    version: int
    canonical_json: str
    content_hash: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    conditions: tuple[ConditionSnapshot, ...]
    logic_signature: dict[str, Any]


class BaselineMode(StrEnum):
    NONE = "none"
    PREVIOUS = "previous"


class ModifiedCondition(RuleModel):
    condition_id: str
    before_path: str
    after_path: str
    changed_fields: tuple[str, ...]
    before: dict[str, Any]
    after: dict[str, Any]


class LogicStructureChange(RuleModel):
    before: dict[str, Any]
    after: dict[str, Any]


class RuleDiff(RuleModel):
    baseline_mode: BaselineMode
    current_hash: str
    baseline_hash: str | None
    added: tuple[ConditionSnapshot, ...]
    removed: tuple[ConditionSnapshot, ...]
    modified: tuple[ModifiedCondition, ...]
    logic_structure_change: LogicStructureChange | None


class ExceptionSetSemantics(StrEnum):
    UNRELEASED_AND_NOT_ELIGIBLE = "unreleased_and_not_release_eligible"
    UNRELEASED_AND_ELIGIBLE = "unreleased_and_release_eligible"


class TargetSchemaField(RuleModel):
    logical_name: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$",
    )
    value_type: RuleValueType


class TargetSchemaSnapshot(RuleModel):
    snapshot_id: str = Field(min_length=1, max_length=160)
    version: StrictInt = Field(ge=1)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    fields: tuple[TargetSchemaField, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_fields(self) -> TargetSchemaSnapshot:
        names = [field.logical_name for field in self.fields]
        duplicates = sorted(name for name in set(names) if names.count(name) > 1)
        if duplicates:
            raise ValueError(f"目标 Schema 包含重复逻辑字段：{duplicates}")
        return self


class RulePlanningPrerequisites(RuleModel):
    exception_set_semantics: ExceptionSetSemantics | None = None
    target_schema: TargetSchemaSnapshot | None = None


class RulePlanningIssue(RuleModel):
    code: str
    message: str
    field_path: str


class RulePlanningReadiness(RuleModel):
    status: Literal["ready", "blocked"]
    issues: tuple[RulePlanningIssue, ...]


def canonicalize_rule(rule: ReleaseRule) -> CanonicalRuleArtifact:
    """为有效规则返回稳定 JSON 表示和内容哈希。"""

    conditions = _condition_snapshots(rule.condition)
    canonical_json = _canonical_model_json(rule)
    return CanonicalRuleArtifact(
        rule_id=rule.rule_id,
        project_id=rule.project_id,
        version=rule.version,
        canonical_json=canonical_json,
        content_hash=f"sha256:{sha256(canonical_json.encode('utf-8')).hexdigest()}",
        conditions=conditions,
        logic_signature=_logic_signature(rule.condition),
    )


def diff_rules(current: ReleaseRule, baseline: ReleaseRule | None) -> RuleDiff:
    """比较当前规则和可选历史基线。"""

    current_artifact = canonicalize_rule(current)
    if baseline is None:
        return RuleDiff(
            baseline_mode=BaselineMode.NONE,
            current_hash=current_artifact.content_hash,
            baseline_hash=None,
            added=current_artifact.conditions,
            removed=(),
            modified=(),
            logic_structure_change=None,
        )

    baseline_artifact = canonicalize_rule(baseline)
    current_conditions = {
        snapshot.condition_id: snapshot for snapshot in current_artifact.conditions
    }
    baseline_conditions = {
        snapshot.condition_id: snapshot for snapshot in baseline_artifact.conditions
    }

    added = tuple(
        current_conditions[condition_id]
        for condition_id in sorted(current_conditions.keys() - baseline_conditions.keys())
    )
    removed = tuple(
        baseline_conditions[condition_id]
        for condition_id in sorted(baseline_conditions.keys() - current_conditions.keys())
    )
    modified: list[ModifiedCondition] = []
    for condition_id in sorted(current_conditions.keys() & baseline_conditions.keys()):
        before = baseline_conditions[condition_id]
        after = current_conditions[condition_id]
        if before.predicate == after.predicate:
            continue
        changed_fields = tuple(
            sorted(
                field_name
                for field_name in before.predicate.keys() | after.predicate.keys()
                if before.predicate.get(field_name) != after.predicate.get(field_name)
            )
        )
        modified.append(
            ModifiedCondition(
                condition_id=condition_id,
                before_path=before.path,
                after_path=after.path,
                changed_fields=changed_fields,
                before=before.predicate,
                after=after.predicate,
            )
        )

    logic_change = None
    if baseline_artifact.logic_signature != current_artifact.logic_signature:
        logic_change = LogicStructureChange(
            before=baseline_artifact.logic_signature,
            after=current_artifact.logic_signature,
        )

    return RuleDiff(
        baseline_mode=BaselineMode.PREVIOUS,
        current_hash=current_artifact.content_hash,
        baseline_hash=baseline_artifact.content_hash,
        added=added,
        removed=removed,
        modified=tuple(modified),
        logic_structure_change=logic_change,
    )


def evaluate_rule_planning_readiness(
    rule: ReleaseRule,
    prerequisites: RulePlanningPrerequisites,
) -> RulePlanningReadiness:
    """业务语义或真实 Schema 缺失时阻塞 SQL 规划。"""

    conditions = _condition_snapshots(rule.condition)
    issues: list[RulePlanningIssue] = []
    if prerequisites.exception_set_semantics is None:
        issues.append(
            RulePlanningIssue(
                code="EXCEPTION_SET_SEMANTICS_MISSING",
                message="异常集合语义必须由业务负责人显式选择",
                field_path="exceptionSetSemantics",
            )
        )
    if prerequisites.target_schema is None:
        issues.append(
            RulePlanningIssue(
                code="TARGET_SCHEMA_MISSING",
                message="必须提供版本化的真实目标 Schema 快照",
                field_path="targetSchema",
            )
        )
    elif not prerequisites.target_schema.fields:
        issues.append(
            RulePlanningIssue(
                code="TARGET_SCHEMA_INCOMPLETE",
                message="目标 Schema 快照必须包含逻辑字段",
                field_path="targetSchema.fields",
            )
        )
    else:
        schema_fields = {
            field.logical_name: field.value_type for field in prerequisites.target_schema.fields
        }
        for condition in conditions:
            logical_name = condition.predicate["field"]
            rule_type = RuleValueType(condition.predicate["value_type"])
            if logical_name not in schema_fields:
                issues.append(
                    RulePlanningIssue(
                        code="RULE_FIELD_NOT_IN_SCHEMA",
                        message=f"规则字段不在目标 Schema 中：{logical_name}",
                        field_path=f"{condition.path}.field",
                    )
                )
            elif schema_fields[logical_name] is not rule_type:
                issues.append(
                    RulePlanningIssue(
                        code="RULE_FIELD_TYPE_MISMATCH",
                        message=f"规则字段类型与目标 Schema 不一致：{logical_name}",
                        field_path=f"{condition.path}.value_type",
                    )
                )

    return RulePlanningReadiness(
        status="blocked" if issues else "ready",
        issues=tuple(issues),
    )


def _condition_snapshots(group: RuleConditionGroup) -> tuple[ConditionSnapshot, ...]:
    snapshots: list[ConditionSnapshot] = []

    def visit(node: RuleConditionGroup, path: str) -> None:
        for index, child in enumerate(node.conditions):
            child_path = f"{path}.conditions[{index}]"
            if isinstance(child, RuleConditionGroup):
                visit(child, child_path)
            else:
                snapshots.append(
                    ConditionSnapshot(
                        condition_id=child.condition_id,
                        path=child_path,
                        predicate=json.loads(_canonical_model_json(child)),
                    )
                )

    visit(group, "$.condition")
    identifiers = [snapshot.condition_id for snapshot in snapshots]
    duplicates = tuple(
        sorted(identifier for identifier in set(identifiers) if identifiers.count(identifier) > 1)
    )
    if duplicates:
        raise DuplicateConditionIdError(duplicates)
    return tuple(snapshots)


def _logic_signature(node: RuleConditionGroup | RulePredicate) -> dict[str, Any]:
    if isinstance(node, RulePredicate):
        return {"node_type": "predicate", "condition_id": node.condition_id}
    signature: dict[str, Any] = {
        "node_type": "group",
        "combinator": node.combinator.value,
        "negate": node.negate,
        "conditions": [_logic_signature(child) for child in node.conditions],
    }
    if node.group_id is not None:
        signature["group_id"] = node.group_id
    return signature


def _canonical_model_json(model: BaseModel) -> str:
    payload = model.model_dump(mode="json", exclude_none=True)
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _canonical_json_value(value: Any) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("规则值必须是有效 JSON") from error


def _validate_typed_value(value: Any, value_type: RuleValueType) -> None:
    if isinstance(value, (dict, tuple)):
        raise ValueError("规则值必须是 JSON 标量或标量数组")
    if value_type is RuleValueType.BOOLEAN:
        valid = isinstance(value, bool)
    elif value_type is RuleValueType.INTEGER:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif value_type is RuleValueType.NUMBER:
        valid = isinstance(value, (int, float)) and not isinstance(value, bool)
        if valid and not math.isfinite(value):
            valid = False
    elif value_type in {RuleValueType.STRING, RuleValueType.DATE, RuleValueType.DATETIME}:
        valid = isinstance(value, str)
    else:
        valid = False
    if not valid:
        raise ValueError(f"value 与 value_type={value_type.value} 不兼容")

    if value_type is RuleValueType.DATE:
        try:
            date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("date 值必须使用 ISO 8601 日历日期格式") from error
    elif value_type is RuleValueType.DATETIME:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("datetime 值必须使用 ISO 8601 格式") from error
        if "T" not in value or parsed.tzinfo is None:
            raise ValueError("datetime 值必须包含时间和时区偏移")


def _validate_ordered_boundaries(values: list[Any], value_type: RuleValueType) -> None:
    lower, upper = values
    if value_type is RuleValueType.DATE:
        lower, upper = date.fromisoformat(lower), date.fromisoformat(upper)
    elif value_type is RuleValueType.DATETIME:
        lower = datetime.fromisoformat(lower.replace("Z", "+00:00"))
        upper = datetime.fromisoformat(upper.replace("Z", "+00:00"))
    if lower > upper:
        raise ValueError("between 边界必须有序")
