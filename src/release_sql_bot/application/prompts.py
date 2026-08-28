"""Versioned prompts for SQL candidate generation."""

from __future__ import annotations

import json
from dataclasses import dataclass

from release_sql_bot.domain.fact_bindings import ValidateFactBindingRequest
from release_sql_bot.domain.sql_candidates import GeneratedCandidatePayload

SQLSERVER_CANDIDATE_PROMPT_VERSION = "sqlserver-fact-candidate-v1"
SQLSERVER_CANDIDATE_MAX_TOKENS = 4_096

_SYSTEM_PROMPT = """You generate one untrusted SQL Server fact-template candidate.
Return exactly one JSON object that conforms to the supplied JSON Schema. Do not return Markdown,
code fences, prose outside JSON, lifecycle status, approval claims, or execution claims.

The request contains exactly one non-derived fact and a versioned SQL Server metadata context.
Generate exactly one SQL query template in sqlTemplate. It must return one scalar column named
fact_value. Use only supplied relations and supplied mapping/entity-key columns. Never invent a
table, view, column, join, or business rule. Use named :parameter placeholders for every fact
parameter and never interpolate parameter values or example values into SQL. Do not use temporary
tables, permanent DDL, DML, stored procedures, dynamic SQL, external access, or multiple queries.

Populate usageCoverage with every supplied usage conditionId exactly once. The candidate is not
safe or executable: later AST validation, safety gates, restricted validation, and human review are
mandatory. The word JSON and this example describe the required envelope:
{"templateCode":"FACT_CODE_V1","sqlTemplate":"SELECT ... AS fact_value WHERE id = :id",
"parameters":[],"result":{},"allowedObjects":[],"usageCoverage":[],"assumptions":[],
"warnings":[]}.
"""


@dataclass(frozen=True, slots=True)
class CandidatePrompt:
    version: str
    system: str
    user: str


def build_sqlserver_candidate_prompt(payload: ValidateFactBindingRequest) -> CandidatePrompt:
    prompt_input = {
        "bindingRequest": payload.binding_request.model_dump(by_alias=True, mode="json"),
        "context": payload.context.model_dump(by_alias=True, mode="json"),
        "outputJsonSchema": GeneratedCandidatePayload.model_json_schema(by_alias=True),
    }
    return CandidatePrompt(
        version=SQLSERVER_CANDIDATE_PROMPT_VERSION,
        system=_SYSTEM_PROMPT,
        user=json.dumps(
            prompt_input,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
