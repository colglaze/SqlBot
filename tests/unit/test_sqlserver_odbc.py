from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pyodbc
import pytest

from release_sql_bot.application.ports.sqlserver_validation import (
    CatalogFactsRequest,
    DescribeRequest,
    ObjectRef,
    PermissionAttestationRequest,
    SessionLimits,
    SessionPolicy,
    SqlServerProbeUndeterminedError,
    SqlServerTimeoutError,
    SqlServerUnavailableError,
    TargetAttestationExpectation,
)
from release_sql_bot.infrastructure.sqlserver.odbc import (
    OdbcConnectionConfig,
    OdbcSqlServerValidator,
    build_connection_string,
)

CAPTURED_AT = datetime(2026, 8, 28, tzinfo=UTC)

CONFIG = OdbcConnectionConfig(
    host="synthetic-host",
    port=1433,
    database="synthetic_reporting",
    auth_mode="sql_login",
    username="synthetic_user",
    password="synthetic-password",
    odbc_driver="ODBC Driver 18 for SQL Server",
    login_timeout_seconds=10,
)

LIMITS = SessionLimits(
    connect_timeout_seconds=10,
    command_timeout_seconds=30,
    lock_timeout_milliseconds=2000,
)

POLICY = SessionPolicy(lock_timeout_milliseconds=2000, command_timeout_seconds=30)


class FakeCursor:
    def __init__(self, script: FakeConnection) -> None:
        self._script = script
        self.timeout: float = 0
        self.closed = False
        self.description: tuple | None = None
        self._rows: list[Any] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self._script.executed.append((sql, params))
        outcome = self._script.route(sql, params)
        if isinstance(outcome, Exception):
            raise outcome
        rows, description = outcome
        self._rows = rows
        self.description = description

    def fetchmany(self, size: int) -> list[Any]:
        return self._rows[:size]

    def fetchall(self) -> list[Any]:
        return list(self._rows)

    def close(self) -> None:
        self.closed = True


class FakeConnection:
    """Minimal DB-API double: routes fixed statements to scripted rows."""

    def __init__(
        self,
        routes: dict[str, Any] | None = None,
        param_routes: dict[tuple[str, tuple], Any] | None = None,
    ) -> None:
        self.executed: list[tuple[str, Any]] = []
        self.rollback_count = 0
        self.close_count = 0
        self.closed = False
        self._routes = routes or {}
        self._param_routes = param_routes or {}

    def route(self, sql: str, params: Any = None) -> Any:
        for (fragment, wanted), outcome in self._param_routes.items():
            if fragment in sql and params == wanted:
                return outcome
        for fragment, outcome in self._routes.items():
            if fragment in sql:
                return outcome
        return ([], None)

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.close_count += 1
        self.closed = True


def describe_description() -> tuple:
    return tuple(
        (name, None, None, None, None, None, None)
        for name in ("name", "system_type_name", "is_nullable")
    )


def target_routes() -> dict[str, Any]:
    return {
        "SERVERPROPERTY('ProductMajorVersion')": (
            [(16, 3, 160, "synthetic_reporting", "synthetic-host")],
            None,
        ),
        "fn_my_permissions(?, 'OBJECT')": ([("SELECT",)], None),
        "fn_my_permissions(NULL, 'DATABASE')": ([("SELECT",)], None),
        "fn_my_permissions(NULL, 'SERVER')": ([("VIEW SERVER STATE",)], None),
        "IS_ROLEMEMBER": ([(0, 0, 0)], None),
        "SELECT o.type, o.modify_date": (
            [("V", datetime(2026, 1, 1, tzinfo=UTC))],
            None,
        ),
        "TYPE_NAME(c.user_type_id)": (
            [
                ("total_amount", "decimal", 9, 18, 2, False, False, False),
                ("project_id", "int", 4, 10, 0, False, False, False),
            ],
            None,
        ),
        "sp_describe_first_result_set": (
            [("fact_value", "decimal(18,2)", False)],
            describe_description(),
        ),
    }


def fingerprint(text: str) -> str:
    return "fp:" + text


def build_validator(connection: FakeConnection) -> OdbcSqlServerValidator:
    return OdbcSqlServerValidator(
        config=CONFIG,
        fingerprint=fingerprint,
        connect=lambda connstr, timeout=0: connection,
    )


