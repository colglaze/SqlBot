from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from release_sql_bot.application.rules import (
    LatestRuleNotFoundError,
    LatestRuleQuery,
    load_latest_rule,
)
from release_sql_bot.domain.rule_versions import StoredRuleVersion
from tests.support import valid_stored_rule_version


class SequenceRuleRepository:
    def __init__(self, rules: list[StoredRuleVersion | None]) -> None:
        self._rules = iter(rules)
        self.calls: list[str] = []

    async def get_latest_rule(
        self,
        *,
        rule_id: str,
    ) -> StoredRuleVersion | None:
        self.calls.append(rule_id)
        return next(self._rules)


def _rule(version: int) -> StoredRuleVersion:
    return StoredRuleVersion.model_validate(
        valid_stored_rule_version(
            rule_version=f"REPORT_RELEASE_ALL_001@V{version}",
            generated_at=datetime(2026, 8, 24, 8, version, tzinfo=UTC),
        )
    )


def test_latest_rule_use_case_queries_repository_on_every_call() -> None:
    repository = SequenceRuleRepository([_rule(3), _rule(4)])
    query = LatestRuleQuery(rule_id="REPORT_RELEASE_ALL_001")

    first = asyncio.run(load_latest_rule(repository, query))
    second = asyncio.run(load_latest_rule(repository, query))

    assert first.rule_version.endswith("@V3")
    assert second.rule_version.endswith("@V4")
    assert repository.calls == ["REPORT_RELEASE_ALL_001", "REPORT_RELEASE_ALL_001"]


def test_latest_rule_use_case_reports_not_found() -> None:
    repository = SequenceRuleRepository([None])

    with pytest.raises(LatestRuleNotFoundError):
        asyncio.run(
            load_latest_rule(
                repository,
                LatestRuleQuery(rule_id="REPORT_RELEASE_ALL_001"),
            )
        )


def test_latest_rule_query_rejects_mongodb_expression_like_rule_ids() -> None:
    with pytest.raises(ValidationError):
        LatestRuleQuery(rule_id="{'$ne': null}")
