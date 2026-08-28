"""最新规则读取所需的应用层仓储端口。"""

from __future__ import annotations

from typing import Protocol

from release_sql_bot.domain.rule_versions import StoredRuleVersion


class RuleRepositoryError(RuntimeError):
    """规则仓储的稳定错误基类，不携带驱动或规则正文。"""


class RuleRepositoryUnavailableError(RuleRepositoryError):
    """规则仓储未启用、未就绪或查询失败。"""


class RuleDocumentInvalidError(RuleRepositoryError):
    """MongoDB 文档不符合当前规则领域契约。"""


class RuleRepository(Protocol):
    async def get_latest_rule(
        self,
        *,
        rule_id: str,
    ) -> StoredRuleVersion | None: ...
