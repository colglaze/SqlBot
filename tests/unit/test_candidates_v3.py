"""M3 V3 candidate generation end-to-end tests.

All tests use synthetic fixtures and fakes. No network, no .env, no
MongoDB/SQL Server/online model. Provider call count is verified for
gate-failure cases (must be 0).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from release_sql_bot.application.candidates_v3 import (
    CandidateGateErrorV3,
    CandidateGenerationOutputInvalidV3Error,
    CandidateGenerationProviderRejectedV3Error,
    CandidateGenerationProviderUnavailableV3Error,
    CandidateScopeErrorV3,
    _check_m3_scope,
    generate_sql_candidate_v3,
)
from release_sql_bot.application.ports.approval_records_v3 import (
    InMemoryApprovalRecordPortV3,
)
from release_sql_bot.application.ports.candidates import (
    CandidateModelResponse,
    CandidateProviderRejectedError,
    CandidateProviderTimeoutError,
)
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffBatchRepositoryV3,
    FactBindingHandoffBatchRepositoryV3UnavailableError,
)
from release_sql_bot.domain.fact_binding_handoffs_v3 import (
    StoredFactBindingHandoffBatchV3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3
from release_sql_bot.domain.sql_candidates_v3 import (
    GenerateSqlCandidateRequestV3,
    SqlTemplateCandidateV3,
)
from tests.fakes import FixedCandidateModelProvider
from tests.v3_metadata_support import (
    valid_handoff_closure_v3_wire,
    valid_resolve_metadata_request_v3_wire,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _UnavailableHandoffRepository(FactBindingHandoffBatchRepositoryV3):
    async def get_batch_by_rule_version(self, rule_version: str):
        raise FactBindingHandoffBatchRepositoryV3UnavailableError("unavailable")


class _EmptyHandoffRepository(FactBindingHandoffBatchRepositoryV3):
    async def get_batch_by_rule_version(self, rule_version: str):
        return None


def _build_synthetic_handoff_batch():
    from release_sql_bot.application.canonical import canonical_sha256

    closure_wire = valid_handoff_closure_v3_wire()
    payload = FactBindingRequestV3.model_validate(closure_wire["payload"])
    rule_version = closure_wire["ruleVersion"]
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)
    request_id = closure_wire["requestId"]
    fact_code = closure_wire["factCode"]
    payload_sha256 = closure_wire["payloadSha256"]

    # Build the request wrapper with datetime objects (not serialized)
    request_dict = {
        "request_id": request_id,
        "rule_version": rule_version,
        "fact_code": fact_code,
        "contract_version": "3.0.0",
        "payload_sha256": payload_sha256,
        "created_at": now,
        "payload": payload.model_dump(by_alias=True, mode="json"),
    }

    # Compute batch_sha256 the same way intake does (canonical_sha256)
    batch_sha256 = canonical_sha256([{"requestId": request_id, "payloadSha256": payload_sha256}])

    return StoredFactBindingHandoffBatchV3.model_validate(
        {
            "_id": rule_version,
            "rule_version": rule_version,
            "contract_version": "3.0.0",
            "request_count": 1,
            "request_ids": [request_id],
            "batch_sha256": batch_sha256,
            "created_at": now,
            "requests": [request_dict],
        }
    )


def _build_synthetic_closure_wire():
    """Build a closure wire with the correct batch hash for the synthetic batch."""

    batch = _build_synthetic_handoff_batch()
    closure_wire = valid_handoff_closure_v3_wire()
    closure_wire["batchSha256"] = batch.batch_sha256
    return closure_wire


class _InMemoryHandoffRepository(FactBindingHandoffBatchRepositoryV3):
    def __init__(self, batch=None) -> None:
        self._batch = batch

    async def get_batch_by_rule_version(self, rule_version: str):
        if self._batch and self._batch.rule_version == rule_version:
            return self._batch
        return None


def _build_synthetic_approval_port():
    req_wire = valid_resolve_metadata_request_v3_wire()
    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    return InMemoryApprovalRecordPortV3(records={approval.approval_id: approval})


def _build_synthetic_handoff_repository():
    batch = _build_synthetic_handoff_batch()
    return _InMemoryHandoffRepository(batch)


def _build_resolution_report_wire() -> dict[str, Any]:
    """Run the real resolution service to get a valid metadataResolved report."""

    from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3

    req_wire = valid_resolve_metadata_request_v3_wire()
    # Fix the batch hash in the closure to match the synthetic batch
    corrected_closure = _build_synthetic_closure_wire()
    req_wire["handoffClosure"]["batchSha256"] = corrected_closure["batchSha256"]
    request = ResolveMetadataRequestV3.model_validate(req_wire)
    report = resolve_metadata_v3(request)
    return report.model_dump(by_alias=True, mode="json")


def _build_generation_request() -> GenerateSqlCandidateRequestV3:
    req_wire = valid_resolve_metadata_request_v3_wire()
    # Fix the batch hash in the closure to match the synthetic batch
    corrected_closure = _build_synthetic_closure_wire()
    req_wire["handoffClosure"]["batchSha256"] = corrected_closure["batchSha256"]
    report_wire = _build_resolution_report_wire()
    return GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": req_wire,
            "resolutionReport": report_wire,
        }
    )


def _synthetic_provider_content() -> str:
    req_wire = valid_resolve_metadata_request_v3_wire()
    binding = req_wire["bindingRequest"]
    fact = binding["fact"]
    usages = binding["usages"]

    parameters = [
        {
            "name": p["name"],
            "dataType": p["dataType"],
            "required": p["required"],
            "source": f"fact.parameters.{p['name']}",
        }
        for p in fact["parameters"]
    ]

    declared_usage_coverage = [
        {
            "stage": u["stage"],
            "ruleCode": u["ruleCode"],
            "priority": u["priority"],
            "conditionId": u["conditionId"],
            "conditionPath": u["conditionPath"],
            "outcome": u["outcome"],
        }
        for u in usages
    ]

    payload = {
        "templateCode": "SYNTHETIC_V3",
        "sqlTemplate": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey"
        ),
        "parameters": parameters,
        "result": {
            "columnName": "fact_value",
            "dataType": fact["dataType"],
            "cardinality": "scalar",
            "nullable": fact["nullable"],
            "nullPolicy": fact["nullPolicy"],
            "unit": fact.get("unit"),
        },
        "declaredObjects": [{"schemaName": "dbo", "relationName": "synthetic_table"}],
        "declaredUsageCoverage": declared_usage_coverage,
        "assumptions": [],
        "warnings": [],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _make_provider(responses: list[CandidateModelResponse | Exception]):
    return FixedCandidateModelProvider(responses)


def _valid_provider():
    return _make_provider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-001",
                model="fixed-model-v3",
                content=_synthetic_provider_content(),
            )
        ]
    )


# ---------------------------------------------------------------------------
# End-to-end success
# ---------------------------------------------------------------------------


def test_source_single_relation_e2e_success():
    """Source fact, single relation, full candidate wire and self-hash."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    # Provider called exactly once
    assert len(provider.calls) == 1

    # Fixed status/audit fields
    assert candidate.status == "candidate"
    assert candidate.executable is False
    assert candidate.review_status == "pending"
    assert candidate.schema_version == "3.0.0"

    # Self-hash valid
    assert candidate.content_sha256 != "0" * 64
    assert len(candidate.content_sha256) == 64

    # Dialect
    assert candidate.dialect == "sqlserver"

    # SQL template is non-empty and contains fact_value
    assert "fact_value" in candidate.sql_template.lower()

    # Parameters present
    assert len(candidate.parameters) > 0

    # Declared objects present
    assert len(candidate.declared_objects) > 0


