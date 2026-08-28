from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any

from release_sql_bot.application.binding_intake_v2 import analyze_binding_gaps_v2
from release_sql_bot.domain.fact_bindings_v2 import FactBindingRequestV2

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fact-binding-request-2.0.0.synthetic-blocked.json"


def _payload() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _analyze(payload: dict[str, Any]):
    return analyze_binding_gaps_v2(FactBindingRequestV2.model_validate(payload))


def _metadata_ready_payload() -> dict[str, Any]:
    payload = _payload()
    query = payload["queryRequirements"]
    query["entity"]["keyParameters"] = ["projectId"]
    query["entity"]["keyResolutionStatus"] = "candidate"
    query["fields"][0]["sourceCandidate"] = {
        "relationName": "synthetic_report_amounts",
        "fieldName": "total_amount",
        "relationActive": True,
        "reviewStatus": "candidate",
    }
    query["fields"][0]["resolutionStatus"] = "candidate"
    query["fields"][1]["sourceCandidate"] = {
        "relationName": "synthetic_report_amounts",
        "fieldName": "project_id",
        "relationActive": True,
        "reviewStatus": "candidate",
    }
    query["fields"][1]["resolutionStatus"] = "candidate"
    query["filters"]["items"][0]["resolutionStatus"] = "candidate"
    query["filters"]["completeness"] = "complete"
    query["aggregation"] = {
        "mode": "precomputed",
        "function": None,
        "inputFieldIds": [],
        "groupByFieldIds": [],
        "distinct": None,
        "resolutionStatus": "candidate",
        "evidenceIds": ["fact.declaration"],
    }
    query["timeRange"] = {
        "mode": "none",
        "timeFieldId": None,
        "start": None,
        "end": None,
        "timezone": None,
        "resolutionStatus": "notApplicable",
        "evidenceIds": ["fact.declaration"],
    }
    payload["mappingCandidate"] = {
        "factCode": "report.total_amount",
        "mappingStatus": "mapped",
        "viewName": "synthetic_report_amounts",
        "viewField": "total_amount",
        "viewActive": True,
        "reviewStatus": "candidate",
        "note": "仅供元数据复核的合成候选。",
    }
    payload["uncertainties"] = []
    return payload


def test_all_six_upstream_blocking_classifications_are_preserved() -> None:
    report = _analyze(_payload())

    assert report.status == "blocked"
    assert report.executable is False
    assert {issue.code for issue in report.blocking_issues} == {
        "ENTITY_KEY_UNRESOLVED",
        "VALUE_FIELD_UNRESOLVED",
        "FILTER_FIELD_UNRESOLVED",
        "FILTER_SET_INCOMPLETE",
        "AGGREGATION_UNRESOLVED",
        "TIME_RANGE_UNRESOLVED",
    }
    assert {issue.owner.value for issue in report.blocking_issues} <= {
        "businessRuleReview",
        "metadataReview",
        "sqlBot",
    }


def test_resolved_semantics_only_become_ready_for_metadata_resolution() -> None:
    report = _analyze(_metadata_ready_payload())
    serialized = report.model_dump_json(by_alias=True)

    assert report.status == "readyForMetadataResolution"
    assert report.executable is False
    assert report.blocking_issues == ()
    assert "readyForGeneration" not in serialized
    assert {warning.code for warning in report.warnings} == {
        "MAPPING_CANDIDATE_NOT_AUTHORIZATION",
        "PARSER_PROVENANCE_NOT_AUTHORIZATION",
        "SOURCE_CANDIDATE_NOT_AUTHORIZATION",
    }


def test_contract_and_rule_versions_are_checked_by_readiness() -> None:
    payload = _payload()
    payload["contractVersion"] = "1.0.0"
    payload["ruleRef"]["schemaVersion"] = "1.0.0"

    report = _analyze(payload)

    assert {issue.code for issue in report.blocking_issues} >= {
        "BINDING_CONTRACT_UNSUPPORTED",
        "RULE_SCHEMA_UNSUPPORTED",
    }


def test_derived_fact_and_fixed_sqlserver_safety_flags_are_blocked() -> None:
    payload = _payload()
    payload["fact"]["factKind"] = "derived"
    payload["targetDialect"] = "postgres"
    payload["requiresMetadataSnapshot"] = False
    payload["tempTableAllowed"] = True

    report = _analyze(payload)

    assert {issue.code for issue in report.blocking_issues} >= {
        "DERIVED_FACT_NOT_SQL_BOUND",
        "DIALECT_UNSUPPORTED",
        "METADATA_SNAPSHOT_REQUIRED",
        "TEMP_TABLE_DISABLED",
    }


def test_forged_request_id_is_blocked() -> None:
    payload = _payload()
    payload["requestId"] = "SYNTHETIC_REPORT_001@forged#report.total_amount"

    report = _analyze(payload)

    assert "REQUEST_ID_MISMATCH" in {issue.code for issue in report.blocking_issues}


