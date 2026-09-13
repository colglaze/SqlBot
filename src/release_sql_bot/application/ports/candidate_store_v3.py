"""Port for persisting generated V3 SQL template candidates.

The V3 store is a one-way sink independent of the V2 store. It never
participates in generation decisions and never changes candidate lifecycle.
Writes are insert-only; the same ``contentSha256`` is an idempotent
duplicate, never an update.

This module is intentionally independent of the V2 store port
(``candidate_store.py``) because that module depends on the V2 candidate
contract. V3 candidates carry ``schemaVersion="3.0.0"`` and must not be
mixed with V2 payloads.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3


class CandidateStoreV3Status(StrEnum):
    STORED = "stored"
    DUPLICATE = "duplicate"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CandidateStoreV3Outcome:
    status: CandidateStoreV3Status
    content_sha256: str


class CandidateTemplateStoreV3(Protocol):
    async def initialize(self) -> None:
        """Ping the deployment and ensure the unique content hash index.

        Implementations must degrade to unavailable instead of raising so a
        storage problem never blocks the service or the generation flow.
        """

    async def save(self, candidate: SqlTemplateCandidateV3) -> CandidateStoreV3Outcome:
        """Insert one V3 candidate payload; never raise, never update."""

    async def close(self) -> None:
        """Release the underlying client; never raise."""
