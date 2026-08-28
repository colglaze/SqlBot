from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any, Literal

from release_sql_bot.application.candidates_v2 import generate_sql_candidate_v2
from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.application.metadata_resolution_v2 import resolve_metadata_v2
from release_sql_bot.application.ports.candidates import CandidateModelResponse
from release_sql_bot.domain.project_bindings_v2 import ResolveMetadataRequestV2
from release_sql_bot.domain.sql_candidates_v2 import GenerateSqlCandidateRequestV2
from release_sql_bot.domain.sql_validation import ValidateSqlCandidateRequestV2
from tests.fakes import FixedCandidateModelProvider
from tests.phase2g_support import (
    generate_candidate_request_payload,
    refresh_gap_report,
    resolve_metadata_payload,
    valid_generated_candidate_v2_payload,
)

VALID_SQL = (
    "SELECT amounts.total_amount AS fact_value "
    "FROM reporting.synthetic_report_amounts AS amounts "
    "WHERE amounts.project_id = :projectId"
)

VALID_JOIN_SQL = (
    "SELECT amounts.total_amount AS fact_value "
    "FROM reporting.synthetic_report_amounts AS amounts "
    "INNER JOIN reporting.synthetic_projects AS projects "
    "ON amounts.project_id = projects.project_id "
    "WHERE projects.project_id = :projectId"
)

ResultMode = Literal["column", "aggregation", "exists"]


def _generation_payload(
    *,
    second_relation: bool,
    result_mode: ResultMode,
) -> dict[str, Any]:
    if result_mode == "column":
        return generate_candidate_request_payload(second_relation=second_relation)

    resolution_payload = resolve_metadata_payload(second_relation=second_relation)
    binding = resolution_payload["bindingRequest"]
    aggregation = binding["queryRequirements"]["aggregation"]
    if result_mode == "aggregation":
        aggregation.update(
            {
                "mode": "compute",
                "function": "sum",
                "inputFieldIds": ["factValue"],
                "groupByFieldIds": [],
                "distinct": False,
                "resolutionStatus": "declared",
            }
        )
    else:
        aggregation.update(
            {
                "mode": "exists",
                "function": None,
                "inputFieldIds": [],
                "groupByFieldIds": [],
                "distinct": None,
                "resolutionStatus": "declared",
            }
        )
        binding["fact"]["factKind"] = "exists"
        binding["fact"]["dataType"] = "boolean"
        binding["fact"]["unit"] = None
        binding["queryRequirements"]["result"]["dataType"] = "boolean"
        binding["queryRequirements"]["result"]["unit"] = None
    refresh_gap_report(resolution_payload)
    resolution_request = ResolveMetadataRequestV2.model_validate(resolution_payload)
    resolution_report = resolve_metadata_v2(resolution_request)
    return {
        "schemaVersion": "1.0.0",
        "resolutionRequest": resolution_payload,
        "resolutionReport": resolution_report.model_dump(by_alias=True, mode="json"),
    }


def validation_payload(
    *,
    sql: str = VALID_SQL,
    second_relation: bool = False,
    result_mode: ResultMode = "column",
) -> dict[str, Any]:
    generation_payload = _generation_payload(
        second_relation=second_relation,
        result_mode=result_mode,
    )
    generation = GenerateSqlCandidateRequestV2.model_validate(generation_payload)
    generated = valid_generated_candidate_v2_payload(second_relation=second_relation)
    generated["sqlTemplate"] = sql
    if result_mode == "exists":
        generated["result"]["dataType"] = "boolean"
        generated["result"]["unit"] = None
    response = CandidateModelResponse(
        provider="fixed-offline",
        request_id="phase4-synthetic-001",
        model="fixed-offline-v2",
        content=json.dumps(
            generated,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        system_fingerprint="phase4-synthetic-fingerprint",
    )
    provider = FixedCandidateModelProvider([response])
    candidate = asyncio.run(
        generate_sql_candidate_v2(
            provider,
            generation,
            model="fixed-offline-v2",
            max_retries=0,
        )
    )
    return {
        "schemaVersion": "1.0.0",
        "generationRequest": generation.model_dump(by_alias=True, mode="json"),
        "candidate": candidate.model_dump(by_alias=True, mode="json"),
    }


def valid_validation_request(
    *,
    sql: str = VALID_SQL,
    second_relation: bool = False,
    result_mode: ResultMode = "column",
) -> ValidateSqlCandidateRequestV2:
    return ValidateSqlCandidateRequestV2.model_validate(
        validation_payload(
            sql=sql,
            second_relation=second_relation,
            result_mode=result_mode,
        )
    )


def reseal_candidate(payload: dict[str, Any]) -> None:
    candidate = deepcopy(payload["candidate"])
    candidate["contentSha256"] = canonical_content_sha256(candidate)
    payload["candidate"] = candidate
