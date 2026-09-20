"""Read-only port for Agent1 3.1.0 complete deliveries."""

from __future__ import annotations

from typing import Protocol

from release_sql_bot.domain.complete_delivery_v31 import StoredCompleteDeliveryV31


class CompleteDeliverySourceV31(Protocol):
    async def get_delivery(self, rule_version: str) -> StoredCompleteDeliveryV31 | None: ...
