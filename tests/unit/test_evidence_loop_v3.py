"""M6 first slice (review fix r2): V3 offline evidence loop tests.

Verifies that run_offline_v3_evidence_loop:

- Runs generate -> store -> static-validate in order
- Measures the *real* provider call count (never guesses from the
  exception type)
- Sets ``repository_verification_status="verified"`` whenever the
  provider was called, even if the provider later failed
- Validates provider/model/promptVersion identifiers against exact
  offline allowlists; unknown values become null with a neutral code
- Records safe model/promptVersion from the request even when the
  provider later fails
- Preserves the original request to the provider (never alters it)
- Assembles a contract-valid EvidencePackV3 for every stage transition
- Never leaks SQL text, parameter values, URIs, or provider raw content

All tests use synthetic fixtures and fakes. No network, no .env, no
MongoDB/SQL Server/online model.
"""

from __future__ import annotations

import asyncio
import json
import logging
from copy import deepcopy
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from release_sql_bot.application.evidence_loop_v3 import (
    CountingProviderProxy,
    run_offline_v3_evidence_loop,
)
from release_sql_bot.application.ports.candidate_store_v3 import (
    CandidateStoreV3Outcome,
    CandidateStoreV3Status,
)
from release_sql_bot.application.ports.candidates import (
    CandidateModelResponse,
    CandidateProviderRejectedError,
    CandidateProviderTransientError,
)
from release_sql_bot.domain.evidence_pack_v3 import EvidencePackV3
from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3
from tests.fakes import FixedCandidateModelProvider
from tests.unit.test_candidates_v3 import (
    _build_generation_request,
    _build_synthetic_approval_port,
    _build_synthetic_handoff_repository,
    _EmptyHandoffRepository,
    _synthetic_provider_content,
    _UnavailableHandoffRepository,
    _valid_provider,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeStoreV3:
    """Records save calls and returns a configurable outcome."""

    def __init__(
        self,
        outcome: CandidateStoreV3Outcome | None = None,
    ) -> None:
        self.saved: list[SqlTemplateCandidateV3] = []
        self._outcome = outcome

    async def initialize(self) -> None:
        return None

    async def save(self, candidate: SqlTemplateCandidateV3) -> CandidateStoreV3Outcome:
        self.saved.append(candidate)
        if self._outcome is not None:
            # Use the candidate's real content hash (never a fake fixed hash)
            return CandidateStoreV3Outcome(
                status=self._outcome.status,
                content_sha256=candidate.content_sha256,
            )
        return CandidateStoreV3Outcome(
            status=CandidateStoreV3Status.STORED,
            content_sha256=candidate.content_sha256,
        )

    async def close(self) -> None:
        return None


class SnapshotStoreV3(FakeStoreV3):
    """Saves an independent wire snapshot of each candidate at save time."""

    def __init__(self) -> None:
        super().__init__()
        self.snapshots: list[dict[str, Any]] = []

    async def save(self, candidate: SqlTemplateCandidateV3) -> CandidateStoreV3Outcome:
        # Deep copy the wire *now*, before any later mutation.
        self.snapshots.append(deepcopy(candidate.model_dump(by_alias=True, mode="json")))
        return await super().save(candidate)


def _no_wait_sleeper() -> Any:
    async def _sleep(_seconds: float) -> None:
        return None

    return _sleep


def _abs_provider() -> FixedCandidateModelProvider:
    """Provider returning ABS() SQL which M4 blocks but M3 output gate accepts."""
    base_content = json.loads(_synthetic_provider_content())
    base_content["sqlTemplate"] = (
        "SELECT ABS(t.synthetic_value) AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )
    blocked_content = json.dumps(base_content, ensure_ascii=False, sort_keys=True)
    return FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-abs",
                model="fixed-model-v3",
                content=blocked_content,
            )
        ]
    )


def _invalid_output_provider(count: int = 2) -> FixedCandidateModelProvider:
    """Provider returning invalid JSON content for ``count`` calls."""
    return FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id=f"req-invalid-{i}",
                model="fixed-model-v3",
                content="not valid json {{{",
            )
            for i in range(count)
        ]
    )


# Markers for leak tests
_MARKER_URI = "https://marker-leak.example.invalid/evidence-pack"
_MARKER_API_KEY = "MARKER_API_KEY_12345_abcdef_SECRET"
_MARKER_RAW_RESPONSE = "MARKER_RAW_RESPONSE_CONTENT_XYZ"
_MARKER_EXCEPTION = "MARKER_IN_EXCEPTION_MESSAGE"
_MARKER_MODEL = "MARKER_UNTRUSTED_MODEL_ID"