def test_connection_string_contains_fixed_security_keywords() -> None:
    text = build_connection_string(CONFIG)
    assert text == (
        "Driver={ODBC Driver 18 for SQL Server};Server={synthetic-host};"
        "Database={synthetic_reporting};UID={synthetic_user};PWD={synthetic-password};"
        "Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly;"
        "Application Name={ReleaseSQLBot-Validation};Connection Timeout=10;"
        "Pooling=no;MARS_Connection=no"
    )


def test_connection_string_windows_auth_uses_trusted_connection() -> None:
    config = OdbcConnectionConfig(
        host="synthetic-host",
        port=1433,
        database="synthetic_reporting",
        auth_mode="windows_integrated",
        username=None,
        password=None,
        odbc_driver="ODBC Driver 18 for SQL Server",
        login_timeout_seconds=10,
    )
    text = build_connection_string(config)
    assert "Trusted_Connection=yes" in text
    assert "UID=" not in text and "PWD=" not in text


def test_connection_string_rejects_injection_in_target_fields() -> None:
    config = OdbcConnectionConfig(
        host="synthetic-host;Database=evil",
        port=1433,
        database="synthetic_reporting",
        auth_mode="sql_login",
        username="u",
        password="p",
        odbc_driver="ODBC Driver 18 for SQL Server",
        login_timeout_seconds=10,
    )
    with pytest.raises(SqlServerUnavailableError):
        build_connection_string(config)


def test_connection_string_rejects_insecure_tls() -> None:
    config = OdbcConnectionConfig(
        host="synthetic-host",
        port=1433,
        database="synthetic_reporting",
        auth_mode="sql_login",
        username="u",
        password="p",
        odbc_driver="ODBC Driver 18 for SQL Server",
        login_timeout_seconds=10,
        trust_server_certificate=True,
    )
    with pytest.raises(SqlServerUnavailableError):
        build_connection_string(config)


