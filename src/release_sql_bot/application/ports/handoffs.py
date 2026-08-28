"""Read-only port for RuleReader-owned fact binding handoffs."""

from __future__ import annotations

from typing import Protocol

from release_sql_bot.domain.fact_binding_handoffs_v2 import StoredFactBindingHandoffV2


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
