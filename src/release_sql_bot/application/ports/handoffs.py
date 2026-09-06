"""Read-only ports for RuleReader-owned fact binding handoffs (V2 and V3)."""

from __future__ import annotations

from typing import Protocol

from release_sql_bot.domain.fact_binding_handoffs_v2 import StoredFactBindingHandoffV2
from release_sql_bot.domain.fact_binding_handoffs_v3 import StoredFactBindingHandoffBatchV3


class FactBindingHandoffRepositoryError(RuntimeError):
    """Stable base error that never carries MongoDB records or connection details."""


class FactBindingHandoffRepositoryUnavailableError(FactBindingHandoffRepositoryError):
    """The read-only collection is disabled, unavailable, or failed to query."""


class FactBindingHandoffDocumentInvalidError(FactBindingHandoffRepositoryError):
    """A RuleReader-owned wrapper cannot be parsed as the frozen V2 envelope."""


class FactBindingHandoffRepository(Protocol):
    async def list_by_rule_version(
        self,
        rule_version: str,
    ) -> tuple[StoredFactBindingHandoffV2, ...]: ...


class FactBindingHandoffBatchRepositoryV3Error(FactBindingHandoffRepositoryError):
    """Stable V3 base error that never carries MongoDB records or connection details."""


class FactBindingHandoffBatchRepositoryV3UnavailableError(FactBindingHandoffBatchRepositoryV3Error):
    """The V3 batch collection is disabled, unavailable, or failed to query."""


class FactBindingHandoffBatchDocumentInvalidV3Error(FactBindingHandoffBatchRepositoryV3Error):
    """A RuleReader-owned batch cannot be parsed as the frozen V3 envelope."""


class FactBindingHandoffBatchRepositoryV3(Protocol):
    async def get_batch_by_rule_version(
        self,
        rule_version: str,
    ) -> StoredFactBindingHandoffBatchV3 | None: ...