# ---------------------------------------------------------------------------
# Scope rejection tests
# ---------------------------------------------------------------------------


def test_scope_rejects_aggregate():
    """Aggregation mode != none is rejected with scope error."""
    payload = _build_generation_request()
    payload.resolution_request.binding_request.query_requirements.aggregation.mode = "compute"
    with pytest.raises(CandidateScopeErrorV3, match="M3_SCOPE_UNSUPPORTED_AGGREGATION"):
        _check_m3_scope(payload)


def test_scope_rejects_time_range():
    """TimeRange mode != none is rejected."""
    payload = _build_generation_request()
    payload.resolution_request.binding_request.query_requirements.time_range.mode = "asOf"
    with pytest.raises(CandidateScopeErrorV3, match="M3_SCOPE_UNSUPPORTED_TIME_RANGE"):
        _check_m3_scope(payload)


def test_scope_rejects_filters():
    """Non-empty filters.items is rejected."""
    payload = _build_generation_request()
    from release_sql_bot.domain.fact_bindings_v3 import FilterRequirementV3

    payload.resolution_request.binding_request.query_requirements.filters.items = [
        FilterRequirementV3.model_validate(
            {
                "filterId": "f1",
                "fieldId": "factValue",
                "operator": "eq",
                "value": {"kind": "parameter", "parameterName": "syntheticKey"},
                "nullPolicy": "fail",
                "required": True,
                "evidenceIds": ["ev-fact-declaration"],
            }
        )
    ]
    with pytest.raises(CandidateScopeErrorV3, match="M3_SCOPE_UNSUPPORTED_FILTERS"):
        _check_m3_scope(payload)


