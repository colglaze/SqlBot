"""View-shaped SQL compile contracts. Not a published or executable template."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from release_sql_bot.domain.fact_bindings_v3 import V3ReportModel

_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_IDENT_PATTERN = r"^[A-Za-z][A-Za-z0-9_]*$"


class KeyBindingV31(V3ReportModel):
    parameter_name: str = Field(pattern=r"^[a-z][A-Za-z0-9]*$", max_length=100)
    column_name: str = Field(pattern=_IDENT_PATTERN, max_length=128)


class SubjectRelationV31(V3ReportModel):
    schema_name: str = Field(pattern=_IDENT_PATTERN, max_length=128)
    relation_name: str = Field(pattern=_IDENT_PATTERN, max_length=128)
    key_bindings: tuple[KeyBindingV31, ...] = Field(min_length=1)

    @field_validator("relation_name")
    @classmethod
    def reject_temp(cls, value: str) -> str:
        if value.startswith(("#", "@")):
            raise ValueError("temporary objects are not allowed")
        return value


class FactMappingGrantV31(V3ReportModel):
    fact_code: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    schema_name: str = Field(pattern=_IDENT_PATTERN, max_length=128)
    relation_name: str = Field(pattern=_IDENT_PATTERN, max_length=128)
    column_name: str = Field(pattern=_IDENT_PATTERN, max_length=128)
    cardinality: Literal["scalar", "set"]
    key_bindings: tuple[KeyBindingV31, ...] = Field(min_length=1)

    @field_validator("relation_name")
    @classmethod
    def reject_temp(cls, value: str) -> str:
        if value.startswith(("#", "@")):
            raise ValueError("temporary objects are not allowed")
        return value


class ViewShapedMappingBundleV31(V3ReportModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    authority: Literal["syntheticCompileGrant", "metadataReviewApproved"] = "syntheticCompileGrant"
    subject: SubjectRelationV31
    grants: tuple[FactMappingGrantV31, ...] = Field(min_length=1)


class ViewShapedSqlCandidateV31(V3ReportModel):
    schema_version: Literal["1.0.0"] = "1.0.0"
    rule_version: str
    dialect: Literal["sqlserver"] = "sqlserver"
    sql_template: str = Field(min_length=1, max_length=100_000)
    parameter_names: tuple[str, ...]
    bound_literals: dict[str, str | int | float | bool]
    content_sha256: str = Field(pattern=_SHA256_PATTERN)
    mapping_sha256: str = Field(pattern=_SHA256_PATTERN)
    sql_generated: Literal[True] = True
    executable: Literal[False] = False
    static_status: Literal["passed", "blocked"]
    static_issue_codes: tuple[str, ...] = ()
