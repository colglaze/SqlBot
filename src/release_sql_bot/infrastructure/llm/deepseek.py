"""DeepSeek Chat Completions adapter for structured candidate generation."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field

from release_sql_bot.application.ports.candidates import (
    CandidateModelRequest,
    CandidateModelResponse,
    CandidateProviderRateLimitError,
    CandidateProviderRejectedError,
    CandidateProviderTimeoutError,
    CandidateProviderUnavailableError,
)


class _DeepSeekMessage(BaseModel):
    content: str | None


class _DeepSeekChoice(BaseModel):
    finish_reason: str
    message: _DeepSeekMessage


class _DeepSeekCompletion(BaseModel):
    id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    choices: list[_DeepSeekChoice] = Field(min_length=1)
    system_fingerprint: str | None = None


class DeepSeekCandidateProvider:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def generate(self, request: CandidateModelRequest) -> CandidateModelResponse:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "response_format": {"type": request.response_format},
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._endpoint,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=body,
                )
        except httpx.TimeoutException:
            raise CandidateProviderTimeoutError("DeepSeek request timed out") from None
        except httpx.RequestError:
            raise CandidateProviderUnavailableError("DeepSeek network request failed") from None

        if response.status_code == 429:
            raise CandidateProviderRateLimitError("DeepSeek rate limit reached")
        if 500 <= response.status_code < 600:
            raise CandidateProviderUnavailableError("DeepSeek service is unavailable")
        if not 200 <= response.status_code < 300:
            raise CandidateProviderRejectedError("DeepSeek rejected the request")

        try:
            completion = _DeepSeekCompletion.model_validate(response.json())
        except (TypeError, ValueError):
            raise CandidateProviderUnavailableError(
                "DeepSeek returned an invalid response envelope"
            ) from None

        choice = completion.choices[0]
        if choice.finish_reason != "stop":
            raise CandidateProviderUnavailableError(
                "DeepSeek did not complete the candidate response"
            )
        return CandidateModelResponse(
            provider="deepseek",
            request_id=completion.id,
            model=completion.model,
            content=choice.message.content or "",
            system_fingerprint=completion.system_fingerprint,
        )