# ---------------------------------------------------------------------------
# Gate failure: provider zero calls
# ---------------------------------------------------------------------------


def test_no_batch_in_repository_zero_provider_calls():
    """Handoff batch not found in repository: provider not called."""
    provider = _valid_provider()
    handoff_repo = _EmptyHandoffRepository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    with pytest.raises(CandidateGateErrorV3):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    assert len(provider.calls) == 0


def test_repository_unavailable_zero_provider_calls():
    """Repository unavailable: provider not called."""
    provider = _valid_provider()
    handoff_repo = _UnavailableHandoffRepository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    with pytest.raises(CandidateGateErrorV3):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    assert len(provider.calls) == 0


def test_approval_record_missing_zero_provider_calls():
    """Approval record not in port: provider not called."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = InMemoryApprovalRecordPortV3()  # empty
    payload = _build_generation_request()

    with pytest.raises(CandidateGateErrorV3):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    assert len(provider.calls) == 0


def test_approval_record_mismatch_zero_provider_calls():
    """Approval record mismatch (tampered): provider not called."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    # Tamper with the approval record ID to cause a mismatch
    payload.resolution_request.approval_record.approval_id = "nonexistent-approval"

    with pytest.raises(CandidateGateErrorV3):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    assert len(provider.calls) == 0


# ---------------------------------------------------------------------------
# Output validation rejection
# ---------------------------------------------------------------------------


