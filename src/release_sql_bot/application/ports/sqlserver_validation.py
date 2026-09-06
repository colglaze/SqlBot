"""Driver-neutral port for restricted SQL Server description validation.

The domain and application layers must not import ``pyodbc``, ODBC handles, or
driver exception types; infrastructure maps driver failures into the fixed
error classes defined here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class SqlServerUnavailableError(Exception):
    """Transient unavailability (connect/driver/network). Maps to inconclusive."""

    def __init__(self, operation: str, sqlstate: str | None = None) -> None:
        self.operation = operation
        self.sqlstate = sqlstate
        super().__init__(f"sqlserver unavailable during {operation}")


class SqlServerTimeoutError(SqlServerUnavailableError):
    """Statement, connection, or lock timeout. Maps to inconclusive."""


class SqlServerDescribeRejectedError(Exception):
    """SQL Server refused to describe the batch. Maps to blocked."""

    def __init__(self, operation: str, sqlstate: str | None = None) -> None:
        self.operation = operation
        self.sqlstate = sqlstate
        super().__init__(f"sqlserver refused describe during {operation}")


class SqlServerProbeUndeterminedError(Exception):
    """A permission or catalog probe could not be evaluated. Maps to blocked."""

    def __init__(self, operation: str, sqlstate: str | None = None) -> None:
        self.operation = operation
        self.sqlstate = sqlstate
        super().__init__(f"sqlserver probe undetermined during {operation}")


@dataclass(frozen=True, slots=True)
class SessionLimits:
    connect_timeout_seconds: int
    command_timeout_seconds: int
    lock_timeout_milliseconds: int


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    lock_timeout_milliseconds: int
    command_timeout_seconds: int
    isolation_level: str = "READ COMMITTED"


@dataclass(frozen=True, slots=True)
class TargetAttestationExpectation:
    expected_server_fingerprint: str
    expected_database_fingerprint: str
    supported_major_versions: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TargetIdentityEvidence:
    server_major_version: int
    engine_edition: int
    compatibility_level: int
    server_fingerprint: str
    database_fingerprint: str
    identity_matched: bool
    version_supported: bool


@dataclass(frozen=True, slots=True)
class ObjectRef:
    schema_name: str
    relation_name: str


@dataclass(frozen=True, slots=True)
class PermissionAttestationRequest:
    objects: tuple[ObjectRef, ...]


@dataclass(frozen=True, slots=True)
class PermissionAttestationResult:
    determined: bool
    select_granted: tuple[ObjectRef, ...]
    select_denied: tuple[ObjectRef, ...]
    forbidden_capabilities: tuple[str, ...]
    server_role_write_detected: bool
    database_role_write_detected: bool


@dataclass(frozen=True, slots=True)
class ColumnFacts:
    column_name: str
    system_type_name: str | None
    is_user_defined_type: bool
    max_length: int
    precision: int
    scale: int
    is_nullable: bool
    is_computed: bool


@dataclass(frozen=True, slots=True)
class RelationFacts:
    schema_name: str
    relation_name: str
    object_type: str | None
    modified_after_capture: bool
    columns: tuple[ColumnFacts, ...]


@dataclass(frozen=True, slots=True)
class CatalogFactsRequest:
    objects: tuple[ObjectRef, ...]
    captured_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CatalogFactsResult:
    relations: tuple[RelationFacts, ...]
    undetermined: tuple[ObjectRef, ...]


@dataclass(frozen=True, slots=True)
class DescribeRequest:
    tsql: str
    parameter_declaration: str
    browse_information_mode: int = 0
    max_rows: int = 1000
    max_bytes: int = 1_000_000


@dataclass(frozen=True, slots=True)
class DescribeColumn:
    name: str | None
    system_type_name: str | None
    is_nullable: bool | None


@dataclass(frozen=True, slots=True)
class DescribeResult:
    columns: tuple[DescribeColumn, ...]
    response_rows: int
    response_bytes: int


class SqlServerValidationSession(Protocol):
    """One dedicated, non-pooled, minimal-privilege SQL Server session."""

    def apply_session_policy(self, policy: SessionPolicy) -> None:
        """Apply the fixed application-owned session statements and read them back."""

    def attest_target(
        self,
        expectation: TargetAttestationExpectation,
    ) -> TargetIdentityEvidence:
        """Read neutral server/database identity facts and fingerprint them."""

    def attest_permissions(
        self,
        request: PermissionAttestationRequest,
    ) -> PermissionAttestationResult:
        """Prove SELECT and detect write/control capabilities for exact objects."""

    def read_catalog_facts(self, request: CatalogFactsRequest) -> CatalogFactsResult:
        """Read live schema facts for exact Phase 4 objects only."""

    def describe_first_result_set(self, request: DescribeRequest) -> DescribeResult:
        """Call ``sys.sp_describe_first_result_set`` with fully bound parameters."""

    def rollback_safely(self) -> None:
        """Roll back any open transaction; never raise."""

    def close(self) -> None:
        """Close cursors and the connection; never raise."""


class SqlServerValidationPort(Protocol):
    def open_session(self, limits: SessionLimits) -> SqlServerValidationSession:
        """Open one new session; implementation must never reuse connections."""
