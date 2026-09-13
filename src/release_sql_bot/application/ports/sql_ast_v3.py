"""V3-specific port for deterministic, offline SQL AST inspection.

Unlike the V2 port (sql_ast.py), this port is designed for the V3
static validation gate and returns evidence structured for semantic
checks: detailed WHERE-clause comparison analysis, not just three sets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class OfflineColumnV3:
    name: str
    sql_type: str


@dataclass(frozen=True)
class OfflineRelationV3:
    schema_name: str
    relation_name: str
    columns: tuple[OfflineColumnV3, ...]


@dataclass(frozen=True)
class SqlGatePolicyV3:
    version: Literal["sqlserver-ast-safety-v1"] = "sqlserver-ast-safety-v1"
    max_sql_characters: int = 100_000
    max_ast_nodes: int = 2_000
    max_ast_depth: int = 32


@dataclass(frozen=True)
class SqlInspectionRequestV3:
    sql: str
    dialect: Literal["tsql"]
    identifier_case_sensitivity: Literal["sensitive", "insensitive"]
    offline_schema: tuple[OfflineRelationV3, ...]
    gate_policy: SqlGatePolicyV3


@dataclass(frozen=True)
class SqlPhysicalObjectEvidenceV3:
    schema_name: str
    relation_name: str
    expression_path: str


@dataclass(frozen=True)
class SqlPlaceholderEvidenceV3:
    name: str
    expression_path: str
    enclosing_clause: Literal["where", "other"]


@dataclass(frozen=True)
class SqlResultColumnEvidenceV3:
    alias: str
    expression_path: str
    source_schema: str
    source_relation: str
    source_column: str


@dataclass(frozen=True)
class SqlComparisonEvidenceV3:
    """A single comparison in the WHERE clause."""

    expression_path: str
    operator: str  # "eq", "ne", "gt", "gte", "lt", "lte", "other"
    left_schema: str | None
    left_relation: str | None
    left_column: str | None
    right_schema: str | None
    right_relation: str | None
    right_column: str | None
    left_parameter: str | None  # :name if left side is a parameter
    right_parameter: str | None  # :name if right side is a parameter
    left_is_literal: bool
    right_is_literal: bool
    left_literal_value: str | None
    right_literal_value: str | None


@dataclass(frozen=True)
class SqlInspectionSummaryV3:
    statement_count: int
    root_kind: str
    node_count: int
    max_depth: int
    physical_objects: tuple[SqlPhysicalObjectEvidenceV3, ...]
    result_columns: tuple[SqlResultColumnEvidenceV3, ...]
    placeholders: tuple[SqlPlaceholderEvidenceV3, ...]
    comparisons: tuple[SqlComparisonEvidenceV3, ...]
    features: tuple[str, ...]


@dataclass(frozen=True)
class SqlInspectionIssueV3:
    code: str
    message: str
    normalized_identifier: str | None = None


@dataclass(frozen=True)
class SqlInspectionResultV3:
    summary: SqlInspectionSummaryV3
    issues: tuple[SqlInspectionIssueV3, ...]


class SqlDialectInspectorV3(Protocol):
    @property
    def parser_name(self) -> str: ...

    @property
    def parser_version(self) -> str: ...

    @property
    def dialect(self) -> str: ...

    @property
    def gate_version(self) -> str: ...

    def inspect(self, request: SqlInspectionRequestV3) -> SqlInspectionResultV3: ...
