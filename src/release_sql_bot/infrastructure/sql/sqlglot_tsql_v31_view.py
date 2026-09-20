"""Independent AST gate for view-shaped 3.1.0 SQL. Does not change the V3 gate."""

from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.scope import Scope, build_scope

_NAMED_PARAMETER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

_FORBIDDEN_ROOT = {
    "Alter",
    "Create",
    "Drop",
    "Delete",
    "Insert",
    "Update",
    "Merge",
    "TruncateTable",
    "Grant",
    "Revoke",
    "Execute",
    "Declare",
    "Set",
    "Commit",
    "Rollback",
    "Transaction",
    "Lock",
}

_ALLOWED_NODES = frozenset(
    {
        "Add",
        "Alias",
        "And",
        "Anonymous",
        "Boolean",
        "Case",
        "Coalesce",
        "Column",
        "Concat",
        "ConcatWs",
        "DateAdd",
        "Div",
        "EQ",
        "Exists",
        "Expression",
        "From",
        "GT",
        "GTE",
        "Identifier",
        "If",
        "Is",
        "Join",
        "LT",
        "LTE",
        "Lateral",
        "Limit",
        "Literal",
        "Mul",
        "NEQ",
        "National",
        "Not",
        "Null",
        "Or",
        "Paren",
        "Placeholder",
        "Select",
        "Sub",
        "Subquery",
        "Table",
        "TableAlias",
        "Where",
        "Distinct",
        "Star",
        "Count",
        "When",
    }
)

_ALLOWED_FUNCS = frozenset(
    {
        "Anonymous",
        "And",
        "Case",
        "Coalesce",
        "Concat",
        "ConcatWs",
        "Count",
        "DateAdd",
        "Exists",
        "If",
        "Not",
        "Or",
    }
)

_ALLOWED_ANONYMOUS = frozenset({"DATEADD", "COALESCE", "CONCAT", "CONCAT_WS", "TOP"})


@dataclass(frozen=True)
class ViewShapedRelationV31:
    schema_name: str
    relation_name: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class ViewShapedSqlInspectionV31:
    issue_codes: tuple[str, ...]
    physical_objects: tuple[tuple[str, str], ...]
    parameter_names: tuple[str, ...]


def inspect_view_shaped_sql_v31(
    sql: str,
    *,
    relations: tuple[ViewShapedRelationV31, ...],
    allowed_parameters: frozenset[str],
) -> ViewShapedSqlInspectionV31:
    issues: list[str] = []
    if len(sql) > 100_000:
        issues.append("SQL_COMPLEXITY_LIMIT")
    try:
        statements = sqlglot.parse(sql, read="tsql", error_level=ErrorLevel.RAISE)
    except (SqlglotError, RecursionError, ValueError):
        return ViewShapedSqlInspectionV31(("SQL_PARSE_ERROR",), (), ())
    if len(statements) != 1 or statements[0] is None:
        return ViewShapedSqlInspectionV31(("SQL_STATEMENT_COUNT",), (), ())
    statement = statements[0]
    nodes = list(statement.walk())
    if not isinstance(statement, exp.Select):
        return ViewShapedSqlInspectionV31(("SQL_ROOT_NOT_SELECT",), (), ())
    if len(nodes) > 4_000:
        issues.append("SQL_COMPLEXITY_LIMIT")
    names = {type(node).__name__ for node in nodes}
    for name in sorted(names):
        if name in _FORBIDDEN_ROOT:
            issues.append("SQL_DML_DDL")
        elif name not in _ALLOWED_NODES:
            issues.append("SQL_NODE_FORBIDDEN")
    if any(isinstance(node, exp.Star) and not isinstance(node.parent, exp.Count) for node in nodes):
        issues.append("SQL_STAR")
    if any(isinstance(node, exp.Window) for node in nodes):
        issues.append("SQL_WINDOW")
    if any(isinstance(node, exp.Into) for node in nodes):
        issues.append("SQL_INTO")
    if any(isinstance(node, exp.CTE) for node in nodes):
        issues.append("SQL_CTE")
    if any(isinstance(node, (exp.Union, exp.Except, exp.Intersect)) for node in nodes):
        issues.append("SQL_SET_OP")
    if any(isinstance(node, (exp.Sum, exp.Avg, exp.Min, exp.Max)) for node in nodes):
        issues.append("SQL_AGG")
    if any(isinstance(node, (exp.Cast, exp.Convert, exp.TryCast)) for node in nodes):
        issues.append("SQL_CAST")
    for node in nodes:
        if not isinstance(node, exp.Func):
            continue
        kind = type(node).__name__
        if kind in {"Column", "Identifier"} or kind in _ALLOWED_FUNCS:
            if kind == "Anonymous":
                raw = node.this if isinstance(node.this, str) else str(node.this)
                if raw.upper() not in _ALLOWED_ANONYMOUS:
                    issues.append("SQL_FUNCTION")
            continue
        issues.append("SQL_FUNCTION")
        break
    allowed = {
        (item.schema_name.casefold(), item.relation_name.casefold()): item for item in relations
    }
    physical: list[tuple[str, str]] = []
    root_scope = build_scope(statement) or Scope()
    tables: list[exp.Table] = []
    seen: set[int] = set()
    for scope in root_scope.traverse():
        for _alias, (_name, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table) and id(source) not in seen:
                seen.add(id(source))
                tables.append(source)
    for table in tables:
        raw_relation = table.name or ""
        if raw_relation.startswith(("#", "@")) or isinstance(table.this, exp.Parameter):
            issues.append("SQL_TEMP_OBJECT")
        if table.catalog:
            issues.append("SQL_CROSS_DATABASE")
        schema_name = table.db or ""
        if (
            not schema_name
            or not _IDENT.fullmatch(schema_name)
            or not _IDENT.fullmatch(raw_relation)
        ):
            issues.append("SQL_OBJECT_NOT_ALLOWED")
            continue
        approved = allowed.get((schema_name.casefold(), raw_relation.casefold()))
        if approved is None:
            issues.append("SQL_OBJECT_NOT_ALLOWED")
        else:
            physical.append((approved.schema_name, approved.relation_name))
    parameters: list[str] = []
    for node in nodes:
        if isinstance(node, exp.Placeholder):
            name = node.this if isinstance(node.this, str) else None
            if name is None or not _NAMED_PARAMETER.fullmatch(name):
                issues.append("SQL_PARAMETER_SYNTAX")
                continue
            if name not in allowed_parameters:
                issues.append("SQL_PARAMETER_UNKNOWN")
            parameters.append(name)
    for literal in statement.find_all(exp.Literal):
        if _in_where_or_join(literal) and bool(getattr(literal, "is_string", False)):
            issues.append("SQL_UNBOUND_LITERAL")
            break
    return ViewShapedSqlInspectionV31(
        tuple(dict.fromkeys(issues)),
        tuple(dict.fromkeys(physical)),
        tuple(dict.fromkeys(parameters)),
    )


def _in_where_or_join(node: exp.Expression) -> bool:
    current = node.parent
    while current is not None:
        if isinstance(current, (exp.Where, exp.Join)):
            return True
        if isinstance(current, exp.Select) and current is not node:
            return False
        current = current.parent
    return False