# ---------------------------------------------------------------------------
# A. Valid SQL -> evidenceComplete
# ---------------------------------------------------------------------------


def test_evidence_complete_on_valid_sql() -> None:
    """Valid SQL: stage=evidenceComplete, save 1, provider 1, static passed."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
            sleeper=_no_wait_sleeper(),
        )
    )

    assert isinstance(pack, EvidencePackV3)
    assert pack.stage == "evidenceComplete"
    assert pack.static_status == "passed"
    assert pack.store_outcome == "stored"
    assert pack.executable is False

    assert len(store.saved) == 1
    assert len(provider.calls) == 1
    assert pack.attempt_count == len(provider.calls)

    assert pack.rule_version
    assert pack.request_id
    assert pack.batch_sha256
    assert pack.payload_sha256
    assert pack.repository_verification_status == "verified"
    assert pack.context_sha256
    assert pack.snapshot_sha256
    assert pack.resolution_report_sha256
    assert pack.candidate_content_sha256
    assert pack.static_report_sha256
    # Trusted identifiers copied
    assert pack.provider == "fixed-offline-v3"
    assert pack.model == "fixed-model-v3"
    assert pack.prompt_version == "sqlserver-fact-candidate-v3.1"
    assert pack.issue_codes == ()


# ---------------------------------------------------------------------------
# B. Missing batch / repository unavailable: provider/save/static all 0
# ---------------------------------------------------------------------------


def test_no_batch_repository_zero_all() -> None:
    """Missing batch: provider 0, save 0, static not called."""
    provider = _valid_provider()
    handoff_repo = _EmptyHandoffRepository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3"
    ) as mock_validate:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
                sleeper=_no_wait_sleeper(),
            )
        )
        mock_validate.assert_not_called()

    assert pack.stage == "blockedUpstream"
    assert pack.repository_verification_status == "failed"
    assert pack.candidate_content_sha256 is None
    assert pack.store_outcome is None
    assert pack.static_status is None
    assert pack.static_report_sha256 is None
    assert pack.attempt_count == 0
    assert pack.model is None
    assert pack.prompt_version is None
    assert "M3_HANDOFF_BATCH_NOT_FOUND" in pack.issue_codes

    assert len(provider.calls) == 0
    assert len(store.saved) == 0


def test_repository_unavailable_zero_all() -> None:
    """Repository unavailable: provider 0, save 0, status=unavailable."""
    provider = _valid_provider()
    handoff_repo = _UnavailableHandoffRepository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3"
    ) as mock_validate:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
                sleeper=_no_wait_sleeper(),
            )
        )
        mock_validate.assert_not_called()

    assert pack.stage == "blockedUpstream"
    assert pack.repository_verification_status == "unavailable"
    assert pack.candidate_content_sha256 is None
    assert pack.attempt_count == 0
    assert pack.model is None
    assert pack.prompt_version is None
    assert "M3_HANDOFF_REPOSITORY_UNAVAILABLE" in pack.issue_codes

    assert len(provider.calls) == 0
    assert len(store.saved) == 0


# ---------------------------------------------------------------------------
# C. Provider failures: real call count, verified repository, safe metadata
# ---------------------------------------------------------------------------


def test_transient_then_rejected_attempt_count_two() -> None:
    """Transient then rejected: 2 real calls, attemptCount=2, repository=verified."""
    provider = FixedCandidateModelProvider(
        [
            CandidateProviderTransientError("timeout-marker"),
            CandidateProviderRejectedError("rejected-marker"),
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3"
    ) as mock_validate:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=2,
                sleeper=_no_wait_sleeper(),
            )
        )
        mock_validate.assert_not_called()

    assert pack.stage == "blockedUpstream"
    assert pack.repository_verification_status == "verified"
    assert len(provider.calls) == 2
    assert pack.attempt_count == 2
    # Safe metadata from the request preserved
    assert pack.model == "fixed-model-v3"
    assert pack.prompt_version == "sqlserver-fact-candidate-v3.1"
    assert "M6_PROVIDER_REJECTED" in pack.issue_codes
    assert "rejected-marker" not in pack.issue_codes
    assert "timeout-marker" not in pack.issue_codes
    # Provider received the original model
    assert provider.calls[0].model == "fixed-model-v3"
    assert provider.calls[1].model == "fixed-model-v3"

    assert len(store.saved) == 0


def test_provider_retry_exhaustion_accurate_count() -> None:
    """Transient errors exhaust retries: 3 calls, attemptCount=3, metadata kept."""
    provider = FixedCandidateModelProvider(
        [
            CandidateProviderTransientError("t1"),
            CandidateProviderTransientError("t2"),
            CandidateProviderTransientError("t3"),
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3"
    ) as mock_validate:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=2,
                sleeper=_no_wait_sleeper(),
            )
        )
        mock_validate.assert_not_called()

    assert pack.stage == "blockedUpstream"
    assert pack.repository_verification_status == "verified"
    assert len(provider.calls) == 3
    assert pack.attempt_count == 3
    assert pack.model == "fixed-model-v3"
    assert pack.prompt_version == "sqlserver-fact-candidate-v3.1"
    assert "M6_PROVIDER_UNAVAILABLE" in pack.issue_codes

    assert len(store.saved) == 0


def test_output_invalid_exhaustion_accurate_count() -> None:
    """Invalid output exhausts retries: 2 calls, attemptCount=2, metadata kept."""
    provider = _invalid_output_provider(count=2)
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3"
    ) as mock_validate:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=1,
                sleeper=_no_wait_sleeper(),
            )
        )
        mock_validate.assert_not_called()

    assert pack.stage == "blockedUpstream"
    assert pack.repository_verification_status == "verified"
    assert len(provider.calls) == 2
    assert pack.attempt_count == 2
    assert pack.model == "fixed-model-v3"
    assert pack.prompt_version == "sqlserver-fact-candidate-v3.1"
    assert "M6_GENERATION_OUTPUT_INVALID" in pack.issue_codes

    assert len(store.saved) == 0


# ---------------------------------------------------------------------------
# D. Store failed/unavailable: save 1, static 0, neutral codes
# ---------------------------------------------------------------------------


def test_store_failed_skips_static_with_code() -> None:
    """Store returns failed: generation success, save 1, static skipped, code set."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3(
        outcome=CandidateStoreV3Outcome(
            status=CandidateStoreV3Status.FAILED,
            content_sha256="placeholder",
        )
    )

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3"
    ) as mock_validate:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
                sleeper=_no_wait_sleeper(),
            )
        )
        mock_validate.assert_not_called()

    assert pack.stage == "candidateGenerated"
    assert pack.repository_verification_status == "verified"
    assert pack.candidate_content_sha256 is not None
    assert pack.store_outcome == "failed"
    assert pack.static_status is None
    assert pack.static_report_sha256 is None
    assert "M6_STORE_SAVE_FAILED" in pack.issue_codes
    assert pack.attempt_count == len(provider.calls) == 1

    assert len(store.saved) == 1


