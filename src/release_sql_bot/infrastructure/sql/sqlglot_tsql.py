"""Fail-closed SQLGlot adapter for the fixed SQL Server AST safety policy."""

from __future__ import annotations

import re
from collections.abc import Iterable
from importlib.metadata import version

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, build_scope
from sqlglot.schema import MappingSchema

from release_sql_bot.application.ports.sql_ast import (
    OfflineRelation,
    SqlInspectionRequest,
    SqlInspectionResult,
)
from release_sql_bot.domain.sql_validation import (
    SqlBaseColumnEvidenceV2,
    SqlInspectionSummaryV2,
    SqlJoinEvidenceV2,
    SqlParserRefV2,
    SqlPhysicalColumnRefV2,
    SqlPhysicalObjectEvidenceV2,
    SqlPlaceholderEvidenceV2,
    SqlResultColumnEvidenceV2,
    SqlValidationIssueV2,
)

_NAMED_PARAMETER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_DOLLAR_PARAMETER = re.compile(r"^\$[0-9]+$")

_ALLOWED_FUNCTION_NODES = {
    "Abs",
    "And",
    "Avg",
    "Cast",
    "Case",
    "Coalesce",
    "Count",
    "DateAdd",
    "DateDiff",
    "Exists",
    "If",
    "Lower",
    "Max",
    "Min",
    "Or",
    "Round",
    "Substring",
    "Sum",
    "Trim",
    "TryCast",
    "Upper",
}

_FORBIDDEN_STATEMENT_NODES = {
    "Alter",
    "Analyze",
    "Attach",
    "Cache",
    "Command",
    "Commit",
    "Copy",
    "Create",
    "Declare",
    "Delete",
    "Describe",
    "Detach",
    "Drop",
    "Execute",
    "Grant",
    "Insert",
    "Kill",
    "LoadData",
    "Lock",
    "Merge",
    "Pragma",
    "Refresh",
    "Revoke",
    "Rollback",
    "Set",
    "Show",
    "Transaction",
    "TruncateTable",
    "Uncache",
    "Update",
    "Use",
}

_SET_OPERATION_NODES = {"Except", "Intersect", "Union"}
_HINT_NODES = {"Hint", "QueryOption", "WithTableHint"}


def _issue(
    gate_order: int,
    code: str,
    field_path: str,
    message: str,
    normalized_identifier: str | None = None,
) -> SqlValidationIssueV2:
    return SqlValidationIssueV2(
        gate_order=gate_order,
        code=code,
        field_path=field_path,
        message=message,
        normalized_identifier=normalized_identifier,
    )


def _normalize(value: str, sensitivity: str) -> str:
    return value if sensitivity == "sensitive" else value.casefold()


def _parser_ref(gate_version: str) -> SqlParserRefV2:
    return SqlParserRefV2(
        name="sqlglot",
        exact_version=version("sqlglot"),
        dialect="tsql",
        gate_version=gate_version,
    )


def _empty_summary(
    request: SqlInspectionRequest,
    *,
    statement_count: int = 0,
    root_kind: str = "none",
) -> SqlInspectionSummaryV2:
    return SqlInspectionSummaryV2(
        parser_ref=_parser_ref(request.gate_policy.version),
        statement_count=statement_count,
        root_kind=root_kind,
        node_count=0,
        max_depth=0,
        cte_count=0,
        join_count=0,
        physical_source_count=0,
    )


def _sort_issues(
    issues: Iterable[SqlValidationIssueV2],
) -> tuple[SqlValidationIssueV2, ...]:
    unique = {
        (
            item.gate_order,
            item.code,
            item.field_path,
            item.message,
            item.normalized_identifier,
        ): item
        for item in issues
    }
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.gate_order,
                item.code,
                item.field_path,
                item.normalized_identifier or "",
            ),
        )
    )


def _max_depth(nodes: list[exp.Expression]) -> int:
    maximum = 0
    for node in nodes:
        depth = 1
        parent = node.parent
        while parent is not None:
            depth += 1
            parent = parent.parent
        maximum = max(maximum, depth)
    return maximum