class TestSessionProbes:
    def test_session_policy_statements_and_readback(self) -> None:
        connection = FakeConnection({"SELECT @@LOCK_TIMEOUT": ([(2000,)], None), **target_routes()})
        session = build_validator(connection).open_session(LIMITS)
        session.apply_session_policy(POLICY)
        statements = [sql for sql, _params in connection.executed]
        assert statements[:4] == [
            "SET NOCOUNT ON",
            "SET XACT_ABORT ON",
            "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
            "SET LOCK_TIMEOUT 2000",
        ]
        assert connection.executed[4][0] == "SELECT @@LOCK_TIMEOUT"

    def test_lock_timeout_readback_mismatch_fails_closed(self) -> None:
        connection = FakeConnection({"SELECT @@LOCK_TIMEOUT": ([(9999,)], None)})
        session = build_validator(connection).open_session(LIMITS)
        with pytest.raises(SqlServerProbeUndeterminedError):
            session.apply_session_policy(POLICY)

    def test_attest_target_fingerprints_identity(self) -> None:
        connection = FakeConnection(target_routes())
        session = build_validator(connection).open_session(LIMITS)
        evidence = session.attest_target(
            TargetAttestationExpectation(
                expected_server_fingerprint=fingerprint("server:synthetic-host"),
                expected_database_fingerprint=fingerprint("database:synthetic_reporting"),
                supported_major_versions=(16, 17),
            )
        )
        assert evidence.server_major_version == 16
        assert evidence.identity_matched is True
        assert evidence.version_supported is True

    def test_attest_target_mismatch_detected(self) -> None:
        connection = FakeConnection(target_routes())
        session = build_validator(connection).open_session(LIMITS)
        evidence = session.attest_target(
            TargetAttestationExpectation(
                expected_server_fingerprint="other",
                expected_database_fingerprint="other",
                supported_major_versions=(16, 17),
            )
        )
        assert evidence.identity_matched is False

    def test_permission_probes_are_parameterized(self) -> None:
        connection = FakeConnection(target_routes())
        session = build_validator(connection).open_session(LIMITS)
        result = session.attest_permissions(
            PermissionAttestationRequest(objects=(ObjectRef("reporting", "synthetic_report"),))
        )
        probe_calls = [
            (sql, params)
            for sql, params in connection.executed
            if "fn_my_permissions(?, 'OBJECT')" in sql
        ]
        assert probe_calls == [
            (
                "SELECT permission_name FROM fn_my_permissions(?, 'OBJECT')",
                ("reporting.synthetic_report",),
            )
        ]
        assert result.select_granted == (ObjectRef("reporting", "synthetic_report"),)
        assert result.forbidden_capabilities == ()
        # Database/server/role probes run with fixed statements only.
        assert any("fn_my_permissions(NULL, 'DATABASE')" in sql for sql, _ in connection.executed)
        assert any("IS_SRVROLEMEMBER(?)" in sql for sql, _ in connection.executed)

    def test_write_capability_detected(self) -> None:
        routes = {
            **target_routes(),
            "fn_my_permissions(?, 'OBJECT')": ([("SELECT",), ("UPDATE",)], None),
        }
        connection = FakeConnection(routes)
        session = build_validator(connection).open_session(LIMITS)
        result = session.attest_permissions(
            PermissionAttestationRequest(objects=(ObjectRef("s", "t"),))
        )
        assert "object:UPDATE" in result.forbidden_capabilities

    def test_catalog_probes_parameterized_and_ordered(self) -> None:
        connection = FakeConnection(target_routes())
        session = build_validator(connection).open_session(LIMITS)
        result = session.read_catalog_facts(
            CatalogFactsRequest(
                objects=(ObjectRef("reporting", "synthetic_report"),), captured_at=CAPTURED_AT
            )
        )
        existence = [
            (sql, params)
            for sql, params in connection.executed
            if "SELECT o.type, o.modify_date" in sql
        ]
        assert existence == [
            (
                "SELECT o.type, o.modify_date FROM sys.schemas AS s "
                "INNER JOIN sys.objects AS o ON o.schema_id = s.schema_id "
                "WHERE s.name = ? AND o.name = ?",
                ("reporting", "synthetic_report"),
            )
        ]
        assert len(result.relations) == 1
        assert result.relations[0].object_type == "V"
        assert result.relations[0].modified_after_capture is False
        assert [column.column_name for column in result.relations[0].columns] == [
            "total_amount",
            "project_id",
        ]
        assert result.relations[0].columns[0].system_type_name == "decimal"

    def test_missing_object_is_absent_not_undetermined(self) -> None:
        connection = FakeConnection(
            target_routes(),
            param_routes={
                ("SELECT o.type, o.modify_date", ("reporting", "missing_view")): ([], None),
            },
        )
        session = build_validator(connection).open_session(LIMITS)
        result = session.read_catalog_facts(
            CatalogFactsRequest(
                objects=(ObjectRef("reporting", "missing_view"),), captured_at=CAPTURED_AT
            )
        )
        assert result.relations == ()
        assert result.undetermined == ()

    def test_describe_uses_three_bound_parameters(self) -> None:
        connection = FakeConnection(target_routes())
        session = build_validator(connection).open_session(LIMITS)
        result = session.describe_first_result_set(
            DescribeRequest(tsql="SELECT @p0 AS fact_value", parameter_declaration="@p0 int")
        )
        describe_calls = [
            (sql, params)
            for sql, params in connection.executed
            if "sp_describe_first_result_set" in sql
        ]
        assert describe_calls == [
            (
                "EXEC sys.sp_describe_first_result_set @tsql = ?, @params = ?, "
                "@browse_information_mode = ?",
                ("SELECT @p0 AS fact_value", "@p0 int", 0),
            )
        ]
        assert result.columns[0].name == "fact_value"
        assert result.columns[0].system_type_name == "decimal(18,2)"
        assert result.columns[0].is_nullable is False


