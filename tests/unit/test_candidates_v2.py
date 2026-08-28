from __future__ import annotations

import asyncio
import json
from copy import deepcopy

import pytest

from release_sql_bot.application.candidates_v2 import (
    MANDATORY_CANDIDATE_WARNING_V2,
    CandidateGenerationOutputInvalidV2Error,
    CandidateGenerationProviderRejectedV2Error,
    CandidateGenerationProviderUnavailableV2Error,
    CandidateInputNotReadyV2Error,
    generate_sql_candidate_v2,
)
from release_sql_bot.application.ports.candidates import (
    CandidateModelResponse,
    CandidateProviderRateLimitError,
    CandidateProviderRejectedError,
    CandidateProviderTimeoutError,
)
from release_sql_bot.application.prompts_v2 import (
    SQLSERVER_CANDIDATE_PROMPT_VERSION_V2,
)
from release_sql_bot.domain.sql_candidates_v2 import GenerateSqlCandidateRequestV2
from tests.fakes import FixedCandidateModelProvider
from tests.phase2g_support import (
    generate_candidate_request_payload,
    valid_generated_candidate_v2_content,
    valid_generated_candidate_v2_payload,
)


def _response(content: str | None = None, request_id: str = "v2-generation-001"):
    return CandidateModelResponse(
        provider="deepseek",
        request_id=request_id,
        model="offline-v2-model-build",
        content=content if content is not None else valid_generated_candidate_v2_content(),
        system_fingerprint="offline-v2-fingerprint",
    )


def _request(*, blocked: bool = False) -> GenerateSqlCandidateRequestV2:
    return GenerateSqlCandidateRequestV2.model_validate(
        generate_candidate_request_payload(blocked=blocked)
    )


class RecordingSleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def test_exact_v2_resolution_generates_one_tracked_non_executable_candidate() -> None:
    provider = FixedCandidateModelProvider([_response()])

    candidate = asyncio.run(
        generate_sql_candidate_v2(
            provider,
            _request(),
            model="offline-configured-model",
            max_retries=2,
        )
    )

    assert candidate.schema_version == "2.0.0"
    assert candidate.status == "candidate"
    assert candidate.executable is False
    assert candidate.review_status == "pending"
    assert candidate.request_ref.request_id.endswith("#report.total_amount")
    assert candidate.resolution_ref.context_ref.context_id == "context.synthetic.001"
    assert candidate.resolution_ref.metadata_snapshot_ref.snapshot_id == ("snapshot.synthetic.001")
    assert [(item.schema_name, item.relation_name) for item in candidate.declared_objects] == [
        ("reporting", "synthetic_report_amounts")
    ]
    assert candidate.declared_usage_coverage == ("amount-positive",)
    assert MANDATORY_CANDIDATE_WARNING_V2 in candidate.warnings
    assert candidate.provenance.prompt_version == SQLSERVER_CANDIDATE_PROMPT_VERSION_V2
    assert candidate.provenance.attempt_count == 1
    assert len(provider.calls) == 1


