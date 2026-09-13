"""V3 candidate persistence application service (M5 second slice).

Within a single application call, this service:

1. Calls :func:`generate_sql_candidate_v3` with its full pre-provider gate
   (handoff repository re-read, approval verification, M2 re-resolution,
   scope check, output cross-validation, bounded retry).
2. On success, passes the exact same candidate object to ``store.save``.
3. Returns both the candidate and the real storage outcome.

Generation failures are raised as-is; the store is never called.
Storage failures are returned as outcome values; the candidate is still
returned. The store's ``initialize``/``close`` lifecycle is owned by the
caller.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from release_sql_bot.application.candidates_v3 import (
    RetrySleeper,
    generate_sql_candidate_v3,
)
from release_sql_bot.application.ports.approval_records_v3 import (
    ApprovalRecordPortV3,
)
from release_sql_bot.application.ports.candidate_store_v3 import (
    CandidateStoreV3Outcome,
    CandidateTemplateStoreV3,
)
from release_sql_bot.application.ports.candidates import (
    CandidateModelProvider,
)
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffBatchRepositoryV3,
)
from release_sql_bot.domain.sql_candidates_v3 import (
    GenerateSqlCandidateRequestV3,
    SqlTemplateCandidateV3,
)


@dataclass(frozen=True, slots=True)
class GenerateAndStoreResultV3:
    """Internal application result for generate-then-store.

    Not a wire contract; no schemaVersion. The store outcome reflects the
    real adapter result (stored/duplicate/unavailable/failed).
    """

    candidate: SqlTemplateCandidateV3
    store_outcome: CandidateStoreV3Outcome


async def generate_and_store_sql_candidate_v3(
    *,
    provider: CandidateModelProvider,
    payload: GenerateSqlCandidateRequestV3,
    handoff_repository: FactBindingHandoffBatchRepositoryV3,
    approval_port: ApprovalRecordPortV3,
    store: CandidateTemplateStoreV3,
    model: str,
    max_retries: int,
    retry_base_delay_seconds: float = 0.25,
    sleeper: RetrySleeper = asyncio.sleep,
) -> GenerateAndStoreResultV3:
    """Generate one V3 candidate and persist it through the insert-only store.

    Args:
        provider: Bounded model provider port.
        payload: V3 generation request (resolution request + report).
        handoff_repository: Read-only V3 handoff batch repository.
        approval_port: Controlled read-only approval record port.
        store: Insert-only V3 candidate store.
        model: Model identifier for the provider.
        max_retries: Maximum retry attempts (0-5).
        retry_base_delay_seconds: Base delay for exponential backoff.
        sleeper: Async sleep function for retry delays.

    Returns:
        GenerateAndStoreResultV3 with the exact candidate object and the
        real store outcome.

    Raises:
        CandidateScopeErrorV3: request outside M3 first-delivery scope.
        CandidateGateErrorV3: any pre-provider gate failure.
        CandidateGenerationOutputInvalidV3Error: output invalid after retries.
        CandidateGenerationProviderUnavailableV3Error: provider exhausted.
        CandidateGenerationProviderRejectedV3Error: provider rejected.
    """
    candidate = await generate_sql_candidate_v3(
        provider=provider,
        payload=payload,
        handoff_repository=handoff_repository,
        approval_port=approval_port,
        model=model,
        max_retries=max_retries,
        retry_base_delay_seconds=retry_base_delay_seconds,
        sleeper=sleeper,
    )
    outcome = await store.save(candidate)
    return GenerateAndStoreResultV3(
        candidate=candidate,
        store_outcome=outcome,
    )
