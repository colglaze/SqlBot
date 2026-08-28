from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from release_sql_bot.application.ports.candidates import (
    CandidateModelRequest,
    CandidateProviderRateLimitError,
    CandidateProviderRejectedError,
    CandidateProviderTimeoutError,
    CandidateProviderUnavailableError,
)
from release_sql_bot.infrastructure.llm.deepseek import DeepSeekCandidateProvider
from tests.support import valid_generated_candidate_content


def _request() -> CandidateModelRequest:
    return CandidateModelRequest(
        model="deepseek-v4-flash",
        prompt_version="sqlserver-fact-candidate-v1",
        system_prompt="Return JSON only.",
        user_prompt='{"fact":"synthetic"}',
        response_format="json_object",
        max_tokens=4096,
    )


def test_deepseek_adapter_uses_non_streaming_json_output_and_tracks_response() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "id": "deepseek-request-001",
                "model": "deepseek-v4-flash-test-build",
                "system_fingerprint": "fingerprint-001",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": valid_generated_candidate_content()},
                    }
                ],
            },
        )

    provider = DeepSeekCandidateProvider(
        api_key="not-a-real-key",
        base_url="https://api.deepseek.example/v1",
        timeout_seconds=10,
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(provider.generate(_request()))

    assert result.provider == "deepseek"
    assert result.request_id == "deepseek-request-001"
    assert result.model == "deepseek-v4-flash-test-build"
    assert result.system_fingerprint == "fingerprint-001"
    assert len(captured) == 1
    sent = captured[0]
    assert str(sent.url) == "https://api.deepseek.example/v1/chat/completions"
    assert sent.headers["authorization"] == "Bearer not-a-real-key"
    body = json.loads(sent.content)
    assert body["model"] == "deepseek-v4-flash"
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 4096
    assert [message["role"] for message in body["messages"]] == ["system", "user"]


def test_deepseek_adapter_maps_timeout_without_response_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("must not leak", request=request)

    provider = DeepSeekCandidateProvider(
        api_key="not-a-real-key",
        base_url="https://api.deepseek.example",
        timeout_seconds=10,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(CandidateProviderTimeoutError) as exc_info:
        asyncio.run(provider.generate(_request()))
    assert "must not leak" not in str(exc_info.value)


@pytest.mark.parametrize(
    ("status_code", "error_type"),
    [
        (429, CandidateProviderRateLimitError),
        (500, CandidateProviderUnavailableError),
        (503, CandidateProviderUnavailableError),
        (400, CandidateProviderRejectedError),
        (401, CandidateProviderRejectedError),
        (402, CandidateProviderRejectedError),
        (422, CandidateProviderRejectedError),
    ],
)
def test_deepseek_adapter_classifies_http_errors(status_code, error_type) -> None:
    provider = DeepSeekCandidateProvider(
        api_key="not-a-real-key",
        base_url="https://api.deepseek.example",
        timeout_seconds=10,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(status_code, text="sensitive provider response")
        ),
    )

    with pytest.raises(error_type) as exc_info:
        asyncio.run(provider.generate(_request()))
    assert "sensitive provider response" not in str(exc_info.value)
