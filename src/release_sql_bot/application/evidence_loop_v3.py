"""V3 offline evidence loop orchestration (M6 first slice, review fix r2).

Within a single application call, in running order:

    generate_and_store_sql_candidate_v3
    -> (only when store outcome is stored or duplicate)
       validate_sql_candidate_v3
    -> assemble EvidencePackV3.

All offline: synthetic handoff, in-memory approval port,
FixedCandidateModelProvider, FakeStoreV3 or MongoCandidateStoreV3 + fake
client. ``initialize``/``close`` of the store is owned by the caller.

Stage transitions (runtime state order)::

    blockedUpstream        generation failed (gate/provider/scope)
    candidateGenerated     generation succeeded, store unavailable/failed
    candidateStored        store stored/duplicate, static blocked
    evidenceComplete       store stored/duplicate, static passed

Review fixes applied (r1 + r2):

- A :class:`CountingProviderProxy` wraps the provider to measure the real
  call count; ``attemptCount`` is never guessed from the exception type.
  Before each call it also records the *safe* model/promptVersion
  extracted from the request and passed through the exact-allowlist
  validators.
- ``repository_verification_status`` is ``"verified"`` whenever the
  provider was called (M3 pre-provider gates passed), regardless of any
  later provider failure. Only gate/scope failures (provider never
  called) map to ``"failed"`` / ``"unavailable"``.
- Provider identifiers, model identifiers, and prompt versions from the
  model response / caller are untrusted. Each is validated against an
  exact offline allowlist; unknown values become null with a neutral
  code. The model sent to the provider is never altered.
- Store ``failed``/``unavailable`` outcomes carry a fixed neutral code.
- ``model``/``promptVersion`` in the evidence pack come from the proxy's
  recorded request values (after allowlist validation), not from the
  untrusted model response or provenance.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from release_sql_bot.application.candidate_persistence_v3 import (
    generate_and_store_sql_candidate_v3,
)
from release_sql_bot.application.candidates_v3 import (
    CandidateGateErrorV3,
    CandidateGenerationOutputInvalidV3Error,
    CandidateGenerationProviderRejectedV3Error,
    CandidateGenerationProviderUnavailableV3Error,
    CandidateScopeErrorV3,
    RetrySleeper,
)
from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.application.ports.approval_records_v3 import ApprovalRecordPortV3
from release_sql_bot.application.ports.candidate_store_v3 import (
    CandidateStoreV3Status,
    CandidateTemplateStoreV3,
)
from release_sql_bot.application.ports.candidates import (
    CandidateModelProvider,
    CandidateModelRequest,
    CandidateModelResponse,
)
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffBatchRepositoryV3,
)
from release_sql_bot.application.sql_validation_v3 import validate_sql_candidate_v3
from release_sql_bot.domain.evidence_pack_v3 import EvidencePackV3
from release_sql_bot.domain.sql_candidates_v3 import GenerateSqlCandidateRequestV3
from release_sql_bot.domain.sql_validation_v3 import ValidateSqlCandidateRequestV3

# Fixed neutral issue codes (registered in DEV-20260906-04)
_STATIC_BLOCKED_CODE = "STATIC_VALIDATION_BLOCKED"
_PROVIDER_REJECTED_CODE = "M6_PROVIDER_REJECTED"
_PROVIDER_UNAVAILABLE_CODE = "M6_PROVIDER_UNAVAILABLE"
_OUTPUT_INVALID_CODE = "M6_GENERATION_OUTPUT_INVALID"
_STORE_FAILED_CODE = "M6_STORE_SAVE_FAILED"
_STORE_UNAVAILABLE_CODE = "M6_STORE_UNAVAILABLE"
_PROVIDER_IDENTITY_UNTRUSTED_CODE = "M6_PROVIDER_IDENTITY_UNTRUSTED"
_MODEL_IDENTITY_UNTRUSTED_CODE = "M6_MODEL_IDENTITY_UNTRUSTED"
_PROMPT_VERSION_UNTRUSTED_CODE = "M6_PROMPT_VERSION_UNTRUSTED"

# Trusted identifiers for the offline slice (exact allowlists).
# Only these values copied from untrusted sources are accepted into the
# evidence pack. Anything else becomes null with a neutral code.
_TRUSTED_PROVIDER_IDENTIFIERS: frozenset[str] = frozenset({"fixed-offline-v3"})
_TRUSTED_MODEL_IDENTIFIERS: frozenset[str] = frozenset({"fixed-model-v3"})
_TRUSTED_PROMPT_VERSIONS: frozenset[str] = frozenset(
    {"sqlserver-fact-candidate-v3.0", "sqlserver-fact-candidate-v3.1"}
)

_REPOSITORY_UNAVAILABLE_CODES: frozenset[str] = frozenset(
    {
        "M3_HANDOFF_REPOSITORY_UNAVAILABLE",
        "M3_APPROVAL_PORT_UNAVAILABLE",
    }
)


class CountingProviderProxy:
    """Transparent proxy that counts real provider calls.

    Implements :class:`CandidateModelProvider`. Every call to
    ``generate`` increments ``call_count`` *before* forwarding, so the
    count reflects attempts that raised as well as ones that returned.
    Before forwarding, the proxy also records the *safe*
    model/promptVersion from the request (passed through the exact
    allowlist validators) so the evidence loop can preserve request
    metadata even when the provider later fails.
    """

    def __init__(self, inner: CandidateModelProvider) -> None:
        self._inner = inner
        self.call_count: int = 0
        self.last_safe_model: str | None = None
        self.last_model_issue: str | None = None
        self.last_safe_prompt_version: str | None = None
        self.last_prompt_version_issue: str | None = None

    async def generate(self, request: CandidateModelRequest) -> CandidateModelResponse:
        self.call_count += 1
        # Record safe metadata from the actual request *before* forwarding.
        self.last_safe_model, self.last_model_issue = _validate_model_identity(request.model)
        (
            self.last_safe_prompt_version,
            self.last_prompt_version_issue,
        ) = _validate_prompt_version(request.prompt_version)
        return await self._inner.generate(request)


def _repository_verification_status(call_count: int, code: str) -> str:
    """Derive repository-verification status from the real call outcome.

    If the provider was called at all, M3 pre-provider gates passed and
    the status is ``"verified"``. Only when the provider was never
    called (gate/scope failure) do we map the error code to
    ``"unavailable"`` (repository/approval source unreachable) or
    ``"failed"`` (any other gate/scope failure).
    """
    if call_count > 0:
        return "verified"
    if code in _REPOSITORY_UNAVAILABLE_CODES:
        return "unavailable"
    return "failed"


def _provider_error_issue_codes(exc: Exception) -> tuple[str, ...]:
    """Map a provider-generation exception to fixed neutral issue codes.

    Gate/scope errors keep their own stable ``code`` attribute. Provider
    exceptions that lack a ``code`` use the DEV-registered neutral codes.
    """
    if isinstance(exc, CandidateGenerationProviderRejectedV3Error):
        return (_PROVIDER_REJECTED_CODE,)
    if isinstance(exc, CandidateGenerationProviderUnavailableV3Error):
        return (_PROVIDER_UNAVAILABLE_CODE,)
    if isinstance(exc, CandidateGenerationOutputInvalidV3Error):
        return (_OUTPUT_INVALID_CODE,)
    code = getattr(exc, "code", "")
    if code:
        return (code,)
    return (_PROVIDER_REJECTED_CODE,)


def _store_outcome_issue_codes(status: CandidateStoreV3Status) -> tuple[str, ...]:
    """Map a non-success store outcome to fixed neutral issue codes."""
    if status is CandidateStoreV3Status.FAILED:
        return (_STORE_FAILED_CODE,)
    if status is CandidateStoreV3Status.UNAVAILABLE:
        return (_STORE_UNAVAILABLE_CODE,)
    return ()


def _validate_provider_identity(provider: str | None) -> tuple[str | None, str | None]:
    """Validate a provider identifier against the offline allowlist."""
    if provider is not None and provider in _TRUSTED_PROVIDER_IDENTIFIERS:
        return provider, None
    return None, _PROVIDER_IDENTITY_UNTRUSTED_CODE


def _validate_model_identity(model: str | None) -> tuple[str | None, str | None]:
    """Validate a model identifier against the offline allowlist."""
    if model is not None and model in _TRUSTED_MODEL_IDENTIFIERS:
        return model, None
    return None, _MODEL_IDENTITY_UNTRUSTED_CODE


def _validate_prompt_version(
    prompt_version: str | None,
) -> tuple[str | None, str | None]:
    """Validate a prompt version against the offline allowlist."""
    if prompt_version is not None and prompt_version in _TRUSTED_PROMPT_VERSIONS:
        return prompt_version, None
    return None, _PROMPT_VERSION_UNTRUSTED_CODE


def _pack_base_fields(payload: GenerateSqlCandidateRequestV3) -> dict[str, Any]:
    """Extract the reference fields that are always available from the payload."""
    binding = payload.resolution_request.binding_request
    report = payload.resolution_report
    return {
        "rule_version": binding.rule_ref.rule_version,
        "request_id": binding.request_id,
        "batch_sha256": report.handoff_refs.batch_sha256,
        "payload_sha256": report.handoff_refs.payload_sha256,
        "context_sha256": payload.resolution_request.project_context.content_sha256,
        "snapshot_sha256": payload.resolution_request.metadata_snapshot.content_sha256,
        "resolution_report_sha256": canonical_sha256(report),
    }


def _safe_metadata(
    proxy: CountingProviderProxy,
    call_count: int,
) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Return safe (model, prompt_version, issue_codes) from the proxy.

    When the provider was never called, both identifiers are null and
    no identity-related issue code is produced. When it was called, the
    proxy's last recorded safe values and their validation issue codes
    are used.
    """
    if call_count == 0:
        return None, None, ()
    codes: tuple[str, ...] = ()
    if proxy.last_model_issue:
        codes = codes + (proxy.last_model_issue,)
    if proxy.last_prompt_version_issue:
        codes = codes + (proxy.last_prompt_version_issue,)
    return proxy.last_safe_model, proxy.last_safe_prompt_version, codes