def _relation_indexes(
    relations: tuple[OfflineRelation, ...],
    sensitivity: str,
) -> tuple[
    dict[tuple[str, str], OfflineRelation],
    dict[tuple[str, str, str], tuple[str, str, str]],
]:
    relation_index: dict[tuple[str, str], OfflineRelation] = {}
    column_index: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    for relation in relations:
        relation_key = (
            _normalize(relation.schema_name, sensitivity),
            _normalize(relation.relation_name, sensitivity),
        )
        relation_index[relation_key] = relation
        for column in relation.columns:
            column_index[(*relation_key, _normalize(column.name, sensitivity))] = (
                relation.schema_name,
                relation.relation_name,
                column.name,
            )
    return relation_index, column_index


def _scope_physical_tables(root_scope: Scope) -> list[exp.Table]:
    tables: list[exp.Table] = []
    seen: set[int] = set()
    for scope in root_scope.traverse():
        for _alias, (_node, source) in scope.selected_sources.items():
            if isinstance(source, exp.Table) and id(source) not in seen:
                seen.add(id(source))
                tables.append(source)
    return tables


def _identifier_copy(expression: exp.Expression, sensitivity: str) -> exp.Expression:
    copied = expression.copy()
    if sensitivity == "sensitive":
        return copied

    def lower_identifier(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Identifier) and isinstance(node.this, str):
            node.set("this", node.this.casefold())
        return node

    return copied.transform(lower_identifier, copy=False)


def _mapping_schema(
    relations: tuple[OfflineRelation, ...],
    sensitivity: str,
) -> MappingSchema:
    mapping: dict[str, dict[str, dict[str, str]]] = {}
    for relation in relations:
        schema_name = _normalize(relation.schema_name, sensitivity)
        relation_name = _normalize(relation.relation_name, sensitivity)
        mapping.setdefault(schema_name, {})[relation_name] = {
            _normalize(column.name, sensitivity): column.sql_type for column in relation.columns
        }
    return MappingSchema(mapping, dialect="tsql", normalize=False)


def _table_relation_key(
    table: exp.Table,
    sensitivity: str,
) -> tuple[str, str] | None:
    if table.catalog or not table.db or not isinstance(table.this, exp.Identifier):
        return None
    return (_normalize(table.db, sensitivity), _normalize(table.name, sensitivity))


def _physical_column_for(
    column: exp.Column,
    scope: Scope,
    relation_index: dict[tuple[str, str], OfflineRelation],
    column_index: dict[tuple[str, str, str], tuple[str, str, str]],
    sensitivity: str,
) -> tuple[str, str, str] | None:
    source = scope.sources.get(column.table)
    if not isinstance(source, exp.Table):
        return None
    relation_key = _table_relation_key(source, sensitivity)
    if relation_key is None or relation_key not in relation_index:
        return None
    return column_index.get((*relation_key, _normalize(column.name, sensitivity)))


def _has_ambiguous_unqualified_column(
    root_scope: Scope,
    column_index: dict[tuple[str, str, str], tuple[str, str, str]],
    sensitivity: str,
) -> bool:
    for scope in root_scope.traverse():
        physical_sources = [
            source for source in scope.sources.values() if isinstance(source, exp.Table)
        ]
        for column in scope.columns:
            if column.table:
                continue
            matches = 0
            normalized_column = _normalize(column.name, sensitivity)
            for source in physical_sources:
                relation_key = _table_relation_key(source, sensitivity)
                if (
                    relation_key is not None
                    and (
                        *relation_key,
                        normalized_column,
                    )
                    in column_index
                ):
                    matches += 1
            if matches > 1:
                return True
    return False


def _select_expression(scope: Scope, name: str, sensitivity: str) -> exp.Expression | None:
    expression = scope.expression
    if not isinstance(expression, exp.Select):
        return None
    normalized_name = _normalize(name, sensitivity)
    matches = [
        selected
        for selected in expression.selects
        if _normalize(selected.alias_or_name, sensitivity) == normalized_name
    ]
    return matches[0] if len(matches) == 1 else None


