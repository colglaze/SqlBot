from __future__ import annotations

import asyncio
import json

import pytest

from release_sql_bot.application.candidates import (
    MANDATORY_CANDIDATE_WARNING,
    CandidateGenerationOutputInvalidError,
    CandidateGenerationProviderRejectedError,
    CandidateGenerationProviderUnavailableError,
    CandidateInputNotReadyError,
    generate_sql_candidate,
)
from release_sql_bot.application.ports.candidates import (
    CandidateModelResponse,
    CandidateProviderRateLimitError,
    CandidateProviderRejectedError,
    CandidateProviderTimeoutError,
)
from release_sql_bot.application.prompts import SQLSERVER_CANDIDATE_PROMPT_VERSION
from release_sql_bot.domain.fact_bindings import ValidateFactBindingRequest
from tests.fakes import FixedCandidateModelProvider
from tests.support import (
    binding_request_sha256,
    valid_binding_payload,
    valid_generated_candidate_content,
    valid_generated_candidate_payload,
)


def _model_response(content: str | None = None) -> CandidateModelResponse:
    return CandidateModelResponse(
        provider="deepseek",
        request_id="generation-test-001",
        model="deepseek-v4-flash-test-build",
        content=content if content is not None else valid_generated_candidate_content(),
        system_fingerprint="fingerprint-test-001",
    )


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _request() -> ValidateFactBindingRequest:
    return ValidateFactBindingRequest.model_validate(valid_binding_payload())


def test_ready_fact_generates_one_tracked_non_executable_candidate() -> None:
    provider = FixedCandidateModelProvider([_model_response()])

    candidate = asyncio.run(
        generate_sql_candidate(
            provider,
            _request(),
            model="deepseek-v4-flash",
            max_retries=2,
        )
    )

    assert candidate.schema_version == "1.1.0"
    assert candidate.status == "candidate"
    assert candidate.executable is False
    assert candidate.review_status == "pending"
    assert candidate.rule_ref.rule_id == "TEST_RELEASE_002"
    assert candidate.binding_ref.contract_version == "1.0.0"
    assert candidate.binding_ref.sha256 == binding_request_sha256()
    assert candidate.fact_ref.fact_code == "task.settlement_fee"
    assert candidate.context_ref.metadata_snapshot_id == "metadata-001"
    assert candidate.usage_coverage == ["settlement-non-negative"]
    assert MANDATORY_CANDIDATE_WARNING in candidate.warnings
    assert candidate.provenance.model == "deepseek-v4-flash"
    assert candidate.provenance.response_model == "deepseek-v4-flash-test-build"
    assert candidate.provenance.provider_request_id == "generation-test-001"
    assert candidate.provenance.attempt_count == 1
    assert candidate.provenance.max_tokens == 4096
    assert candidate.provenance.response_format == "json_object"
    assert len(provider.calls) == 1
    model_request = provider.calls[0]
    assert model_request.prompt_version == SQLSERVER_CANDIDATE_PROMPT_VERSION
    assert model_request.response_format == "json_object"
    assert "API Key" not in model_request.user_prompt


def test_timeout_and_rate_limit_retry_with_a_fixed_total_bound() -> None:
    provider = FixedCandidateModelProvider(
        [
            CandidateProviderTimeoutError("secret timeout detail"),
            CandidateProviderRateLimitError("secret rate-limit detail"),
            _model_response(),
        ]
    )
    sleeper = RecordingSleeper()

    candidate = asyncio.run(
        generate_sql_candidate(
            provider,
            _request(),
            model="deepseek-v4-flash",
            max_retries=2,
            retry_base_delay_seconds=0.1,
            sleeper=sleeper,
        )
    )

    assert candidate.provenance.attempt_count == 3
    assert len(provider.calls) == 3
    assert sleeper.delays == [0.1, 0.2]


