"""Describe-only ODBC adapter for restricted SQL Server validation.

The adapter owns every protocol statement (session policy, permission probes,
catalog probes, ``sp_describe_first_result_set``); candidate SQL is never
concatenated into any of them. All variable input reaches SQL Server through
driver parameter binding. Driver failures are mapped to the fixed port error
classes without exposing driver message text.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pyodbc

from release_sql_bot.application.ports.sqlserver_validation import (
    CatalogFactsRequest,
    CatalogFactsResult,
    ColumnFacts,
    DescribeColumn,
    DescribeRequest,
    DescribeResult,
    ObjectRef,
    PermissionAttestationRequest,
    PermissionAttestationResult,
    RelationFacts,
    SessionLimits,
    SessionPolicy,
    SqlServerDescribeRejectedError,
    SqlServerProbeUndeterminedError,
    SqlServerTimeoutError,
    SqlServerUnavailableError,
    SqlServerValidationSession,
    TargetAttestationExpectation,
    TargetIdentityEvidence,
)
from release_sql_bot.domain.sqlserver_validation import forbidden_object_permissions

_APPLICATION_NAME = "ReleaseSQLBot-Validation"

_TIMEOUT_SQLSTATES = ("HYT00", "HYT01")
_CONNECTION_SQLSTATES = (
    "08001",
    "08003",
    "08004",
    "08007",
    "08S01",
    "IM002",
    "IM003",
)

_ALLOWED_KEYWORDS = {
    "driver",
    "server",
    "database",
    "uid",
    "pwd",
    "trusted_connection",
    "encrypt",
    "trustservercertificate",
    "applicationintent",
    "applicationname",
    "connectiontimeout",
    "pooling",
    "mars_connection",
}

# Fixed, application-owned session policy statements. LOCK_TIMEOUT only
# interpolates a config-validated integer into a constant template.
_SESSION_POLICY_STATEMENTS = (
    "SET NOCOUNT ON",
    "SET XACT_ABORT ON",
    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
)

_PERMISSION_PROBE_SQL = "SELECT permission_name FROM fn_my_permissions(?, 'OBJECT')"
_DATABASE_PERMISSION_PROBE_SQL = "SELECT permission_name FROM fn_my_permissions(NULL, 'DATABASE')"
_SERVER_PERMISSION_PROBE_SQL = "SELECT permission_name FROM fn_my_permissions(NULL, 'SERVER')"
_ROLE_PROBE_SQL = "SELECT IS_ROLEMEMBER(?), IS_ROLEMEMBER(?), IS_SRVROLEMEMBER(?)"
_OBJECT_EXISTENCE_PROBE_SQL = (
    "SELECT o.type, o.modify_date "
    "FROM sys.schemas AS s INNER JOIN sys.objects AS o ON o.schema_id = s.schema_id "
    "WHERE s.name = ? AND o.name = ?"
)
_COLUMN_PROBE_SQL = (
    "SELECT c.name, TYPE_NAME(c.user_type_id), c.max_length, c.precision, c.scale, "
    "c.is_nullable, c.is_computed, t.is_user_defined "
    "FROM sys.schemas AS s "
    "INNER JOIN sys.objects AS o ON o.schema_id = s.schema_id "
    "INNER JOIN sys.columns AS c ON c.object_id = o.object_id "
    "INNER JOIN sys.types AS t ON t.user_type_id = c.user_type_id "
    "WHERE s.name = ? AND o.name = ? "
    "ORDER BY c.column_id"
)
_TARGET_PROBE_SQL = (
    "SELECT CAST(SERVERPROPERTY('ProductMajorVersion') AS int), "
    "CAST(SERVERPROPERTY('EngineEdition') AS int), "
    "(SELECT CAST(compatibility_level AS int) FROM sys.databases WHERE database_id = DB_ID()), "
    "CAST(DB_NAME() AS nvarchar(128)), "
    "CAST(SERVERPROPERTY('ServerName') AS nvarchar(128))"
)
_DESCRIBE_SQL = (
    "EXEC sys.sp_describe_first_result_set @tsql = ?, @params = ?, @browse_information_mode = ?"
)


def _sqlstate_of(exc: Exception) -> str | None:
    args = exc.args
    if args and isinstance(args[0], str) and len(args[0]) == 5:
        return args[0]
    return None


def _reject_unsafe_value(value: str) -> str:
    if not value or value != value.strip():
        raise SqlServerUnavailableError("connectionString")
    if any(token in value for token in (";", "{", "}", "\x00")):
        raise SqlServerUnavailableError("connectionString")
    return value


def build_connection_string(config: OdbcConnectionConfig) -> str:
    """Build the fixed-keyword ODBC connection string from structured fields."""

    driver = _reject_unsafe_value(config.odbc_driver)
    server = _reject_unsafe_value(config.host)
    database = _reject_unsafe_value(config.database)
    if not config.encrypt or config.trust_server_certificate:
        raise SqlServerUnavailableError("connectionString")
    parts = [
        f"Driver={{{driver}}}",
        f"Server={{{server}}}",
        f"Database={{{database}}}",
    ]
    if config.auth_mode == "sql_login":
        username = _reject_unsafe_value(config.username or "")
        password = _reject_unsafe_value(config.password or "")
        parts.append(f"UID={{{username}}}")
        parts.append(f"PWD={{{password}}}")
    else:
        parts.append("Trusted_Connection=yes")
    parts.extend(
        [
            "Encrypt=yes",
            "TrustServerCertificate=no",
            "ApplicationIntent=ReadOnly",
            f"Application Name={{{_APPLICATION_NAME}}}",
            f"Connection Timeout={config.login_timeout_seconds}",
            "Pooling=no",
            "MARS_Connection=no",
        ]
    )
    connection_string = ";".join(parts)
    assert_connection_string_safe(connection_string)
    return connection_string


def assert_connection_string_safe(connection_string: str) -> None:
    """Fail closed on unknown or duplicated security keywords."""

    seen: set[str] = set()
    for part in connection_string.split(";"):
        if not part:
            continue
        if "=" not in part:
            raise SqlServerUnavailableError("connectionString")
        key, _, value = part.partition("=")
        normalized = key.strip().casefold().replace(" ", "").replace("-", "").replace("_", "_")
        if normalized in seen or normalized not in _ALLOWED_KEYWORDS:
            raise SqlServerUnavailableError("connectionString")
        seen.add(normalized)
        if value.startswith("{") != value.endswith("}"):
            raise SqlServerUnavailableError("connectionString")
    required_keywords = (
        "encrypt",
        "trustservercertificate",
        "applicationintent",
        "pooling",
        "mars_connection",
    )
    for keyword in required_keywords:
        if keyword not in seen:
            raise SqlServerUnavailableError("connectionString")


@dataclass(frozen=True, slots=True)
class OdbcConnectionConfig:
    host: str
    port: int
    database: str
    auth_mode: str
    username: str | None
    password: str | None
    odbc_driver: str
    login_timeout_seconds: int
    encrypt: bool = True
    trust_server_certificate: bool = False


class OdbcSqlServerValidationSession(SqlServerValidationSession):
    """One non-pooled session implementing the validation port with pyodbc."""

    def __init__(
        self,
        connection: Any,
        limits: SessionLimits,
        fingerprint: Callable[[str], str],
    ) -> None:
        self._connection = connection
        self._limits = limits
        self._fingerprint = fingerprint

    # -- helpers ----------------------------------------------------------

    def _execute(
        self,
        operation: str,
        sql: str,
        params: tuple[Any, ...] = (),
        *,
        max_rows: int | None = None,
    ) -> tuple[tuple[Any, ...] | None, list[Any]]:
        cursor = self._connection.cursor()
        try:
            cursor.timeout = self._limits.command_timeout_seconds
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            rows = (
                list(cursor.fetchmany(max_rows))
                if max_rows is not None
                else list(cursor.fetchall())
            )
            description = cursor.description
        except Exception as exc:
            self._close_cursor(cursor)
            raise self._map_error(exc, operation) from None
        self._close_cursor(cursor)
        return description, rows

    @staticmethod
    def _close_cursor(cursor: Any) -> None:
        try:
            cursor.close()
        except Exception:  # noqa: BLE001 - cleanup must never mask the real error
            pass

    def _map_error(self, exc: Exception, operation: str) -> Exception:
        if isinstance(exc, (SqlServerTimeoutError, SqlServerUnavailableError)):
            return exc
        sqlstate = _sqlstate_of(exc)
        if sqlstate in _TIMEOUT_SQLSTATES:
            return SqlServerTimeoutError(operation, sqlstate)
        if sqlstate in _CONNECTION_SQLSTATES:
            return SqlServerUnavailableError(operation, sqlstate)
        if operation.startswith("describe"):
            return SqlServerDescribeRejectedError(operation, sqlstate)
        return SqlServerProbeUndeterminedError(operation, sqlstate)

    # -- port implementation ----------------------------------------------

    def apply_session_policy(self, policy: SessionPolicy) -> None:
        for statement in _SESSION_POLICY_STATEMENTS:
            self._execute("sessionPolicy", statement)
        self._execute(
            "sessionPolicy",
            f"SET LOCK_TIMEOUT {int(policy.lock_timeout_milliseconds)}",
        )
        _description, rows = self._execute(
            "sessionPolicy",
            "SELECT @@LOCK_TIMEOUT",
            max_rows=1,
        )
        if not rows or rows[0][0] != policy.lock_timeout_milliseconds:
            raise SqlServerProbeUndeterminedError("sessionPolicy")

    def attest_target(
        self,
        expectation: TargetAttestationExpectation,
    ) -> TargetIdentityEvidence:
        _description, rows = self._execute("target", _TARGET_PROBE_SQL, max_rows=1)
        if not rows:
            raise SqlServerProbeUndeterminedError("target")
        major, edition, compatibility, database_name, server_name = rows[0]
        if major is None or edition is None or compatibility is None:
            raise SqlServerProbeUndeterminedError("target")
        server_fingerprint = self._fingerprint(f"server:{server_name}")
        database_fingerprint = self._fingerprint(f"database:{database_name}")
        return TargetIdentityEvidence(
            server_major_version=int(major),
            engine_edition=int(edition),
            compatibility_level=int(compatibility),
            server_fingerprint=server_fingerprint,
            database_fingerprint=database_fingerprint,
            identity_matched=(
                server_fingerprint == expectation.expected_server_fingerprint
                and database_fingerprint == expectation.expected_database_fingerprint
            ),
            version_supported=int(major) in expectation.supported_major_versions,
        )

    def attest_permissions(
        self,
        request: PermissionAttestationRequest,
    ) -> PermissionAttestationResult:
        select_granted: list[ObjectRef] = []
        select_denied: list[ObjectRef] = []
        forbidden: set[str] = set()
        for obj in request.objects:
            qualified = f"{obj.schema_name}.{obj.relation_name}"
            _description, rows = self._execute(
                "permission.object",
                _PERMISSION_PROBE_SQL,
                (qualified,),
            )
            names = {str(row[0]) for row in rows}
            if "SELECT" in names:
                select_granted.append(obj)
            else:
                select_denied.append(obj)
            for permission in forbidden_object_permissions():
                if permission in names:
                    forbidden.add(f"object:{permission}")

        _description, database_rows = self._execute(
            "permission.database",
            _DATABASE_PERMISSION_PROBE_SQL,
        )
        database_permissions = {str(row[0]) for row in database_rows}
        for permission in forbidden_object_permissions():
            if permission in database_permissions:
                forbidden.add(f"database:{permission}")
        _description, server_rows = self._execute(
            "permission.server",
            _SERVER_PERMISSION_PROBE_SQL,
        )
        server_permissions = {str(row[0]) for row in server_rows}
        for permission in ("IMPERSONATE ANY LOGIN", "CONTROL SERVER", "ALTER ANY LOGIN"):
            if permission in server_permissions:
                forbidden.add(f"server:{permission}")

        _description, role_rows = self._execute(
            "permission.roles",
            _ROLE_PROBE_SQL,
            ("db_owner", "db_datawriter", "sysadmin"),
        )
        if not role_rows:
            raise SqlServerProbeUndeterminedError("permission.roles")
        db_owner, db_datawriter, sysadmin = role_rows[0]
        if db_owner is None or db_datawriter is None or sysadmin is None:
            raise SqlServerProbeUndeterminedError("permission.roles")
        database_role_write = bool(db_owner) or bool(db_datawriter)
        server_role_write = bool(sysadmin)
        if database_role_write:
            forbidden.add("role:db_ownerOrDataWriter")
        if server_role_write:
            forbidden.add("role:sysadmin")

        return PermissionAttestationResult(
            determined=True,
            select_granted=tuple(select_granted),
            select_denied=tuple(select_denied),
            forbidden_capabilities=tuple(sorted(forbidden)),
            server_role_write_detected=server_role_write,
            database_role_write_detected=database_role_write,
        )

    def read_catalog_facts(self, request: CatalogFactsRequest) -> CatalogFactsResult:
        relations: list[RelationFacts] = []
        undetermined: list[ObjectRef] = []
        for obj in request.objects:
            _description, existence = self._execute(
                "catalog.existence",
                _OBJECT_EXISTENCE_PROBE_SQL,
                (obj.schema_name, obj.relation_name),
                max_rows=1,
            )
            if not existence:
                continue
            object_type, modify_date = existence[0][0], existence[0][1]
            _description, column_rows = self._execute(
                "catalog.columns",
                _COLUMN_PROBE_SQL,
                (obj.schema_name, obj.relation_name),
            )
            if not column_rows:
                undetermined.append(obj)
                continue
            columns = tuple(
                ColumnFacts(
                    column_name=str(row[0]),
                    system_type_name=None if row[7] else (str(row[1]) if row[1] else None),
                    is_user_defined_type=bool(row[7]),
                    max_length=int(row[2]),
                    precision=int(row[3]),
                    scale=int(row[4]),
                    is_nullable=bool(row[5]),
                    is_computed=bool(row[6]),
                )
                for row in column_rows
            )
            modified_after = (
                isinstance(modify_date, datetime)
                and isinstance(request.captured_at, datetime)
                and modify_date > request.captured_at
            )
            relations.append(
                RelationFacts(
                    schema_name=obj.schema_name,
                    relation_name=obj.relation_name,
                    object_type=str(object_type) if object_type else None,
                    modified_after_capture=modified_after,
                    columns=columns,
                )
            )
        return CatalogFactsResult(relations=tuple(relations), undetermined=tuple(undetermined))

    def describe_first_result_set(self, request: DescribeRequest) -> DescribeResult:
        description, rows = self._execute(
            "describe",
            _DESCRIBE_SQL,
            (request.tsql, request.parameter_declaration, request.browse_information_mode),
            max_rows=request.max_rows + 1,
        )
        response_rows = len(rows)
        response_bytes = 0
        if response_rows > request.max_rows:
            rows = rows[: request.max_rows]
        for row in rows:
            response_bytes += sum(len(str(value)) if value is not None else 0 for value in row)
        if response_bytes > request.max_bytes:
            return DescribeResult(
                columns=(),
                response_rows=response_rows,
                response_bytes=response_bytes,
            )
        columns = self._describe_columns(description, rows)
        return DescribeResult(
            columns=columns,
            response_rows=response_rows,
            response_bytes=response_bytes,
        )

    @staticmethod
    def _describe_columns(
        description: tuple[Any, ...] | None,
        rows: list[Any],
    ) -> tuple[DescribeColumn, ...]:
        if description is None:
            raise SqlServerDescribeRejectedError("describe.metadata")
        names = [item[0] for item in description]
        try:
            name_index = names.index("name")
            type_index = names.index("system_type_name")
            nullable_index = names.index("is_nullable")
        except ValueError:
            raise SqlServerDescribeRejectedError("describe.metadata") from None
        return tuple(
            DescribeColumn(
                name=str(row[name_index]) if row[name_index] is not None else None,
                system_type_name=(str(row[type_index]) if row[type_index] is not None else None),
                is_nullable=None if row[nullable_index] is None else bool(row[nullable_index]),
            )
            for row in rows
        )

    def rollback_safely(self) -> None:
        try:
            self._connection.rollback()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001 - cleanup must never raise
            pass


class OdbcSqlServerValidator:
    """Describe-only validation port backed by pyodbc and ODBC Driver 17/18."""

    def __init__(
        self,
        config: OdbcConnectionConfig,
        fingerprint: Callable[[str], str],
        connect: Callable[..., Any] | None = None,
    ) -> None:
        self._config = config
        self._fingerprint = fingerprint
        self._connect = connect if connect is not None else pyodbc.connect

    def open_session(self, limits: SessionLimits) -> OdbcSqlServerValidationSession:
        if not self._config.encrypt or self._config.trust_server_certificate:
            raise SqlServerUnavailableError("open")
        connection_string = build_connection_string(self._config)
        try:
            connection = self._connect(
                connection_string,
                timeout=limits.connect_timeout_seconds,
            )
        except pyodbc.Error as exc:
            raise self._map_open_error(exc) from None
        return OdbcSqlServerValidationSession(connection, limits, self._fingerprint)

    @staticmethod
    def _map_open_error(exc: Exception) -> Exception:
        sqlstate = _sqlstate_of(exc)
        if sqlstate in _TIMEOUT_SQLSTATES:
            return SqlServerTimeoutError("open", sqlstate)
        return SqlServerUnavailableError("open", sqlstate)
