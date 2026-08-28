"""Static SQL inspection adapter assembly."""

from release_sql_bot.application.ports.sql_ast import SqlDialectInspector
from release_sql_bot.infrastructure.sql.sqlglot_tsql import SqlglotTsqlInspector


def build_sql_dialect_inspector() -> SqlDialectInspector:
    return SqlglotTsqlInspector()


__all__ = ["build_sql_dialect_inspector"]
