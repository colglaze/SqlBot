"""V3 SQLGlot adapter with detailed AST analysis for the static gate.

Returns structured evidence for semantic checks including per-comparison
WHERE-clause analysis. Same locked SQLGlot version and tsql dialect as V2.
"""

from __future__ import annotations

import re
from importlib.metadata import version

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope
from sqlglot.schema import MappingSchema

from release_sql_bot.application.ports.sql_ast_v3 import (
    OfflineRelationV3,
    SqlComparisonEvidenceV3,
    SqlInspectionIssueV3,
    SqlInspectionRequestV3,
    SqlInspectionResultV3,
    SqlInspectionSummaryV3,
    SqlPhysicalObjectEvidenceV3,
    SqlPlaceholderEvidenceV3,
    SqlResultColumnEvidenceV3,
)

_NAMED_PARAMETER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_FORBIDDEN_NODES = {
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

_SET_OPERATION_NODES = {"Except", "Intersect", "Union"}

# Whitelist: node types allowed in first-delivery scope.
_ALLOWED_NODES_MINIMAL: frozenset[str] = frozenset(
    {
        "Alias",
        "And",
        "Column",
        "EQ",
        "Expression",
        "From",
        "Identifier",
        "Paren",
        "Placeholder",
        "Select",
        "Table",
        "TableAlias",
        "Where",
    }
)


def _issue(code, msg, nid=None):
    return SqlInspectionIssueV3(code=code, message=msg, normalized_identifier=nid)


def _normalize(value: str, sensitivity: str) -> str:
    return value if sensitivity == "sensitive" else value.casefold()


def _rel_indexes(relations, sensitivity):
    ri: dict[tuple[str, str], OfflineRelationV3] = {}
    ci: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    for rel in relations:
        rk = (
            _normalize(rel.schema_name, sensitivity),
            _normalize(rel.relation_name, sensitivity),
        )
        ri[rk] = rel
        for col in rel.columns:
            ck = (*rk, _normalize(col.name, sensitivity))
            ci[ck] = (rel.schema_name, rel.relation_name, col.name)
    return ri, ci


def _build_alias_map(physical_tables, sensitivity: str) -> dict[str, tuple[str, str]]:
    """Map table alias -> (schema, relation) from physical tables."""
    result: dict[str, tuple[str, str]] = {}
    for t in physical_tables:
        rk = _table_key(t, sensitivity)
        if rk is not None and t.alias:
            result[t.alias] = rk
    return result


def _scope_tables(root_scope: Scope) -> list[exp.Table]:
    tables: list[exp.Table] = []
    seen: set[int] = set()
    for scope in root_scope.traverse():
        for _a, (_n, src) in scope.selected_sources.items():
            if isinstance(src, exp.Table) and id(src) not in seen:
                seen.add(id(src))
                tables.append(src)
    return tables


def _table_key(table: exp.Table, sensitivity: str) -> tuple[str, str] | None:
    # Reject cross-database / multi-part references (catalog = database prefix)
    if table.catalog or not table.db or not isinstance(table.this, exp.Identifier):
        return None
    return (_normalize(table.db, sensitivity), _normalize(table.name, sensitivity))


def _has_extra_qualifiers(col: exp.Column) -> bool:
    """Check if a Column reference has more than just table.column.

    Uses AST: col.parts gives the dot-separated components.
    Rejects: db.table.column (3+ parts), server.db.table.column (4+ parts).
    Accepts: column (1 part), alias.column / table.column (2 parts).
    """
    return len(col.parts) > 2


def _find_source_by_alias(scope, table_name, sensitivity):
    """Find a physical table source by alias/name (case-insensitive)."""
    for alias, source in scope.sources.items():
        if isinstance(source, exp.Table) and _normalize(alias, sensitivity) == _normalize(
            table_name, sensitivity
        ):
            return source
    return None


def _physical_col(col, scope, ri, ci, sensitivity):
    """Resolve a Column to its physical (schema, relation, column) triple.

    Supports:
    - Qualified columns: table.column (table can be alias or physical name)
    - Unqualified columns: resolved if exactly one physical source exists

    Rejects multi-part references (db.table.column, etc.).
    """
    # Reject column references with extra qualifiers (db.table.column)
    if _has_extra_qualifiers(col):
        return None
    if col.table:
        # Qualified column: look up the source by alias/name
        src = scope.sources.get(col.table)
        if not isinstance(src, exp.Table):
            # Try case-insensitive alias lookup
            src = _find_source_by_alias(scope, col.table, sensitivity)
            if src is None:
                return None
        rk = _table_key(src, sensitivity)
        if rk is None or rk not in ri:
            return None
        return ci.get((*rk, _normalize(col.name, sensitivity)))
    else:
        # Unqualified column: resolve if exactly one physical source
        physical_sources = [s for s in scope.sources.values() if isinstance(s, exp.Table)]
        if len(physical_sources) != 1:
            return None  # Ambiguous or no source
        rk = _table_key(physical_sources[0], sensitivity)
        if rk is None or rk not in ri:
            return None
        return ci.get((*rk, _normalize(col.name, sensitivity)))


def _max_depth(nodes):
    mx = 0
    for n in nodes:
        d = 1
        p = n.parent
        while p is not None:
            d += 1
            p = p.parent
        mx = max(mx, d)
    return mx


def _copy_expr(expr, sensitivity):
    c = expr.copy()
    if sensitivity == "sensitive":
        return c
    return c.transform(_lower_id, copy=False)


def _lower_id(node):
    if isinstance(node, exp.Identifier) and isinstance(node.this, str):
        node.set("this", node.this.casefold())
    return node


def _mapping_schema(relations, sensitivity):
    m: dict[str, dict[str, dict[str, str]]] = {}
    for rel in relations:
        sn = _normalize(rel.schema_name, sensitivity)
        rn = _normalize(rel.relation_name, sensitivity)
        m.setdefault(sn, {})[rn] = {
            _normalize(c.name, sensitivity): c.sql_type for c in rel.columns
        }
    return MappingSchema(m, dialect="tsql", normalize=False)


def _comp_from_node(node, path):
    """Check if node is a comparison operator. Return (op_name, path) or None."""
    if isinstance(node, exp.EQ):
        return ("eq", path)
    elif isinstance(node, exp.NEQ):
        return ("ne", path)
    elif isinstance(node, exp.GT):
        return ("gt", path)
    elif isinstance(node, exp.GTE):
        return ("gte", path)
    elif isinstance(node, exp.LT):
        return ("lt", path)
    elif isinstance(node, exp.LTE):
        return ("lte", path)
    return None


def _extract_comparisons(where_clause, alias_map, scope, ri, ci, sensitivity):
    """Extract comparisons with full physical column resolution."""
    results: list[SqlComparisonEvidenceV3] = []

    def _resolve_column(col):
        """Resolve a Column to its physical (schema, relation, column) triple."""
        if not isinstance(col, exp.Column):
            return None
        # Reject multi-part references (db.table.column) upfront
        if _has_extra_qualifiers(col):
            return None
        # Direct resolution through scope
        phys = _physical_col(col, scope, ri, ci, sensitivity)
        if phys is not None:
            return phys
        # Fallback: resolve alias -> physical relation, then look up column
        if col.table:
            alias_norm = _normalize(col.table, sensitivity)
            for phys_alias, (ps, pr) in alias_map.items():
                if _normalize(phys_alias, sensitivity) == alias_norm:
                    col_key = (
                        _normalize(ps, sensitivity),
                        _normalize(pr, sensitivity),
                        _normalize(col.name, sensitivity),
                    )
                    if col_key in ci:
                        return ci[col_key]
        return None

    def _resolve_side(node):
        """Resolve one side to (schema, relation, column, param, is_lit, lit_val)."""
        if isinstance(node, exp.Column):
            phys = _resolve_column(node)
            if phys is not None:
                return (*phys, None, False, None)
            return (None, None, None, None, False, None)
        if isinstance(node, exp.Placeholder):
            nm = node.this if isinstance(node.this, str) and node.this else None
            return (None, None, None, nm, False, None)
        if isinstance(node, (exp.Literal, exp.Boolean)):
            v = node.this if isinstance(node.this, str) else str(node.this)
            return (None, None, None, None, True, v)
        return (None, None, None, None, False, None)

    def walk(node, path):
        """Recursively validate WHERE structure.

        - Unwrap Paren
        - AND branches must both recurse successfully
        - EQ comparisons are collected
        - Any other node type (bare Column, bare Placeholder, etc.) is rejected
        """
        if node is None:
            return True  # Empty is fine
        # Unwrap parentheses
        if isinstance(node, exp.Paren):
            return walk(node.this, f"{path}/paren")
        # Comparison node
        comp = _comp_from_node(node, path)
        if comp is not None:
            op, comp_path = comp
            ls, lr, lc, lp, ll, lv = _resolve_side(node.this)
            rs, rr, rc, rp, rl, rv = _resolve_side(node.expression)
            resolved = SqlComparisonEvidenceV3(
                expression_path=comp_path,
                operator=op,
                left_schema=ls,
                left_relation=lr,
                left_column=lc,
                left_parameter=lp,
                left_is_literal=ll,
                left_literal_value=lv,
                right_schema=rs,
                right_relation=rr,
                right_column=rc,
                right_parameter=rp,
                right_is_literal=rl,
                right_literal_value=rv,
            )
            results.append(resolved)
            return True
        # AND: both branches must be valid
        if isinstance(node, exp.And):
            exprs = node.flatten()
            for idx, child in enumerate(exprs):
                if not walk(child, f"{path}/and/{idx}"):
                    return False
            return True
        # WHERE wrapper
        if isinstance(node, exp.Where):
            return walk(node.this, f"{path}/this")
        # Any other node type is invalid in WHERE
        return False

    is_valid = walk(where_clause, "/where")
    if not is_valid:
        # Return empty comparisons; the caller will detect the structural issue
        # through the features/issues mechanism
        return []
    seen: dict[str, SqlComparisonEvidenceV3] = {}
    for c in results:
        if c.expression_path not in seen:
            seen[c.expression_path] = c
    return list(seen.values())


def _in_where(node):
    p = node.parent
    while p is not None:
        if isinstance(p, exp.Where):
            return True
        if isinstance(p, (exp.Select, exp.Subquery)):
            return False
        p = p.parent
    return False


def _find_src_col(sel, scope, ri, ci, sensitivity):
    """Resolve physical column of a SELECT expression.

    Only accepts a direct column: unwrap Alias and Paren, then require
    exactly one Column node. Any function, arithmetic, CASE, EQ, etc.
    is rejected even if it contains an authorized column as descendant.
    """
    expr = sel
    while isinstance(expr, exp.Alias):
        expr = expr.this
    while isinstance(expr, exp.Paren):
        expr = expr.this
    if not isinstance(expr, exp.Column):
        return None
    return _physical_col(expr, scope, ri, ci, sensitivity)


class SqlglotTsqlInspectorV3:
    @property
    def parser_name(self) -> str:
        return "sqlglot"

    @property
    def parser_version(self) -> str:
        return version("sqlglot")

    @property
    def dialect(self) -> str:
        return "tsql"

    @property
    def gate_version(self) -> str:
        return "sqlserver-ast-safety-v1"

    def inspect(self, request: SqlInspectionRequestV3) -> SqlInspectionResultV3:
        policy = request.gate_policy
        issues: list[SqlInspectionIssueV3] = []

        if len(request.sql) > policy.max_sql_characters:
            issues.append(_issue("SQL_COMPLEXITY_LIMIT", "SQL 字符数超限。"))

        try:
            stmts = sqlglot.parse(request.sql, read=request.dialect, error_level=ErrorLevel.RAISE)
        except (SqlglotError, RecursionError, ValueError):
            issues.append(_issue("SQL_PARSE_ERROR", "SQL 解析失败。"))
            return SqlInspectionResultV3(
                SqlInspectionSummaryV3(0, "none", 0, 0, (), (), (), (), ()),
                tuple(issues),
            )

        if len(stmts) != 1 or stmts[0] is None:
            issues.append(_issue("SQL_STATEMENT_COUNT", "SQL 必须恰好一条语句。"))
            return SqlInspectionResultV3(
                SqlInspectionSummaryV3(len(stmts), "none", 0, 0, (), (), (), (), ()),
                tuple(issues),
            )

        stmt = stmts[0]
        nodes = list(stmt.walk())
        root_kind = type(stmt).__name__
        nc = len(nodes)
        md = _max_depth(nodes)

        if nc > policy.max_ast_nodes:
            issues.append(_issue("SQL_COMPLEXITY_LIMIT", "AST 节点数超限。"))
        if md > policy.max_ast_depth:
            issues.append(_issue("SQL_COMPLEXITY_LIMIT", "AST 深度超限。"))

        if not isinstance(stmt, exp.Select):
            issues.append(_issue("SQL_ROOT_NOT_SELECT", "根节点必须是 SELECT。"))
            return SqlInspectionResultV3(
                SqlInspectionSummaryV3(1, root_kind, nc, md, (), (), (), (), ()),
                tuple(issues),
            )

        features: set[str] = set()
        nnames = {type(n).__name__ for n in nodes}

        # Whitelist: reject any node type not in the minimal allowed set
        for nm in sorted(nnames):
            if nm not in _ALLOWED_NODES_MINIMAL:
                features.add(f"forbidden:{nm}")
                issues.append(_issue("SQL_NODE_FORBIDDEN", f"首版门禁不支持 {nm} 结构。", nm))

        # Structural checks for specific features
        if stmt.args.get("limit") is not None or stmt.args.get("offset") is not None:
            features.add("limit")
            issues.append(_issue("SQL_LIMIT", "首版门禁禁止 TOP/OFFSET。"))

        if stmt.args.get("order") is not None:
            features.add("order")
            issues.append(_issue("SQL_ORDER_BY", "首版门禁禁止 ORDER BY。"))

        if any(isinstance(n, exp.Distinct) for n in nodes):
            features.add("distinct")
            issues.append(_issue("SQL_DISTINCT", "首版门禁禁止 DISTINCT。"))

        if any(isinstance(n, exp.Group) for n in nodes):
            features.add("group")
            issues.append(_issue("SQL_GROUP_BY", "首版门禁禁止 GROUP BY。"))

        if any(isinstance(n, exp.Having) for n in nodes):
            features.add("having")
            issues.append(_issue("SQL_HAVING", "首版门禁禁止 HAVING。"))

        if any(isinstance(n, exp.Window) for n in nodes):
            features.add("window")
            issues.append(_issue("SQL_WINDOW", "首版门禁禁止窗口函数。"))

        # Functions: any Func subclass, EXCEPT And/Or/Not (logical connectors).
        # And is a Func subclass in sqlglot, so we must exclude it explicitly.
        _logical_funcs = {"And", "Or", "Not"}
        for n in nodes:
            if isinstance(n, exp.Func) and type(n).__name__ not in (
                "Column",
                "Identifier",
                *_logical_funcs,
            ):
                features.add("func")
                issues.append(
                    _issue(
                        "SQL_FUNCTION",
                        f"首版门禁禁止函数调用：{type(n).__name__}。",
                        type(n).__name__,
                    )
                )
                break

        if any(isinstance(n, (exp.Cast, exp.Convert, exp.TryCast)) for n in nodes):
            features.add("cast")
            issues.append(_issue("SQL_CAST", "首版门禁禁止 CAST/CONVERT。"))

        if any(isinstance(n, exp.Case) for n in nodes):
            features.add("case")
            issues.append(_issue("SQL_CASE", "首版门禁禁止 CASE。"))

        if any(isinstance(n, (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max)) for n in nodes):
            features.add("agg")
            issues.append(_issue("SQL_AGG", "首版门禁禁止聚合函数。"))

        if any(isinstance(n, (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)) for n in nodes):
            features.add("arith")
            issues.append(_issue("SQL_ARITH", "首版门禁禁止算术运算。"))

        if any(isinstance(n, exp.Join) for n in nodes):
            features.add("join")
            issues.append(_issue("SQL_JOIN", "首版门禁禁止 JOIN。"))

        for scope in (build_scope(stmt) or Scope()).traverse():
            if scope.is_subquery:
                features.add("subquery")
                issues.append(_issue("SQL_SUBQUERY", "首版门禁禁止子查询。"))
                break

        if any(isinstance(n, exp.Star) for n in nodes):
            features.add("star")
            issues.append(_issue("SQL_STAR", "首版门禁禁止星号。"))

        if any(isinstance(n, exp.Into) for n in nodes):
            features.add("into")
            issues.append(_issue("SQL_INTO", "首版门禁禁止 SELECT INTO。"))

        if any(isinstance(n, exp.CTE) for n in nodes):
            features.add("cte")
            issues.append(_issue("SQL_CTE", "首版门禁禁止 CTE。"))

        if any(isinstance(n, (exp.Union, exp.Except, exp.Intersect)) for n in nodes):
            features.add("setOp")
            issues.append(_issue("SQL_SET_OP", "首版门禁禁止集合运算。"))

        # WHERE checks
        where_node = None
        for n in nodes:
            if isinstance(n, exp.Where):
                where_node = n
                break
        if where_node is not None:
            if any(isinstance(n, exp.Or) for n in where_node.find_all(exp.Or)):
                features.add("or")
                issues.append(_issue("SQL_OR", "首版门禁只允许 AND 条件。"))
            if any(isinstance(n, exp.Not) for n in where_node.find_all(exp.Not)):
                features.add("not")
                issues.append(_issue("SQL_NOT", "首版门禁禁止 NOT。"))
            # Non-EQ predicates (LIKE, IN, IS, BETWEEN, GT, LT, etc.)
            for n in where_node.walk():
                if n is where_node:
                    continue
                nname = type(n).__name__
                if nname in (
                    "Like",
                    "ILike",
                    "In",
                    "Is",
                    "Between",
                    "GT",
                    "GTE",
                    "LT",
                    "LTE",
                    "NEQ",
                ):
                    features.add("predicate")
                    issues.append(
                        _issue("SQL_PREDICATE", f"首版门禁只允许等值比较，不支持 {nname}。", nname)
                    )
                    break

        # Pre-qualification: check original column references for extra qualifiers.
        # qualify() may strip schema prefixes, so we must check the raw AST first.
        for col in stmt.find_all(exp.Column):
            if _has_extra_qualifiers(col):
                features.add("extraQualifier")
                issues.append(
                    _issue(
                        "SQL_COLUMN_QUALIFIER",
                        "列引用包含多余限定符，本切片仅支持 column 或 alias.column。",
                    )
                )
                break

        sens = request.identifier_case_sensitivity
        ri, ci = _rel_indexes(request.offline_schema, sens)
        rs = build_scope(stmt)
        pt = _scope_tables(rs) if rs is not None else []
        alias_map = _build_alias_map(pt, sens)

        pobjects = []
        for idx, t in enumerate(pt):
            raw_s = t.db or ""
            raw_r = t.name or ""
            tmp = raw_r.startswith(("#", "@")) or isinstance(t.this, exp.Parameter)
            if tmp:
                features.add("temp")
                issues.append(_issue("SQL_TEMP_OBJECT", "禁止临时对象。"))
            if t.catalog:
                features.add("crossDb")
                issues.append(_issue("SQL_CROSS_DATABASE", "禁止跨库。"))
            rk = (_normalize(raw_s, sens), _normalize(raw_r, sens))
            ap = ri.get(rk)
            if ap is not None:
                po = SqlPhysicalObjectEvidenceV3(ap.schema_name, ap.relation_name, f"/ps/{idx}")
                pobjects.append(po)
            elif raw_s and raw_r and isinstance(t.this, exp.Identifier) and not t.catalog:
                features.add("unknownObj")
                issues.append(_issue("SQL_OBJECT_NOT_ALLOWED", "对象不在快照。"))

        phs = []
        for node in nodes:
            if isinstance(node, exp.Placeholder):
                nm = node.this if isinstance(node.this, str) and node.this else None
                cl = "where" if _in_where(node) else "other"
                phs.append(SqlPlaceholderEvidenceV3(nm, f"/ph/{len(phs)}", cl))
                if nm is None or not _NAMED_PARAMETER.fullmatch(nm):
                    issues.append(_issue("SQL_PARAMETER_SYNTAX", "参数格式错误。"))

        rcols = []
        if rs is not None:
            try:
                schema = _mapping_schema(request.offline_schema, sens)
                ql = qualify(
                    _copy_expr(stmt, sens),
                    dialect=request.dialect,
                    schema=schema,
                    expand_stars=False,
                    validate_qualify_columns=True,
                    allow_partial_qualification=False,
                    quote_identifiers=False,
                    identify=False,
                )
                qs = build_scope(ql)
                if qs is not None and isinstance(qs.expression, exp.Select):
                    for idx, sel in enumerate(qs.expression.selects):
                        al = sel.alias if isinstance(sel, exp.Alias) else sel.name
                        src = _find_src_col(sel, qs, ri, ci, sens)
                        rc = SqlResultColumnEvidenceV3(
                            al,
                            f"/sel/{idx}",
                            src[0] if src else "",
                            src[1] if src else "",
                            src[2] if src else "",
                        )
                        rcols.append(rc)
            except SqlglotError:
                pass

        comps = (
            _extract_comparisons(where_node, alias_map, rs, ri, ci, sens)
            if where_node is not None
            else []
        )
        # If WHERE exists but no valid comparisons were extracted,
        # the WHERE structure is invalid (e.g., bare Column, bare Placeholder)
        if where_node is not None and not comps:
            features.add("invalid_where")
            issues.append(
                _issue("SQL_WHERE_INVALID", "WHERE 子句结构不合法：仅允许 EQ 比较和 AND 连接。")
            )

        summary = SqlInspectionSummaryV3(
            1,
            root_kind,
            nc,
            md,
            tuple(pobjects),
            tuple(rcols),
            tuple(phs),
            tuple(comps),
            tuple(sorted(features)),
        )
        return SqlInspectionResultV3(summary, tuple(issues))
