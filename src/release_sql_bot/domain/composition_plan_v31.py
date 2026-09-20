"""Frozen combination contract for one view-shaped read-only SQL. Not a compiled query."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from release_sql_bot.domain.fact_bindings_v3 import V3ReportModel

_SHA256_PATTERN = r"^[a-f0-9]{64}$"

COMPILATION_BLOCKERS_V31 = (
    "METADATA_REVIEW",
    "SQL_NOT_COMPILED",
)


class RuntimeParameterBindingV31(V3ReportModel):
    name: str
    role: str
    data_type: str
    sql_parameter: bool
    database_column: Literal[False] = False


class MemberSetQueryV31(V3ReportModel):
    collection_fact_code: str
    returns_entity_keys: Literal[True] = True
    member_eligible_boolean_column: Literal[False] = False
    quantifier_stays_on_rule_side: Literal[True] = True


class FactQueryRefV31(V3ReportModel):
    fact_code: str
    cardinality: Literal["scalar", "set"]
    grain: str


class ViewShapedResultContractV31(V3ReportModel):
    grain: str
    entity_key_parameters: tuple[str, ...]
    columns: tuple[str, ...]
    like_reference_view: Literal[True] = True
    matches_current_release_view_or_branches: Literal[False] = False
    single_statement: Literal[True] = True
    read_only: Literal[True] = True
    parameterized: Literal[True] = True
    dialect: Literal["sqlserver"] = "sqlserver"


class ViewShapedSqlCompositionPlanV31(V3ReportModel):
    schema_version: Literal["1.0.0"]
    rule_version: str
    catalog_digest: str = Field(pattern=_SHA256_PATTERN)
    candidate_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    stage_order: tuple[str, ...]
    eligibility_on_hit: Literal["firstMatchThenContinue"]
    other_stages_on_hit: Literal["terminate"]
    combination: Literal["rule-tree-over-fact-queries"]
    description_is_not_sql: Literal[True] = True
    result: ViewShapedResultContractV31
    fact_queries: tuple[FactQueryRefV31, ...]
    runtime_parameters: tuple[RuntimeParameterBindingV31, ...]
    member_sets: tuple[MemberSetQueryV31, ...]
    compilation_blockers: tuple[str, ...]
    sql_generated: Literal[False] = False
    executable: Literal[False] = False
