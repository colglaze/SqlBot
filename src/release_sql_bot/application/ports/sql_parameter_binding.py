"""Driver-neutral port for deterministic candidate parameter binding."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from release_sql_bot.domain.sqlserver_validation import BINDER_VERSION

_QMARK_PARAMETER_STYLE = "qmarkPositional"


class SqlParameterBindingError(Exception):
    """Raised when a placeholder cannot be bound deterministically.

    The binder never falls back to string interpolation; every unresolvable
    token fails closed and the original candidate stays untouched.
    """


@dataclass(frozen=True, slots=True)
class BoundSqlV2:
    """Deterministic derivation of one ``:name`` parameterized candidate SQL.

    ``describe_sql`` replaces ``:name`` with ``@pN`` named parameters for
    ``sp_describe_first_result_set``. ``execute_sql`` replaces ``:name`` with
    qmark placeholders; it is reserved for the later bounded-execution phase and
    is never executed in Phase 5A.
    """

    binder_version: str = BINDER_VERSION
    source_sql_sha256: str = ""
    describe_sql: str = ""
    describe_sql_sha256: str = ""
    execute_sql: str = ""
    execute_sql_sha256: str = ""
    ordered_parameter_names: tuple[str, ...] = ()
    occurrence_names: tuple[str, ...] = ()
    execute_parameter_style: str = _QMARK_PARAMETER_STYLE


class SqlParameterBinder(Protocol):
    """Bind ``:name`` placeholders using token spans, never whole-text regex."""

    def bind(self, sql: str) -> BoundSqlV2:
        """Derive the describe/qmark forms of one candidate SQL string.

        Implementations must keep the source bytes unchanged, must not touch
        colons inside strings, comments, or bracketed identifiers, must support
        repeated parameters in stable first-appearance order, and must raise
        :class:`SqlParameterBindingError` instead of guessing.
        """
