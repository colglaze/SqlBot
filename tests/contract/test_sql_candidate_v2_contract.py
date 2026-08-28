from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from release_sql_bot.application.candidates_v2 import generate_sql_candidate_v2
from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.application.ports.candidates import CandidateModelResponse
from release_sql_bot.domain.sql_candidates_v2 import (
    GeneratedCandidatePayloadV2,
    GenerateSqlCandidateRequestV2,
)
from tests.fakes import FixedCandidateModelProvider
from tests.phase2g_support import (
    generate_candidate_request_payload,
    valid_generated_candidate_v2_content,
    valid_generated_candidate_v2_payload,
)


def _response() -> CandidateModelResponse:
    return CandidateModelResponse(
        provider="deepseek",
        request_id="v2-contract-001",
        model="offline-v2-fixture",
        content=valid_generated_candidate_v2_content(),
    )


def test_v2_generation_request_is_lossless_strict_camel_case() -> None:
    payload = generate_candidate_request_payload()

    request = GenerateSqlCandidateRequestV2.model_validate(payload)

    assert request.model_dump(by_alias=True, mode="json") == payload


def test_v2_generation_request_rejects_extra_and_nested_snake_case() -> None:
    extra = generate_candidate_request_payload()
    extra["resolutionReport"]["readyForGeneration"] = True

    with pytest.raises(ValidationError, match="readyForGeneration"):
        GenerateSqlCandidateRequestV2.model_validate(extra)

    snake = generate_candidate_request_payload()
    snake["resolutionReport"]["request_id"] = snake["resolutionReport"].pop("requestId")

    with pytest.raises(ValidationError, match="snake_case"):
        GenerateSqlCandidateRequestV2.model_validate(snake)


def test_untrusted_v2_model_payload_cannot_set_lifecycle_or_repeat_claims() -> None:
    lifecycle = valid_generated_candidate_v2_payload()
    lifecycle["reviewStatus"] = "approved"

    with pytest.raises(ValidationError, match="reviewStatus"):
        GeneratedCandidatePayloadV2.model_validate(lifecycle)

    duplicate = valid_generated_candidate_v2_payload()
    duplicate["declaredUsageCoverage"].append("amount-positive")

    with pytest.raises(ValidationError, match="duplicates"):
        GeneratedCandidatePayloadV2.model_validate(duplicate)


def test_final_v2_candidate_is_hash_closed_and_never_executable() -> None:
    request = GenerateSqlCandidateRequestV2.model_validate(generate_candidate_request_payload())
    provider = FixedCandidateModelProvider([_response()])

    candidate = asyncio.run(
        generate_sql_candidate_v2(
            provider,
            request,
            model="offline-configured-model",
            max_retries=0,
        )
    )
    serialized = candidate.model_dump(by_alias=True, mode="json")

    assert serialized["schemaVersion"] == "2.0.0"
    assert serialized["status"] == "candidate"
    assert serialized["executable"] is False
    assert serialized["reviewStatus"] == "pending"
    assert canonical_content_sha256(candidate) == candidate.content_sha256
    assert "readyForGeneration" not in json.dumps(serialized)
