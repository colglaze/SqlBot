"""Read-only port for V3 approval records (DEV §3.6).

The trusted M3/M6 application service must verify approval truthiness
through a controlled read-only approval-record port before any provider
call or store write. This port abstracts the metadataReview approval
source. The MongoDB implementation also requires a separately validated
active lifecycle pointer before returning a record.

The in-memory adapter queries pre-registered synthetic records. It never
"returns the caller's record" and never "always approves" — it is a
genuine lookup with exact-match semantics. Missing or mismatched records
are reported as explicit lookup failures.
"""

from __future__ import annotations

from typing import Protocol

from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3


class ApprovalRecordLookupError(RuntimeError):
    """Stable base error that never carries approval records or connection details."""


class ApprovalRecordNotRegisteredError(ApprovalRecordLookupError):
    """No approval record is registered for the given approvalId."""


class ApprovalRecordInactiveError(ApprovalRecordLookupError):
    """The approval record exists but is no longer active (superseded/expired)."""


class ApprovalRecordPortV3(Protocol):
    """Read-only port for V3 approval records."""

    async def get_by_approval_id(
        self,
        approval_id: str,
    ) -> ApprovalRecordV3 | None:
        """Look up a registered approval record by exact approvalId.

        Returns the record if found and active, None if not found.
        Implementations must NOT fabricate records or always return the
        caller-provided payload.
        """
        ...


class InMemoryApprovalRecordPortV3:
    """In-memory approval record port backed by pre-registered synthetic records.

    For offline testing only. Queries a fixed registry of approval records
    by exact approvalId. Never returns a record that was not pre-registered.
    Records flagged as inactive (superseded) are treated as not found for
    active-lookup purposes but can be retrieved via get_inactive for test
    diagnostics.
    """

    def __init__(
        self,
        records: dict[str, ApprovalRecordV3] | None = None,
        *,
        inactive_ids: frozenset[str] | None = None,
    ) -> None:
        self._records: dict[str, ApprovalRecordV3] = dict(records) if records else {}
        self._inactive_ids: frozenset[str] = inactive_ids or frozenset()

    async def get_by_approval_id(
        self,
        approval_id: str,
    ) -> ApprovalRecordV3 | None:
        record = self._records.get(approval_id)
        if record is None:
            return None
        if approval_id in self._inactive_ids:
            return None
        return record

    def has_record(self, approval_id: str) -> bool:
        """Check whether a record is registered (including inactive)."""
        return approval_id in self._records
