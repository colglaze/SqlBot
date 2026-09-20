"""Independent 3.1.0 rule-tree evaluator. Mirrors Agent1 stage semantics."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from release_sql_bot.domain.fact_bindings_v3 import (
    FactDataTypeV3,
    NullPolicyV3,
    RuleOperatorV3,
    RuleOutcomeV3,
    RuleStageNameV3,
)
from release_sql_bot.domain.rule_structure_v31 import (
    EVALUATION_TIMEZONE_V31,
    ConditionKindV31,
    ConditionNodeV31,
    EmptyCollectionPolicyV31,
    ExpressionKindV31,
    ExpressionNodeV31,
    MissingMemberPolicyV31,
    RuleNodeStatusV31,
    RuleStructureCandidateV31,
    RuntimeParameterV31,
)

_BUSINESS_TZ = ZoneInfo(EVALUATION_TIMEZONE_V31)

INDETERMINATE_REASON = "FACT_VALUE_MISSING_OR_INVALID"
CONFIRMATION_REASON = "BUSINESS_CONFIRMATION_REQUIRED"


class EvaluationResultV31(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INDETERMINATE = "indeterminate"


class _Missing:
    pass


MISSING = _Missing()
EvalValue = str | int | float | bool | Decimal | date | datetime | list[Any] | None | _Missing


@dataclass(frozen=True, slots=True)
class RuleEvaluationV31:
    outcome: RuleOutcomeV3
    matched_rule_codes: tuple[str, ...]
    reason_code: str


@dataclass(frozen=True, slots=True)
class EvaluationContextV31:
    facts: Mapping[str, Any]
    runtime: Mapping[str, Any]
    members: Mapping[str, Mapping[str, Any]]


def evaluate_rule_structure_v31(
    candidate: RuleStructureCandidateV31,
    given: Mapping[str, Any],
    *,
    runtime: Mapping[str, Any] | None = None,
    members: Mapping[str, Mapping[str, Any]] | None = None,
) -> RuleEvaluationV31:
    if candidate.blocking_issues:
        return RuleEvaluationV31(RuleOutcomeV3.INDETERMINATE, (), CONFIRMATION_REASON)
    resolved = dict(runtime or {})
    for parameter in candidate.runtime_parameters:
        if parameter.name not in resolved and parameter.bound_value is not None:
            resolved[parameter.name] = parameter.bound_value
    context = EvaluationContextV31(facts=given, runtime=resolved, members=members or {})
    parameters = {item.name: item for item in candidate.runtime_parameters}
    outcome = candidate.default_outcome
    reason_code = candidate.default_reason_code
    matched: list[str] = []
    for stage in candidate.stages:
        indeterminate = False
        for rule in stage.rules:
            if rule.status is RuleNodeStatusV31.BLOCKED or rule.when is None:
                continue
            try:
                result = evaluate_condition_v31(rule.when, context, parameters)
            except (ArithmeticError, TypeError, ValueError):
                result = EvaluationResultV31.INDETERMINATE
            if result is EvaluationResultV31.PASS:
                if rule.outcome is not None:
                    outcome = rule.outcome
                if rule.reason_code is not None:
                    reason_code = rule.reason_code
                matched.append(rule.rule_code)
                if stage.stage != RuleStageNameV3.ELIGIBILITY.value:
                    return RuleEvaluationV31(outcome, tuple(matched), reason_code)
                break
            if result is EvaluationResultV31.INDETERMINATE:
                indeterminate = True
        else:
            if indeterminate:
                return RuleEvaluationV31(
                    RuleOutcomeV3.INDETERMINATE,
                    tuple(matched),
                    INDETERMINATE_REASON,
                )
    return RuleEvaluationV31(outcome, tuple(matched), reason_code)


def evaluate_condition_v31(
    node: ConditionNodeV31,
    context: EvaluationContextV31,
    parameters: dict[str, RuntimeParameterV31],
) -> EvaluationResultV31:
    if not node.enabled:
        return EvaluationResultV31.PASS
    if node.kind is ConditionKindV31.COMPARE:
        return _evaluate_compare(node, context, parameters)
    if node.kind is ConditionKindV31.ALL_MEMBERS:
        return _evaluate_all_members(node, context, parameters)
    results = [evaluate_condition_v31(child, context, parameters) for child in node.children]
    if node.kind is ConditionKindV31.NOT:
        child = results[0]
        if child is EvaluationResultV31.PASS:
            return EvaluationResultV31.FAIL
        if child is EvaluationResultV31.FAIL:
            return EvaluationResultV31.PASS
        return EvaluationResultV31.INDETERMINATE
    if node.kind is ConditionKindV31.ALL:
        if EvaluationResultV31.FAIL in results:
            return EvaluationResultV31.FAIL
        if EvaluationResultV31.INDETERMINATE in results:
            return EvaluationResultV31.INDETERMINATE
        return EvaluationResultV31.PASS
    if EvaluationResultV31.PASS in results:
        return EvaluationResultV31.PASS
    if EvaluationResultV31.INDETERMINATE in results:
        return EvaluationResultV31.INDETERMINATE
    return EvaluationResultV31.FAIL


def _evaluate_compare(
    node: ConditionNodeV31,
    context: EvaluationContextV31,
    parameters: dict[str, RuntimeParameterV31],
) -> EvaluationResultV31:
    assert node.left is not None and node.operator is not None
    left = _evaluate_expression(node.left, context, parameters)
    operator = node.operator
    if operator is RuleOperatorV3.IS_NULL:
        return EvaluationResultV31.PASS if left is None else EvaluationResultV31.FAIL
    if operator is RuleOperatorV3.IS_NOT_NULL:
        return EvaluationResultV31.FAIL if left is None else EvaluationResultV31.PASS
    if operator is RuleOperatorV3.IS_BLANK:
        return EvaluationResultV31.PASS if left is None or left == "" else EvaluationResultV31.FAIL
    if operator is RuleOperatorV3.IS_NOT_BLANK:
        return EvaluationResultV31.FAIL if left is None or left == "" else EvaluationResultV31.PASS
    if isinstance(left, _Missing) or left is None or node.right is None:
        return _null_result(node.null_policy)
    right = _evaluate_expression(node.right, context, parameters)
    if isinstance(right, _Missing) or right is None:
        return _null_result(node.null_policy)
    try:
        matched = _compare_values(operator, left, right)
    except (TypeError, ValueError):
        return EvaluationResultV31.INDETERMINATE
    return EvaluationResultV31.PASS if matched else EvaluationResultV31.FAIL


def _compare_values(operator: RuleOperatorV3, left: Any, right: Any) -> bool:
    if operator is RuleOperatorV3.EQ:
        return left == right
    if operator is RuleOperatorV3.NE:
        return left != right
    if operator is RuleOperatorV3.IN:
        return isinstance(right, list) and left in right
    if operator is RuleOperatorV3.NOT_IN:
        return isinstance(right, list) and left not in right
    left_value: Any = left
    right_value: Any = right
    left_decimal = _as_decimal(left)
    right_decimal = _as_decimal(right)
    if not isinstance(left_decimal, _Missing) and not isinstance(right_decimal, _Missing):
        left_value, right_value = left_decimal, right_decimal
    if operator is RuleOperatorV3.GT:
        return left_value > right_value
    if operator is RuleOperatorV3.GTE:
        return left_value >= right_value
    if operator is RuleOperatorV3.LT:
        return left_value < right_value
    return left_value <= right_value


def _evaluate_all_members(
    node: ConditionNodeV31,
    context: EvaluationContextV31,
    parameters: dict[str, RuntimeParameterV31],
) -> EvaluationResultV31:
    assert node.collection_fact_code is not None
    assert node.empty_collection_policy is not None
    assert node.missing_member_policy is not None
    assert node.member_predicate is not None
    raw = context.facts.get(node.collection_fact_code, MISSING)
    if isinstance(raw, _Missing) or raw is None or not isinstance(raw, list):
        return EvaluationResultV31.INDETERMINATE
    keys = _unique_preserve_order(raw)
    if not keys:
        if node.empty_collection_policy is EmptyCollectionPolicyV31.PASS:
            return EvaluationResultV31.PASS
        if node.empty_collection_policy is EmptyCollectionPolicyV31.FAIL:
            return EvaluationResultV31.FAIL
        return EvaluationResultV31.INDETERMINATE
    saw_indeterminate = False
    for key in keys:
        snapshot = context.members.get(str(key))
        if snapshot is None:
            if node.missing_member_policy is MissingMemberPolicyV31.FAIL:
                return EvaluationResultV31.FAIL
            saw_indeterminate = True
            continue
        member_context = EvaluationContextV31(
            facts=snapshot,
            runtime=context.runtime,
            members=context.members,
        )
        predicate = evaluate_condition_v31(node.member_predicate, member_context, parameters)
        if predicate is EvaluationResultV31.FAIL:
            return EvaluationResultV31.FAIL
        if predicate is EvaluationResultV31.INDETERMINATE:
            saw_indeterminate = True
    if saw_indeterminate:
        return EvaluationResultV31.INDETERMINATE
    return EvaluationResultV31.PASS


def _evaluate_expression(
    expression: ExpressionNodeV31,
    context: EvaluationContextV31,
    parameters: dict[str, RuntimeParameterV31],
) -> EvalValue:
    if expression.kind is ExpressionKindV31.LITERAL:
        return expression.value
    if expression.kind is ExpressionKindV31.PARAMETER:
        name = expression.parameter_name or ""
        parameter = parameters.get(name)
        if name in context.runtime:
            value: EvalValue = context.runtime[name]
            if parameter is not None and parameter.data_type in {
                FactDataTypeV3.DATE,
                FactDataTypeV3.DATETIME,
            }:
                return to_business_date(value)
            return value
        if parameter is not None and parameter.bound_value is not None:
            if parameter.data_type in {FactDataTypeV3.DATE, FactDataTypeV3.DATETIME}:
                return to_business_date(parameter.bound_value)
            return parameter.bound_value
        return MISSING
    if expression.kind is ExpressionKindV31.FACT:
        code = expression.fact_code or ""
        if code in context.facts:
            return context.facts[code]
        return MISSING
    values = [_evaluate_expression(child, context, parameters) for child in expression.children]
    if expression.kind is ExpressionKindV31.COALESCE:
        return next(
            (item for item in values if not isinstance(item, _Missing) and item is not None),
            MISSING,
        )
    if expression.kind is ExpressionKindV31.DATE_ADD:
        temporal = to_business_date(values[0])
        amount = _as_decimal(values[1])
        if isinstance(temporal, _Missing) or isinstance(amount, _Missing):
            return MISSING
        return temporal + timedelta(days=int(amount))
    numbers = [_as_decimal(item) for item in values]
    if any(isinstance(item, _Missing) for item in numbers):
        return MISSING
    decimals = [item for item in numbers if isinstance(item, Decimal)]
    if expression.kind is ExpressionKindV31.ADD:
        return sum(decimals, Decimal(0))
    if expression.kind is ExpressionKindV31.MULTIPLY:
        result = Decimal(1)
        for item in decimals:
            result *= item
        return result
    if expression.kind is ExpressionKindV31.SUBTRACT:
        return decimals[0] - decimals[1]
    if decimals[1] == 0:
        return MISSING
    return decimals[0] / decimals[1]


def _null_result(policy: NullPolicyV3) -> EvaluationResultV31:
    if policy is NullPolicyV3.PASS:
        return EvaluationResultV31.PASS
    if policy is NullPolicyV3.FAIL:
        return EvaluationResultV31.FAIL
    return EvaluationResultV31.INDETERMINATE


def to_business_date(value: object) -> date | _Missing:
    if isinstance(value, _Missing) or value is None:
        return MISSING
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=_BUSINESS_TZ)
        return moment.astimezone(_BUSINESS_TZ).date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return MISSING
    try:
        if "T" in value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return to_business_date(parsed)
        return date.fromisoformat(value)
    except ValueError:
        return MISSING


def _as_decimal(value: EvalValue) -> Decimal | _Missing:
    if isinstance(value, _Missing) or value is None or isinstance(value, bool):
        return MISSING
    if isinstance(value, Decimal):
        return value
    try:
        if isinstance(value, int | float | str):
            return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return MISSING
    return MISSING


def _unique_preserve_order(values: Sequence[Any]) -> list[Any]:
    seen: set[str] = set()
    unique: list[Any] = []
    for item in values:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