def test_store_unavailable_skips_static_with_code() -> None:
    """Store returns unavailable: save 1, static skipped, code set."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3(
        outcome=CandidateStoreV3Outcome(
            status=CandidateStoreV3Status.UNAVAILABLE,
            content_sha256="placeholder",
        )
    )

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3"
    ) as mock_validate:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
                sleeper=_no_wait_sleeper(),
            )
        )
        mock_validate.assert_not_called()

    assert pack.stage == "candidateGenerated"
    assert pack.repository_verification_status == "verified"
    assert pack.candidate_content_sha256 is not None
    assert pack.store_outcome == "unavailable"
    assert pack.static_status is None
    assert "M6_STORE_UNAVAILABLE" in pack.issue_codes
    assert pack.attempt_count == len(provider.calls) == 1

    assert len(store.saved) == 1


# ---------------------------------------------------------------------------
# E. ABS() SQL: static blocked, candidate snapshot unchanged
# ---------------------------------------------------------------------------


def test_abs_sql_static_blocked_candidate_stored() -> None:
    """ABS() SQL: save 1 STORED, static blocked, stage=candidateStored."""
    provider = _abs_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
            sleeper=_no_wait_sleeper(),
        )
    )

    assert pack.stage == "candidateStored"
    assert pack.static_status == "blocked"
    assert pack.store_outcome == "stored"
    # Original stable error code restored
    assert "STATIC_VALIDATION_BLOCKED" in pack.issue_codes
    assert pack.static_report_sha256 is not None
    assert pack.attempt_count == len(provider.calls) == 1

    assert len(store.saved) == 1
    candidate = store.saved[0]
    assert candidate.status == "candidate"
    assert candidate.executable is False
    assert candidate.review_status == "pending"


def test_abs_sql_candidate_snapshot_unchanged() -> None:
    """Candidate wire at save time equals wire after the same loop call."""
    provider = _abs_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = SnapshotStoreV3()

    asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
            sleeper=_no_wait_sleeper(),
        )
    )

    assert len(store.snapshots) == 1
    assert len(store.saved) == 1
    current_wire = store.saved[0].model_dump(by_alias=True, mode="json")
    assert current_wire == store.snapshots[0]


# ---------------------------------------------------------------------------
# F. Leak check: markers must not appear in pack JSON/repr/caplog
# ---------------------------------------------------------------------------


def test_no_leak_of_markers_in_pack(caplog: pytest.LogCaptureFixture) -> None:
    """Markers injected into provider response/content must not leak."""
    base_content = json.loads(_synthetic_provider_content())
    base_content["sqlTemplate"] = (
        f"SELECT t.synthetic_value AS fact_value "
        f"FROM dbo.synthetic_table t "
        f"WHERE t.synthetic_key = :syntheticKey "
        f"/* {_MARKER_RAW_RESPONSE} */"
    )
    content = json.dumps(base_content, ensure_ascii=False, sort_keys=True)

    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider=f"untrusted-{_MARKER_URI}",
                request_id=f"req-{_MARKER_API_KEY}",
                model="fixed-model-v3",
                content=content,
            )
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    caplog.set_level(logging.DEBUG)

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
            sleeper=_no_wait_sleeper(),
        )
    )

    pack_json = pack.model_dump_json(by_alias=True)
    pack_repr = repr(pack)
    caplog_text = caplog.text

    for marker in (_MARKER_URI, _MARKER_API_KEY, _MARKER_RAW_RESPONSE, _MARKER_EXCEPTION):
        assert marker not in pack_json, f"marker leaked into JSON: {marker}"
        assert marker not in pack_repr, f"marker leaked into repr: {marker}"
        assert marker not in caplog_text, f"marker leaked into caplog: {marker}"

    assert pack.provider is None
    assert "M6_PROVIDER_IDENTITY_UNTRUSTED" in pack.issue_codes

    assert "FROM dbo.synthetic_table" not in pack_json
    assert "synthetic_key" not in pack_json
    assert "mongodb://" not in pack_json


def test_no_leak_of_exception_marker_in_caplog(caplog: pytest.LogCaptureFixture) -> None:
    """Exception message markers must not appear in pack or caplog."""
    provider = FixedCandidateModelProvider([CandidateProviderRejectedError(_MARKER_EXCEPTION)])
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    caplog.set_level(logging.DEBUG)

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
            sleeper=_no_wait_sleeper(),
        )
    )

    pack_json = pack.model_dump_json(by_alias=True)
    assert _MARKER_EXCEPTION not in pack_json
    assert _MARKER_EXCEPTION not in caplog.text
    assert _MARKER_EXCEPTION not in repr(pack)
    assert "M6_PROVIDER_REJECTED" in pack.issue_codes


# ---------------------------------------------------------------------------
# G. Input payload wire unchanged
# ---------------------------------------------------------------------------


def test_input_payload_unchanged_after_call() -> None:
    """The input payload wire must be unchanged after the evidence loop call."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    original_wire = payload.model_dump(by_alias=True, mode="json")
    original_json = json.dumps(original_wire, sort_keys=True)

    asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
            sleeper=_no_wait_sleeper(),
        )
    )

    after_wire = payload.model_dump(by_alias=True, mode="json")
    after_json = json.dumps(after_wire, sort_keys=True)
    assert after_json == original_json


