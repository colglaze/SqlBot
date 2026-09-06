"""Token-span ``:name`` binder for SQL Server describe validation.

The binder never rewrites SQL through an AST serializer and never applies a
regex to the whole statement. It only acts on tokenizer ``COLON`` tokens
immediately followed by a ``VAR`` token matching the Phase 4 name grammar.
Colons inside string literals, national strings, comments, or bracketed
identifiers never produce ``COLON`` tokens, so they cannot be replaced.
"""

from __future__ import annotations

from hashlib import sha256
from re import fullmatch

import sqlglot
from sqlglot.tokens import Token, TokenType

from release_sql_bot.application.ports.sql_parameter_binding import (
    BoundSqlV2,
    SqlParameterBindingError,
)
from release_sql_bot.domain.sqlserver_validation import BINDER_VERSION

_PARAMETER_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


def _sha256(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


class SqlServerTokenParameterBinder:
    """Deterministic ``:name`` -> ``@pN`` / ``?`` derivation for one SQL string."""

    def bind(self, sql: str) -> BoundSqlV2:
        if not sql:
            raise SqlParameterBindingError("empty sql template")
        try:
            tokens: list[Token] = sqlglot.tokenize(sql, read="tsql")
        except Exception as exc:  # noqa: BLE001 - tokenizer failure must fail closed
            raise SqlParameterBindingError("sql template could not be tokenized") from exc

        occurrences: list[tuple[int, int, str]] = []
        for index, token in enumerate(tokens):
            if token.token_type is not TokenType.COLON:
                continue
            nxt = tokens[index + 1] if index + 1 < len(tokens) else None
            if nxt is None or nxt.token_type is not TokenType.VAR:
                raise SqlParameterBindingError("colon token is not followed by a name token")
            name = nxt.text
            if not fullmatch(_PARAMETER_NAME_PATTERN, name):
                raise SqlParameterBindingError("placeholder name does not match the fixed grammar")
            # token start/end are inclusive character offsets into the source text.
            occurrences.append((token.start, nxt.end + 1, name))

        ordered_names: list[str] = []
        occurrence_names: list[str] = []
        name_to_slot: dict[str, int] = {}
        for _, _, name in occurrences:
            occurrence_names.append(name)
            if name not in name_to_slot:
                name_to_slot[name] = len(ordered_names)
                ordered_names.append(name)

        describe_sql = sql
        execute_sql = sql
        # Replace from the end so earlier spans stay valid.
        for start, end, name in reversed(occurrences):
            replacement = f"@p{name_to_slot[name]}"
            describe_sql = describe_sql[:start] + replacement + describe_sql[end:]
            execute_sql = execute_sql[:start] + "?" + execute_sql[end:]

        return BoundSqlV2(
            binder_version=BINDER_VERSION,
            source_sql_sha256=_sha256(sql),
            describe_sql=describe_sql,
            describe_sql_sha256=_sha256(describe_sql),
            execute_sql=execute_sql,
            execute_sql_sha256=_sha256(execute_sql),
            ordered_parameter_names=tuple(ordered_names),
            occurrence_names=tuple(occurrence_names),
        )