def test_invalid_parameter_declaration_rejected():
    """Model output with wrong parameters is rejected."""
    bad_content = json.dumps(
        {
            "templateCode": "BAD_V3",
            "sqlTemplate": "SELECT 1",
            "parameters": [
                {
                    "name": "wrongParam",
                    "dataType": "string",
                    "required": True,
                    "source": "fact.parameters.wrongParam",
                }
            ],
            "result": {
                "columnName": "fact_value",
                "dataType": "integer",
                "cardinality": "scalar",
                "nullable": False,
                "nullPolicy": "fail",
                "unit": None,
            },
            "declaredObjects": [{"schemaName": "dbo", "relationName": "synthetic_table"}],
            "declaredUsageCoverage": [
                {
                    "stage": "eligibility",
                    "ruleCode": "RULE_A",
                    "priority": 1,
                    "conditionId": "cond-1",
                    "conditionPath": "/rules/a",
                    "outcome": "READY",
                }
            ],
            "assumptions": [],
            "warnings": [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    provider = _make_provider(
        [
            CandidateModelResponse(
                provider="p",
                request_id="r",
                model="m",
                content=bad_content,
            )
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    with pytest.raises(CandidateGenerationOutputInvalidV3Error):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    assert len(provider.calls) == 1


def test_invalid_result_declaration_rejected():
    """Model output with wrong result type is rejected."""
    req_wire = valid_resolve_metadata_request_v3_wire()
    binding = req_wire["bindingRequest"]
    fact = binding["fact"]

    bad_content = json.dumps(
        {
            "templateCode": "BAD_V3",
            "sqlTemplate": "SELECT 1",
            "parameters": [
                {
                    "name": p["name"],
                    "dataType": p["dataType"],
                    "required": p["required"],
                    "source": f"fact.parameters.{p['name']}",
                }
                for p in fact["parameters"]
            ],
            "result": {
                "columnName": "fact_value",
                "dataType": "string",  # wrong — fact is integer
                "cardinality": "scalar",
                "nullable": False,
                "nullPolicy": "fail",
                "unit": None,
            },
            "declaredObjects": [{"schemaName": "dbo", "relationName": "synthetic_table"}],
            "declaredUsageCoverage": [
                {
                    "stage": u["stage"],
                    "ruleCode": u["ruleCode"],
                    "priority": u["priority"],
                    "conditionId": u["conditionId"],
                    "conditionPath": u["conditionPath"],
                    "outcome": u["outcome"],
                }
                for u in binding["usages"]
            ],
            "assumptions": [],
            "warnings": [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    provider = _make_provider(
        [
            CandidateModelResponse(
                provider="p",
                request_id="r",
                model="m",
                content=bad_content,
            )
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    with pytest.raises(CandidateGenerationOutputInvalidV3Error):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )


def test_invalid_declared_objects_rejected():
    """Model output with unauthorized objects is rejected."""
    req_wire = valid_resolve_metadata_request_v3_wire()
    binding = req_wire["bindingRequest"]
    fact = binding["fact"]

    bad_content = json.dumps(
        {
            "templateCode": "BAD_V3",
            "sqlTemplate": "SELECT 1",
            "parameters": [
                {
                    "name": p["name"],
                    "dataType": p["dataType"],
                    "required": p["required"],
                    "source": f"fact.parameters.{p['name']}",
                }
                for p in fact["parameters"]
            ],
            "result": {
                "columnName": "fact_value",
                "dataType": fact["dataType"],
                "cardinality": "scalar",
                "nullable": fact["nullable"],
                "nullPolicy": fact["nullPolicy"],
                "unit": fact.get("unit"),
            },
            "declaredObjects": [
                {"schemaName": "hacked", "relationName": "unauthorized_table"},
            ],
            "declaredUsageCoverage": [
                {
                    "stage": u["stage"],
                    "ruleCode": u["ruleCode"],
                    "priority": u["priority"],
                    "conditionId": u["conditionId"],
                    "conditionPath": u["conditionPath"],
                    "outcome": u["outcome"],
                }
                for u in binding["usages"]
            ],
            "assumptions": [],
            "warnings": [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    provider = _make_provider(
        [
            CandidateModelResponse(
                provider="p",
                request_id="r",
                model="m",
                content=bad_content,
            )
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    with pytest.raises(CandidateGenerationOutputInvalidV3Error):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )


# ---------------------------------------------------------------------------
# Same conditionId: multiple usages preserved
# ---------------------------------------------------------------------------


def test_same_condition_id_different_usage_preserved():
    """Same conditionId with different six-tuples must not be merged."""
    from release_sql_bot.domain.sql_candidates_v3 import (
        GeneratedCandidatePayloadV3,
    )

    payload = GeneratedCandidatePayloadV3.model_validate(
        {
            "templateCode": "T",
            "sqlTemplate": "SELECT 1",
            "parameters": [],
            "result": {
                "columnName": "fact_value",
                "dataType": "integer",
                "cardinality": "scalar",
                "nullable": False,
                "nullPolicy": "fail",
                "unit": None,
            },
            "declaredObjects": [{"schemaName": "dbo", "relationName": "t"}],
            "declaredUsageCoverage": [
                {
                    "stage": "eligibility",
                    "ruleCode": "RULE_A",
                    "priority": 1,
                    "conditionId": "cond-1",
                    "conditionPath": "/a",
                    "outcome": "READY",
                },
                {
                    "stage": "eligibility",
                    "ruleCode": "RULE_B",
                    "priority": 2,
                    "conditionId": "cond-1",
                    "conditionPath": "/a",
                    "outcome": "READY",
                },
            ],
            "assumptions": [],
            "warnings": [],
        }
    )

    assert len(payload.declared_usage_coverage) == 2


# ---------------------------------------------------------------------------
# Timeout/retry with bounded limit
# ---------------------------------------------------------------------------


def test_transient_error_retries_with_limit():
    """Transient errors are retried up to maxRetries+1 times."""
    provider = _make_provider(
        [
            CandidateProviderTimeoutError("timeout-1"),
            CandidateProviderTimeoutError("timeout-2"),
            CandidateModelResponse(
                provider="p",
                request_id="r",
                model="m",
                content=_synthetic_provider_content(),
            ),
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=2,
            retry_base_delay_seconds=0.001,
        )
    )

    # 2 failures + 1 success = 3 calls
    assert len(provider.calls) == 3
    assert candidate.status == "candidate"


def test_transient_error_exhaustion_raises():
    """All retries exhausted raises provider unavailable."""
    provider = _make_provider(
        [
            CandidateProviderTimeoutError("timeout"),
            CandidateProviderTimeoutError("timeout"),
            CandidateProviderTimeoutError("timeout"),
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    with pytest.raises(CandidateGenerationProviderUnavailableV3Error):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=2,
                retry_base_delay_seconds=0.001,
            )
        )

    # 3 attempts (maxRetries+1)
    assert len(provider.calls) == 3


def test_provider_rejected_no_retry():
    """Permanent rejection is NOT retried."""
    provider = _make_provider(
        [
            CandidateProviderRejectedError("rejected"),
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    with pytest.raises(CandidateGenerationProviderRejectedV3Error):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=3,
            )
        )

    # Only 1 call — no retry on permanent rejection
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# Candidate fixed fields
# ---------------------------------------------------------------------------


def test_candidate_fixed_status_and_audit():
    """Candidate must have fixed candidate/executable/pending and model cannot override."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert candidate.status == "candidate"
    assert candidate.executable is False
    assert candidate.review_status == "pending"
    assert candidate.schema_version == "3.0.0"
    assert candidate.provenance.prompt_version == "sqlserver-fact-candidate-v3.0"


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------


def test_generate_request_contract():
    """GenerateSqlCandidateRequestV3 has correct schemaVersion."""
    req_wire = valid_resolve_metadata_request_v3_wire()
    report_wire = _build_resolution_report_wire()
    req = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": req_wire,
            "resolutionReport": report_wire,
        }
    )
    assert req.schema_version == "1.0.0"


def test_candidate_schema_version_fixed():
    """SqlTemplateCandidateV3 schemaVersion is fixed at 3.0.0."""
    assert SqlTemplateCandidateV3.model_fields["schema_version"].default == "3.0.0"


def test_contract_rejects_snake_case():
    """V3 contracts reject snake_case keys."""
    with pytest.raises(ValidationError):
        GenerateSqlCandidateRequestV3.model_validate(
            {
                "schema_version": "1.0.0",  # snake_case
                "resolutionRequest": {},
                "resolutionReport": {},
            }
        )


# ---------------------------------------------------------------------------
# Six-tuple conditionPath regression
# ---------------------------------------------------------------------------


def test_condition_path_tampering_rejected():
    """Model output that only changes conditionPath must be rejected."""
    # Build content with tampered conditionPath
    good_content = _synthetic_provider_content()
    data = json.loads(good_content)
    # Tamper only the conditionPath
    data["declaredUsageCoverage"][0]["conditionPath"] = "/TAMPERED/PATH"
    bad_content = json.dumps(data, ensure_ascii=False, sort_keys=True)

    provider = _make_provider(
        [
            CandidateModelResponse(
                provider="p",
                request_id="r",
                model="m",
                content=bad_content,
            )
        ]
    )
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    with pytest.raises(CandidateGenerationOutputInvalidV3Error):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    assert len(provider.calls) == 1


def test_same_condition_id_different_condition_path_preserved_e2e():
    """Two usages with same conditionId but different conditionPath must both be preserved."""
    from copy import deepcopy

    from release_sql_bot.application.canonical import canonical_sha256
    from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
    from release_sql_bot.domain.project_bindings_v3 import (
        ResolveMetadataRequestV3,
    )

    # Build a generation request with two usages differing only in conditionPath
    req_wire = valid_resolve_metadata_request_v3_wire()
    binding_req = req_wire["bindingRequest"]

    # Add a second usage that differs from the original ONLY in conditionPath.
    # All other five identity fields (stage, ruleCode, priority, conditionId,
    # outcome) must remain identical.
    original_usage = binding_req["usages"][0]
    second_usage = deepcopy(original_usage)
    second_usage["conditionPath"] = "/rule/stages/eligibility/2"
    binding_req["usages"].append(second_usage)

    # Precondition: the two usages are identical except for conditionPath.
    assert original_usage["stage"] == second_usage["stage"]
    assert original_usage["ruleCode"] == second_usage["ruleCode"]
    assert original_usage["priority"] == second_usage["priority"]
    assert original_usage["conditionId"] == second_usage["conditionId"]
    assert original_usage["outcome"] == second_usage["outcome"]
    assert original_usage["conditionPath"] != second_usage["conditionPath"]

    # Reclose the handoff closure: payload must match the modified binding request
    binding_model = FactBindingRequestV3.model_validate(binding_req)
    new_payload_hash = canonical_sha256(binding_model)
    req_wire["handoffClosure"]["payload"] = binding_req
    req_wire["handoffClosure"]["payloadSha256"] = new_payload_hash
    # Recompute batch hash from the new payload identity
    req_wire["handoffClosure"]["batchSha256"] = canonical_sha256(
        [{"requestId": binding_model.request_id, "payloadSha256": new_payload_hash}]
    )

    # Re-run resolution to get a valid report
    request = ResolveMetadataRequestV3.model_validate(req_wire)
    report = resolve_metadata_v3(request)
    assert report.status == "metadataResolved", (
        f"resolution failed: {[i.code for i in report.issues]}"
    )
    report_wire = report.model_dump(by_alias=True, mode="json")

    payload = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": req_wire,
            "resolutionReport": report_wire,
        }
    )

    # Build provider content with both usages
    fact = binding_req["fact"]
    usages = binding_req["usages"]

    parameters = [
        {
            "name": p["name"],
            "dataType": p["dataType"],
            "required": p["required"],
            "source": f"fact.parameters.{p['name']}",
        }
        for p in fact["parameters"]
    ]

    declared_usage_coverage = [
        {
            "stage": u["stage"],
            "ruleCode": u["ruleCode"],
            "priority": u["priority"],
            "conditionId": u["conditionId"],
            "conditionPath": u["conditionPath"],
            "outcome": u["outcome"],
        }
        for u in usages
    ]

    content = json.dumps(
        {
            "templateCode": "SYNTHETIC_V3",
            "sqlTemplate": (
                "SELECT t.synthetic_value AS fact_value "
                "FROM dbo.synthetic_table t "
                "WHERE t.synthetic_key = :syntheticKey"
            ),
            "parameters": parameters,
            "result": {
                "columnName": "fact_value",
                "dataType": fact["dataType"],
                "cardinality": "scalar",
                "nullable": fact["nullable"],
                "nullPolicy": fact["nullPolicy"],
                "unit": fact.get("unit"),
            },
            "declaredObjects": [{"schemaName": "dbo", "relationName": "synthetic_table"}],
            "declaredUsageCoverage": declared_usage_coverage,
            "assumptions": [],
            "warnings": [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )

    provider = _make_provider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-001",
                model="fixed-model-v3",
                content=content,
            )
        ]
    )

    # Rebuild the handoff batch with the new payload (two usages)
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)
    closure = req_wire["handoffClosure"]
    new_payload_hash = closure["payloadSha256"]
    batch_request_id = closure["requestId"]
    batch_rule_version = closure["ruleVersion"]
    request_dict = {
        "request_id": batch_request_id,
        "rule_version": batch_rule_version,
        "fact_code": closure["factCode"],
        "contract_version": "3.0.0",
        "payload_sha256": new_payload_hash,
        "created_at": now,
        "payload": binding_req,
    }
    new_batch_sha256 = canonical_sha256(
        [{"requestId": batch_request_id, "payloadSha256": new_payload_hash}]
    )
    new_batch = StoredFactBindingHandoffBatchV3.model_validate(
        {
            "_id": batch_rule_version,
            "rule_version": batch_rule_version,
            "contract_version": "3.0.0",
            "request_count": 1,
            "request_ids": [batch_request_id],
            "batch_sha256": new_batch_sha256,
            "created_at": now,
            "requests": [request_dict],
        }
    )
    handoff_repo = _InMemoryHandoffRepository(new_batch)
    approval_port = _build_synthetic_approval_port()

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    # Both usages must be preserved
    assert len(candidate.declared_usage_coverage) == 2
    paths = {u.condition_path for u in candidate.declared_usage_coverage}
    assert "/rule/stages/eligibility/1" in paths
    assert "/rule/stages/eligibility/2" in paths
    # Same conditionId
    ids = {u.condition_id for u in candidate.declared_usage_coverage}
    assert ids == {"cond-synthetic-001"}

    # Self-hash must be valid
    from release_sql_bot.application.canonical import canonical_content_sha256

    assert candidate.content_sha256 == canonical_content_sha256(candidate)

    # Usage traceability must be from the report (which includes both usages)
    assert candidate.usage_traceability_sha256 == report.usage_traceability_sha256


# ---------------------------------------------------------------------------
# Field scope regression
# ---------------------------------------------------------------------------


def test_scope_rejects_non_entitykey_role_even_if_field_id_matches_param():
    """Fields with role=filter/groupBy/time must be rejected even if fieldId matches param name."""
    from release_sql_bot.domain.fact_bindings_v3 import FieldRequirementV3

    payload = _build_generation_request()
    # Add a field with role=filter but fieldId equal to the entity-key parameter name
    payload.resolution_request.binding_request.query_requirements.fields.append(
        FieldRequirementV3.model_validate(
            {
                "fieldId": "syntheticKey",  # Same as param name
                "role": "filter",
                "logicalName": "filter_field",
                "dataType": "string",
                "required": True,
                "evidenceIds": ["ev-fact-declaration"],
            }
        )
    )
    with pytest.raises(CandidateScopeErrorV3, match="M3_SCOPE_UNSUPPORTED_FIELDS"):
        _check_m3_scope(payload)


def test_scope_accepts_entitykey_field_with_different_param_name():
    """Scope check: entity-key fieldId != parameterName but valid authorization is accepted.

    This is a scope-check regression, not an end-to-end generation test.
    """

    payload = _build_generation_request()

    # Change the entity-key field's fieldId to "actualKey" while keeping
    # parameterName="syntheticKey", and update both the field requirement
    # and the authorizations to match.
    for field in payload.resolution_request.binding_request.query_requirements.fields:
        if field.field_id == "syntheticKey":
            field.field_id = "actualKey"
            break

    # Update entity-key authorizations to reference the new fieldId
    for eka in payload.resolution_request.project_context.entity_key_authorizations:
        if eka.field_id == "syntheticKey":
            eka.field_id = "actualKey"

    # Update field-binding authorizations to reference the new fieldId
    for fba in payload.resolution_request.project_context.field_binding_authorizations:
        if fba.field_id == "syntheticKey":
            fba.field_id = "actualKey"

    # Verify fieldId != parameterName
    for field in payload.resolution_request.binding_request.query_requirements.fields:
        if field.field_id == "factValue":
            continue
        assert field.field_id == "actualKey"
        # parameterName is still "syntheticKey" in entity_key_authorizations
        param_names = {
            eka.parameter_name
            for eka in payload.resolution_request.project_context.entity_key_authorizations
            if eka.request_id == payload.resolution_request.binding_request.request_id
        }
        assert "syntheticKey" in param_names  # parameterName unchanged
        assert field.field_id != "syntheticKey"  # fieldId != parameterName

    # Scope check must accept: field is authorized entity-key, despite name mismatch
    _check_m3_scope(payload)


# ---------------------------------------------------------------------------
# Repository read count regression
# ---------------------------------------------------------------------------


def test_repository_read_count_is_one_on_success():
    """Successful generation reads the repository exactly once."""
    provider = _valid_provider()

    # Create a counting repository wrapper
    inner_repo = _build_synthetic_handoff_repository()

    class CountingRepo:
        def __init__(self, inner):
            self._inner = inner
            self.read_count = 0

        async def get_batch_by_rule_version(self, rule_version: str):
            self.read_count += 1
            return await self._inner.get_batch_by_rule_version(rule_version)

    counting_repo = CountingRepo(inner_repo)
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=payload,
            handoff_repository=counting_repo,
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert candidate.status == "candidate"
    assert counting_repo.read_count == 1


# ---------------------------------------------------------------------------
# Approval inactive regression
# ---------------------------------------------------------------------------


def test_approval_inactive_zero_provider_calls():
    """Approval record marked as inactive: provider not called."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()

    # Create approval port with the record but marked inactive
    req_wire = valid_resolve_metadata_request_v3_wire()
    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    approval_port = InMemoryApprovalRecordPortV3(
        records={approval.approval_id: approval},
        inactive_ids=frozenset([approval.approval_id]),
    )
    payload = _build_generation_request()

    with pytest.raises(CandidateGateErrorV3):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    assert len(provider.calls) == 0


# ---------------------------------------------------------------------------
# M2 report tampered regression
# ---------------------------------------------------------------------------


def test_resolution_report_tampered_zero_provider_calls():
    """Carried M2 report that doesn't match recomputed: provider not called."""
    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    # Tamper with the carried report's batch hash
    payload.resolution_report.handoff_refs.batch_sha256 = "f" * 64

    with pytest.raises(CandidateGateErrorV3):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    assert len(provider.calls) == 0


# ---------------------------------------------------------------------------
# contentSha256 exact match regression
# ---------------------------------------------------------------------------


def test_content_sha256_exact_match():
    """contentSha256 must equal canonical_content_sha256(candidate), not just length."""
    from release_sql_bot.application.canonical import canonical_content_sha256

    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    # Must exactly match the canonical hash
    assert candidate.content_sha256 == canonical_content_sha256(candidate)
    # Verify it's not the placeholder
    assert candidate.content_sha256 != "0" * 64
    # Verify it's a valid SHA-256 hex string
    assert len(candidate.content_sha256) == 64
    assert int(candidate.content_sha256, 16) > 0


# ---------------------------------------------------------------------------
# Preview JSON validation
# ---------------------------------------------------------------------------


def test_preview_json_passes_contract_validation(tmp_path):
    """Preview output must pass SqlTemplateCandidateV3 validation with correct self-hash."""
    from release_sql_bot.application.canonical import canonical_content_sha256

    provider = _valid_provider()
    handoff_repo = _build_synthetic_handoff_repository()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    # Serialize and re-validate through the contract
    wire = candidate.model_dump(by_alias=True, mode="json")
    revalidated = SqlTemplateCandidateV3.model_validate(wire)
    assert revalidated.content_sha256 == candidate.content_sha256
    assert revalidated.content_sha256 == canonical_content_sha256(revalidated)
    assert revalidated.status == "candidate"
    assert revalidated.executable is False
    assert revalidated.review_status == "pending"


# ---------------------------------------------------------------------------
# Repository error mapping
# ---------------------------------------------------------------------------


def test_repository_document_invalid_mapping():
    """DocumentInvalid error maps to M3_HANDOFF_BATCH_INVALID with zero provider calls."""
    from release_sql_bot.application.ports.handoffs import (
        FactBindingHandoffBatchDocumentInvalidV3Error,
    )

    class _InvalidDocRepo(FactBindingHandoffBatchRepositoryV3):
        async def get_batch_by_rule_version(self, rule_version: str):
            raise FactBindingHandoffBatchDocumentInvalidV3Error("invalid doc")

    provider = _valid_provider()
    handoff_repo = _InvalidDocRepo()
    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    with pytest.raises(CandidateGateErrorV3, match="M3_HANDOFF_BATCH_INVALID"):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    assert len(provider.calls) == 0


# ---------------------------------------------------------------------------
# Synthetic marker not leaked
# ---------------------------------------------------------------------------


def test_synthetic_marker_not_in_neutral_errors(caplog):
    """Synthetic markers must not leak in str/repr/traceback/caplog."""
    import logging

    # Marker must match fieldId pattern ^[a-z][A-Za-z0-9_.-]*$
    marker = "syntheticPrivateMarker"

    # Test scope error with marker in field
    payload = _build_generation_request()
    from release_sql_bot.domain.fact_bindings_v3 import FieldRequirementV3

    payload.resolution_request.binding_request.query_requirements.fields.append(
        FieldRequirementV3.model_validate(
            {
                "fieldId": marker,
                "role": "filter",
                "logicalName": marker,
                "dataType": "string",
                "required": True,
                "evidenceIds": ["ev-fact-declaration"],
            }
        )
    )
    with pytest.raises(CandidateScopeErrorV3) as exc_info:
        _check_m3_scope(payload)

    error_str = str(exc_info.value)
    error_repr = repr(exc_info.value)
    assert marker not in error_str
    assert marker not in error_repr

    # Test gate error with marker
    with caplog.at_level(logging.ERROR):
        provider = _valid_provider()
        handoff_repo = _build_synthetic_handoff_repository()
        approval_port = _build_synthetic_approval_port()

        payload2 = _build_generation_request()
        payload2.resolution_request.approval_record.approval_id = marker

        with pytest.raises(CandidateGateErrorV3):
            asyncio.run(
                generate_sql_candidate_v3(
                    provider=provider,
                    payload=payload2,
                    handoff_repository=handoff_repo,
                    approval_port=approval_port,
                    model="fixed-model-v3",
                    max_retries=0,
                )
            )

    # Check caplog doesn't contain marker
    assert marker not in caplog.text
    assert len(provider.calls) == 0