# ---------------------------------------------------------------------------
# H. Model safety: untrusted model/promptVersion -> null + neutral code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "untrusted_model,marker",
    [
        (f"untrusted-{_MARKER_URI}", _MARKER_URI),
        (f"untrusted-{_MARKER_API_KEY}", _MARKER_API_KEY),
    ],
)
def test_model_safety_untrusted_model_null_with_code(
    caplog: pytest.LogCaptureFixture, untrusted_model: str, marker: str
) -> None:
    """Untrusted model param -> pack.model=null, code set, provider gets original."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    caplog.set_level(logging.DEBUG)

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model=untrusted_model,
            max_retries=0,
            sleeper=_no_wait_sleeper(),
        )
    )

    assert pack.stage == "evidenceComplete"
    # Untrusted model -> null in pack
    assert pack.model is None
    assert "M6_MODEL_IDENTITY_UNTRUSTED" in pack.issue_codes
    # Marker not in JSON/repr/caplog
    pack_json = pack.model_dump_json(by_alias=True)
    assert marker not in pack_json
    assert marker not in repr(pack)
    assert marker not in caplog.text
    # Provider received the original untrusted model
    assert provider.calls[0].model == untrusted_model
    # Other trusted identifiers preserved
    assert pack.provider == "fixed-offline-v3"
    assert pack.prompt_version == "sqlserver-fact-candidate-v3.1"


def test_untrusted_prompt_version_null_with_code() -> None:
    """Untrusted prompt version in request -> null + code, real generate()."""
    from release_sql_bot.application.evidence_loop_v3 import (
        CountingProviderProxy,
        _safe_metadata,
    )
    from release_sql_bot.application.ports.candidates import CandidateModelRequest

    untrusted_pv = f"malicious-prompt-{_MARKER_URI}"
    request = CandidateModelRequest(
        model="fixed-model-v3",
        prompt_version=untrusted_pv,
        system_prompt="s",
        user_prompt="u",
        response_format="json_object",
        max_tokens=100,
    )

    # Case 1: underlying provider succeeds
    inner = _valid_provider()
    proxy = CountingProviderProxy(inner)

    async def run_success() -> None:
        resp = await proxy.generate(request)
        assert resp is not None  # provider returned a response

    asyncio.run(run_success())

    assert proxy.call_count == 1
    assert len(inner.calls) == 1
    # Inner provider received the original untrusted prompt_version
    assert inner.calls[0].prompt_version == untrusted_pv
    # Proxy recorded safe metadata
    assert proxy.last_safe_model == "fixed-model-v3"
    assert proxy.last_model_issue is None
    assert proxy.last_safe_prompt_version is None
    assert proxy.last_prompt_version_issue == "M6_PROMPT_VERSION_UNTRUSTED"
    # _safe_metadata reflects the recorded state
    model, pv, codes = _safe_metadata(proxy, proxy.call_count)
    assert model == "fixed-model-v3"
    assert pv is None
    assert "M6_PROMPT_VERSION_UNTRUSTED" in codes

    # Case 2: underlying provider raises (recording happens before forwarding)
    inner_fail = FixedCandidateModelProvider([CandidateProviderRejectedError("boom")])
    proxy_fail = CountingProviderProxy(inner_fail)

    async def run_fail() -> None:
        with pytest.raises(CandidateProviderRejectedError):
            await proxy_fail.generate(request)

    asyncio.run(run_fail())

    assert proxy_fail.call_count == 1
    assert len(inner_fail.calls) == 1
    # Even on failure, safe metadata was recorded before forwarding
    assert proxy_fail.last_safe_model == "fixed-model-v3"
    assert proxy_fail.last_safe_prompt_version is None
    assert proxy_fail.last_prompt_version_issue == "M6_PROMPT_VERSION_UNTRUSTED"


# ---------------------------------------------------------------------------
# I. Provider failure preserves safe metadata
# ---------------------------------------------------------------------------


def test_metadata_preserved_on_provider_rejected() -> None:
    """Provider called then rejected: safe model/promptVersion preserved."""
    provider = FixedCandidateModelProvider([CandidateProviderRejectedError("rejected")])
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3"
    ) as mock_validate:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
                sleeper=_no_wait_sleeper(),
            )
        )
        mock_validate.assert_not_called()

    assert pack.stage == "blockedUpstream"
    assert pack.attempt_count == 1
    # Safe metadata from request preserved
    assert pack.model == "fixed-model-v3"
    assert pack.prompt_version == "sqlserver-fact-candidate-v3.1"
    assert "M6_PROVIDER_REJECTED" in pack.issue_codes


def test_metadata_null_when_provider_never_called() -> None:
    """Provider never called (gate failure): model/promptVersion null."""
    provider = _valid_provider()
    handoff_repo = _EmptyHandoffRepository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
            sleeper=_no_wait_sleeper(),
        )
    )

    assert pack.attempt_count == 0
    assert pack.model is None
    assert pack.prompt_version is None
    assert pack.provider is None


# ---------------------------------------------------------------------------
# J. Run order: save before static, parameterized stored/duplicate × passed/blocked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "use_duplicate,static_blocked,expected_stage",
    [
        # stored × passed
        (False, False, "evidenceComplete"),
        # stored × blocked
        (False, True, "candidateStored"),
        # duplicate × passed
        (True, False, "evidenceComplete"),
        # duplicate × blocked
        (True, True, "candidateStored"),
    ],
)
def test_run_order_save_before_static_parametrized(
    use_duplicate: bool,
    static_blocked: bool,
    expected_stage: str,
) -> None:
    """Save before static, static once, snapshot unchanged, outcome/stage accurate."""
    provider = _abs_provider() if static_blocked else _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    # SnapshotStoreV3 saves an independent wire deep copy at save time.
    # For duplicate cases, override save to return DUPLICATE with real hash.
    store = SnapshotStoreV3()

    if use_duplicate:

        async def duplicate_save(self, candidate):  # type: ignore[no-untyped-def]
            # Still take the snapshot, then return DUPLICATE with real hash
            self.snapshots.append(deepcopy(candidate.model_dump(by_alias=True, mode="json")))
            self.saved.append(candidate)
            return CandidateStoreV3Outcome(
                status=CandidateStoreV3Status.DUPLICATE,
                content_sha256=candidate.content_sha256,
            )

        # Bind the method to our instance
        import types

        store.save = types.MethodType(duplicate_save, store)

    events: list[str] = []
    original_save = store.save

    async def recording_save(candidate):  # type: ignore[no-untyped-def]
        events.append("save")
        return await original_save(candidate)

    store.save = recording_save  # type: ignore[method-assign]

    def recording_validate(request):  # type: ignore[no-untyped-def]
        events.append("static")
        from release_sql_bot.application.sql_validation_v3 import validate_sql_candidate_v3

        return validate_sql_candidate_v3(request)

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3",
        side_effect=recording_validate,
    ):
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
                sleeper=_no_wait_sleeper(),
            )
        )

    assert pack.stage == expected_stage
    assert pack.store_outcome == ("duplicate" if use_duplicate else "stored")
    # Strict ordering: save then static
    assert events == ["save", "static"]
    # Exactly one save, one static
    assert len(store.saved) == 1

    if static_blocked:
        assert "STATIC_VALIDATION_BLOCKED" in pack.issue_codes
    else:
        assert pack.static_status == "passed"

    # Snapshot taken at save time must match the candidate wire now
    assert len(store.snapshots) == 1
    current_wire = store.saved[0].model_dump(by_alias=True, mode="json")
    assert current_wire == store.snapshots[0]


# ---------------------------------------------------------------------------
# K. CountingProviderProxy unit tests
# ---------------------------------------------------------------------------


def test_counting_proxy_counts_successful_calls() -> None:
    """Proxy counts successful calls and forwards transparently."""
    inner = _valid_provider()
    proxy = CountingProviderProxy(inner)

    request = provider_request_stub()

    async def run() -> None:
        resp = await proxy.generate(request)
        assert resp.provider == "fixed-offline-v3"

    asyncio.run(run())

    assert proxy.call_count == 1
    assert len(inner.calls) == 1
    assert proxy.last_safe_model == "fixed-model-v3"


def test_counting_proxy_counts_failed_calls() -> None:
    """Proxy counts calls that raise exceptions and records safe metadata."""
    inner = FixedCandidateModelProvider([CandidateProviderTransientError("boom")])
    proxy = CountingProviderProxy(inner)

    request = provider_request_stub()

    async def run() -> None:
        with pytest.raises(CandidateProviderTransientError):
            await proxy.generate(request)

    asyncio.run(run())

    assert proxy.call_count == 1
    assert len(inner.calls) == 1
    assert proxy.last_safe_model == "fixed-model-v3"
    assert proxy.last_safe_prompt_version == "sqlserver-fact-candidate-v3.1"


def test_counting_proxy_untrusted_model_recorded() -> None:
    """Proxy records null + issue code for untrusted model."""
    inner = _valid_provider()
    proxy = CountingProviderProxy(inner)

    from release_sql_bot.application.ports.candidates import CandidateModelRequest

    request = CandidateModelRequest(
        model="untrusted-model-123",
        prompt_version="sqlserver-fact-candidate-v3.1",
        system_prompt="s",
        user_prompt="u",
        response_format="json_object",
        max_tokens=100,
    )

    async def run() -> None:
        await proxy.generate(request)

    asyncio.run(run())

    assert proxy.call_count == 1
    assert proxy.last_safe_model is None
    assert proxy.last_model_issue == "M6_MODEL_IDENTITY_UNTRUSTED"
    assert proxy.last_safe_prompt_version == "sqlserver-fact-candidate-v3.1"
    assert proxy.last_prompt_version_issue is None


def provider_request_stub() -> Any:
    from release_sql_bot.application.ports.candidates import CandidateModelRequest

    return CandidateModelRequest(
        model="fixed-model-v3",
        prompt_version="sqlserver-fact-candidate-v3.1",
        system_prompt="stub",
        user_prompt="stub",
        response_format="json_object",
        max_tokens=100,
    )


# ---------------------------------------------------------------------------
# L. Contract: field presence, stage consistency, constraints
# ---------------------------------------------------------------------------


def _valid_pack_dict() -> dict[str, Any]:
    """Build a minimal valid evidenceComplete pack dict."""
    return {
        "schemaVersion": "1.0.0",
        "stage": "evidenceComplete",
        "ruleVersion": "RULE_V1",
        "requestId": "req-001",
        "batchSha256": "a" * 64,
        "payloadSha256": "b" * 64,
        "repositoryVerificationStatus": "verified",
        "contextSha256": "c" * 64,
        "snapshotSha256": "d" * 64,
        "resolutionReportSha256": "e" * 64,
        "candidateContentSha256": "f" * 64,
        "storeOutcome": "stored",
        "staticStatus": "passed",
        "staticReportSha256": "1" * 64,
        "provider": "fixed-offline-v3",
        "model": "fixed-model-v3",
        "promptVersion": "sqlserver-fact-candidate-v3.1",
        "attemptCount": 1,
        "issueCodes": [],
        "startedAt": "2026-09-14T00:00:00Z",
        "endedAt": "2026-09-14T00:00:01Z",
    }


@pytest.mark.parametrize(
    "field",
    [
        "candidateContentSha256",
        "storeOutcome",
        "staticStatus",
        "staticReportSha256",
        "provider",
        "model",
        "promptVersion",
    ],
)
def test_nullable_field_is_required(field: str) -> None:
    """Deleting any of the 7 nullable fields must fail validation."""
    data = _valid_pack_dict()
    del data[field]
    with pytest.raises(ValidationError):
        EvidencePackV3.model_validate(data)


def test_legal_null_scenarios_pass() -> None:
    """Legal null scenarios pass the stage-consistency validator."""
    # blockedUpstream: all downstream fields null, attemptCount=0
    data = _valid_pack_dict()
    data["stage"] = "blockedUpstream"
    data["candidateContentSha256"] = None
    data["storeOutcome"] = None
    data["staticStatus"] = None
    data["staticReportSha256"] = None
    data["provider"] = None
    data["model"] = None
    data["promptVersion"] = None
    data["attemptCount"] = 0
    pack = EvidencePackV3.model_validate(data)
    assert pack.stage == "blockedUpstream"

    # blockedUpstream with attemptCount>0: safe model/promptVersion allowed
    data2 = data.copy()
    data2["attemptCount"] = 2
    data2["model"] = "fixed-model-v3"
    data2["promptVersion"] = "sqlserver-fact-candidate-v3.1"
    pack2 = EvidencePackV3.model_validate(data2)
    assert pack2.stage == "blockedUpstream"
    assert pack2.model == "fixed-model-v3"

    # candidateGenerated: candidate hash set, store failed, static null
    data3 = _valid_pack_dict()
    data3["stage"] = "candidateGenerated"
    data3["storeOutcome"] = "failed"
    data3["staticStatus"] = None
    data3["staticReportSha256"] = None
    pack3 = EvidencePackV3.model_validate(data3)
    assert pack3.stage == "candidateGenerated"


def test_blocked_upstream_attempt_with_null_metadata_rejected() -> None:
    """blockedUpstream attemptCount=0 with non-null model is rejected."""
    data = _valid_pack_dict()
    data["stage"] = "blockedUpstream"
    data["candidateContentSha256"] = None
    data["storeOutcome"] = None
    data["staticStatus"] = None
    data["staticReportSha256"] = None
    data["provider"] = None
    data["model"] = "fixed-model-v3"  # non-null but attemptCount=0
    data["promptVersion"] = None
    data["attemptCount"] = 0
    with pytest.raises(ValidationError, match="attemptCount=0 requires model=null"):
        EvidencePackV3.model_validate(data)


def test_unknown_store_outcome_rejected() -> None:
    """Unknown storeOutcome value is rejected by the Literal constraint."""
    data = _valid_pack_dict()
    data["storeOutcome"] = "unknown-value"
    with pytest.raises(ValidationError):
        EvidencePackV3.model_validate(data)


@pytest.mark.parametrize(
    "overrides,expected_error",
    [
        # evidenceComplete with null candidate hash
        ({"candidateContentSha256": None}, "requires candidateContentSha256"),
        # evidenceComplete with failed store
        ({"storeOutcome": "failed"}, "requires storeOutcome=stored|duplicate"),
        # evidenceComplete with blocked static
        ({"staticStatus": "blocked"}, "requires staticStatus=passed"),
        # evidenceComplete with null static report
        ({"staticReportSha256": None}, "requires staticReportSha256"),
        # candidateStored with passed static
        (
            {"stage": "candidateStored", "staticStatus": "passed"},
            "requires staticStatus=blocked",
        ),
        # candidateGenerated with passed static
        (
            {
                "stage": "candidateGenerated",
                "storeOutcome": "failed",
                "staticStatus": "passed",
            },
            "requires staticStatus=null",
        ),
        # candidateGenerated with stored outcome
        (
            {"stage": "candidateGenerated", "storeOutcome": "stored"},
            "requires storeOutcome=failed|unavailable",
        ),
    ],
)
def test_stage_contradictions_rejected(overrides: dict[str, Any], expected_error: str) -> None:
    """Stage/field contradictions are rejected by the consistency validator."""
    data = _valid_pack_dict()
    data.update(overrides)
    with pytest.raises(ValidationError, match=expected_error):
        EvidencePackV3.model_validate(data)


def test_extra_fields_rejected() -> None:
    """extra=forbid rejects unknown fields."""
    data = _valid_pack_dict()
    data["unknownField"] = "value"
    with pytest.raises(ValidationError):
        EvidencePackV3.model_validate(data)


def test_evidence_pack_frozen() -> None:
    """EvidencePackV3 is frozen: mutation is rejected."""
    pack = EvidencePackV3.model_validate(_valid_pack_dict())
    with pytest.raises(ValidationError):
        pack.stage = "evidenceComplete"  # type: ignore[misc]


def test_evidence_pack_round_trip() -> None:
    """The assembled pack round-trips through its own Pydantic contract."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
            sleeper=_no_wait_sleeper(),
        )
    )

    wire = pack.model_dump_json(by_alias=True)
    rebuilt = EvidencePackV3.model_validate_json(wire)
    assert rebuilt.stage == pack.stage
    assert rebuilt.static_status == pack.static_status
    assert rebuilt.executable is False
    assert rebuilt.schema_version == "1.0.0"
    assert rebuilt.issue_codes == pack.issue_codes


