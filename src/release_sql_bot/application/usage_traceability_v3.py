"""V3 usage traceability sextuplet digest (M2 第二子任务).

Computes a canonical SHA-256 over the complete usage sextuplet projection
to prove traceability linkage. This is pure computation: no repository,
provider, SQL, environment or time access.

The digest proves only that the complete usage sextuplet projection is
consistent. It does NOT prove AST coverage, business semantics, MongoDB
repository truth, metadata authorization, or that SQL can execute.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.domain.fact_bindings_v3 import FactUsageV3

# Stage business order (DEV §5.4 冻结)
_STAGE_ORDER: dict[str, int] = {
    "stateGuards": 0,
    "prerequisites": 1,
    "eligibility": 2,
    "postGates": 3,
    "exclusions": 4,
}


def compute_usage_traceability_sha256_v3(
    usages: Sequence[FactUsageV3],
) -> str:
    """Compute the usage traceability digest over complete sextuplet projections.

    Args:
        usages: Sequence of V3 fact usages. Must not be empty.

    Returns:
        64-char lowercase hex SHA-256 digest.

    Raises:
        ValueError: if usages is empty.
    """
    if not usages:
        raise ValueError("usages cannot be empty")

    # Check upstream quadruplet uniqueness (stage, ruleCode, conditionId, conditionPath)
    seen: set[tuple[str, str, str, str]] = set()
    for usage in usages:
        key = (usage.stage, usage.rule_code, usage.condition_id, usage.condition_path)
        if key in seen:
            raise ValueError("fact usages must be unique")
        seen.add(key)

    # Project exactly six camelCase fields per usage
    projected: list[dict[str, Any]] = []
    for usage in usages:
        projected.append(
            {
                "stage": usage.stage,
                "ruleCode": usage.rule_code,
                "priority": usage.priority,
                "conditionId": usage.condition_id,
                "conditionPath": usage.condition_path,
                "outcome": usage.outcome,
            }
        )

    # Sort by complete stable key (DEV §5.4 冻结)
    projected.sort(
        key=lambda item: (
            _STAGE_ORDER[item["stage"]],
            item["priority"],
            item["ruleCode"],
            item["conditionId"],
            item["conditionPath"],
            item["outcome"],
        ),
    )

    return canonical_sha256(projected)  # type: ignore[arg-type]
