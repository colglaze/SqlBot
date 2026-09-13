"""Independent V3 SQL AST static validation contracts (M4 first delivery).

V3 Phase 4: ValidateSqlCandidateRequestV3 / SqlStaticValidationReportV3.

This module defines the authoritative V3 validation contracts.
It does NOT import from application.ports or infrastructure.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator
from pydantic.alias_generators import to_camel

from release_sql_bot.domain.fact_bindings_v3 import V3ReportModel
from release_sql_bot.domain.sql_candidates_v3 import (
    GenerateSqlCandidateRequestV3,
    SqlTemplateCandidateV3,
)

_SHA256_PATTERN = r"^[a-f0-9]{64}$"


def _reject_snake_case_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and "_" in key:
                raise ValueError(f"snake_case key is not accepted: {key}")
            _reject_snake_case_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_snake_case_keys(nested)


class _V3Base(V3ReportModel):
    """V3 report base: camelCase, extra=forbid, frozen."""

    model_config = dict(
        alias_generator=to_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_nested_snake_case_fallback(cls, value: Any) -> Any:
        _reject_snake_case_keys(value)
        return value


class ValidateSqlCandidateRequestV3(_V3Base):
    """V3 static-validation request contract (M4)."""

    schema_version: Literal["1.0.0"]
    generation_request: GenerateSqlCandidateRequestV3
    candidate: SqlTemplateCandidateV3


class SqlValidationIssueV3(_V3Base):
    """Stable neutral issue with gate order and field path."""

    gate_order: int = Field(ge=1, le=20)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    field_path: str = Field(pattern=r"^/", max_length=1_000)
    message: str = Field(min_length=1, max_length=500)
    normalized_identifier: str | None = Field(default=None, max_length=500)


class SqlParserRefV3(_V3Base):
    name: Literal["sqlglot"]
    exact_version: str = Field(min_length=1, max_length=80)
    dialect: Literal["tsql"]
    gate_version: Literal["sqlserver-ast-safety-v1"]


class SqlPhysicalObjectEvidenceV3(_V3Base):
    schema_name: str
    relation_name: str
    expression_path: str


class SqlPlaceholderEvidenceV3(_V3Base):
    name: str | None
    expression_path: str
    enclosing_clause: Literal["where", "other"]


class SqlResultColumnEvidenceV3(_V3Base):
    alias: str
    expression_path: str
    source_schema: str
    source_relation: str
    source_column: str


class SqlComparisonEvidenceV3(_V3Base):
    """A single comparison in the WHERE clause."""

    expression_path: str
    operator: str
    left_schema: str | None = None
    left_relation: str | None = None
    left_column: str | None = None
    right_schema: str | None = None
    right_relation: str | None = None
    right_column: str | None = None
    left_parameter: str | None = None
    right_parameter: str | None = None
    left_is_literal: bool = False
    right_is_literal: bool = False
    left_literal_value: str | None = None
    right_literal_value: str | None = None


class SqlInspectionSummaryV3(_V3Base):
    """Authoritative V3 inspection summary contract.

    NOTE: rawKind / sourceColumns / joinOn are intentionally NOT included
    in the first-delivery contract because M4 scope does not support JOINs.
    If JOIN support is added in a future milestone, these fields should be
    added with appropriate backward-compatible handling.
    """

    statement_count: int = Field(ge=0)
    root_kind: str
    node_count: int = Field(ge=0)
    max_depth: int = Field(ge=0)
    physical_objects: tuple[SqlPhysicalObjectEvidenceV3, ...] = ()
    result_columns: tuple[SqlResultColumnEvidenceV3, ...] = ()
    placeholders: tuple[SqlPlaceholderEvidenceV3, ...] = ()
    comparisons: tuple[SqlComparisonEvidenceV3, ...] = ()
    features: tuple[str, ...] = ()


class SqlCandidateValidationRefV3(_V3Base):
    candidate_content_sha256: str = Field(pattern=_SHA256_PATTERN)
    sql_template_sha256: str = Field(pattern=_SHA256_PATTERN)
    generation_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    context_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)


class SqlStaticValidationReportV3(_V3Base):
    """V3 static validation report (M4).

    status passed | blocked. executable is always False.
    inspection is None when blocked before parser call.
    """

    schema_version: Literal["1.0.0"] = "1.0.0"
    gate_version: Literal["sqlserver-ast-safety-v1"] = "sqlserver-ast-safety-v1"
    status: Literal["passed", "blocked"]
    executable: Literal[False] = False
    candidate_ref: SqlCandidateValidationRefV3
    parser_ref: SqlParserRefV3
    issues: tuple[SqlValidationIssueV3, ...]
    inspection: SqlInspectionSummaryV3 | None
    usage_traceability_sha256: str = Field(pattern=_SHA256_PATTERN)
    handoff_refs: dict[str, str] = Field(default_factory=dict)