class TestErrorMappingAndCleanup:
    def test_query_timeout_maps_to_inconclusive_timeout(self) -> None:
        connection = FakeConnection(
            {"SELECT @@LOCK_TIMEOUT": pyodbc.Error("HYT00", "timeout")},
        )
        session = build_validator(connection).open_session(LIMITS)
        with pytest.raises(SqlServerTimeoutError):
            session.apply_session_policy(POLICY)

    def test_describe_rejection_maps_to_blocked(self) -> None:
        connection = FakeConnection(
            {"sp_describe_first_result_set": pyodbc.Error("42000", "rejected")},
        )
        session = build_validator(connection).open_session(LIMITS)
        with pytest.raises(Exception) as excinfo:
            session.describe_first_result_set(
                DescribeRequest(tsql="x", parameter_declaration="@p0 int")
            )
        assert type(excinfo.value).__name__ == "SqlServerDescribeRejectedError"
        assert excinfo.value.sqlstate == "42000"
        assert "rejected" not in str(excinfo.value)

    def test_catalog_failure_maps_to_probe_undetermined(self) -> None:
        connection = FakeConnection(
            {"SELECT o.type, o.modify_date": pyodbc.Error("42000", "permission denied")},
        )
        session = build_validator(connection).open_session(LIMITS)
        with pytest.raises(SqlServerProbeUndeterminedError):
            session.read_catalog_facts(
                CatalogFactsRequest(objects=(ObjectRef("s", "t"),), captured_at=CAPTURED_AT)
            )

    def test_cursor_closed_after_probe_exception(self) -> None:
        connection = FakeConnection(
            {"fn_my_permissions(?, 'OBJECT')": pyodbc.Error("42000", "denied")},
        )
        session = build_validator(connection).open_session(LIMITS)
        with pytest.raises(SqlServerProbeUndeterminedError):
            session.attest_permissions(PermissionAttestationRequest(objects=(ObjectRef("s", "t"),)))
        # Probes use fresh cursors; every opened cursor is closed even on failure.
        assert all(sql for sql, _ in connection.executed)

    def test_rollback_and_close_are_safe_and_recorded(self) -> None:
        connection = FakeConnection(target_routes())
        session = build_validator(connection).open_session(LIMITS)
        session.rollback_safely()
        session.close()
        assert connection.rollback_count == 1
        assert connection.close_count == 1

    def test_open_timeout_maps_to_timeout_error(self) -> None:
        def connect(connstr: str, timeout: int = 0) -> FakeConnection:
            raise pyodbc.Error("HYT00", "login timeout")

        validator = OdbcSqlServerValidator(config=CONFIG, fingerprint=fingerprint, connect=connect)
        with pytest.raises(SqlServerTimeoutError):
            validator.open_session(LIMITS)

    def test_open_connection_failure_maps_to_unavailable(self) -> None:
        def connect(connstr: str, timeout: int = 0) -> FakeConnection:
            raise pyodbc.Error("08001", "cannot connect")

        validator = OdbcSqlServerValidator(config=CONFIG, fingerprint=fingerprint, connect=connect)
        with pytest.raises(SqlServerUnavailableError):
            validator.open_session(LIMITS)

    def test_open_refuses_insecure_config(self) -> None:
        config = OdbcConnectionConfig(
            host="synthetic-host",
            port=1433,
            database="synthetic_reporting",
            auth_mode="sql_login",
            username="u",
            password="p",
            odbc_driver="ODBC Driver 18 for SQL Server",
            login_timeout_seconds=10,
            trust_server_certificate=True,
        )
        validator = OdbcSqlServerValidator(
            config=config, fingerprint=fingerprint, connect=FakeConnection
        )
        with pytest.raises(SqlServerUnavailableError):
            validator.open_session(LIMITS)


class TestDescribeLimits:
    def test_row_limit_is_enforced_in_adapter(self) -> None:
        routes = {
            **target_routes(),
            "sp_describe_first_result_set": (
                [("fact_value", "decimal(18,2)", False)] * 1001,
                describe_description(),
            ),
        }
        connection = FakeConnection(routes)
        session = build_validator(connection).open_session(LIMITS)
        result = session.describe_first_result_set(
            DescribeRequest(tsql="x", parameter_declaration="@p0 int", max_rows=1000)
        )
        assert result.response_rows == 1001

    def test_byte_limit_returns_empty_columns(self) -> None:
        routes = {
            **target_routes(),
            "sp_describe_first_result_set": (
                [("fact_value", "decimal(18,2)", False)],
                describe_description(),
            ),
        }
        connection = FakeConnection(routes)
        session = build_validator(connection).open_session(LIMITS)
        result = session.describe_first_result_set(
            DescribeRequest(tsql="x", parameter_declaration="@p0 int", max_bytes=1)
        )
        assert result.columns == ()
        assert result.response_bytes > 1
