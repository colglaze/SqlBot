"""Port for persisting generated V2 SQL template candidates.

The store is a one-way sink at the end of the generation flow: it never
participates in generation decisions and never changes candidate lifecycle.
Writes are insert-only; the same ``contentSha256`` is an idempotent duplicate,
never an update.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from release_sql_bot.domain.sql_candidates_v2 import SqlTemplateCandidateV2


class CandidateStoreStatus(StrEnum):
    STORED = "stored"
    DUPLICATE = "duplicate"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CandidateStoreOutcome:
    status: CandidateStoreStatus
    content_sha256: str


class CandidateTemplateStore(Protocol):
    async def initialize(self) -> None:
        """Ping the deployment and ensure the unique content hash index.

        Implementations must degrade to unavailable instead of raising so a
        storage problem never blocks the service or the generation flow.
        """

    async def save(self, candidate: SqlTemplateCandidateV2) -> CandidateStoreOutcome:
        """Insert one candidate payload; never raise, never update."""

    async def close(self) -> None:
        """Release the underlying client; never raise."""
