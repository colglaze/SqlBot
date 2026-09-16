"""Encoding map, null handling, rowset cardinality, and rule-change tests.

Synthetic offline fixtures only. No private object names or production codes.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from release_sql_bot.application.candidates_v3 import (
    CandidateGateErrorV3,
    generate_sql_candidate_v3,
)
from release_sql_bot.application.canonical import canonical_content_sha256, canonical_sha256
from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3
from release_sql_bot.application.ports.approval_records_v3 import InMemoryApprovalRecordPortV3
from release_sql_bot.application.ports.candidates import CandidateModelResponse
from release_sql_bot.application.ports.handoffs import FactBindingHandoffBatchRepositoryV3
from release_sql_bot.application.sql_validation_v3 import validate_sql_candidate_v3
from release_sql_bot.domain.fact_binding_handoffs_v3 import StoredFactBindingHandoffBatchV3
from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3, ResolveMetadataRequestV3
from release_sql_bot.domain.sql_candidates_v3 import GenerateSqlCandidateRequestV3
from release_sql_bot.domain.sql_validation_v3 import ValidateSqlCandidateRequestV3
from tests.fakes import FixedCandidateModelProvider
from tests.v3_metadata_support import (
    _reclose_all_hashes_from_wire,
    valid_resolve_metadata_request_v3_wire,
)

_SOURCE_SHA = "c" * 64


def _as_string_flag_fact(req_wire: dict) -> None:
    binding = req_wire["bindingRequest"]
    binding["fact"]["dataType"] = "string"
    binding["fact"]["nullable"] = True
    binding["fact"]["nullPolicy"] = "indeterminate"
    binding["fact"]["allowedValues"] = ["yes", "no"]
    binding["queryRequirements"]["result"]["dataType"] = "string"
    binding["queryRequirements"]["result"]["nullable"] = True
    binding["queryRequirements"]["result"]["nullPolicy"] = "indeterminate"
    for field in binding["queryRequirements"]["fields"]:
        if field["fieldId"] == "factValue":
            field["dataType"] = "string"
            field["required"] = False


def _encoding_binding(request_id: str) -> dict:
    return {
        "bindingId": "enc-synth-flag",
        "requestId": request_id,
        "fieldId": "factValue",
        "columnGrantId": "colgrant-value",
        "projectionKind": "mappedCase",
        "comparisonKind": "exactString",
        "nullInput": "preserve",
        "unknownPhysical": "null",
        "entries": [
            {"physicalValue": "P0", "logicalValue": "yes"},
            {"physicalValue": "P1", "logicalValue": "no"},
        ],
        "sourceKind": "viewPhysicalBaseline",
        "sourceSha256": _SOURCE_SHA,
    }


def _result_semantics_binding(request_id: str) -> dict:
    return {
        "bindingId": "rs-synth-flag",
        "requestId": request_id,
        "emptyMatch": "emptyResultSet",
        "extraRows": "returnAll",
        "matchedNull": "preserve",
        "consumerCardinality": "rowset",
        "grainConflictPolicy": "surfaceAllRows",
    }


def _prepare_encoding_request(*, include_encoding: bool = True, include_rowset: bool = True):
    req_wire = valid_resolve_metadata_request_v3_wire()
    _as_string_flag_fact(req_wire)
    ctx = req_wire["projectContext"]
    ctx["schemaVersion"] = "1.2.0"
    request_id = req_wire["bindingRequest"]["requestId"]
    ctx["valueEncodingBindings"] = [_encoding_binding(request_id)] if include_encoding else []
    ctx["resultSemanticsBindings"] = (
        [_result_semantics_binding(request_id)] if include_rowset else []
    )
    return _reclose_all_hashes_from_wire(req_wire)


def _case_sql() -> str:
    return (
        "SELECT CASE t.synthetic_value WHEN 'P0' THEN 'yes' WHEN 'P1' THEN 'no' "
        "ELSE NULL END AS fact_value FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )


def _provider_content(req: ResolveMetadataRequestV3, sql: str, cardinality: str) -> str:
    fact = req.binding_request.fact
    payload = {
        "templateCode": "SYNTHETIC_V3",
        "sqlTemplate": sql,
        "parameters": [
            {
                "name": item.name,
                "dataType": str(item.data_type),
                "required": item.required,
                "source": f"fact.parameters.{item.name}",
            }
            for item in fact.parameters
        ],
        "result": {
            "columnName": "fact_value",
            "dataType": str(fact.data_type),
            "cardinality": cardinality,
            "nullable": fact.nullable,
            "nullPolicy": str(fact.null_policy),
            "unit": fact.unit,
        },
        "declaredObjects": [{"schemaName": "dbo", "relationName": "synthetic_table"}],
        "declaredUsageCoverage": [
            {
                "stage": str(item.stage),
                "ruleCode": item.rule_code,
                "priority": item.priority,
                "conditionId": item.condition_id,
                "conditionPath": item.condition_path,
                "outcome": str(item.outcome),
            }
            for item in req.binding_request.usages
        ],
        "assumptions": [],
        "warnings": [],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class _InMemoryHandoffRepository(FactBindingHandoffBatchRepositoryV3):
    def __init__(self, batch) -> None:
        self._batch = batch

    async def get_batch_by_rule_version(self, rule_version: str):
        if self._batch and self._batch.rule_version == rule_version:
            return self._batch
        return None


def _batch_for(req: ResolveMetadataRequestV3) -> StoredFactBindingHandoffBatchV3:
    payload = req.binding_request
    rule_version = payload.rule_ref.rule_version
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)
    payload_sha256 = req.handoff_closure.payload_sha256
    request_id = payload.request_id
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
            "requests": [
                {
                    "request_id": request_id,
                    "rule_version": rule_version,
                    "fact_code": payload.fact.fact_code,
                    "contract_version": "3.0.0",
                    "payload_sha256": payload_sha256,
                    "created_at": now,
                    "payload": payload.model_dump(by_alias=True, mode="json"),
                }
            ],
        }
    )


def _align_batch_hash(req: ResolveMetadataRequestV3) -> ResolveMetadataRequestV3:
    batch = _batch_for(req)
    wire = req.model_dump(by_alias=True, mode="json")
    wire["handoffClosure"]["batchSha256"] = batch.batch_sha256
    return ResolveMetadataRequestV3.model_validate(wire), batch


def test_allowed_values_without_encoding_are_blocked() -> None:
    request = _prepare_encoding_request(include_encoding=False, include_rowset=False)
    report = resolve_metadata_v3(request)
    assert report.status == "blocked"
    assert any(item.code == "VALUE_ENCODING_REQUIRED" for item in report.issues)


def test_missing_encoding_blocks_generation_before_provider() -> None:
    request = _prepare_encoding_request(include_encoding=False, include_rowset=False)
    request, batch = _align_batch_hash(request)
    report = resolve_metadata_v3(request)
    generation = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": request.model_dump(by_alias=True, mode="json"),
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="enc-required",
                model="fixed-model-v3",
                content=_provider_content(request, _case_sql(), "scalar"),
            )
        ]
    )
    approval = ApprovalRecordV3.model_validate(
        request.approval_record.model_dump(by_alias=True, mode="json")
    )
    with pytest.raises(CandidateGateErrorV3, match="M3_RESOLUTION_BLOCKED"):
        asyncio.run(
            generate_sql_candidate_v3(
                provider=provider,
                payload=generation,
                handoff_repository=_InMemoryHandoffRepository(batch),
                approval_port=InMemoryApprovalRecordPortV3(
                    records={approval.approval_id: approval}
                ),
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    assert len(provider.calls) == 0


def test_mapped_case_encoding_resolves() -> None:
    request = _prepare_encoding_request()
    report = resolve_metadata_v3(request)
    assert report.status == "metadataResolved"
    assert len(report.resolved_value_encodings) == 1
    encoding = report.resolved_value_encodings[0]
    assert encoding.projection_kind == "mappedCase"
    assert encoding.column_name == "synthetic_value"
    assert report.resolved_result_semantics is not None
    assert report.resolved_result_semantics.consumer_cardinality == "rowset"


def test_logical_value_outside_allowed_values_is_blocked() -> None:
    request = _prepare_encoding_request()
    wire = request.model_dump(by_alias=True, mode="json")
    wire["projectContext"]["valueEncodingBindings"][0]["entries"][0]["logicalValue"] = "maybe"
    request = _reclose_all_hashes_from_wire(wire)
    report = resolve_metadata_v3(request)
    assert report.status == "blocked"
    assert any(item.code == "VALUE_ENCODING_VALUES_INVALID" for item in report.issues)


def test_mapped_case_candidate_passes_static_gate() -> None:
    request = _prepare_encoding_request()
    request, batch = _align_batch_hash(request)
    report = resolve_metadata_v3(request)
    assert report.status == "metadataResolved"
    generation = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": request.model_dump(by_alias=True, mode="json"),
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="enc-1",
                model="fixed-model-v3",
                content=_provider_content(request, _case_sql(), "rowset"),
            )
        ]
    )
    approval = ApprovalRecordV3.model_validate(
        request.approval_record.model_dump(by_alias=True, mode="json")
    )
    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=generation,
            handoff_repository=_InMemoryHandoffRepository(batch),
            approval_port=InMemoryApprovalRecordPortV3(records={approval.approval_id: approval}),
            model="fixed-model-v3",
            max_retries=0,
        )
    )
    assert candidate.schema_version == "3.1.0"
    assert candidate.result.cardinality == "rowset"
    assert candidate.provenance.prompt_version == "sqlserver-fact-candidate-v3.2"
    static = validate_sql_candidate_v3(
        ValidateSqlCandidateRequestV3.model_validate(
            {
                "schemaVersion": "1.0.0",
                "generationRequest": generation.model_dump(by_alias=True, mode="json"),
                "candidate": candidate.model_dump(by_alias=True, mode="json"),
            }
        )
    )
    assert static.status == "passed"
    assert static.issues == ()
    assert static.inspection is not None
    assert static.inspection.encoding_cases


def test_else_default_no_is_rejected() -> None:
    request = _prepare_encoding_request()
    request, batch = _align_batch_hash(request)
    report = resolve_metadata_v3(request)
    generation = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": request.model_dump(by_alias=True, mode="json"),
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    sql = (
        "SELECT CASE t.synthetic_value WHEN 'P0' THEN 'yes' ELSE 'no' END AS fact_value "
        "FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey"
    )
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="enc-bad-else",
                model="fixed-model-v3",
                content=_provider_content(request, sql, "rowset"),
            )
        ]
    )
    approval = ApprovalRecordV3.model_validate(
        request.approval_record.model_dump(by_alias=True, mode="json")
    )
    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=generation,
            handoff_repository=_InMemoryHandoffRepository(batch),
            approval_port=InMemoryApprovalRecordPortV3(records={approval.approval_id: approval}),
            model="fixed-model-v3",
            max_retries=0,
        )
    )
    static = validate_sql_candidate_v3(
        ValidateSqlCandidateRequestV3.model_validate(
            {
                "schemaVersion": "1.0.0",
                "generationRequest": generation.model_dump(by_alias=True, mode="json"),
                "candidate": candidate.model_dump(by_alias=True, mode="json"),
            }
        )
    )
    assert static.status == "blocked"
    assert any(item.code == "SQL_ENCODING_ELSE" for item in static.issues)


def test_isnull_default_is_rejected() -> None:
    request = _prepare_encoding_request()
    request, batch = _align_batch_hash(request)
    report = resolve_metadata_v3(request)
    generation = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": request.model_dump(by_alias=True, mode="json"),
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    sql = (
        "SELECT ISNULL(t.synthetic_value, 'no') AS fact_value "
        "FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey"
    )
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="enc-isnull",
                model="fixed-model-v3",
                content=_provider_content(request, sql, "rowset"),
            )
        ]
    )
    approval = ApprovalRecordV3.model_validate(
        request.approval_record.model_dump(by_alias=True, mode="json")
    )
    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=generation,
            handoff_repository=_InMemoryHandoffRepository(batch),
            approval_port=InMemoryApprovalRecordPortV3(records={approval.approval_id: approval}),
            model="fixed-model-v3",
            max_retries=0,
        )
    )
    static = validate_sql_candidate_v3(
        ValidateSqlCandidateRequestV3.model_validate(
            {
                "schemaVersion": "1.0.0",
                "generationRequest": generation.model_dump(by_alias=True, mode="json"),
                "candidate": candidate.model_dump(by_alias=True, mode="json"),
            }
        )
    )
    assert static.status == "blocked"
    codes = {item.code for item in static.issues}
    assert "SQL_FUNCTION" in codes or "SQL_NODE_FORBIDDEN" in codes or "SQL_ENCODING_CASE" in codes


def test_top_is_rejected_for_rowset() -> None:
    request = _prepare_encoding_request()
    request, batch = _align_batch_hash(request)
    report = resolve_metadata_v3(request)
    generation = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": request.model_dump(by_alias=True, mode="json"),
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    sql = (
        "SELECT TOP 1 CASE t.synthetic_value WHEN 'P0' THEN 'yes' WHEN 'P1' THEN 'no' "
        "ELSE NULL END AS fact_value FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="enc-top",
                model="fixed-model-v3",
                content=_provider_content(request, sql, "rowset"),
            )
        ]
    )
    approval = ApprovalRecordV3.model_validate(
        request.approval_record.model_dump(by_alias=True, mode="json")
    )
    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=generation,
            handoff_repository=_InMemoryHandoffRepository(batch),
            approval_port=InMemoryApprovalRecordPortV3(records={approval.approval_id: approval}),
            model="fixed-model-v3",
            max_retries=0,
        )
    )
    static = validate_sql_candidate_v3(
        ValidateSqlCandidateRequestV3.model_validate(
            {
                "schemaVersion": "1.0.0",
                "generationRequest": generation.model_dump(by_alias=True, mode="json"),
                "candidate": candidate.model_dump(by_alias=True, mode="json"),
            }
        )
    )
    assert static.status == "blocked"
    codes = {item.code for item in static.issues}
    assert "SQL_LIMIT" in codes or "SQL_ROW_CLIP" in codes


def test_integer_literal_when_is_rejected() -> None:
    request = _prepare_encoding_request()
    request, batch = _align_batch_hash(request)
    report = resolve_metadata_v3(request)
    generation = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": request.model_dump(by_alias=True, mode="json"),
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    sql = (
        "SELECT CASE t.synthetic_value WHEN 0 THEN 'yes' WHEN 1 THEN 'no' "
        "ELSE NULL END AS fact_value FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="enc-int",
                model="fixed-model-v3",
                content=_provider_content(request, sql, "rowset"),
            )
        ]
    )
    approval = ApprovalRecordV3.model_validate(
        request.approval_record.model_dump(by_alias=True, mode="json")
    )
    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=generation,
            handoff_repository=_InMemoryHandoffRepository(batch),
            approval_port=InMemoryApprovalRecordPortV3(records={approval.approval_id: approval}),
            model="fixed-model-v3",
            max_retries=0,
        )
    )
    static = validate_sql_candidate_v3(
        ValidateSqlCandidateRequestV3.model_validate(
            {
                "schemaVersion": "1.0.0",
                "generationRequest": generation.model_dump(by_alias=True, mode="json"),
                "candidate": candidate.model_dump(by_alias=True, mode="json"),
            }
        )
    )
    assert static.status == "blocked"
    codes = {item.code for item in static.issues}
    assert "SQL_ENCODING_CASE" in codes or "SQL_ENCODING_ARMS" in codes


def test_rule_version_change_does_not_reuse_old_hashes() -> None:
    request_a = _prepare_encoding_request()
    wire_b = request_a.model_dump(by_alias=True, mode="json")
    new_version = "SYNTH_RULE_SET@20260916T000000000000Z-bbbbbbbbbbbb"
    fact_code = wire_b["bindingRequest"]["fact"]["factCode"]
    new_request_id = f"{new_version}#{fact_code}"
    wire_b["bindingRequest"]["ruleRef"]["ruleVersion"] = new_version
    wire_b["bindingRequest"]["requestId"] = new_request_id
    wire_b["handoffClosure"]["ruleVersion"] = new_version
    wire_b["handoffClosure"]["requestId"] = new_request_id
    wire_b["projectContext"]["ruleRef"]["ruleVersion"] = new_version
    wire_b["projectContext"]["requestIds"] = [new_request_id]
    wire_b["projectContext"]["contextVersion"] = 3
    wire_b["approvalRecord"]["contextRef"]["contextVersion"] = 3
    for item in wire_b["projectContext"]["fieldBindingAuthorizations"]:
        item["requestId"] = new_request_id
    for item in wire_b["projectContext"]["entityKeyAuthorizations"]:
        item["requestId"] = new_request_id
    for item in wire_b["projectContext"]["valueEncodingBindings"]:
        item["requestId"] = new_request_id
    for item in wire_b["projectContext"]["resultSemanticsBindings"]:
        item["requestId"] = new_request_id
    request_b = _reclose_all_hashes_from_wire(wire_b)
    report_a = resolve_metadata_v3(request_a)
    report_b = resolve_metadata_v3(request_b)
    assert report_a.status == "metadataResolved"
    assert report_b.status == "metadataResolved"
    assert report_a.handoff_refs.payload_sha256 != report_b.handoff_refs.payload_sha256
    assert report_a.context_ref.sha256 != report_b.context_ref.sha256
    assert report_a.usage_traceability_sha256 == report_b.usage_traceability_sha256


def test_new_usage_does_not_reuse_old_coverage() -> None:
    request = _prepare_encoding_request()
    request, batch = _align_batch_hash(request)
    report = resolve_metadata_v3(request)
    generation = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": request.model_dump(by_alias=True, mode="json"),
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="enc-usage",
                model="fixed-model-v3",
                content=_provider_content(request, _case_sql(), "rowset"),
            )
        ]
    )
    approval = ApprovalRecordV3.model_validate(
        request.approval_record.model_dump(by_alias=True, mode="json")
    )
    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=generation,
            handoff_repository=_InMemoryHandoffRepository(batch),
            approval_port=InMemoryApprovalRecordPortV3(records={approval.approval_id: approval}),
            model="fixed-model-v3",
            max_retries=0,
        )
    )
    original_hash = candidate.content_sha256
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["declaredUsageCoverage"][0]["conditionId"] = "other-condition"
    wire["contentSha256"] = "0" * 64
    from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3

    tampered = SqlTemplateCandidateV3.model_validate(wire)
    tampered_dump = tampered.model_dump(by_alias=True, mode="json")
    tampered_dump["contentSha256"] = canonical_content_sha256(tampered)
    tampered = SqlTemplateCandidateV3.model_validate(tampered_dump)
    static = validate_sql_candidate_v3(
        ValidateSqlCandidateRequestV3.model_validate(
            {
                "schemaVersion": "1.0.0",
                "generationRequest": generation.model_dump(by_alias=True, mode="json"),
                "candidate": tampered.model_dump(by_alias=True, mode="json"),
            }
        )
    )
    assert static.status == "blocked"
    assert any(item.code == "USAGE_COVERAGE" for item in static.issues)
    assert original_hash != tampered.content_sha256


def test_old_scalar_candidate_schema_still_readable() -> None:
    from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3

    req_wire = valid_resolve_metadata_request_v3_wire()
    report = resolve_metadata_v3(ResolveMetadataRequestV3.model_validate(req_wire))
    assert report.status == "metadataResolved"
    # Existing 3.0.0 scalar contract remains constructible after the rowset addition.
    sample = {
        "schemaVersion": "3.0.0",
        "templateCode": "SYNTHETIC_V3",
        "status": "candidate",
        "executable": False,
        "reviewStatus": "pending",
        "ruleRef": req_wire["bindingRequest"]["ruleRef"],
        "requestRef": {
            "requestId": req_wire["bindingRequest"]["requestId"],
            "payloadSha256": req_wire["handoffClosure"]["payloadSha256"],
        },
        "projectRef": {"projectId": "proj-1", "projectVersion": 1},
        "resolutionRef": {
            "reportSha256": canonical_sha256(report),
            "contextRef": report.context_ref.model_dump(by_alias=True, mode="json"),
            "metadataSnapshotRef": report.snapshot_ref.model_dump(by_alias=True, mode="json"),
            "authorizationPolicyVersion": req_wire["projectContext"]["authorizationPolicyVersion"],
        },
        "handoffRefs": report.handoff_refs.model_dump(by_alias=True, mode="json"),
        "generationInputSha256": "a" * 64,
        "factRef": {
            "factCode": req_wire["bindingRequest"]["fact"]["factCode"],
            "factKind": req_wire["bindingRequest"]["fact"]["factKind"],
            "dataType": req_wire["bindingRequest"]["fact"]["dataType"],
            "grain": req_wire["bindingRequest"]["fact"]["grain"],
        },
        "dialect": "sqlserver",
        "sqlTemplate": (
            "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey"
        ),
        "parameters": [
            {
                "name": "syntheticKey",
                "dataType": "string",
                "required": True,
                "source": "fact.parameters.syntheticKey",
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
                "stage": item["stage"],
                "ruleCode": item["ruleCode"],
                "priority": item["priority"],
                "conditionId": item["conditionId"],
                "conditionPath": item["conditionPath"],
                "outcome": item["outcome"],
            }
            for item in req_wire["bindingRequest"]["usages"]
        ],
        "usageTraceabilitySha256": report.usage_traceability_sha256,
        "assumptions": [],
        "warnings": ["候选 SQL 未通过 AST、安全门禁、受限验证和人工审核，不得执行。"],
        "provenance": {
            "provider": "fixed-offline-v3",
            "model": "fixed-model-v3",
            "responseModel": "fixed-model-v3",
            "promptVersion": "sqlserver-fact-candidate-v3.1",
            "providerRequestId": "old-1",
            "systemFingerprint": None,
            "attemptCount": 1,
            "maxTokens": 4096,
            "responseFormat": "json_object",
        },
        "contentSha256": "b" * 64,
    }
    candidate = SqlTemplateCandidateV3.model_validate(sample)
    assert candidate.schema_version == "3.0.0"
    assert candidate.result.cardinality == "scalar"
    sample["result"]["cardinality"] = "rowset"
    from pydantic import ValidationError

    try:
        SqlTemplateCandidateV3.model_validate(sample)
        raise AssertionError("3.0.0 must reject rowset")
    except ValidationError:
        pass
