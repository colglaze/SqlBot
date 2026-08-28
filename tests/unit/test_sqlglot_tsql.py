from __future__ import annotations

import pytest

from release_sql_bot.application.ports.sql_ast import (
    OfflineColumn,
    OfflineRelation,
    SqlGatePolicy,
    SqlInspectionRequest,
)
from release_sql_bot.infrastructure.sql.sqlglot_tsql import SqlglotTsqlInspector


def _schema() -> tuple[OfflineRelation, ...]:
    return (
        OfflineRelation(
            schema_name="reporting",
            relation_name="synthetic_report_amounts",
            columns=(
                OfflineColumn(name="total_amount", sql_type="decimal(18,2)"),
                OfflineColumn(name="project_id", sql_type="int"),
                OfflineColumn(name="event_at", sql_type="datetime2"),
            ),
        ),
        OfflineRelation(
            schema_name="reporting",
            relation_name="synthetic_projects",
            columns=(
                OfflineColumn(name="project_id", sql_type="int"),
                OfflineColumn(name="project_code", sql_type="nvarchar(40)"),
            ),
        ),
    )


def _inspect(
    sql: str,
    *,
    sensitivity: str = "insensitive",
    policy: SqlGatePolicy | None = None,
):
    return SqlglotTsqlInspector().inspect(
        SqlInspectionRequest(
            sql=sql,
            dialect="tsql",
            identifier_case_sensitivity=sensitivity,
            offline_schema=_schema(),
            gate_policy=policy or SqlGatePolicy(),
        )
    )


def _codes(sql: str, **kwargs: object) -> set[str]:
    return {item.code for item in _inspect(sql, **kwargs).issues}


def test_valid_tsql_produces_scope_qualified_offline_evidence() -> None:
    result = _inspect(
        "SELECT a.total_amount AS fact_value "
        "FROM reporting.synthetic_report_amounts AS a "
        "WHERE a.project_id = :projectId"
    )

    assert result.issues == ()
    assert result.summary.parser_ref.exact_version == "30.17.0"
    assert result.summary.statement_count == 1
    assert result.summary.root_kind == "Select"
    assert {
        (item.schema_name, item.relation_name, item.column_name)
        for item in result.summary.base_columns
    } == {
        ("reporting", "synthetic_report_amounts", "project_id"),
        ("reporting", "synthetic_report_amounts", "total_amount"),
    }
    assert result.summary.placeholders[0].name == "projectId"
    assert result.summary.placeholders[0].enclosing_clause == "where"
    assert result.summary.result_columns[0].source_columns[0].column_name == "total_amount"


def test_cte_is_not_misclassified_as_a_physical_object_and_lineage_reaches_base() -> None:
    result = _inspect(
        "WITH c AS ("
        "SELECT a.total_amount, a.project_id "
        "FROM reporting.synthetic_report_amounts AS a"
        ") "
        "SELECT c.total_amount AS fact_value FROM c "
        "WHERE c.project_id = :projectId"
    )

    assert result.issues == ()
    assert result.summary.cte_count == 1
    assert [(item.schema_name, item.relation_name) for item in result.summary.physical_objects] == [
        ("reporting", "synthetic_report_amounts")
    ]
    assert result.summary.result_columns[0].source_columns[0].column_name == "total_amount"


def test_join_sources_and_join_on_parameter_are_inspected_offline() -> None:
    result = _inspect(
        "SELECT a.total_amount AS fact_value "
        "FROM reporting.synthetic_report_amounts AS a "
        "INNER JOIN reporting.synthetic_projects AS p "
        "ON a.project_id = p.project_id AND p.project_code = :projectCode"
    )

    assert result.issues == ()
    assert result.summary.join_count == 1
    assert result.summary.physical_source_count == 2
    assert result.summary.placeholders[0].enclosing_clause == "joinOn"


def test_not_null_time_boundary_and_repeated_named_parameters_remain_ast_evidence() -> None:
    result = _inspect(
        "SELECT a.total_amount AS fact_value "
        "FROM reporting.synthetic_report_amounts AS a "
        "WHERE NOT (a.total_amount IS NULL) "
        "AND a.event_at >= :asOf "
        "AND (a.project_id = :projectId OR a.project_id = :projectId)"
    )

    assert result.issues == ()
    assert [item.name for item in result.summary.placeholders].count("projectId") == 2
    assert {item.name for item in result.summary.placeholders} == {"asOf", "projectId"}