def _projection_sources(
    expression: exp.Expression,
    scope: Scope,
    relation_index: dict[tuple[str, str], OfflineRelation],
    column_index: dict[tuple[str, str, str], tuple[str, str, str]],
    sensitivity: str,
    visited: set[tuple[int, str]],
) -> set[tuple[str, str, str]]:
    expression_column_ids = {id(item) for item in expression.find_all(exp.Column)}
    sources: set[tuple[str, str, str]] = set()
    for column in scope.columns:
        if id(column) not in expression_column_ids:
            continue
        physical = _physical_column_for(
            column,
            scope,
            relation_index,
            column_index,
            sensitivity,
        )
        if physical is not None:
            sources.add(physical)
            continue
        source = scope.sources.get(column.table)
        if not isinstance(source, Scope):
            continue
        visit_key = (id(source), _normalize(column.name, sensitivity))
        if visit_key in visited:
            continue
        visited.add(visit_key)
        selected = _select_expression(source, column.name, sensitivity)
        if selected is not None:
            sources.update(
                _projection_sources(
                    selected,
                    source,
                    relation_index,
                    column_index,
                    sensitivity,
                    visited,
                )
            )
    for subquery_scope in scope.subquery_scopes:
        parent = subquery_scope.expression.parent
        belongs_to_expression = False
        while parent is not None:
            if parent is expression:
                belongs_to_expression = True
                break
            parent = parent.parent
        visit_key = (id(subquery_scope), "__subquery__")
        if belongs_to_expression and visit_key not in visited:
            visited.add(visit_key)
            sources.update(
                _projection_sources(
                    subquery_scope.expression,
                    subquery_scope,
                    relation_index,
                    column_index,
                    sensitivity,
                    visited,
                )
            )
    return sources


def _enclosing_clause(node: exp.Expression) -> str:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Where):
            return "where"
        if isinstance(parent, exp.Join):
            return "joinOn"
        if isinstance(parent, (exp.Select, exp.Subquery)):
            return "other"
        parent = parent.parent
    return "other"


def _join_type(join: exp.Join) -> str:
    side = (join.args.get("side") or "").casefold()
    kind = (join.args.get("kind") or "").casefold()
    if kind == "cross":
        return "cross"
    if side in {"left", "right", "full"}:
        return side
    if kind in {"", "inner"}:
        return "inner"
    return "unknown"


def _physical_ref(value: tuple[str, str, str]) -> SqlPhysicalColumnRefV2:
    return SqlPhysicalColumnRefV2(
        schema_name=value[0],
        relation_name=value[1],
        column_name=value[2],
    )