def test_invalid_json_can_retry_then_succeed() -> None:
    provider = FixedCandidateModelProvider([_model_response("not-json"), _model_response()])
    sleeper = RecordingSleeper()

    candidate = asyncio.run(
        generate_sql_candidate(
            provider,
            _request(),
            model="deepseek-v4-flash",
            max_retries=1,
            sleeper=sleeper,
        )
    )

    assert candidate.provenance.attempt_count == 2
    assert len(provider.calls) == 2
    assert len(sleeper.delays) == 1


def test_invalid_json_retry_exhaustion_is_bounded() -> None:
    provider = FixedCandidateModelProvider(
        [_model_response("not-json"), _model_response(""), _model_response("[]")]
    )
    sleeper = RecordingSleeper()

    with pytest.raises(CandidateGenerationOutputInvalidError):
        asyncio.run(
            generate_sql_candidate(
                provider,
                _request(),
                model="deepseek-v4-flash",
                max_retries=2,
                sleeper=sleeper,
            )
        )

    assert len(provider.calls) == 3
    assert len(sleeper.delays) == 2


def test_contract_cross_references_cannot_claim_missing_usage_or_objects() -> None:
    generated = valid_generated_candidate_payload()
    generated["allowedObjects"] = ["dbo.UnapprovedRelation"]
    generated["usageCoverage"] = ["invented-condition"]
    provider = FixedCandidateModelProvider(
        [_model_response(json.dumps(generated, ensure_ascii=False))]
    )

    with pytest.raises(CandidateGenerationOutputInvalidError):
        asyncio.run(
            generate_sql_candidate(
                provider,
                _request(),
                model="deepseek-v4-flash",
                max_retries=0,
            )
        )


def test_provider_retry_exhaustion_and_permanent_rejection_are_distinct() -> None:
    transient = FixedCandidateModelProvider(
        [CandidateProviderTimeoutError("hidden"), CandidateProviderRateLimitError("hidden")]
    )
    with pytest.raises(CandidateGenerationProviderUnavailableError):
        asyncio.run(
            generate_sql_candidate(
                transient,
                _request(),
                model="deepseek-v4-flash",
                max_retries=1,
                sleeper=RecordingSleeper(),
            )
        )
    assert len(transient.calls) == 2

    rejected = FixedCandidateModelProvider(
        [CandidateProviderRejectedError("provider response must stay hidden"), _model_response()]
    )
    with pytest.raises(CandidateGenerationProviderRejectedError):
        asyncio.run(
            generate_sql_candidate(
                rejected,
                _request(),
                model="deepseek-v4-flash",
                max_retries=2,
            )
        )
    assert len(rejected.calls) == 1


def test_blocked_binding_never_calls_the_provider() -> None:
    payload = valid_binding_payload()
    payload["bindingRequest"]["fact"]["factKind"] = "derived"
    request = ValidateFactBindingRequest.model_validate(payload)
    provider = FixedCandidateModelProvider([_model_response()])

    with pytest.raises(CandidateInputNotReadyError):
        asyncio.run(
            generate_sql_candidate(
                provider,
                request,
                model="deepseek-v4-flash",
                max_retries=2,
            )
        )

    assert provider.calls == []


def test_binding_reference_hash_changes_when_the_request_changes() -> None:
    first_payload = valid_binding_payload()
    second_payload = valid_binding_payload()
    second_payload["bindingRequest"]["fact"]["description"] = "变更后的合成事实描述"

    first_provider = FixedCandidateModelProvider([_model_response()])
    second_provider = FixedCandidateModelProvider([_model_response()])
    first = asyncio.run(
        generate_sql_candidate(
            first_provider,
            ValidateFactBindingRequest.model_validate(first_payload),
            model="deepseek-v4-flash",
            max_retries=0,
        )
    )
    second = asyncio.run(
        generate_sql_candidate(
            second_provider,
            ValidateFactBindingRequest.model_validate(second_payload),
            model="deepseek-v4-flash",
            max_retries=0,
        )
    )

    assert first.binding_ref.sha256 != second.binding_ref.sha256
