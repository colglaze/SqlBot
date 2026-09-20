"""Deterministic view-shaped SQL compiler from a 3.1.0 rule tree."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.domain.complete_delivery_v31 import CompleteDeliveryV31
from release_sql_bot.domain.composition_plan_v31 import COMPILATION_BLOCKERS_V31
from release_sql_bot.domain.fact_bindings_v3 import (
    AggregationFunctionV3,
    AggregationModeV3,
    FactKindV3,
    RuleOperatorV3,
    RuleOutcomeV3,
    RuleStageNameV3,
)
from release_sql_bot.domain.rule_structure_v31 import (
    ConditionKindV31,
    ConditionNodeV31,
    EmptyCollectionPolicyV31,
    ExpressionKindV31,
    ExpressionNodeV31,
    RuleNodeStatusV31,
    RuleNodeV31,
    RuleStageV31,
)
from release_sql_bot.domain.view_shaped_sql_v31 import (
    FactMappingGrantV31,
    SubjectRelationV31,
    ViewShapedMappingBundleV31,
    ViewShapedSqlCandidateV31,
)

_STAGE_PREFIX = {
    "stateGuards": "sg",
    "prerequisites": "pr",
    "eligibility": "el",
    "postGates": "pg",
    "exclusions": "ex",
}
_DATEADD_UNIT = {"day": "DAY", "hour": "HOUR", "minute": "MINUTE"}
_EARLY_TERMINATE = (
    RuleStageNameV3.STATE_GUARDS.value,
    RuleStageNameV3.PREREQUISITES.value,
)
_OVERRIDE_STAGES = (
    RuleStageNameV3.POST_GATES.value,
    RuleStageNameV3.EXCLUSIONS.value,
)


class ViewShapedSqlCompilationBlockedV31(RuntimeError):
    def __init__(self, blockers: tuple[str, ...]) -> None:
        super().__init__("View-shaped SQL is not compiled")
        self.blockers = blockers


def compile_view_shaped_sql_v31(
    delivery: CompleteDeliveryV31,
    mapping: ViewShapedMappingBundleV31 | None = None,
) -> ViewShapedSqlCandidateV31:
    blockers = compile_blockers_v31(delivery, mapping)
    if mapping is None or blockers:
        raise ViewShapedSqlCompilationBlockedV31(blockers or ("METADATA_REVIEW",))
    return _compile(delivery, mapping)


def mapping_capability_blockers(delivery: CompleteDeliveryV31) -> tuple[str, ...]:
    blockers: list[str] = []
    for request in delivery.batch.requests:
        blocker = _unsupported_fact_shape(request.payload.fact.fact_kind, request.payload)
        if blocker is not None:
            blockers.append(blocker)
    return tuple(dict.fromkeys(blockers))


def compile_blockers_v31(
    delivery: CompleteDeliveryV31,
    mapping: ViewShapedMappingBundleV31 | None = None,
) -> tuple[str, ...]:
    blockers: list[str] = []
    blockers.extend(mapping_capability_blockers(delivery))
    if mapping is None:
        blockers.extend(COMPILATION_BLOCKERS_V31)
        return tuple(dict.fromkeys(blockers))
    required = set(delivery.candidate.required_fact_codes)
    granted = {item.fact_code for item in mapping.grants}
    if required - granted:
        blockers.append("METADATA_REVIEW")
    return tuple(dict.fromkeys(blockers))


def _compile(
    delivery: CompleteDeliveryV31,
    mapping: ViewShapedMappingBundleV31,
) -> ViewShapedSqlCandidateV31:
    grants = {item.fact_code: item for item in mapping.grants}
    literals: dict[str, str | int | float | bool] = {}
    parameters: list[str] = []
    for binding in mapping.subject.key_bindings:
        _add_param(parameters, binding.parameter_name)
    for grant in mapping.grants:
        for binding in grant.key_bindings:
            _add_param(parameters, binding.parameter_name)
    for runtime in delivery.candidate.runtime_parameters:
        _add_param(parameters, runtime.name)

    fact_by_code = {item.payload.fact.fact_code: item.payload for item in delivery.batch.requests}
    fact_applies = [
        _fact_apply(grant, mapping.subject, fact_by_code.get(grant.fact_code))
        for grant in mapping.grants
        if grant.cardinality == "scalar"
    ]
    hit_selects: list[str] = []
    active_rules: list[tuple[str, RuleNodeV31]] = []
    for stage in delivery.candidate.stages:
        for rule in stage.rules:
            if rule.status is RuleNodeStatusV31.BLOCKED or rule.when is None:
                continue
            flag = _hit_flag(stage, rule)
            predicate = _condition_sql(rule.when, grants, literals, parameters, member=False)
            hit_selects.append(
                "CASE WHEN "
                f"{predicate} THEN 1 WHEN NOT ({predicate}) THEN 0 ELSE NULL END AS {_q(flag)}"
            )
            active_rules.append((stage.stage, rule))

    subject = mapping.subject
    subject_key = subject.key_bindings[0]
    if hit_selects:
        outcome_sql = _outcome_case(delivery, active_rules)
        reason_sql = _reason_case(delivery, active_rules)
        matched_sql = _matched_case(active_rules)
        hit_apply = [
            "CROSS APPLY (",
            "  SELECT",
            "    " + ",\n    ".join(hit_selects),
            f") AS {_q('hits')}",
        ]
    else:
        outcome_sql = _n(delivery.candidate.default_outcome.value)
        reason_sql = _n(delivery.candidate.default_reason_code)
        matched_sql = _n("")
        hit_apply = []
    where_sql = f"{_q('subject')}.{_q(subject_key.column_name)} = {_p(subject_key.parameter_name)}"
    sql = "\n".join(
        [
            "SELECT",
            f"  {_q('subject')}.{_q(subject_key.column_name)} AS {_q('entity_keys')},",
            f"  {outcome_sql} AS {_q('outcome')},",
            f"  {reason_sql} AS {_q('reasonCode')},",
            f"  {matched_sql} AS {_q('matchedRuleCodes')}",
            f"FROM {_q(subject.schema_name)}.{_q(subject.relation_name)} AS {_q('subject')}",
            *fact_applies,
            *hit_apply,
            f"WHERE {where_sql}",
        ]
    )
    mapping_sha = canonical_sha256(mapping)
    content_sha = canonical_sha256(
        {
            "ruleVersion": delivery.rule_version,
            "sqlTemplate": sql,
            "parameterNames": parameters,
            "mappingSha256": mapping_sha,
        }
    )
    return ViewShapedSqlCandidateV31(
        rule_version=delivery.rule_version,
        sql_template=sql,
        parameter_names=tuple(parameters),
        bound_literals=literals,
        content_sha256=content_sha,
        mapping_sha256=mapping_sha,
        static_status="blocked",
    )


def _unsupported_fact_shape(kind: FactKindV3, payload: object) -> str | None:
    aggregation = getattr(getattr(payload, "query_requirements", None), "aggregation", None)
    if kind is FactKindV3.AGGREGATE:
        allowed = (
            aggregation is not None
            and aggregation.mode is AggregationModeV3.COMPUTE
            and aggregation.function is AggregationFunctionV3.COUNT
            and not aggregation.group_by_field_ids
            and aggregation.distinct is not True
        )
        return None if allowed else "AGGREGATION"
    if kind is FactKindV3.EXISTS:
        if aggregation is not None and aggregation.mode is AggregationModeV3.EXISTS:
            return None
        return "EXISTS_FACT"
    return None


def _key_joins(grant: FactMappingGrantV31, subject: SubjectRelationV31, src: str) -> str:
    subject_by_param = {item.parameter_name: item for item in subject.key_bindings}
    joins: list[str] = []
    for binding in grant.key_bindings:
        subject_binding = subject_by_param.get(binding.parameter_name)
        if subject_binding is None:
            right = _p(binding.parameter_name)
        else:
            right = f"{_q('subject')}.{_q(subject_binding.column_name)}"
        joins.append(f"{_q(src)}.{_q(binding.column_name)} = {right}")
    return " AND ".join(joins)


def _fact_apply(
    grant: FactMappingGrantV31,
    subject: SubjectRelationV31,
    payload: object | None,
) -> str:
    kind = getattr(getattr(payload, "fact", None), "fact_kind", None)
    if kind is FactKindV3.AGGREGATE:
        return _count_apply(grant, subject)
    if kind is FactKindV3.EXISTS:
        return _exists_apply(grant, subject)
    return _scalar_apply(grant, subject)


def _count_apply(grant: FactMappingGrantV31, subject: SubjectRelationV31) -> str:
    alias = _fact_alias(grant.fact_code)
    src = "src"
    return (
        f"OUTER APPLY (\n"
        f"  SELECT COUNT(*) AS {_q('fact_value')}\n"
        f"  FROM {_q(grant.schema_name)}.{_q(grant.relation_name)} AS {_q(src)}\n"
        f"  WHERE {_key_joins(grant, subject, src)}\n"
        f") AS {_q(alias)}"
    )


def _exists_apply(grant: FactMappingGrantV31, subject: SubjectRelationV31) -> str:
    alias = _fact_alias(grant.fact_code)
    src = "src"
    return (
        f"OUTER APPLY (\n"
        f"  SELECT CASE WHEN EXISTS (\n"
        f"    SELECT 1 FROM {_q(grant.schema_name)}.{_q(grant.relation_name)} AS {_q(src)}\n"
        f"    WHERE {_key_joins(grant, subject, src)}\n"
        f"  ) THEN 1 ELSE 0 END AS {_q('fact_value')}\n"
        f") AS {_q(alias)}"
    )


def _scalar_apply(grant: FactMappingGrantV31, subject: SubjectRelationV31) -> str:
    alias = _fact_alias(grant.fact_code)
    src = "src"
    return (
        f"OUTER APPLY (\n"
        f"  SELECT TOP (1) {_q(src)}.{_q(grant.column_name)} AS {_q('fact_value')}\n"
        f"  FROM {_q(grant.schema_name)}.{_q(grant.relation_name)} AS {_q(src)}\n"
        f"  WHERE {_key_joins(grant, subject, src)}\n"
        f") AS {_q(alias)}"
    )


def _condition_sql(
    node: ConditionNodeV31,
    grants: Mapping[str, FactMappingGrantV31],
    literals: dict[str, str | int | float | bool],
    parameters: list[str],
    *,
    member: bool,
) -> str:
    if not node.enabled:
        return "(1 = 1)"
    if node.kind is ConditionKindV31.ALL:
        parts = [
            _condition_sql(child, grants, literals, parameters, member=member)
            for child in node.children
        ]
        return "(" + " AND ".join(parts) + ")"
    if node.kind is ConditionKindV31.ANY:
        parts = [
            _condition_sql(child, grants, literals, parameters, member=member)
            for child in node.children
        ]
        return "(" + " OR ".join(parts) + ")"
    if node.kind is ConditionKindV31.NOT:
        inner = _condition_sql(node.children[0], grants, literals, parameters, member=member)
        return f"(NOT {inner})"
    if node.kind is ConditionKindV31.ALL_MEMBERS:
        return _all_members_sql(node, grants, literals, parameters)
    return _compare_sql(node, grants, literals, parameters, member=member)


def _compare_sql(
    node: ConditionNodeV31,
    grants: Mapping[str, FactMappingGrantV31],
    literals: dict[str, str | int | float | bool],
    parameters: list[str],
    *,
    member: bool,
) -> str:
    assert node.left is not None and node.operator is not None
    left = _expression_sql(node.left, grants, literals, parameters, member=member)
    operator = node.operator
    if operator is RuleOperatorV3.IS_NULL:
        return f"({left} IS NULL)"
    if operator is RuleOperatorV3.IS_NOT_NULL:
        return f"({left} IS NOT NULL)"
    if operator is RuleOperatorV3.IS_BLANK:
        blank = _p(_lit(node.id, "", literals, parameters))
        return f"({left} IS NULL OR {left} = {blank})"
    if operator is RuleOperatorV3.IS_NOT_BLANK:
        blank = _p(_lit(node.id, "", literals, parameters))
        return f"({left} IS NOT NULL AND {left} <> {blank})"
    assert node.right is not None
    right = _expression_sql(node.right, grants, literals, parameters, member=member)
    sql_op = {
        RuleOperatorV3.EQ: "=",
        RuleOperatorV3.NE: "<>",
        RuleOperatorV3.GT: ">",
        RuleOperatorV3.GTE: ">=",
        RuleOperatorV3.LT: "<",
        RuleOperatorV3.LTE: "<=",
    }.get(operator)
    if sql_op is None:
        raise ViewShapedSqlCompilationBlockedV31(("UNSUPPORTED_OPERATOR",))
    return f"({left} {sql_op} {right})"


def _all_members_sql(
    node: ConditionNodeV31,
    grants: Mapping[str, FactMappingGrantV31],
    literals: dict[str, str | int | float | bool],
    parameters: list[str],
) -> str:
    assert node.collection_fact_code is not None and node.member_predicate is not None
    grant = grants.get(node.collection_fact_code)
    if grant is None or grant.cardinality != "set":
        raise ViewShapedSqlCompilationBlockedV31(("METADATA_REVIEW",))
    key = grant.key_bindings[0]
    member_alias = "member"
    predicate = _condition_sql(node.member_predicate, grants, literals, parameters, member=True)
    applies = _member_fact_applies(node, grants)
    apply_sql = f"\n{applies}" if applies else ""
    exists_failing = (
        f"EXISTS (\n"
        f"    SELECT 1\n"
        f"    FROM {_q(grant.schema_name)}.{_q(grant.relation_name)} "
        f"AS {_q(member_alias)}{apply_sql}\n"
        f"    WHERE {_q(member_alias)}.{_q(key.column_name)} = {_p(key.parameter_name)}\n"
        f"      AND NOT ({predicate})\n"
        f"  )"
    )
    if node.empty_collection_policy is EmptyCollectionPolicyV31.PASS:
        return f"(NOT {exists_failing})"
    has_members = (
        f"EXISTS (\n"
        f"    SELECT 1\n"
        f"    FROM {_q(grant.schema_name)}.{_q(grant.relation_name)} "
        f"AS {_q(member_alias)}\n"
        f"    WHERE {_q(member_alias)}.{_q(key.column_name)} = {_p(key.parameter_name)}\n"
        f"  )"
    )
    return f"({has_members} AND NOT {exists_failing})"


def _member_fact_applies(
    node: ConditionNodeV31,
    grants: Mapping[str, FactMappingGrantV31],
) -> str:
    assert node.collection_fact_code is not None and node.member_predicate is not None
    collection = grants.get(node.collection_fact_code)
    if collection is None:
        return ""
    applies: list[str] = []
    for code in sorted(node.member_predicate.referenced_fact_codes()):
        grant = grants.get(code)
        if grant is None or grant.cardinality != "scalar":
            continue
        alias = _member_fact_alias(code)
        key = grant.key_bindings[0]
        src = "msrc"
        applies.append(
            f"    OUTER APPLY (\n"
            f"      SELECT TOP (1) {_q(src)}.{_q(grant.column_name)} "
            f"AS {_q('fact_value')}\n"
            f"      FROM {_q(grant.schema_name)}.{_q(grant.relation_name)} AS {_q(src)}\n"
            f"      WHERE {_q(src)}.{_q(key.column_name)} = "
            f"{_q('member')}.{_q(collection.column_name)}\n"
            f"    ) AS {_q(alias)}"
        )
    return "\n".join(applies)


def _expression_sql(
    node: ExpressionNodeV31,
    grants: Mapping[str, FactMappingGrantV31],
    literals: dict[str, str | int | float | bool],
    parameters: list[str],
    *,
    member: bool,
) -> str:
    if node.kind is ExpressionKindV31.FACT:
        assert node.fact_code is not None
        alias = _member_fact_alias(node.fact_code) if member else _fact_alias(node.fact_code)
        return f"{_q(alias)}.{_q('fact_value')}"
    if node.kind is ExpressionKindV31.PARAMETER:
        assert node.parameter_name is not None
        _add_param(parameters, node.parameter_name)
        return _p(node.parameter_name)
    if node.kind is ExpressionKindV31.LITERAL:
        name = _lit(f"expr{len(literals)}", node.value, literals, parameters)
        return _p(name)
    if node.kind is ExpressionKindV31.COALESCE:
        args = ", ".join(
            _expression_sql(child, grants, literals, parameters, member=member)
            for child in node.children
        )
        return f"COALESCE({args})"
    if node.kind is ExpressionKindV31.DATE_ADD:
        unit = _DATEADD_UNIT.get(node.unit.value if node.unit is not None else "")
        if unit is None:
            raise ViewShapedSqlCompilationBlockedV31(("UNSUPPORTED_DATE_UNIT",))
        base = _expression_sql(node.children[0], grants, literals, parameters, member=member)
        amount = _expression_sql(node.children[1], grants, literals, parameters, member=member)
        return f"DATEADD({unit}, {amount}, {base})"
    if node.kind is ExpressionKindV31.ADD:
        return _binary(" + ", node.children, grants, literals, parameters, member)
    if node.kind is ExpressionKindV31.SUBTRACT:
        return _binary(" - ", node.children, grants, literals, parameters, member)
    if node.kind is ExpressionKindV31.MULTIPLY:
        return _binary(" * ", node.children, grants, literals, parameters, member)
    if node.kind is ExpressionKindV31.DIVIDE:
        return _binary(" / ", node.children, grants, literals, parameters, member)
    raise ViewShapedSqlCompilationBlockedV31(("UNSUPPORTED_EXPRESSION",))


def _binary(
    op: str,
    children: list[ExpressionNodeV31],
    grants: Mapping[str, FactMappingGrantV31],
    literals: dict[str, str | int | float | bool],
    parameters: list[str],
    member: bool,
) -> str:
    parts = [
        _expression_sql(child, grants, literals, parameters, member=member) for child in children
    ]
    return "(" + op.join(parts) + ")"


def _stage_whens(
    active: list[tuple[str, RuleNodeV31]],
    stages: tuple[str, ...],
    value_of: Callable[[RuleNodeV31], str],
) -> list[str]:
    return [
        f"WHEN {_q('hits')}.{_q(_hit_flag_stage(stage, rule))} = 1 THEN {value_of(rule)}"
        for stage, rule in active
        if stage in stages
    ]


def _eligibility_first_match(
    active: list[tuple[str, RuleNodeV31]],
    value_of: Callable[[RuleNodeV31], str],
) -> str:
    rules = [rule for stage, rule in active if stage == RuleStageNameV3.ELIGIBILITY.value]
    if not rules:
        return "N''"
    whens = [
        f"WHEN {_q('hits')}.{_q(_hit_name('eligibility', rule))} = 1 THEN {value_of(rule)}"
        for rule in rules
    ]
    return "CASE " + " ".join(whens) + " END"


def _flag_column(stage: str, rule: RuleNodeV31) -> str:
    return f"{_q('hits')}.{_q(_hit_flag_stage(stage, rule))}"


def _stage_unknown_when(
    active: list[tuple[str, RuleNodeV31]],
    stage: str,
    then_sql: str,
) -> str | None:
    flags = [_flag_column(item_stage, rule) for item_stage, rule in active if item_stage == stage]
    if not flags:
        return None
    nulls = " OR ".join(f"{flag} IS NULL" for flag in flags)
    if stage == RuleStageNameV3.ELIGIBILITY.value:
        no_pass = " AND ".join(f"COALESCE({flag}, 0) <> 1" for flag in flags)
        return f"WHEN ({nulls}) AND {no_pass} THEN {then_sql}"
    return f"WHEN {nulls} THEN {then_sql}"


def _decision_whens(
    active: list[tuple[str, RuleNodeV31]],
    value_of: Callable[[RuleNodeV31], str],
    *,
    unknown_sql: str,
) -> list[str]:
    whens: list[str] = []
    for stage in _EARLY_TERMINATE:
        whens.extend(_stage_whens(active, (stage,), value_of))
        unknown = _stage_unknown_when(active, stage, unknown_sql)
        if unknown is not None:
            whens.append(unknown)
    unknown_eligibility = _stage_unknown_when(
        active, RuleStageNameV3.ELIGIBILITY.value, unknown_sql
    )
    if unknown_eligibility is not None:
        whens.append(unknown_eligibility)
    for stage in _OVERRIDE_STAGES:
        whens.extend(_stage_whens(active, (stage,), value_of))
        unknown = _stage_unknown_when(active, stage, unknown_sql)
        if unknown is not None:
            whens.append(unknown)
    whens.extend(_stage_whens(active, (RuleStageNameV3.ELIGIBILITY.value,), value_of))
    return whens


def _outcome_case(delivery: CompleteDeliveryV31, active: list[tuple[str, RuleNodeV31]]) -> str:
    unknown = _n(RuleOutcomeV3.INDETERMINATE.value)
    whens = _decision_whens(
        active,
        lambda rule: _n(rule.outcome.value if rule.outcome else ""),
        unknown_sql=unknown,
    )
    default = _n(delivery.candidate.default_outcome.value)
    if not whens:
        return default
    return "CASE\n    " + "\n    ".join(whens) + f"\n    ELSE {default}\n  END"


def _reason_case(delivery: CompleteDeliveryV31, active: list[tuple[str, RuleNodeV31]]) -> str:
    unknown = _n("FACT_VALUE_MISSING_OR_INVALID")
    whens = _decision_whens(
        active,
        lambda rule: _n(rule.reason_code if rule.reason_code else ""),
        unknown_sql=unknown,
    )
    default = _n(delivery.candidate.default_reason_code)
    if not whens:
        return default
    return "CASE\n    " + "\n    ".join(whens) + f"\n    ELSE {default}\n  END"


def _matched_case(active: list[tuple[str, RuleNodeV31]]) -> str:
    eligibility_code = _eligibility_first_match(active, lambda rule: _n(rule.rule_code))
    empty = _n("")
    whens: list[str] = []
    for stage in _EARLY_TERMINATE:
        whens.extend(_stage_whens(active, (stage,), lambda rule: _n(rule.rule_code)))
        unknown = _stage_unknown_when(active, stage, empty)
        if unknown is not None:
            whens.append(unknown)
    unknown_eligibility = _stage_unknown_when(active, RuleStageNameV3.ELIGIBILITY.value, empty)
    if unknown_eligibility is not None:
        whens.append(unknown_eligibility)
    for stage in _OVERRIDE_STAGES:
        for item_stage, rule in active:
            if item_stage != stage:
                continue
            concat = f"CONCAT_WS(N',', {eligibility_code}, {_n(rule.rule_code)})"
            whens.append(f"WHEN {_flag_column(stage, rule)} = 1 THEN {concat}")
        unknown = _stage_unknown_when(active, stage, eligibility_code)
        if unknown is not None:
            whens.append(unknown)
    whens.extend(
        _stage_whens(active, (RuleStageNameV3.ELIGIBILITY.value,), lambda rule: _n(rule.rule_code))
    )
    if not whens:
        return empty
    return "CASE\n    " + "\n    ".join(whens) + f"\n    ELSE {empty}\n  END"


def _hit_flag(stage: RuleStageV31, rule: RuleNodeV31) -> str:
    return _hit_flag_stage(stage.stage, rule)


def _hit_flag_stage(stage: str, rule: RuleNodeV31) -> str:
    return _hit_name(stage, rule)


def _hit_name(stage: str, rule: RuleNodeV31) -> str:
    return f"{_STAGE_PREFIX[stage]}_{rule.rule_code}"


def _fact_alias(fact_code: str) -> str:
    return "fact_" + fact_code.replace(".", "_")


def _member_fact_alias(fact_code: str) -> str:
    return "mfact_" + fact_code.replace(".", "_")


def _lit(
    condition_id: str,
    value: object,
    literals: dict[str, str | int | float | bool],
    parameters: list[str],
) -> str:
    slug = condition_id.replace("-", "_")
    name = f"lit_{slug}"
    if not isinstance(value, str | int | float | bool):
        raise ViewShapedSqlCompilationBlockedV31(("UNSUPPORTED_LITERAL",))
    literals[name] = value
    _add_param(parameters, name)
    return name


def _add_param(parameters: list[str], name: str) -> None:
    if name not in parameters:
        parameters.append(name)


def _q(name: str) -> str:
    if name.startswith(("#", "@")) or "]" in name:
        raise ViewShapedSqlCompilationBlockedV31(("INVALID_IDENTIFIER",))
    return f"[{name}]"


def _p(name: str) -> str:
    return f":{name}"


def _n(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"N'{escaped}'"