class SqlglotTsqlInspector:
    """Inspect one untrusted SQL string without performing any external I/O."""

    @property
    def parser_ref(self) -> SqlParserRefV2:
        return _parser_ref("sqlserver-ast-safety-v1")

    def inspect(self, request: SqlInspectionRequest) -> SqlInspectionResult:
        policy = request.gate_policy
        issues: list[SqlValidationIssueV2] = []
        if len(request.sql) > policy.max_sql_characters:
            issues.append(
                _issue(
                    4,
                    "SQL_COMPLEXITY_LIMIT",
                    "/candidate/sqlTemplate",
                    "SQL 字符数超过固定门禁上限。",
                )
            )

        try:
            statements = sqlglot.parse(
                request.sql,
                read=request.dialect,
                error_level=ErrorLevel.RAISE,
            )
        except (SqlglotError, RecursionError, ValueError):
            summary = _empty_summary(request)
            issues.append(
                _issue(
                    4,
                    "SQL_PARSE_ERROR",
                    "/candidate/sqlTemplate",
                    "SQL 无法由固定 T-SQL parser 完整解析。",
                )
            )
            return SqlInspectionResult(summary=summary, issues=_sort_issues(issues))

        if len(statements) != 1 or statements[0] is None:
            summary = _empty_summary(request, statement_count=len(statements))
            issues.append(
                _issue(
                    4,
                    "SQL_STATEMENT_COUNT",
                    "/candidate/sqlTemplate",
                    "SQL 必须完整解析为一个且仅一个非空语句。",
                )
            )
            return SqlInspectionResult(summary=summary, issues=_sort_issues(issues))

        statement = statements[0]
        assert statement is not None
        nodes = list(statement.walk())
        root_kind = type(statement).__name__
        node_count = len(nodes)
        maximum_depth = _max_depth(nodes)
        cte_count = sum(isinstance(node, exp.CTE) for node in nodes)
        join_count = sum(isinstance(node, exp.Join) for node in nodes)

        root_scope = build_scope(statement)
        physical_tables = _scope_physical_tables(root_scope) if root_scope is not None else []
        physical_source_count = len(physical_tables)

        for value, limit, field_path in (
            (node_count, policy.max_ast_nodes, "/inspection/nodeCount"),
            (maximum_depth, policy.max_ast_depth, "/inspection/maxDepth"),
            (cte_count, policy.max_ctes, "/inspection/cteCount"),
            (join_count, policy.max_joins, "/inspection/joinCount"),
            (
                physical_source_count,
                policy.max_physical_sources,
                "/inspection/physicalSourceCount",
            ),
        ):
            if value > limit:
                issues.append(
                    _issue(
                        4,
                        "SQL_COMPLEXITY_LIMIT",
                        field_path,
                        "SQL AST 复杂度超过固定门禁上限。",
                    )
                )

        if not isinstance(statement, exp.Select):
            issues.append(
                _issue(
                    5,
                    "SQL_ROOT_NOT_SELECT",
                    "/inspection/rootKind",
                    "SQL 根节点必须是普通 SELECT。",
                    root_kind,
                )
            )

        features: set[str] = set()
        node_names = {type(node).__name__ for node in nodes}
        for node_name in sorted(node_names & _FORBIDDEN_STATEMENT_NODES):
            features.add("forbiddenNode")
            issues.append(
                _issue(
                    5,
                    "SQL_FORBIDDEN_NODE",
                    "/ast",
                    "SQL 包含只读候选门禁禁止的语句结构。",
                    node_name,
                )
            )
        if node_names & _SET_OPERATION_NODES:
            features.add("setOperation")
            issues.append(
                _issue(
                    5,
                    "SQL_SET_OPERATION",
                    "/ast",
                    "首版门禁禁止集合运算。",
                )
            )
        if node_names & _HINT_NODES:
            features.add("hint")
            issues.append(_issue(5, "SQL_HINT_FORBIDDEN", "/ast", "首版门禁禁止查询或表 hint。"))
        if any(isinstance(node, exp.Into) for node in nodes):
            features.add("selectInto")
            issues.append(_issue(5, "SQL_SELECT_INTO", "/ast/into", "首版门禁禁止 SELECT INTO。"))
        for node in statement.find_all(exp.With):
            if node.args.get("recursive"):
                features.add("recursiveCte")
                issues.append(
                    _issue(
                        5,
                        "SQL_FORBIDDEN_NODE",
                        "/ast/with",
                        "首版门禁禁止递归 CTE。",
                        "recursiveCte",
                    )
                )

        allowed_functions = _ALLOWED_FUNCTION_NODES
        for index, node in enumerate(node for node in nodes if isinstance(node, exp.Func)):
            node_name = type(node).__name__
            if node_name not in allowed_functions:
                features.add("forbiddenFunction")
                issues.append(
                    _issue(
                        6,
                        "SQL_FUNCTION_FORBIDDEN",
                        f"/ast/functions/{index}",
                        "SQL 包含固定函数 allowlist 之外的函数。",
                        node_name,
                    )
                )

        sensitivity = request.identifier_case_sensitivity
        relation_index, column_index = _relation_indexes(request.offline_schema, sensitivity)
        physical_objects: list[SqlPhysicalObjectEvidenceV2] = []
        source_structure_invalid = False
        for index, table in enumerate(physical_tables):
            path = f"/ast/physicalSources/{index}"
            raw_schema = table.db or ""
            raw_relation = table.name or ""
            normalized_identifier = ".".join(
                part
                for part in (
                    _normalize(raw_schema, sensitivity),
                    _normalize(raw_relation, sensitivity),
                )
                if part
            )
            temporary = (
                raw_relation.startswith(("#", "@"))
                or isinstance(table.this, exp.Parameter)
                or bool(getattr(table.this, "args", {}).get("temporary"))
                or bool(getattr(table.this, "args", {}).get("global_"))
            )
            if temporary:
                features.add("temporaryObject")
                source_structure_invalid = True
                issues.append(_issue(6, "SQL_TEMP_OBJECT", path, "首版门禁禁止临时对象和表变量。"))
            if table.catalog or isinstance(table.this, exp.Dot):
                features.add("crossDatabase")
                source_structure_invalid = True
                issues.append(
                    _issue(
                        6,
                        "SQL_CROSS_DATABASE",
                        path,
                        "物理源不得包含数据库、服务器或四段限定。",
                        normalized_identifier or None,
                    )
                )
            if not isinstance(table.this, exp.Identifier):
                features.add("externalOrFunctionSource")
                source_structure_invalid = True
                issues.append(
                    _issue(
                        6,
                        "SQL_EXTERNAL_SOURCE",
                        path,
                        "首版门禁禁止外部或函数型物理源。",
                    )
                )
            if not raw_schema:
                source_structure_invalid = True
                issues.append(
                    _issue(
                        6,
                        "SQL_SCHEMA_REQUIRED",
                        path,
                        "每个物理源必须显式写出 schema.relation。",
                        _normalize(raw_relation, sensitivity) or None,
                    )
                )

            relation_key = (
                _normalize(raw_schema, sensitivity),
                _normalize(raw_relation, sensitivity),
            )
            approved_relation = relation_index.get(relation_key)
            evidence_schema = (
                approved_relation.schema_name if approved_relation is not None else raw_schema
            )
            evidence_relation = (
                approved_relation.relation_name if approved_relation is not None else raw_relation
            )
            if evidence_schema and evidence_relation:
                physical_objects.append(
                    SqlPhysicalObjectEvidenceV2(
                        schema_name=evidence_schema,
                        relation_name=evidence_relation,
                        expression_path=path,
                    )
                )
            if (
                approved_relation is None
                and raw_schema
                and raw_relation
                and isinstance(table.this, exp.Identifier)
                and not table.catalog
                and not temporary
            ):
                source_structure_invalid = True
                issues.append(
                    _issue(
                        6,
                        "SQL_OBJECT_NOT_ALLOWED",
                        path,
                        "物理源不在当前离线元数据快照中。",
                        normalized_identifier or None,
                    )
                )

        placeholders: list[SqlPlaceholderEvidenceV2] = []
        placeholder_index = 0
        for node in nodes:
            if isinstance(node, exp.Placeholder):
                name = node.this if isinstance(node.this, str) and node.this else None
                raw_kind = "colonNamed" if name is not None else "anonymous"
                clause = _enclosing_clause(node)
                path = f"/ast/placeholders/{placeholder_index}"
                placeholders.append(
                    SqlPlaceholderEvidenceV2(
                        name=name,
                        raw_kind=raw_kind,
                        expression_path=path,
                        enclosing_clause=clause,
                    )
                )
                if name is None or not _NAMED_PARAMETER.fullmatch(name):
                    issues.append(
                        _issue(
                            8,
                            "SQL_PARAMETER_SYNTAX",
                            path,
                            "参数必须使用合法的 :name 命名占位符。",
                        )
                    )
                if clause == "other":
                    issues.append(
                        _issue(
                            8,
                            "SQL_PARAMETER_POSITION",
                            path,
                            "参数只能出现在 WHERE 或 JOIN ON 标量表达式中。",
                            name,
                        )
                    )
                placeholder_index += 1
            elif isinstance(node, exp.Parameter):
                name = node.name or None
                path = f"/ast/placeholders/{placeholder_index}"
                placeholders.append(
                    SqlPlaceholderEvidenceV2(
                        name=name,
                        raw_kind="atNamed",
                        expression_path=path,
                        enclosing_clause=_enclosing_clause(node),
                    )
                )
                issues.append(
                    _issue(
                        8,
                        "SQL_PARAMETER_SYNTAX",
                        path,
                        "参数只允许 :name 形式。",
                        name,
                    )
                )
                placeholder_index += 1
        for identifier in statement.find_all(exp.Identifier):
            if isinstance(identifier.this, str) and _DOLLAR_PARAMETER.fullmatch(identifier.this):
                path = f"/ast/placeholders/{placeholder_index}"
                placeholders.append(
                    SqlPlaceholderEvidenceV2(
                        name=identifier.this[1:],
                        raw_kind="dollarPositional",
                        expression_path=path,
                        enclosing_clause=_enclosing_clause(identifier),
                    )
                )
                issues.append(
                    _issue(
                        8,
                        "SQL_PARAMETER_SYNTAX",
                        path,
                        "参数只允许 :name 形式。",
                    )
                )
                placeholder_index += 1

        star_found = any(isinstance(node, exp.Star) for node in nodes)
        if star_found:
            features.add("star")
            issues.append(_issue(7, "SQL_STAR_FORBIDDEN", "/ast", "首版门禁禁止星号投影或展开。"))

        if root_scope is not None:
            for scope in root_scope.traverse():
                for column in scope.columns:
                    source = scope.sources.get(column.table)
                    relation_key = (
                        _table_relation_key(source, sensitivity)
                        if isinstance(source, exp.Table)
                        else None
                    )
                    if (
                        column.table
                        and relation_key is not None
                        and relation_key in relation_index
                        and (
                            *relation_key,
                            _normalize(column.name, sensitivity),
                        )
                        not in column_index
                    ):
                        issues.append(
                            _issue(
                                7,
                                "SQL_COLUMN_UNKNOWN",
                                "/ast/columns",
                                "基础列未按快照标识符大小写策略精确命中。",
                                _normalize(column.name, sensitivity),
                            )
                        )

        result_columns: list[SqlResultColumnEvidenceV2] = []
        join_evidence: list[SqlJoinEvidenceV2] = []
        base_columns: list[SqlBaseColumnEvidenceV2] = []
        can_qualify = (
            isinstance(statement, exp.Select)
            and root_scope is not None
            and not source_structure_invalid
            and not star_found
        )
        qualified_scope: Scope | None = None
        ambiguous_unqualified = root_scope is not None and _has_ambiguous_unqualified_column(
            root_scope,
            column_index,
            sensitivity,
        )
        if can_qualify:
            try:
                qualified = qualify(
                    _identifier_copy(statement, sensitivity),
                    dialect=request.dialect,
                    schema=_mapping_schema(request.offline_schema, sensitivity),
                    expand_stars=False,
                    validate_qualify_columns=True,
                    allow_partial_qualification=False,
                    quote_identifiers=False,
                    identify=False,
                )
                qualified_scope = build_scope(qualified)
            except SqlglotError as exc:
                error_kind = (
                    "SQL_COLUMN_AMBIGUOUS"
                    if ambiguous_unqualified or "ambiguous" in str(exc).casefold()
                    else "SQL_COLUMN_UNKNOWN"
                )
                issues.append(
                    _issue(
                        7,
                        error_kind,
                        "/ast/columns",
                        "基础列无法基于当前离线快照唯一 qualification。",
                    )
                )

        if qualified_scope is not None:
            discovered_columns: list[tuple[str, str, str]] = []
            for scope in qualified_scope.traverse():
                for column in scope.columns:
                    physical = _physical_column_for(
                        column,
                        scope,
                        relation_index,
                        column_index,
                        sensitivity,
                    )
                    if physical is not None:
                        discovered_columns.append(physical)
                    else:
                        source = scope.sources.get(column.table)
                        relation_key = (
                            _table_relation_key(source, sensitivity)
                            if isinstance(source, exp.Table)
                            else None
                        )
                        if relation_key is not None and relation_key in relation_index:
                            issues.append(
                                _issue(
                                    7,
                                    "SQL_COLUMN_UNKNOWN",
                                    "/ast/columns",
                                    "基础列未按快照标识符大小写策略精确命中。",
                                    _normalize(column.name, sensitivity),
                                )
                            )
            for index, (schema_name, relation_name, column_name) in enumerate(
                sorted(set(discovered_columns))
            ):
                base_columns.append(
                    SqlBaseColumnEvidenceV2(
                        schema_name=schema_name,
                        relation_name=relation_name,
                        column_name=column_name,
                        expression_path=f"/ast/baseColumns/{index}",
                    )
                )

            top_expression = qualified_scope.expression
            if isinstance(top_expression, exp.Select):
                for index, selected in enumerate(top_expression.selects):
                    source_columns = _projection_sources(
                        selected,
                        qualified_scope,
                        relation_index,
                        column_index,
                        sensitivity,
                        set(),
                    )
                    original_selected = statement.selects[index]
                    result_columns.append(
                        SqlResultColumnEvidenceV2(
                            alias=(
                                original_selected.alias
                                if isinstance(original_selected, exp.Alias)
                                else ""
                            ),
                            expression_path=f"/ast/select/expressions/{index}",
                            source_columns=tuple(
                                SqlPhysicalColumnRefV2(
                                    schema_name=schema_name,
                                    relation_name=relation_name,
                                    column_name=column_name,
                                )
                                for schema_name, relation_name, column_name in sorted(
                                    source_columns
                                )
                            ),
                        )
                    )

            join_index = 0
            for scope in qualified_scope.traverse():
                if not isinstance(scope.expression, exp.Select):
                    continue
                for join in scope.expression.args.get("joins") or []:
                    join_type = _join_type(join)
                    joined_alias = _normalize(join.alias_or_name, sensitivity)
                    on_expression = join.args.get("on")
                    pairs: list[tuple[tuple[str, str, str], tuple[str, str, str]]] = []
                    if isinstance(on_expression, exp.Expression):
                        for equality in on_expression.find_all(exp.EQ):
                            left = equality.this
                            right = equality.expression
                            if not isinstance(left, exp.Column) or not isinstance(
                                right, exp.Column
                            ):
                                continue
                            left_sources = _projection_sources(
                                left,
                                scope,
                                relation_index,
                                column_index,
                                sensitivity,
                                set(),
                            )
                            right_sources = _projection_sources(
                                right,
                                scope,
                                relation_index,
                                column_index,
                                sensitivity,
                                set(),
                            )
                            if len(left_sources) != 1 or len(right_sources) != 1:
                                continue
                            left_source = next(iter(left_sources))
                            right_source = next(iter(right_sources))
                            if left_source[:2] == right_source[:2]:
                                continue
                            if _normalize(left.table, sensitivity) == joined_alias:
                                left_source, right_source = right_source, left_source
                            pairs.append((left_source, right_source))
                    if not pairs:
                        issues.append(
                            _issue(
                                6,
                                "SQL_JOIN_NOT_ALLOWED",
                                f"/ast/joins/{join_index}",
                                "join 未形成可与 Phase 2G 授权关系核对的基础列等值连接。",
                            )
                        )
                    for pair_index, (left_source, right_source) in enumerate(pairs):
                        join_evidence.append(
                            SqlJoinEvidenceV2(
                                join_type=join_type,
                                left_column=_physical_ref(left_source),
                                right_column=_physical_ref(right_source),
                                expression_path=(
                                    f"/ast/joins/{join_index}/columnPairs/{pair_index}"
                                ),
                            )
                        )
                    join_index += 1

        if isinstance(statement, exp.Select):
            selections = statement.selects
            if (
                len(selections) != 1
                or not isinstance(selections[0], exp.Alias)
                or selections[0].alias != "fact_value"
            ):
                issues.append(
                    _issue(
                        9,
                        "SQL_RESULT_SHAPE",
                        "/ast/select/expressions",
                        "顶层必须只有一个显式命名为 fact_value 的投影。",
                    )
                )

        summary = SqlInspectionSummaryV2(
            parser_ref=_parser_ref(policy.version),
            statement_count=len(statements),
            root_kind=root_kind,
            node_count=node_count,
            max_depth=maximum_depth,
            cte_count=cte_count,
            join_count=join_count,
            physical_source_count=physical_source_count,
            physical_objects=tuple(physical_objects),
            base_columns=tuple(base_columns),
            placeholders=tuple(placeholders),
            result_columns=tuple(result_columns),
            joins=tuple(join_evidence),
            features=tuple(sorted(features)),
        )
        return SqlInspectionResult(summary=summary, issues=_sort_issues(issues))
