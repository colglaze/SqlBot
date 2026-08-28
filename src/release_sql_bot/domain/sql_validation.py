"""Parser-neutral contracts for the V2 offline SQL AST safety gate."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from release_sql_bot.domain.fact_bindings_v2 import ReportModel, V2ConsumerModel
from release_sql_bot.domain.sql_candidates_v2 import (
    GenerateSqlCandidateRequestV2,
    SqlTemplateCandidateV2,
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


def _same_wire_shape(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_wire_shape(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_wire_shape(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


class ValidateSqlCandidateRequestV2(V2ConsumerModel):
    schema_version: Literal["1.0.0"]
    generation_request: GenerateSqlCandidateRequestV2
    candidate: SqlTemplateCandidateV2

    @model_validator(mode="before")
    @classmethod
    def reject_nested_snake_case_fallback(cls, value: Any) -> Any:
        _reject_snake_case_keys(value)
        if isinstance(value, dict):
            generation_raw = value.get("generationRequest")
            candidate_raw = value.get("candidate")
            if isinstance(generation_raw, dict):
                generation = GenerateSqlCandidateRequestV2.model_validate(generation_raw)
                if not _same_wire_shape(
                    generation.model_dump(by_alias=True, mode="json"),
                    generation_raw,
                ):
                    raise ValueError("generationRequest must be complete and require no coercion")
            if isinstance(candidate_raw, dict):
                candidate = SqlTemplateCandidateV2.model_validate(candidate_raw)
                if not _same_wire_shape(
                    candidate.model_dump(by_alias=True, mode="json"),
                    candidate_raw,
                ):
                    raise ValueError("candidate must be complete and require no coercion")
        return value


class SqlParserRefV2(ReportModel):
    name: Literal["sqlglot"]
    exact_version: str = Field(min_length=1, max_length=80)
    dialect: Literal["tsql"]
    gate_version: Literal["sqlserver-ast-safety-v1"]


class SqlValidationIssueV2(ReportModel):
    gate_order: int = Field(ge=1, le=20)
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=120)
    field_path: str = Field(pattern=r"^/", max_length=1_000)
    message: str = Field(min_length=1, max_length=500)
    normalized_identifier: str | None = Field(default=None, max_length=500)


class SqlPhysicalObjectEvidenceV2(ReportModel):
    schema_name: str
    relation_name: str
    source_kind: Literal["physical"] = "physical"
    expression_path: str


class SqlPhysicalColumnRefV2(ReportModel):
    schema_name: str
    relation_name: str
    column_name: str


class SqlBaseColumnEvidenceV2(ReportModel):
    schema_name: str
    relation_name: str
    column_name: str
    expression_path: str

    def as_physical_ref(self) -> SqlPhysicalColumnRefV2:
        return SqlPhysicalColumnRefV2(
            schema_name=self.schema_name,
            relation_name=self.relation_name,
            column_name=self.column_name,
        )


class SqlPlaceholderEvidenceV2(ReportModel):
    name: str | None
    raw_kind: Literal["colonNamed", "anonymous", "atNamed", "dollarPositional"]
    expression_path: str
    enclosing_clause: Literal["where", "joinOn", "other"]


class SqlResultColumnEvidenceV2(ReportModel):
    alias: str
    expression_path: str
    source_columns: tuple[SqlPhysicalColumnRefV2, ...]


class SqlJoinEvidenceV2(ReportModel):
    join_type: Literal["inner", "left", "right", "full", "cross", "unknown"]
    left_column: SqlPhysicalColumnRefV2
    right_column: SqlPhysicalColumnRefV2
    expression_path: str


class SqlInspectionSummaryV2(ReportModel):
    parser_ref: SqlParserRefV2
    statement_count: int = Field(ge=0)
    root_kind: str
    node_count: int = Field(ge=0)
    max_depth: int = Field(ge=0)
    cte_count: int = Field(ge=0)
    join_count: int = Field(ge=0)
    physical_source_count: int = Field(ge=0)
    physical_objects: tuple[SqlPhysicalObjectEvidenceV2, ...] = ()
    base_columns: tuple[SqlBaseColumnEvidenceV2, ...] = ()
    placeholders: tuple[SqlPlaceholderEvidenceV2, ...] = ()
    result_columns: tuple[SqlResultColumnEvidenceV2, ...] = ()
    joins: tuple[SqlJoinEvidenceV2, ...] = ()
    features: tuple[str, ...] = ()


class SqlCandidateValidationRefV2(ReportModel):
    candidate_content_sha256: str = Field(pattern=_SHA256_PATTERN)
    sql_template_sha256: str = Field(pattern=_SHA256_PATTERN)
    generation_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    resolution_report_sha256: str = Field(pattern=_SHA256_PATTERN)
    context_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)


class SqlUsageCoverageEvidenceV2(ReportModel):
    condition_id: str
    condition_path: str
    fact_code: str
    result_expression_path: str


class SqlStaticValidationReportV2(ReportModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    gate_version: Literal["sqlserver-ast-safety-v1"] = "sqlserver-ast-safety-v1"
    status: Literal["passed", "blocked"]
    executable: Literal[False] = False
    candidate_ref: SqlCandidateValidationRefV2
    parser_ref: SqlParserRefV2
    issues: tuple[SqlValidationIssueV2, ...]
    inspection: SqlInspectionSummaryV2 | None
    usage_coverage: tuple[SqlUsageCoverageEvidenceV2, ...]
