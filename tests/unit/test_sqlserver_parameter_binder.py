from __future__ import annotations

import pytest

from release_sql_bot.application.ports.sql_parameter_binding import SqlParameterBindingError
from release_sql_bot.infrastructure.sql.sqlserver_parameter_binder import (
    SqlServerTokenParameterBinder,
)

BINDER = SqlServerTokenParameterBinder()


def test_single_parameter_binds_to_named_and_qmark_forms() -> None:
    sql = (
        "SELECT amounts.total_amount AS fact_value "
        "FROM reporting.synthetic_report_amounts AS amounts "
        "WHERE amounts.project_id = :projectId"
    )
    bound = BINDER.bind(sql)
    assert bound.describe_sql == sql.replace(":projectId", "@p0")
    assert bound.execute_sql == sql.replace(":projectId", "?")
    assert bound.ordered_parameter_names == ("projectId",)
    assert bound.occurrence_names == ("projectId",)
    assert bound.binder_version == "sqlserver-token-binder-v1"


def test_repeated_parameters_keep_stable_order_and_occurrences() -> None:
    sql = (
        "SELECT a AS fact_value FROM s.t AS x "
        "WHERE x.a = :projectId AND x.b = :other AND x.c = :projectId"
    )
    bound = BINDER.bind(sql)
    assert bound.ordered_parameter_names == ("projectId", "other")
    assert bound.occurrence_names == ("projectId", "other", "projectId")
    assert bound.describe_sql.count("@p0") == 2
    assert bound.describe_sql.count("@p1") == 1
    assert bound.execute_sql.count("?") == 3


def test_source_sql_bytes_unchanged_and_hashes_stable() -> None:
    sql = "SELECT a AS fact_value FROM s.t AS x WHERE x.a = :p1"
    bound = BINDER.bind(sql)
    assert bound.describe_sql != sql
    # The binder returns derived SQL; the original candidate is owned by the caller.
    assert sql.endswith(":p1")
    rebound = BINDER.bind(sql)
    assert bound == rebound


def test_colon_inside_string_literal_is_not_replaced() -> None:
    sql = "SELECT 'total :fake' AS fact_value FROM s.t AS x WHERE x.a = :projectId"
    bound = BINDER.bind(sql)
    assert "'total :fake'" in bound.describe_sql
    assert "@p0" in bound.describe_sql
    assert bound.ordered_parameter_names == ("projectId",)


def test_colon_inside_national_string_and_comment_is_not_replaced() -> None:
    sql = (
        "SELECT N'前缀 :fake 后缀' AS fact_value "
        "FROM s.t AS x -- comment :fakeComment\n"
        "WHERE x.a = :projectId"
    )
    bound = BINDER.bind(sql)
    assert "N'前缀 :fake 后缀'" in bound.describe_sql
    assert "-- comment :fakeComment" in bound.describe_sql
    assert bound.occurrence_names == ("projectId",)


def test_colon_inside_bracketed_identifier_is_not_replaced() -> None:
    sql = "SELECT [weird:col] AS fact_value FROM s.t AS x WHERE x.a = :projectId"
    bound = BINDER.bind(sql)
    assert "[weird:col]" in bound.describe_sql
    assert bound.ordered_parameter_names == ("projectId",)


def test_double_colon_type_cast_is_not_treated_as_parameter() -> None:
    sql = "SELECT 1::int AS fact_value FROM s.t AS x WHERE x.a = :projectId"
    bound = BINDER.bind(sql)
    assert "1::int" in bound.describe_sql
    assert bound.ordered_parameter_names == ("projectId",)


def test_trailing_colon_fails_closed() -> None:
    with pytest.raises(SqlParameterBindingError):
        BINDER.bind("SELECT a AS fact_value FROM s.t AS x WHERE x.a = :")


def test_colon_followed_by_non_name_token_fails_closed() -> None:
    with pytest.raises(SqlParameterBindingError):
        BINDER.bind("SELECT a AS fact_value FROM s.t AS x WHERE x.a = :123")


def test_adjacent_placeholders_bind_deterministically() -> None:
    # ":ok:bad" is two adjacent placeholders; the binder mirrors the tokenizer
    # and AST view exactly. Such SQL can never pass the Phase 4 position gate,
    # so the application preflight would reject it before binding is used.
    bound = BINDER.bind("SELECT a AS fact_value FROM s.t AS x WHERE x.a = :ok:bad")
    assert bound.ordered_parameter_names == ("ok", "bad")
    assert bound.occurrence_names == ("ok", "bad")


def test_untokenizable_sql_fails_closed() -> None:
    with pytest.raises(SqlParameterBindingError):
        BINDER.bind("SELECT 'unterminated AS fact_value FROM s.t AS x")


def test_derived_sql_contains_no_leftover_colon_parameters() -> None:
    sql = (
        "SELECT a AS fact_value FROM s.t AS x "
        "WHERE x.a = :pOne AND x.b = 'text :keep' AND x.c = :pTwo"
    )
    bound = BINDER.bind(sql)
    assert bound.describe_sql == (
        "SELECT a AS fact_value FROM s.t AS x WHERE x.a = @p0 AND x.b = 'text :keep' AND x.c = @p1"
    )
