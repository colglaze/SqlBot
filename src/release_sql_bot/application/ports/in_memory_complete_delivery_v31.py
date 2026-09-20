"""In-memory complete-delivery source for offline tests."""

from __future__ import annotations

from release_sql_bot.domain.complete_delivery_v31 import StoredCompleteDeliveryV31


class InMemoryCompleteDeliverySourceV31:
    def __init__(self, deliveries: dict[str, StoredCompleteDeliveryV31] | None = None) -> None:
        self.deliveries = dict(deliveries or {})
        self.calls: list[str] = []

    async def get_delivery(self, rule_version: str) -> StoredCompleteDeliveryV31 | None:
        self.calls.append(rule_version)
        return self.deliveries.get(rule_version)
