"""Candidate model provider assembly."""

from __future__ import annotations

from release_sql_bot.application.ports.candidates import CandidateModelProvider
from release_sql_bot.config.settings import Settings
from release_sql_bot.infrastructure.llm.deepseek import DeepSeekCandidateProvider


def build_candidate_provider(settings: Settings) -> CandidateModelProvider | None:
    if not settings.deepseek_configured:
        return None
    assert settings.deepseek_api_key is not None
    assert settings.deepseek_base_url is not None
    return DeepSeekCandidateProvider(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=str(settings.deepseek_base_url),
        timeout_seconds=settings.deepseek_timeout_seconds,
    )