def test_exists_projection_lineage_includes_authorized_subquery_dependencies() -> None:
    result = _inspect(
        "SELECT CASE WHEN EXISTS ("
        "SELECT 1 FROM reporting.synthetic_report_amounts AS a "
        "WHERE a.project_id = :projectId"
        ") THEN CAST(1 AS bit) ELSE CAST(0 AS bit) END AS fact_value"
    )

    assert result.issues == ()
    assert {
        (item.schema_name, item.relation_name, item.column_name)
        for item in result.summary.result_columns[0].source_columns
    } == {("reporting", "synthetic_report_amounts", "project_id")}


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("", "SQL_STATEMENT_COUNT"),
        ("SELECT 1;;", "SQL_STATEMENT_COUNT"),
        (";SELECT 1", "SQL_STATEMENT_COUNT"),
        ("SELECT 1; SELECT 2", "SQL_STATEMENT_COUNT"),
        ("SELECT ('unterminated", "SQL_PARSE_ERROR"),
    ],
)
def test_complete_parse_rejects_empty_slots_multiple_statements_and_errors(
    sql: str,
    expected: str,
) -> None:
    assert expected in _codes(sql)


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        ("DELETE FROM reporting.synthetic_report_amounts", "SQL_ROOT_NOT_SELECT"),
        (
            "SELECT a.total_amount AS fact_value INTO #x "
            "FROM reporting.synthetic_report_amounts AS a",
            "SQL_SELECT_INTO",
        ),
        (
            "SELECT a.total_amount AS fact_value "
            "FROM reporting.synthetic_report_amounts AS a UNION SELECT 1",
            "SQL_SET_OPERATION",
        ),
        (
            "SELECT a.total_amount AS fact_value "
            "FROM reporting.synthetic_report_amounts AS a WITH (NOLOCK)",
            "SQL_HINT_FORBIDDEN",
        ),
        (
            "SELECT RAND() AS fact_value FROM reporting.synthetic_report_amounts AS a",
            "SQL_FUNCTION_FORBIDDEN",
        ),
        (
            "SELECT f.x AS fact_value FROM reporting.fn(:projectId) AS f",
            "SQL_EXTERNAL_SOURCE",
        ),
        ("SELECT t.x AS fact_value FROM @tableValue AS t", "SQL_TEMP_OBJECT"),
        ("SELECT t.x AS fact_value FROM #temp AS t", "SQL_TEMP_OBJECT"),
    ],
)
def test_forbidden_readonly_structures_fail_closed(sql: str, expected: str) -> None:
    assert expected in _codes(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO reporting.synthetic_report_amounts(project_id) VALUES (:projectId)",
        "UPDATE reporting.synthetic_report_amounts SET project_id = :projectId",
        "DELETE FROM reporting.synthetic_report_amounts OUTPUT deleted.project_id",
        "CREATE TABLE reporting.created_by_candidate(id int)",
        "DROP TABLE reporting.synthetic_report_amounts",
        "EXEC dbo.synthetic_procedure :projectId",
        "EXEC sp_executesql N'SELECT 1'",
        "USE synthetic_database",
        "SET NOCOUNT ON",
        "DECLARE @projectId int",
        (
            "MERGE reporting.synthetic_report_amounts AS target "
            "USING reporting.synthetic_projects AS source "
            "ON target.project_id = source.project_id "
            "WHEN MATCHED THEN DELETE;"
        ),
    ],
)
def test_mutating_session_and_dynamic_statement_families_are_all_blocked(sql: str) -> None:
    result = _inspect(sql)

    assert result.issues
    assert result.summary.root_kind != "Select" or {item.code for item in result.issues} & {
        "SQL_FORBIDDEN_NODE",
        "SQL_PARAMETER_SYNTAX",
    }


def test_query_option_and_recursive_cte_shape_are_blocked() -> None:
    query_option = _codes(
        "SELECT a.total_amount AS fact_value "
        "FROM reporting.synthetic_report_amounts AS a OPTION (RECOMPILE)"
    )
    recursive_shape = _codes(
        "WITH c AS ("
        "SELECT a.total_amount, a.project_id "
        "FROM reporting.synthetic_report_amounts AS a "
        "UNION ALL "
        "SELECT c.total_amount, c.project_id FROM c"
        ") SELECT c.total_amount AS fact_value FROM c"
    )

    assert "SQL_HINT_FORBIDDEN" in query_option
    assert "SQL_SET_OPERATION" in recursive_shape