# ---------------------------------------------------------------------------
# M. Mongo integration: real MongoCandidateStoreV3 + fake client
# ---------------------------------------------------------------------------


def test_evidence_complete_with_mongo_fake_client() -> None:
    """End-to-end with the real MongoCandidateStoreV3 and a fake client."""
    from tests.unit.test_candidate_store_v3 import build_store

    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store, client = build_store()
    asyncio.run(store.initialize())

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
            sleeper=_no_wait_sleeper(),
        )
    )

    assert pack.stage == "evidenceComplete"
    assert pack.static_status == "passed"
    assert pack.store_outcome == "stored"
    assert pack.executable is False
    assert pack.attempt_count == len(provider.calls) == 1

    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None
    assert len(v3_collection.inserted) == 1

    document = v3_collection.inserted[0]
    assert document["schemaVersion"] == "1.0.0"
    assert document["contentSha256"] == pack.candidate_content_sha256
    assert document["candidate"]["executable"] is False
    assert document["candidate"]["reviewStatus"] == "pending"


# ---------------------------------------------------------------------------
# N. Scope rejection: stage=blockedUpstream, provider 0
# ---------------------------------------------------------------------------


def test_scope_rejection_blocked_upstream() -> None:
    """Request outside M3 scope: stage=blockedUpstream, provider 0."""
    from release_sql_bot.domain.fact_bindings_v3 import FilterRequirementV3

    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    payload.resolution_request.binding_request.query_requirements.filters.items = [
        FilterRequirementV3.model_validate(
            {
                "filterId": "marker_filter",
                "fieldId": "factValue",
                "operator": "eq",
                "value": {"kind": "parameter", "parameterName": "syntheticKey"},
                "nullPolicy": "fail",
                "required": True,
                "evidenceIds": ["ev-filter"],
            }
        )
    ]

    store = FakeStoreV3()

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3"
    ) as mock_validate:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
                sleeper=_no_wait_sleeper(),
            )
        )
        mock_validate.assert_not_called()

    assert pack.stage == "blockedUpstream"
    assert pack.repository_verification_status == "failed"
    assert pack.candidate_content_sha256 is None
    assert pack.attempt_count == 0
    assert pack.model is None
    assert pack.prompt_version is None
    assert any(c.startswith("M3_SCOPE_") for c in pack.issue_codes)

    assert len(provider.calls) == 0
    assert len(store.saved) == 0
