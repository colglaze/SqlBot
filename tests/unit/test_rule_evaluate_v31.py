from __future__ import annotations

import asyncio

from release_sql_bot.application.complete_delivery_intake_v31 import select_complete_delivery_v31
from release_sql_bot.application.ports.in_memory_complete_delivery_v31 import (
    InMemoryCompleteDeliverySourceV31,
)
from release_sql_bot.application.rule_evaluate_v31 import (
    INDETERMINATE_REASON,
    EvaluationContextV31,
    EvaluationResultV31,
    evaluate_condition_v31,
    evaluate_rule_structure_v31,
)
from release_sql_bot.domain.fact_bindings_v3 import RuleOutcomeV3
from release_sql_bot.domain.purpose_v31 import DeliveryPurposeV31
from release_sql_bot.domain.rule_structure_v31 import ConditionNodeV31
from tests.v31_delivery_support import build_synthetic_complete_delivery


def _delivery():
    stored, _meta = build_synthetic_complete_delivery()
    source = InMemoryCompleteDeliverySourceV31({stored.rule_version: stored})
    return asyncio.run(
        select_complete_delivery_v31(
            source,
            purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
            rule_version=stored.rule_version,
        )
    )


def test_synthetic_test_case_matches_independent_evaluator() -> None:
    delivery = _delivery()
    case = delivery.result.test_cases[0]
    members = {item.member_key: item.facts for item in case.members}

    result = evaluate_rule_structure_v31(
        delivery.candidate,
        case.given,
        runtime=case.runtime,
        members=members,
    )

    assert result.outcome == case.expected_outcome
    assert result.reason_code == case.expected_reason_code
    assert list(result.matched_rule_codes) == case.expected_matched_rule_codes


def test_missing_fact_is_indeterminate() -> None:
    delivery = _delivery()

    result = evaluate_rule_structure_v31(
        delivery.candidate,
        {},
        runtime={"evaluationDate": "2026-09-17"},
    )

    assert result.outcome is RuleOutcomeV3.INDETERMINATE
    assert result.reason_code == INDETERMINATE_REASON
    assert result.matched_rule_codes == ()


def test_empty_members_after_ready_status_hits_post_gate() -> None:
    delivery = _delivery()

    result = evaluate_rule_structure_v31(
        delivery.candidate,
        {"task.status_code": 19, "group.member_keys": []},
        runtime={"evaluationDate": "2026-09-17"},
    )

    assert result.outcome is RuleOutcomeV3.WAITING_CONDITIONS
    assert result.reason_code == "MEMBERS_INCOMPLETE_RESULT"
    assert result.matched_rule_codes == ("STATUS_READY", "MEMBERS_INCOMPLETE")


def test_empty_all_members_with_pass_policy_is_true() -> None:
    node = ConditionNodeV31.model_validate(
        {
            "id": "members-empty",
            "kind": "allMembers",
            "description": "Synthetic empty collection.",
            "enabled": True,
            "children": [],
            "collectionFactCode": "group.member_keys",
            "emptyCollectionPolicy": "pass",
            "duplicateMemberPolicy": "uniquePreserveOrder",
            "missingMemberPolicy": "indeterminate",
            "memberPredicate": {
                "id": "member-ready",
                "kind": "compare",
                "description": "Synthetic member compare.",
                "enabled": True,
                "children": [],
                "left": {"kind": "fact", "factCode": "task.status_code", "children": []},
                "operator": "eq",
                "right": {"kind": "literal", "value": 19, "children": []},
                "nullPolicy": "indeterminate",
            },
        }
    )
    context = EvaluationContextV31(facts={"group.member_keys": []}, runtime={}, members={})

    assert evaluate_condition_v31(node, context, {}) is EvaluationResultV31.PASS