@pytest.mark.parametrize(
    ("placeholder", "expected"),
    [
        ("@projectId", "SQL_PARAMETER_SYNTAX"),
        ("?", "SQL_PARAMETER_SYNTAX"),
        ("$1", "SQL_PARAMETER_SYNTAX"),
        (":projectId", None),
    ],
)
def test_only_colon_named_parameters_are_accepted(
    placeholder: str,
    expected: str | None,
) -> None:
    codes = _codes(
        "SELECT a.total_amount AS fact_value "
        "FROM reporting.synthetic_report_amounts AS a "
        f"WHERE a.project_id = {placeholder}"
    )

    assert ("SQL_PARAMETER_SYNTAX" in codes) is (expected is not None)


def test_parameter_outside_where_or_join_on_is_rejected() -> None:
    assert "SQL_PARAMETER_POSITION" in _codes(
        "SELECT :projectId AS fact_value FROM reporting.synthetic_report_amounts AS a"
    )


def test_string_and_comment_text_do_not_create_placeholder_evidence() -> None:
    result = _inspect(
        "SELECT a.total_amount AS fact_value "
        "FROM reporting.synthetic_report_amounts AS a "
        "WHERE ':projectId' = ':projectId' -- :fake"
    )

    assert result.summary.placeholders == ()


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT a.total_amount AS fact_value FROM synthetic_report_amounts AS a",
            "SQL_SCHEMA_REQUIRED",
        ),
        (
            "SELECT a.total_amount AS fact_value "
            "FROM catalog.reporting.synthetic_report_amounts AS a",
            "SQL_CROSS_DATABASE",
        ),
        (
            "SELECT * FROM reporting.synthetic_report_amounts AS a",
            "SQL_STAR_FORBIDDEN",
        ),
        (
            "SELECT a.unknown_column AS fact_value FROM reporting.synthetic_report_amounts AS a",
            "SQL_COLUMN_UNKNOWN",
        ),
        (
            "SELECT project_id AS fact_value "
            "FROM reporting.synthetic_report_amounts AS a "
            "JOIN reporting.synthetic_projects AS p ON a.project_id = p.project_id",
            "SQL_COLUMN_AMBIGUOUS",
        ),
    ],
)
def test_object_and_column_qualification_is_fail_closed(sql: str, expected: str) -> None:
    assert expected in _codes(sql)


def test_identifier_case_policy_comes_only_from_snapshot_policy() -> None:
    sql = "SELECT A.TOTAL_AMOUNT AS fact_value FROM REPORTING.SYNTHETIC_REPORT_AMOUNTS AS A"

    assert _inspect(sql, sensitivity="insensitive").issues == ()
    assert "SQL_OBJECT_NOT_ALLOWED" in _codes(sql, sensitivity="sensitive")

    column_case_sql = (
        "SELECT a.TOTAL_AMOUNT AS fact_value FROM reporting.synthetic_report_amounts AS a"
    )
    assert _inspect(column_case_sql, sensitivity="insensitive").issues == ()
    assert "SQL_COLUMN_UNKNOWN" in _codes(
        column_case_sql,
        sensitivity="sensitive",
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT a.total_amount FROM reporting.synthetic_report_amounts AS a",
        "SELECT a.total_amount AS wrong FROM reporting.synthetic_report_amounts AS a",
        (
            "SELECT a.total_amount AS fact_value, a.project_id AS other "
            "FROM reporting.synthetic_report_amounts AS a"
        ),
    ],
)
def test_result_shape_requires_one_explicit_fact_value_projection(sql: str) -> None:
    assert "SQL_RESULT_SHAPE" in _codes(sql)


def test_fixed_complexity_policy_is_enforced() -> None:
    result = _inspect(
        "SELECT a.total_amount AS fact_value FROM reporting.synthetic_report_amounts AS a",
        policy=SqlGatePolicy(max_ast_nodes=1),
    )

    assert "SQL_COMPLEXITY_LIMIT" in {item.code for item in result.issues}
