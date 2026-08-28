"""最新规则查询用例。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from release_sql_bot.application.ports.rules import RuleRepository
from release_sql_bot.domain.rule_versions import StoredRuleVersion


class LatestRuleQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    rule_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )


class LatestRuleNotFoundError(LookupError):
    """指定 rule_id 还没有规则版本。"""


async def load_latest_rule(
    repository: RuleRepository,
    query: LatestRuleQuery,
) -> StoredRuleVersion:
    """每次调用仓储读取当前最新规则，不在应用层缓存结果。"""

    rule = await repository.get_latest_rule(
        rule_id=query.rule_id,
    )
    if rule is None:
        raise LatestRuleNotFoundError
    return rule
