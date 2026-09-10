"""Unit tests for V3 usage traceability sextuplet digest (M2 第二子任务).

All tests use synthetic data. No repository, provider, SQL or environment access.
"""

from __future__ import annotations

import pytest

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.application.usage_traceability_v3 import (
    compute_usage_traceability_sha256_v3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactUsageV3

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_usage(
    *,
    stage: str = "eligibility",
    rule_code: str = "SYNTH_RULE",
    priority: int = 1,
    condition_id: str = "cond-synthetic-001",
    condition_path: str = "/rule/stages/eligibility/1",
    outcome: str = "READY",
    evidence_ids: list[str] | None = None,
) -> FactUsageV3:
    return FactUsageV3.model_validate(
        {
            "stage": stage,
            "ruleCode": rule_code,
            "priority": priority,
            "conditionId": condition_id,
            "conditionPath": condition_path,
            "outcome": outcome,
            "evidenceIds": evidence_ids or ["ev-condition-usage"],
        }
    )


def _expected_digest(projected: list[dict]) -> str:
    """Compute expected digest using the repository canonical algorithm."""
    return canonical_sha256(projected)  # type: ignore[arg-type]


def _assert_input_unchanged(
    usages: list[FactUsageV3],
    original_ids: list[int],
    original_dumps: list[dict],
) -> None:
    """Assert input list length, object identity order, and content unchanged."""
    assert len(usages) == len(original_ids)
    assert [id(u) for u in usages] == original_ids
    for usage, original_dump in zip(usages, original_dumps, strict=True):
        assert usage.model_dump() == original_dump


# ===================================================================
# Section 1: Basic success
# ===================================================================


def test_single_usage_returns_64_char_sha256() -> None:
    """Single valid usage returns a 64-char lowercase hex SHA-256."""
    usage = _make_usage()
    result = compute_usage_traceability_sha256_v3([usage])
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


def test_single_usage_matches_manual_projection() -> None:
    """Digest matches manually computed sextuplet projection."""
    usage = _make_usage(
        stage="eligibility",
        rule_code="SYNTH_RULE",
        priority=1,
        condition_id="cond-001",
        condition_path="/rule/stages/eligibility/1",
        outcome="READY",
    )
    projected = [
        {
            "stage": "eligibility",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "cond-001",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        }
    ]
    expected = _expected_digest(projected)
    assert compute_usage_traceability_sha256_v3([usage]) == expected


# ===================================================================
# Section 2: Sorting invariance
# ===================================================================


def test_array_reordering_does_not_change_digest() -> None:
    """Input array reordering does not change the digest."""
    u1 = _make_usage(stage="eligibility", priority=1, condition_id="c1")
    u2 = _make_usage(stage="eligibility", priority=2, condition_id="c2")
    u3 = _make_usage(stage="exclusions", priority=1, condition_id="c3")
    digest1 = compute_usage_traceability_sha256_v3([u1, u2, u3])
    digest2 = compute_usage_traceability_sha256_v3([u3, u1, u2])
    digest3 = compute_usage_traceability_sha256_v3([u2, u3, u1])
    assert digest1 == digest2 == digest3


def test_stage_follows_five_stage_business_order() -> None:
    """Stage follows the five-stage business order."""
    u_state = _make_usage(stage="stateGuards", priority=1, condition_id="c1")
    u_pre = _make_usage(stage="prerequisites", priority=1, condition_id="c2")
    u_elig = _make_usage(stage="eligibility", priority=1, condition_id="c3")
    u_post = _make_usage(stage="postGates", priority=1, condition_id="c4")
    u_excl = _make_usage(stage="exclusions", priority=1, condition_id="c5")
    digest = compute_usage_traceability_sha256_v3(
        [
            u_excl,
            u_elig,
            u_state,
            u_post,
            u_pre,
        ]
    )
    # Verify expected sort order: stateGuards < prerequisites <
    # eligibility < postGates < exclusions
    projected = [
        {
            "stage": "stateGuards",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "c1",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
        {
            "stage": "prerequisites",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "c2",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
        {
            "stage": "eligibility",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "c3",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
        {
            "stage": "postGates",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "c4",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
        {
            "stage": "exclusions",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "c5",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
    ]
    expected = _expected_digest(projected)
    assert digest == expected


def test_priority_uses_numeric_sort() -> None:
    """Priority uses numeric sort, not string sort."""
    u1 = _make_usage(priority=1, condition_id="c1")
    u2 = _make_usage(priority=10, condition_id="c2")
    u3 = _make_usage(priority=2, condition_id="c3")
    digest = compute_usage_traceability_sha256_v3([u1, u2, u3])
    # Numeric order: 1, 2, 10
    projected = [
        {
            "stage": "eligibility",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "c1",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
        {
            "stage": "eligibility",
            "ruleCode": "SYNTH_RULE",
            "priority": 2,
            "conditionId": "c3",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
        {
            "stage": "eligibility",
            "ruleCode": "SYNTH_RULE",
            "priority": 10,
            "conditionId": "c2",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
    ]
    expected = _expected_digest(projected)
    assert digest == expected


@pytest.mark.parametrize(
    "param_name,camel_name,value_a,value_b",
    [
        ("rule_code", "ruleCode", "RULE_A", "RULE_B"),
        ("condition_id", "conditionId", "cond-a", "cond-b"),
        ("condition_path", "conditionPath", "/path/a", "/path/b"),
    ],
)
def test_sort_respects_each_key(
    param_name: str,
    camel_name: str,
    value_a: str,
    value_b: str,
) -> None:
    """Each sort key (ruleCode, conditionId, conditionPath) can decide order."""
    # Same stage and priority; only the tested key differs
    u1 = _make_usage(**{param_name: value_a})  # type: ignore[arg-type]
    u2 = _make_usage(**{param_name: value_b})  # type: ignore[arg-type]
    # value_a < value_b, so u1 should come first
    projected = [
        {
            "stage": "eligibility",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "cond-synthetic-001",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
        {
            "stage": "eligibility",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "cond-synthetic-001",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
    ]
    # Override the tested field in the projected output
    projected[0][camel_name] = value_a
    projected[1][camel_name] = value_b
    expected = _expected_digest(projected)
    assert compute_usage_traceability_sha256_v3([u1, u2]) == expected


def test_any_field_change_alters_digest() -> None:
    """Any sextuplet field change alters the digest."""
    base = _make_usage()
    base_digest = compute_usage_traceability_sha256_v3([base])
    assert compute_usage_traceability_sha256_v3([_make_usage(stage="exclusions")]) != base_digest
    assert (
        compute_usage_traceability_sha256_v3([_make_usage(rule_code="OTHER_RULE")]) != base_digest
    )
    assert compute_usage_traceability_sha256_v3([_make_usage(priority=99)]) != base_digest
    assert (
        compute_usage_traceability_sha256_v3([_make_usage(condition_id="other-cond")])
        != base_digest
    )
    assert (
        compute_usage_traceability_sha256_v3([_make_usage(condition_path="/other/path")])
        != base_digest
    )
    assert compute_usage_traceability_sha256_v3([_make_usage(outcome="SKIPPED")]) != base_digest


def test_evidence_ids_do_not_affect_digest() -> None:
    """evidenceIds changes do not affect the digest."""
    u1 = _make_usage(evidence_ids=["ev-1"])
    u2 = _make_usage(evidence_ids=["ev-2", "ev-3"])
    assert compute_usage_traceability_sha256_v3([u1]) == compute_usage_traceability_sha256_v3([u2])


# ===================================================================
# Section 3: Same conditionId, different stage/ruleCode/conditionPath
# ===================================================================


def test_same_condition_id_different_stage_legal() -> None:
    """Same conditionId in different stages is legal and all participate."""
    u1 = _make_usage(stage="eligibility", condition_id="cond-same")
    u2 = _make_usage(stage="exclusions", condition_id="cond-same")
    digest = compute_usage_traceability_sha256_v3([u1, u2])
    projected = [
        {
            "stage": "eligibility",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "cond-same",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
        {
            "stage": "exclusions",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "cond-same",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
    ]
    expected = _expected_digest(projected)
    assert digest == expected


def test_same_condition_id_different_rule_code_legal() -> None:
    """Same conditionId with different ruleCode is legal and both participate."""
    u1 = _make_usage(rule_code="RULE_A", condition_id="cond-same")
    u2 = _make_usage(rule_code="RULE_B", condition_id="cond-same")
    digest = compute_usage_traceability_sha256_v3([u1, u2])
    projected = [
        {
            "stage": "eligibility",
            "ruleCode": "RULE_A",
            "priority": 1,
            "conditionId": "cond-same",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
        {
            "stage": "eligibility",
            "ruleCode": "RULE_B",
            "priority": 1,
            "conditionId": "cond-same",
            "conditionPath": "/rule/stages/eligibility/1",
            "outcome": "READY",
        },
    ]
    expected = _expected_digest(projected)
    assert digest == expected


def test_same_condition_id_different_condition_path_legal() -> None:
    """Same conditionId with different conditionPath is legal and both participate."""
    u1 = _make_usage(condition_path="/path/a", condition_id="cond-same")
    u2 = _make_usage(condition_path="/path/b", condition_id="cond-same")
    digest = compute_usage_traceability_sha256_v3([u1, u2])
    projected = [
        {
            "stage": "eligibility",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "cond-same",
            "conditionPath": "/path/a",
            "outcome": "READY",
        },
        {
            "stage": "eligibility",
            "ruleCode": "SYNTH_RULE",
            "priority": 1,
            "conditionId": "cond-same",
            "conditionPath": "/path/b",
            "outcome": "READY",
        },
    ]
    expected = _expected_digest(projected)
    assert digest == expected


# ===================================================================
# Section 4: Quadruplet uniqueness rejection
# ===================================================================


def test_duplicate_quadruplet_rejected() -> None:
    """Identical quadruplet (stage, ruleCode, conditionId, conditionPath) is rejected."""
    u1 = _make_usage(
        stage="eligibility",
        rule_code="RULE",
        priority=1,
        condition_id="cond-1",
        condition_path="/p",
        outcome="READY",
    )
    u2 = _make_usage(
        stage="eligibility",
        rule_code="RULE",
        priority=1,
        condition_id="cond-1",
        condition_path="/p",
        outcome="READY",
    )
    with pytest.raises(ValueError, match="unique"):
        compute_usage_traceability_sha256_v3([u1, u2])


def test_duplicate_quadruplet_different_priority_rejected() -> None:
    """Same quadruplet but different priority is still rejected."""
    u1 = _make_usage(
        stage="eligibility",
        rule_code="RULE",
        priority=1,
        condition_id="cond-1",
        condition_path="/p",
        outcome="READY",
    )
    u2 = _make_usage(
        stage="eligibility",
        rule_code="RULE",
        priority=2,
        condition_id="cond-1",
        condition_path="/p",
        outcome="READY",
    )
    with pytest.raises(ValueError, match="unique"):
        compute_usage_traceability_sha256_v3([u1, u2])


def test_duplicate_quadruplet_different_outcome_rejected() -> None:
    """Same quadruplet but different outcome is still rejected."""
    u1 = _make_usage(
        stage="eligibility",
        rule_code="RULE",
        priority=1,
        condition_id="cond-1",
        condition_path="/p",
        outcome="READY",
    )
    u2 = _make_usage(
        stage="eligibility",
        rule_code="RULE",
        priority=1,
        condition_id="cond-1",
        condition_path="/p",
        outcome="SKIPPED",
    )
    with pytest.raises(ValueError, match="unique"):
        compute_usage_traceability_sha256_v3([u1, u2])


# ===================================================================
# Section 5: Empty input
# ===================================================================


def test_empty_usages_rejected() -> None:
    """Empty usages raises ValueError."""
    with pytest.raises(ValueError, match="empty"):
        compute_usage_traceability_sha256_v3([])


# ===================================================================
# Section 6: Immutability — input not modified
# ===================================================================


def test_input_not_modified_on_success() -> None:
    """Input list, object identity order, and content unchanged on success."""
    u1 = _make_usage(stage="exclusions", condition_id="c1")
    u2 = _make_usage(stage="eligibility", condition_id="c2")
    usages = [u1, u2]
    original_ids = [id(u) for u in usages]
    original_dumps = [u.model_dump() for u in usages]
    compute_usage_traceability_sha256_v3(usages)
    _assert_input_unchanged(usages, original_ids, original_dumps)


def test_input_not_modified_on_failure() -> None:
    """Input list, object identity order, and content unchanged on failure."""
    u1 = _make_usage(condition_id="cond-1")
    u2 = _make_usage(condition_id="cond-1")  # same quadruplet
    usages = [u1, u2]
    original_ids = [id(u) for u in usages]
    original_dumps = [u.model_dump() for u in usages]
    with pytest.raises(ValueError):
        compute_usage_traceability_sha256_v3(usages)
    _assert_input_unchanged(usages, original_ids, original_dumps)


# ===================================================================
# Section 7: No infrastructure dependencies
# ===================================================================


def test_module_does_not_import_v2_or_infrastructure() -> None:
    from pathlib import Path

    import release_sql_bot.application.usage_traceability_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "fact_bindings_v2" not in source
    assert "project_bindings_v2" not in source
    assert "mongodb" not in source
    assert "pymongo" not in source
    assert "sqlglot" not in source.lower()
    assert "os.environ" not in source
    assert "getenv" not in source