def test_blocked_or_forged_resolution_never_calls_provider() -> None:
    blocked_provider = FixedCandidateModelProvider([_response()])
    with pytest.raises(CandidateInputNotReadyV2Error):
        asyncio.run(
            generate_sql_candidate_v2(
                blocked_provider,
                _request(blocked=True),
                model="offline-configured-model",
                max_retries=0,
            )
        )
    assert blocked_provider.calls == []

    forged_payload = generate_candidate_request_payload()
    forged_payload["resolutionReport"]["resolvedBindings"][0]["authorizationId"] = (
        "forged.authorization"
    )
    forged = GenerateSqlCandidateRequestV2.model_validate(forged_payload)
    forged_provider = FixedCandidateModelProvider([_response()])
    with pytest.raises(CandidateInputNotReadyV2Error):
        asyncio.run(
            generate_sql_candidate_v2(
                forged_provider,
                forged,
                model="offline-configured-model",
                max_retries=0,
            )
        )
    assert forged_provider.calls == []

    tampered_payload = generate_candidate_request_payload()
    tampered_payload["resolutionRequest"]["projectContext"]["authorizationPolicyVersion"] = (
        "tampered-policy"
    )
    tampered = GenerateSqlCandidateRequestV2.model_validate(tampered_payload)
    tampered_provider = FixedCandidateModelProvider([_response()])
    with pytest.raises(CandidateInputNotReadyV2Error):
        asyncio.run(
            generate_sql_candidate_v2(
                tampered_provider,
                tampered,
                model="offline-configured-model",
                max_retries=0,
            )
        )
    assert tampered_provider.calls == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update({"reviewStatus": "approved"}),
        lambda value: value["parameters"][0].update({"required": False}),
        lambda value: value["result"].update({"dataType": "string"}),
        lambda value: value["declaredObjects"].append(
            {"schemaName": "reporting", "relationName": "unauthorized"}
        ),
        lambda value: value.update({"declaredUsageCoverage": ["invented-condition"]}),
    ],
)
def test_invalid_model_contract_and_cross_references_never_form_candidate(mutation) -> None:
    generated = valid_generated_candidate_v2_payload()
    mutation(generated)
    provider = FixedCandidateModelProvider([_response(json.dumps(generated, ensure_ascii=False))])

    with pytest.raises(CandidateGenerationOutputInvalidV2Error):
        asyncio.run(
            generate_sql_candidate_v2(
                provider,
                _request(),
                model="offline-configured-model",
                max_retries=0,
            )
        )


def test_invalid_json_and_transient_failures_retry_with_fixed_bound() -> None:
    provider = FixedCandidateModelProvider(
        [
            _response("```json\n{}\n```"),
            CandidateProviderRateLimitError("hidden provider text"),
            _response(),
        ]
    )
    sleeper = RecordingSleeper()

    candidate = asyncio.run(
        generate_sql_candidate_v2(
            provider,
            _request(),
            model="offline-configured-model",
            max_retries=2,
            retry_base_delay_seconds=0.1,
            sleeper=sleeper,
        )
    )

    assert candidate.provenance.attempt_count == 3
    assert len(provider.calls) == 3
    assert sleeper.delays == [0.1, 0.2]


def test_retry_exhaustion_and_provider_rejection_are_distinct() -> None:
    invalid = FixedCandidateModelProvider([_response(""), _response("[]")])
    with pytest.raises(CandidateGenerationOutputInvalidV2Error):
        asyncio.run(
            generate_sql_candidate_v2(
                invalid,
                _request(),
                model="offline-configured-model",
                max_retries=1,
                sleeper=RecordingSleeper(),
            )
        )
    assert len(invalid.calls) == 2

    transient = FixedCandidateModelProvider(
        [CandidateProviderTimeoutError("hidden"), CandidateProviderTimeoutError("hidden")]
    )
    with pytest.raises(CandidateGenerationProviderUnavailableV2Error):
        asyncio.run(
            generate_sql_candidate_v2(
                transient,
                _request(),
                model="offline-configured-model",
                max_retries=1,
                sleeper=RecordingSleeper(),
            )
        )
    assert len(transient.calls) == 2

    rejected = FixedCandidateModelProvider(
        [CandidateProviderRejectedError("must stay hidden"), _response()]
    )
    with pytest.raises(CandidateGenerationProviderRejectedV2Error):
        asyncio.run(
            generate_sql_candidate_v2(
                rejected,
                _request(),
                model="offline-configured-model",
                max_retries=2,
            )
        )
    assert len(rejected.calls) == 1


def test_candidate_hash_changes_with_sql_without_mutating_generation_input() -> None:
    request = _request()
    before = request.model_dump(by_alias=True, mode="json")
    changed = deepcopy(valid_generated_candidate_v2_payload())
    changed["sqlTemplate"] += " ORDER BY amounts.total_amount"

    first = asyncio.run(
        generate_sql_candidate_v2(
            FixedCandidateModelProvider([_response(request_id="same-run")]),
            request,
            model="offline-configured-model",
            max_retries=0,
        )
    )
    second = asyncio.run(
        generate_sql_candidate_v2(
            FixedCandidateModelProvider(
                [_response(json.dumps(changed, ensure_ascii=False), request_id="same-run")]
            ),
            request,
            model="offline-configured-model",
            max_retries=0,
        )
    )

    assert first.content_sha256 != second.content_sha256
    assert request.model_dump(by_alias=True, mode="json") == before
