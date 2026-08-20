from __future__ import annotations

import json
from copy import deepcopy

import pytest

from release_sql_bot.domain.rule_analysis import (
    BaselineMode,
    DuplicateConditionIdError,
    ReleaseRule,
    RulePlanningPrerequisites,
    canonicalize_rule,
    diff_rules,
    evaluate_rule_planning_readiness,
)
from tests.support import valid_release_rule


def _rule(payload: dict[str, object]) -> ReleaseRule:
    return ReleaseRule.model_validate(payload)


def _next_version(payload: dict[str, object]) -> dict[str, object]:
    current = deepcopy(payload)
    current["version"] = int(current["version"]) + 1
    current["metadata"]["change_reason"] = "Synthetic rule change"
    return current


def test_canonicalization_and_hash_ignore_json_key_order_and_whitespace() -> None:
    payload = valid_release_rule()
    pretty = json.dumps(payload, ensure_ascii=False, indent=4)
    reordered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    first = canonicalize_rule(ReleaseRule.model_validate_json(pretty))
    second = canonicalize_rule(ReleaseRule.model_validate_json(reordered))

    assert first.canonical_json == second.canonical_json
    assert first.content_hash == second.content_hash
    assert first.content_hash.startswith("sha256:")
    assert '"negate":false' in first.canonical_json


def test_canonicalization_preserves_whitespace_inside_string_content() -> None:
    baseline_payload = valid_release_rule()
    changed_payload = deepcopy(baseline_payload)
    changed_payload["metadata"]["change_reason"] += " "

    baseline = canonicalize_rule(_rule(baseline_payload))
    changed = canonicalize_rule(_rule(changed_payload))

    assert baseline.content_hash != changed.content_hash


def test_diff_reports_added_condition() -> None:
    baseline_payload = valid_release_rule()
    current_payload = _next_version(baseline_payload)
    current_payload["condition"]["conditions"].append(
        {
            "condition_id": "report.owner_assigned",
            "field": "owner_id",
            "operator": "not_null",
            "value_type": "integer",
            "null_policy": "fail",
        }
    )

    result = diff_rules(_rule(current_payload), _rule(baseline_payload))

    assert [item.condition_id for item in result.added] == ["report.owner_assigned"]
    assert result.removed == ()
    assert result.modified == ()
    assert result.logic_structure_change is not None


def test_diff_reports_removed_condition() -> None:
    baseline_payload = valid_release_rule()
    current_payload = _next_version(baseline_payload)
    current_payload["condition"]["conditions"].pop(1)

    result = diff_rules(_rule(current_payload), _rule(baseline_payload))

    assert [item.condition_id for item in result.removed] == ["report.qc_passed"]
    assert result.added == ()
    assert result.modified == ()
    assert result.logic_structure_change is not None


def test_diff_reports_modified_condition_without_logic_change() -> None:
    baseline_payload = valid_release_rule()
    current_payload = _next_version(baseline_payload)
    current_payload["condition"]["conditions"][0]["value"] = "APPROVED"

    result = diff_rules(_rule(current_payload), _rule(baseline_payload))

    assert len(result.modified) == 1
    assert result.modified[0].condition_id == "report.finalized"
    assert result.modified[0].changed_fields == ("value",)
    assert result.modified[0].before["value"] == "FINAL"
    assert result.modified[0].after["value"] == "APPROVED"
    assert result.logic_structure_change is None


def test_diff_reports_logical_structure_change_without_leaf_changes() -> None:
    baseline_payload = valid_release_rule()
    current_payload = _next_version(baseline_payload)
    current_payload["condition"]["combinator"] = "any"

    result = diff_rules(_rule(current_payload), _rule(baseline_payload))

    assert result.added == ()
    assert result.removed == ()
    assert result.modified == ()
    assert result.logic_structure_change is not None
    assert result.logic_structure_change.before["combinator"] == "all"
    assert result.logic_structure_change.after["combinator"] == "any"


def test_condition_order_changes_hash_and_logic_signature() -> None:
    baseline_payload = valid_release_rule()
    current_payload = _next_version(baseline_payload)
    current_payload["condition"]["conditions"].reverse()

    result = diff_rules(_rule(current_payload), _rule(baseline_payload))

    assert result.current_hash != result.baseline_hash
    assert result.modified == ()
    assert result.logic_structure_change is not None


def test_duplicate_condition_id_is_rejected_across_the_tree() -> None:
    payload = valid_release_rule()
    duplicate = deepcopy(payload["condition"]["conditions"][0])
    payload["condition"]["conditions"].append({"combinator": "any", "conditions": [duplicate]})

    with pytest.raises(DuplicateConditionIdError) as error:
        canonicalize_rule(_rule(payload))

    assert error.value.condition_ids == ("report.finalized",)


def test_no_baseline_is_explicit_and_does_not_invent_logic_change() -> None:
    result = diff_rules(_rule(valid_release_rule()), baseline=None)

    assert result.baseline_mode is BaselineMode.NONE
    assert result.baseline_hash is None
    assert [item.condition_id for item in result.added] == [
        "report.finalized",
        "report.qc_passed",
    ]
    assert result.removed == ()
    assert result.modified == ()
    assert result.logic_structure_change is None


def test_missing_exception_semantics_and_real_schema_explicitly_block_planning() -> None:
    result = evaluate_rule_planning_readiness(
        _rule(valid_release_rule()),
        RulePlanningPrerequisites(),
    )

    assert result.status == "blocked"
    assert {issue.code for issue in result.issues} == {
        "EXCEPTION_SET_SEMANTICS_MISSING",
        "TARGET_SCHEMA_MISSING",
    }
