"""M5 second slice: V3 generate-then-store application service tests.

Verifies that generate_and_store_sql_candidate_v3:
- Calls the real generate_sql_candidate_v3 with all gates
- Calls store.save exactly once on success
- Returns the exact candidate and real outcome
- Never calls save on generation failure
- Preserves candidate lifecycle and audit fields
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from release_sql_bot.application.candidate_persistence_v3 import (
    generate_and_store_sql_candidate_v3,
)
from release_sql_bot.application.candidates_v3 import (
    CandidateGateErrorV3,
    CandidateGenerationOutputInvalidV3Error,
    CandidateGenerationProviderRejectedV3Error,
    CandidateGenerationProviderUnavailableV3Error,
    CandidateScopeErrorV3,
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
from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3
from tests.fakes import FixedCandidateModelProvider
from tests.unit.test_candidates_v3 import (
    _build_generation_request,
    _build_synthetic_approval_port,
    _build_synthetic_handoff_repository,
    _EmptyHandoffRepository,
    _UnavailableHandoffRepository,
    _valid_provider,
)

# ---------------------------------------------------------------------------
# Fake store that records calls and returns configured outcomes
# ---------------------------------------------------------------------------


class FakeStoreV3:
    """Records save calls and returns a configurable outcome."""

    def __init__(
        self,
        outcome: CandidateStoreV3Outcome | None = None,
    ) -> None:
        self.saved: list[SqlTemplateCandidateV3] = []
        self.calls: list[int] = []
        self._outcome = outcome

    async def initialize(self) -> None:
        return None

    async def save(self, candidate: SqlTemplateCandidateV3) -> CandidateStoreV3Outcome:
        self.saved.append(candidate)
        self.calls.append(len(self.calls) + 1)
        if self._outcome is not None:
            return self._outcome
        return CandidateStoreV3Outcome(
            status=CandidateStoreV3Status.STORED,
            content_sha256=candidate.content_sha256,
        )

    async def close(self) -> None:
        return None


def _no_wait_sleeper() -> Any:
    """Return an async sleeper that does nothing."""

    async def _sleep(_seconds: float) -> None:
        return None

    return _sleep


# ---------------------------------------------------------------------------
# A. Normal generate and save
# ---------------------------------------------------------------------------


def test_e2e_generate_and_save_success() -> None:
    """Real generation + save, exactly one save call, same candidate object."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    result = asyncio.run(
        generate_and_store_sql_candidate_v3(
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

    # Provider called exactly once
    assert len(provider.calls) == 1

    # Store called exactly once
    assert len(store.calls) == 1
    assert len(store.saved) == 1

    # Same candidate object passed to store
    assert store.saved[0] is result.candidate

    # Outcome matches store's return
    assert result.store_outcome.status is CandidateStoreV3Status.STORED
    assert result.store_outcome.content_sha256 == result.candidate.content_sha256

    # Candidate lifecycle unchanged
    assert result.candidate.status == "candidate"
    assert result.candidate.executable is False
    assert result.candidate.review_status == "pending"
    assert result.candidate.schema_version == "3.0.0"

    # Self-hash valid
    from release_sql_bot.application.canonical import canonical_content_sha256

    assert result.candidate.content_sha256 == canonical_content_sha256(result.candidate)

    # Full usage six-tuple preserved
    assert len(result.candidate.declared_usage_coverage) > 0
    for usage in result.candidate.declared_usage_coverage:
        assert usage.stage
        assert usage.rule_code
        assert usage.condition_id
        assert usage.condition_path
        assert usage.outcome

    # handoffRefs present
    assert result.candidate.handoff_refs.batch_sha256
    assert result.candidate.handoff_refs.payload_sha256


# ---------------------------------------------------------------------------
# B. Four store outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        CandidateStoreV3Status.STORED,
        CandidateStoreV3Status.DUPLICATE,
        CandidateStoreV3Status.UNAVAILABLE,
        CandidateStoreV3Status.FAILED,
    ],
)
def test_four_store_outcomes(status: CandidateStoreV3Status) -> None:
    """Each outcome: save called once, candidate returned, no re-generation."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    outcome = CandidateStoreV3Outcome(
        status=status,
        content_sha256="a" * 64,
    )
    store = FakeStoreV3(outcome=outcome)

    result = asyncio.run(
        generate_and_store_sql_candidate_v3(
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

    # Provider called once
    assert len(provider.calls) == 1

    # Store called once
    assert len(store.calls) == 1

    # Correct outcome returned
    assert result.store_outcome.status is status
    assert result.store_outcome.content_sha256 == outcome.content_sha256

    # Candidate still returned
    assert result.candidate.status == "candidate"
    assert result.candidate.executable is False


# ---------------------------------------------------------------------------
# C. Pre-provider gate failures: store.save zero calls
# ---------------------------------------------------------------------------


def test_no_batch_in_repository_zero_save() -> None:
    """Missing batch: gate fails, store never called."""
    provider = _valid_provider()
    handoff_repo = _EmptyHandoffRepository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with pytest.raises(CandidateGateErrorV3):
        asyncio.run(
            generate_and_store_sql_candidate_v3(
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

    assert len(provider.calls) == 0
    assert len(store.calls) == 0


def test_repository_unavailable_zero_save() -> None:
    """Repository unavailable: gate fails, store never called."""
    provider = _valid_provider()
    handoff_repo = _UnavailableHandoffRepository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with pytest.raises(CandidateGateErrorV3):
        asyncio.run(
            generate_and_store_sql_candidate_v3(
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

    assert len(provider.calls) == 0
    assert len(store.calls) == 0


def test_scope_exceeded_zero_save() -> None:
    """Request outside M3 scope: gate fails, store never called."""
    from release_sql_bot.domain.fact_bindings_v3 import FilterRequirementV3

    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    # Make it out of scope by adding a filter (structurally valid, scope-checked)
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

    with pytest.raises(CandidateScopeErrorV3):
        asyncio.run(
            generate_and_store_sql_candidate_v3(
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

    assert len(provider.calls) == 0
    assert len(store.calls) == 0


# ---------------------------------------------------------------------------
# D. Model/generation failures: store.save zero calls
# ---------------------------------------------------------------------------


def test_provider_rejected_zero_save() -> None:
    """Provider rejected: no retry, store never called."""
    provider = FixedCandidateModelProvider([CandidateProviderRejectedError("rejected")])
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with pytest.raises(CandidateGenerationProviderRejectedV3Error):
        asyncio.run(
            generate_and_store_sql_candidate_v3(
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

    assert len(provider.calls) == 1
    assert len(store.calls) == 0


def test_provider_transient_exhaustion_zero_save() -> None:
    """Transient errors exhaust retries: store never called."""
    provider = FixedCandidateModelProvider(
        [
            CandidateProviderTransientError("timeout"),
            CandidateProviderTransientError("timeout"),
            CandidateProviderTransientError("timeout"),
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with pytest.raises(CandidateGenerationProviderUnavailableV3Error):
        asyncio.run(
            generate_and_store_sql_candidate_v3(
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

    # max_retries=2 means 3 total attempts
    assert len(provider.calls) == 3
    assert len(store.calls) == 0


def test_invalid_output_exhaustion_zero_save() -> None:
    """Invalid output exhausts retries: store never called."""
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-001",
                model="fixed-model-v3",
                content="not valid json",
            ),
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-002",
                model="fixed-model-v3",
                content="still not valid",
            ),
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    with pytest.raises(CandidateGenerationOutputInvalidV3Error):
        asyncio.run(
            generate_and_store_sql_candidate_v3(
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

    assert len(provider.calls) == 2
    assert len(store.calls) == 0


# ---------------------------------------------------------------------------
# E. Run order: generate → save → static validate
# ---------------------------------------------------------------------------


def test_run_order_then_static_passed() -> None:
    """Generate → save → static validate, in that order, for valid SQL."""
    from release_sql_bot.application.sql_validation_v3 import validate_sql_candidate_v3
    from release_sql_bot.domain.sql_validation_v3 import ValidateSqlCandidateRequestV3

    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    result = asyncio.run(
        generate_and_store_sql_candidate_v3(
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

    # BEFORE validate: store must have saved exactly once
    assert result.store_outcome.status is CandidateStoreV3Status.STORED
    assert len(store.calls) == 1
    assert store.saved[0] is result.candidate

    # Run static validation on the same candidate
    val_req = ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": payload.model_dump(by_alias=True, mode="json"),
            "candidate": result.candidate.model_dump(by_alias=True, mode="json"),
        }
    )
    report = validate_sql_candidate_v3(val_req)
    assert report.status == "passed"

    # AFTER validate: save count unchanged (static validate does not re-save)
    assert len(store.calls) == 1


def test_run_order_then_static_blocked() -> None:
    """Generate → save → static validate: static blocked candidate is still stored.

    Uses ABS() which M4 blocks but M3 output gate accepts.
    """
    from copy import deepcopy

    from release_sql_bot.application.sql_validation_v3 import validate_sql_candidate_v3
    from release_sql_bot.domain.sql_validation_v3 import ValidateSqlCandidateRequestV3
    from tests.unit.test_candidates_v3 import _synthetic_provider_content

    # Build provider content with ABS() which M4 blocks
    base_content = json.loads(_synthetic_provider_content())
    base_content["sqlTemplate"] = (
        "SELECT ABS(t.synthetic_value) AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )
    blocked_content = json.dumps(base_content, ensure_ascii=False, sort_keys=True)

    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-abs",
                model="fixed-model-v3",
                content=blocked_content,
            )
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    result = asyncio.run(
        generate_and_store_sql_candidate_v3(
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

    # BEFORE validate: must prove storage succeeded
    assert result.store_outcome.status is CandidateStoreV3Status.STORED
    assert len(store.calls) == 1
    assert store.saved[0] is result.candidate
    assert result.candidate.status == "candidate"
    assert result.candidate.executable is False
    assert result.candidate.review_status == "pending"

    # Deep copy the candidate dump as archive snapshot
    candidate_snapshot = deepcopy(result.candidate.model_dump(by_alias=True, mode="json"))

    # Run static validation on the same candidate
    val_req = ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": payload.model_dump(by_alias=True, mode="json"),
            "candidate": result.candidate.model_dump(by_alias=True, mode="json"),
        }
    )
    report = validate_sql_candidate_v3(val_req)

    # AFTER validate: static blocked
    assert report.status == "blocked"
    assert report.executable is False
    # Save count unchanged (static validate does not re-save)
    assert len(store.calls) == 1
    # Candidate unchanged from snapshot
    assert result.candidate.model_dump(by_alias=True, mode="json") == candidate_snapshot
    # Lifecycle fields still intact
    assert result.candidate.status == "candidate"
    assert result.candidate.executable is False
    assert result.candidate.review_status == "pending"


def test_generate_and_store_mongo_fake_inserts_once() -> None:
    """Integration with real MongoCandidateStoreV3 and fake Mongo client."""
    from release_sql_bot.domain.stored_candidate_v3 import StoredCandidateV3
    from tests.unit.test_candidate_store_v3 import build_store

    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    store, client = build_store()
    asyncio.run(store.initialize())

    result = asyncio.run(
        generate_and_store_sql_candidate_v3(
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

    # Outcome is STORED with correct hash
    assert result.store_outcome.status is CandidateStoreV3Status.STORED
    assert result.store_outcome.content_sha256 == result.candidate.content_sha256

    # Exactly one document in the V3 collection
    v3_collection = client.get_collection("release_sql_bot", "sql_template_candidates_v3")
    assert v3_collection is not None
    assert len(v3_collection.inserted) == 1

    document = v3_collection.inserted[0]
    assert document["schemaVersion"] == "1.0.0"
    assert document["contentSha256"] == result.candidate.content_sha256
    assert document["candidate"]["executable"] is False
    assert document["candidate"]["reviewStatus"] == "pending"

    # Round-trip through wrapper contract
    stored = StoredCandidateV3.model_validate(document)
    assert stored.candidate.executable is False


# ---------------------------------------------------------------------------
# F. Independent re-verification
# ---------------------------------------------------------------------------


def test_independent_re_verification() -> None:
    """Second call with removed batch must fail independently."""
    provider = _valid_provider()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    # First call: valid
    handoff_repo = _build_synthetic_handoff_repository()
    result1 = asyncio.run(
        generate_and_store_sql_candidate_v3(
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
    assert result1.store_outcome.status is CandidateStoreV3Status.STORED

    # Second call: batch removed
    empty_repo = _EmptyHandoffRepository()
    with pytest.raises(CandidateGateErrorV3):
        asyncio.run(
            generate_and_store_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=empty_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
                sleeper=_no_wait_sleeper(),
            )
        )

    # Provider called only once (first call)
    assert len(provider.calls) == 1
    # Store called only once (first call)
    assert len(store.calls) == 1


# ---------------------------------------------------------------------------
# G. Input immutability
# ---------------------------------------------------------------------------


def test_input_request_unchanged() -> None:
    """Original request wire is unchanged after the call."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()
    store = FakeStoreV3()

    original_wire = payload.model_dump(by_alias=True, mode="json")
    original_json = json.dumps(original_wire, sort_keys=True)

    asyncio.run(
        generate_and_store_sql_candidate_v3(
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