async def run_offline_v3_evidence_loop(
    *,
    provider: CandidateModelProvider,
    payload: GenerateSqlCandidateRequestV3,
    handoff_repository: FactBindingHandoffBatchRepositoryV3,
    approval_port: ApprovalRecordPortV3,
    store: CandidateTemplateStoreV3,
    model: str,
    max_retries: int,
    sleeper: RetrySleeper = asyncio.sleep,
) -> EvidencePackV3:
    """Run the full V3 evidence loop offline and return an evidence pack.

    Args:
        provider: Bounded model provider port.
        payload: V3 generation request (resolution request + report).
        handoff_repository: Read-only V3 handoff batch repository.
        approval_port: Controlled read-only approval record port.
        store: Insert-only V3 candidate store (caller owns lifecycle).
        model: Model identifier for the provider.
        max_retries: Maximum retry attempts (0-5).
        sleeper: Async sleep function for retry delays.

    Returns:
        EvidencePackV3 capturing the outcome of every pipeline stage.
        ``executable`` is always False.
    """
    started_at = datetime.now(UTC)
    base = _pack_base_fields(payload)

    # Wrap the provider so we can measure the real call count and
    # record safe metadata from each request.
    counting_provider = CountingProviderProxy(provider)

    # ------------------------------------------------------------------
    # Stage 1: generate + store (generate_and_store_sql_candidate_v3)
    # ------------------------------------------------------------------
    try:
        result = await generate_and_store_sql_candidate_v3(
            provider=counting_provider,
            payload=payload,
            handoff_repository=handoff_repository,
            approval_port=approval_port,
            store=store,
            model=model,
            max_retries=max_retries,
            sleeper=sleeper,
        )
    except (
        CandidateScopeErrorV3,
        CandidateGateErrorV3,
        CandidateGenerationOutputInvalidV3Error,
        CandidateGenerationProviderUnavailableV3Error,
        CandidateGenerationProviderRejectedV3Error,
    ) as exc:
        ended_at = datetime.now(UTC)
        call_count = counting_provider.call_count
        code = getattr(exc, "code", "")
        safe_model, safe_pv, metadata_issues = _safe_metadata(counting_provider, call_count)
        return EvidencePackV3(
            stage="blockedUpstream",
            **base,
            repository_verification_status=_repository_verification_status(call_count, code),
            candidate_content_sha256=None,
            store_outcome=None,
            static_status=None,
            static_report_sha256=None,
            provider=None,
            model=safe_model,
            prompt_version=safe_pv,
            attempt_count=call_count,
            issue_codes=_provider_error_issue_codes(exc) + metadata_issues,
            started_at=started_at,
            ended_at=ended_at,
        )

    candidate = result.candidate
    store_outcome = result.store_outcome
    provenance = candidate.provenance
    call_count = counting_provider.call_count

    # Provider identity from the model response is untrusted; validate
    # against the explicit offline allowlist.
    trusted_provider, provider_issue_code = _validate_provider_identity(provenance.provider)
    # Model/promptVersion come from the proxy-recorded safe request values.
    safe_model, safe_pv, metadata_issues = _safe_metadata(counting_provider, call_count)

    # ------------------------------------------------------------------
    # Stage 2: static validation (only when stored or duplicate)
    # ------------------------------------------------------------------
    if store_outcome.status not in (
        CandidateStoreV3Status.STORED,
        CandidateStoreV3Status.DUPLICATE,
    ):
        # Store unavailable/failed: record the real outcome + neutral code,
        # skip static validation.
        ended_at = datetime.now(UTC)
        store_issues = _store_outcome_issue_codes(store_outcome.status)
        identity_issues = (provider_issue_code,) if provider_issue_code else ()
        issue_codes = store_issues + metadata_issues + identity_issues
        return EvidencePackV3(
            stage="candidateGenerated",
            **base,
            repository_verification_status="verified",
            candidate_content_sha256=candidate.content_sha256,
            store_outcome=store_outcome.status.value,
            static_status=None,
            static_report_sha256=None,
            provider=trusted_provider,
            model=safe_model,
            prompt_version=safe_pv,
            attempt_count=call_count,
            issue_codes=issue_codes,
            started_at=started_at,
            ended_at=ended_at,
        )

    # Build the static-validation request from the exact same candidate.
    val_request = ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": payload.model_dump(by_alias=True, mode="json"),
            "candidate": candidate.model_dump(by_alias=True, mode="json"),
        }
    )
    static_report = validate_sql_candidate_v3(val_request)
    ended_at = datetime.now(UTC)

    if static_report.status == "blocked":
        static_issues = (_STATIC_BLOCKED_CODE,) + tuple(
            issue.code for issue in static_report.issues
        )
        identity_issues = (provider_issue_code,) if provider_issue_code else ()
        issue_codes = static_issues + metadata_issues + identity_issues
        return EvidencePackV3(
            stage="candidateStored",
            **base,
            repository_verification_status="verified",
            candidate_content_sha256=candidate.content_sha256,
            store_outcome=store_outcome.status.value,
            static_status="blocked",
            static_report_sha256=canonical_sha256(static_report),
            provider=trusted_provider,
            model=safe_model,
            prompt_version=safe_pv,
            attempt_count=call_count,
            issue_codes=issue_codes,
            started_at=started_at,
            ended_at=ended_at,
        )

    # static passed
    identity_issues = (provider_issue_code,) if provider_issue_code else ()
    issue_codes = metadata_issues + identity_issues
    return EvidencePackV3(
        stage="evidenceComplete",
        **base,
        repository_verification_status="verified",
        candidate_content_sha256=candidate.content_sha256,
        store_outcome=store_outcome.status.value,
        static_status="passed",
        static_report_sha256=canonical_sha256(static_report),
        provider=trusted_provider,
        model=safe_model,
        prompt_version=safe_pv,
        attempt_count=call_count,
        issue_codes=issue_codes,
        started_at=started_at,
        ended_at=ended_at,
    )