def test_unknown_evidence_id_is_blocked_without_removing_it() -> None:
    payload = _payload()
    payload["usages"][0]["evidenceIds"] = ["condition.unknown"]

    request = FactBindingRequestV2.model_validate(payload)
    report = analyze_binding_gaps_v2(request)

    issue = next(issue for issue in report.blocking_issues if issue.code == "EVIDENCE_ID_UNKNOWN")
    assert issue.evidence_ids == ("condition.unknown",)
    assert request.usages[0].evidence_ids == ["condition.unknown"]


def test_dangling_filter_field_id_is_blocked() -> None:
    payload = _payload()
    payload["queryRequirements"]["filters"]["items"][0]["fieldId"] = "missing.field"

    report = _analyze(payload)

    assert "FIELD_REFERENCE_UNKNOWN" in {issue.code for issue in report.blocking_issues}


def test_aggregation_and_time_range_field_references_must_close() -> None:
    payload = _metadata_ready_payload()
    query = payload["queryRequirements"]
    query["aggregation"] = {
        "mode": "compute",
        "function": "sum",
        "inputFieldIds": ["missing.amount"],
        "groupByFieldIds": ["missing.group"],
        "distinct": False,
        "resolutionStatus": "declared",
        "evidenceIds": ["fact.declaration"],
    }
    query["timeRange"] = {
        "mode": "asOf",
        "timeFieldId": "missing.time",
        "start": None,
        "end": {
            "kind": "literal",
            "parameterName": None,
            "value": "2026-08-27",
            "inclusive": True,
        },
        "timezone": "Asia/Shanghai",
        "resolutionStatus": "declared",
        "evidenceIds": ["fact.declaration"],
    }

    report = _analyze(payload)
    field_issues = [
        issue for issue in report.blocking_issues if issue.code == "FIELD_REFERENCE_UNKNOWN"
    ]

    assert {issue.field_path for issue in field_issues} == {
        "/queryRequirements/aggregation",
        "/queryRequirements/timeRange/timeFieldId",
    }


def test_missing_blocking_uncertainty_is_reported_but_not_synthesized_into_request() -> None:
    payload = _payload()
    payload["uncertainties"] = [
        item for item in payload["uncertainties"] if item["code"] != "FILTER_SET_INCOMPLETE"
    ]

    request = FactBindingRequestV2.model_validate(payload)
    report = analyze_binding_gaps_v2(request)

    missing = [
        issue for issue in report.blocking_issues if issue.code == "BLOCKING_UNCERTAINTY_MISSING"
    ]
    assert len(missing) == 1
    assert "FILTER_SET_INCOMPLETE" in missing[0].message
    assert all(item.code != "FILTER_SET_INCOMPLETE" for item in request.uncertainties)


def test_warning_uncertainty_stays_warning_and_does_not_grant_authorization() -> None:
    payload = _metadata_ready_payload()
    payload["uncertainties"] = [
        {
            "uncertaintyId": "source.review",
            "code": "SOURCE_REVIEW_REQUIRED",
            "category": "source",
            "fieldPath": "/mappingCandidate",
            "impact": "warning",
            "reason": "合成候选仍需元数据审核。",
            "resolutionHint": "与受治理快照交叉复核。",
            "evidenceIds": ["fact.declaration"],
        }
    ]

    report = _analyze(payload)

    assert report.status == "readyForMetadataResolution"
    assert "SOURCE_REVIEW_REQUIRED" in {warning.code for warning in report.warnings}
    assert "SOURCE_REVIEW_REQUIRED" not in {issue.code for issue in report.blocking_issues}


def test_warning_cannot_replace_required_blocking_uncertainty() -> None:
    payload = _payload()
    uncertainty = next(
        item for item in payload["uncertainties"] if item["code"] == "TIME_RANGE_UNRESOLVED"
    )
    uncertainty["impact"] = "warning"

    report = _analyze(payload)

    assert "TIME_RANGE_UNRESOLVED" in {warning.code for warning in report.warnings}
    assert any(
        issue.code == "BLOCKING_UNCERTAINTY_MISSING" and "TIME_RANGE_UNRESOLVED" in issue.message
        for issue in report.blocking_issues
    )


def test_request_payload_rule_and_source_hashes_are_deterministic() -> None:
    payload = _metadata_ready_payload()
    request = FactBindingRequestV2.model_validate(payload)
    first = analyze_binding_gaps_v2(request)
    second = analyze_binding_gaps_v2(request)
    canonical_payload = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    canonical_rule = json.dumps(
        payload["ruleRef"],
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )

    assert first == second
    assert first.hashes.request_sha256 == sha256(payload["requestId"].encode("utf-8")).hexdigest()
    assert first.hashes.payload_sha256 == sha256(canonical_payload.encode("utf-8")).hexdigest()
    assert first.hashes.rule_sha256 == sha256(canonical_rule.encode("utf-8")).hexdigest()
    assert first.hashes.source_sha256 == payload["provenance"]["source"]["sha256"]


def test_analysis_does_not_mutate_the_v2_payload() -> None:
    payload = _payload()
    original = deepcopy(payload)

    _analyze(payload)

    assert payload == original
