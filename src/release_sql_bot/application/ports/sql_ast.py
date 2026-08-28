"""Port for deterministic, offline SQL dialect inspection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from release_sql_bot.domain.sql_validation import (
    SqlInspectionSummaryV2,
    SqlParserRefV2,
    SqlValidationIssueV2,
)


@dataclass(frozen=True)
class OfflineColumn:
    name: str
    sql_type: str


@dataclass(frozen=True)
class OfflineRelation:
    schema_name: str
    relation_name: str
    columns: tuple[OfflineColumn, ...]


@dataclass(frozen=True)
class SqlGatePolicy:
    version: Literal["sqlserver-ast-safety-v1"] = "sqlserver-ast-safety-v1"
    max_sql_characters: int = 100_000
    max_ast_nodes: int = 2_000
    max_ast_depth: int = 32
    max_ctes: int = 32
    max_joins: int = 32
    max_physical_sources: int = 100


@dataclass(frozen=True)
class SqlInspectionRequest:
    sql: str
    dialect: Literal["tsql"]
    identifier_case_sensitivity: Literal["sensitive", "insensitive"]
    offline_schema: tuple[OfflineRelation, ...]
    gate_policy: SqlGatePolicy


@dataclass(frozen=True)
class SqlInspectionResult:
    summary: SqlInspectionSummaryV2
    issues: tuple[SqlValidationIssueV2, ...]


class SqlDialectInspector(Protocol):
    @property
    def parser_ref(self) -> SqlParserRefV2: ...

    def inspect(self, request: SqlInspectionRequest) -> SqlInspectionResult: ...
