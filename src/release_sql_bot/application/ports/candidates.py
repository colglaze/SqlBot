"""Provider-neutral port for structured SQL candidate model calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class CandidateModelRequest:
    model: str
    prompt_version: str
    system_prompt: str
    user_prompt: str
    response_format: Literal["json_object"]
    max_tokens: int


@dataclass(frozen=True, slots=True)
class CandidateModelResponse:
    provider: str
    request_id: str
    model: str
    content: str
    system_fingerprint: str | None = None


class CandidateProviderError(RuntimeError):
    """Base error that never carries provider response content."""


class CandidateProviderTransientError(CandidateProviderError):
    """A provider failure that may be retried within the application bound."""


class CandidateProviderTimeoutError(CandidateProviderTransientError):
    pass


class CandidateProviderRateLimitError(CandidateProviderTransientError):
    pass


class CandidateProviderUnavailableError(CandidateProviderTransientError):
    pass


class CandidateProviderRejectedError(CandidateProviderError):
    """A permanent request rejection such as auth, balance, or invalid input."""


class CandidateModelProvider(Protocol):
    async def generate(self, request: CandidateModelRequest) -> CandidateModelResponse: ...
