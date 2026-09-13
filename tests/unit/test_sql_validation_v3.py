"""M4 V3 SQL AST static validation tests.

All tests use synthetic fixtures. No network, no .env,
MongoDB/SQL Server/online model.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3
from release_sql_bot.application.canonical import canonical_content_sha256, canonical_sha256
from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3
from release_sql_bot.application.ports.approval_records_v3 import (
    InMemoryApprovalRecordPortV3,
)
from release_sql_bot.application.ports.candidates import CandidateModelResponse
from release_sql_bot.application.sql_validation_v3 import validate_sql_candidate_v3
from release_sql_bot.domain.fact_binding_handoffs_v3 import (
    StoredFactBindingHandoffBatchV3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3
from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3
from release_sql_bot.domain.sql_validation_v3 import (
    SqlStaticValidationReportV3,
    ValidateSqlCandidateRequestV3,
)
from tests.fakes import FixedCandidateModelProvider
from tests.v3_metadata_support import (
    valid_handoff_closure_v3_wire,
    valid_resolve_metadata_request_v3_wire,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_two_key_request_wire():
    """Build a valid request wire with two entity keys."""
    import json

    from tests.v3_metadata_support import (
        _reclose_all_hashes_from_wire,
        valid_resolve_metadata_request_v3_wire,
    )

    req_wire = json.loads(json.dumps(valid_resolve_metadata_request_v3_wire()))
    binding_req = req_wire["bindingRequest"]

    binding_req["queryRequirements"]["fields"].append(
        {
            "fieldId": "secondKeyField",
            "role": "entityKey",
            "logicalName": "second_entity_key",
            "dataType": "string",
            "required": True,
            "evidenceIds": ["ev-fact-declaration"],
        }
    )
    binding_req["fact"]["parameters"].append(
        {
            "name": "secondKey",
            "role": "entityKey",
            "dataType": "string",
            "required": True,
            "description": "second key",
        }
    )
    binding_req["queryRequirements"]["entity"]["keyParameters"] = ["syntheticKey", "secondKey"]
    binding_req["queryRequirements"]["entity"]["evidenceIds"] = ["ev-fact-declaration"]
    req_wire["metadataSnapshot"]["relations"][0]["columns"].append(
        {
            "columnName": "second_key",
            "sqlType": "nvarchar(100)",
            "nullable": False,
        }
    )
    req_wire["projectContext"]["columnGrants"].append(
        {
            "grantId": "colgrant-secondkey",
            "relationGrantId": "relgrant-1",
            "columnName": "second_key",
        }
    )
    req_wire["projectContext"]["fieldBindingAuthorizations"].append(
        {
            "authorizationId": "fba-secondkey",
            "requestId": binding_req["requestId"],
            "fieldId": "secondKeyField",
            "role": "entityKey",
            "columnGrantId": "colgrant-secondkey",
        }
    )
    req_wire["projectContext"]["entityKeyAuthorizations"].append(
        {
            "authorizationId": "eka-2",
            "requestId": binding_req["requestId"],
            "parameterName": "secondKey",
            "fieldId": "secondKeyField",
            "columnGrantId": "colgrant-secondkey",
        }
    )

    _reclose_all_hashes_from_wire(req_wire)
    return req_wire


def _build_candidate_payload() -> dict[str, Any]:
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

    return {
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


def _build_closure_wire() -> dict[str, Any]:
    closure_wire = valid_handoff_closure_v3_wire()
    binding_model = FactBindingRequestV3.model_validate(closure_wire["payload"])
    payload_hash = canonical_sha256(binding_model)
    closure_wire["payloadSha256"] = payload_hash
    closure_wire["batchSha256"] = canonical_sha256(
        [{"requestId": closure_wire["requestId"], "payloadSha256": payload_hash}]
    )
    return closure_wire


def _build_handoff_batch() -> StoredFactBindingHandoffBatchV3:
    closure_wire = _build_closure_wire()
    payload_model = FactBindingRequestV3.model_validate(closure_wire["payload"])
    request_id = closure_wire["requestId"]
    rule_version = closure_wire["ruleVersion"]
    fact_code = closure_wire["factCode"]
    payload_sha256 = closure_wire["payloadSha256"]
    batch_sha256 = closure_wire["batchSha256"]
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)

    request_dict = {
        "request_id": request_id,
        "rule_version": rule_version,
        "fact_code": fact_code,
        "contract_version": "3.0.0",
        "payload_sha256": payload_sha256,
        "created_at": now,
        "payload": payload_model.model_dump(by_alias=True, mode="json"),
    }
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


def _build_valid_candidate() -> SqlTemplateCandidateV3:
    import asyncio

    batch = _build_handoff_batch()

    class _FakeRepo:
        async def get_batch_by_rule_version(self, rv):
            return batch

    req_wire = valid_resolve_metadata_request_v3_wire()
    closure = _build_closure_wire()
    req_wire["handoffClosure"]["batchSha256"] = closure["batchSha256"]

    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    approval_port = InMemoryApprovalRecordPortV3(records={approval.approval_id: approval})

    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-001",
                model="fixed-model-v3",
                content=_synthetic_provider_content(),
            )
        ]
    )

    report_wire = _build_resolution_report_wire()
    # Build a candidate by constructing the generation request properly
    # We need a minimal valid candidate for testing - use the real generation service
    # but with a synthetic provider that returns our payload
    from release_sql_bot.domain.sql_candidates_v3 import GenerateSqlCandidateRequestV3

    gen_req = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": req_wire,
            "resolutionReport": report_wire,
        }
    )

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=gen_req,
            handoff_repository=_FakeRepo(),
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )
    return candidate


def _synthetic_provider_content() -> str:
    import json

    return json.dumps(_build_candidate_payload(), ensure_ascii=False, sort_keys=True)


def _build_resolution_report_wire() -> dict[str, Any]:
    from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3

    req_wire = valid_resolve_metadata_request_v3_wire()
    closure = _build_closure_wire()
    req_wire["handoffClosure"]["batchSha256"] = closure["batchSha256"]
    request = ResolveMetadataRequestV3.model_validate(req_wire)
    report = resolve_metadata_v3(request)
    return report.model_dump(by_alias=True, mode="json")


def _tamper_sql(sql: str) -> str:
    """Return the SQL for a given tamper mode."""
    mapping = {
        "valid": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey"
        ),
        "drop_predicate": "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t",
        "wrong_param": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :wrongParam"
        ),
        "literal": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = 'hardcoded'"
        ),
        "second_stmt": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey; SELECT 1"
        ),
        "select_into": (
            "SELECT t.synthetic_value AS fact_value INTO #tmp "
            "FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey"
        ),
        "dml": "UPDATE dbo.synthetic_table SET synthetic_value = 1",
        "exec": "EXEC sp_evil",
        "temp": (
            "SELECT t.synthetic_value AS fact_value FROM #tmp t "
            "WHERE t.synthetic_key = :syntheticKey"
        ),
        "cross_db": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM other_db.dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey"
        ),
        "wrong_source": (
            "SELECT t.synthetic_key AS fact_value "
            "FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey"
        ),
        "star": "SELECT * FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey",
        "multi_col": (
            "SELECT t.synthetic_value AS fact_value, t.synthetic_key AS other_col "
            "FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey"
        ),
        "join": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "JOIN dbo.other_table o ON t.synthetic_key = o.key "
            "WHERE t.synthetic_key = :syntheticKey"
        ),
        "subquery": ("SELECT (SELECT MAX(synthetic_value) FROM dbo.synthetic_table) AS fact_value"),
        "agg": "SELECT SUM(t.synthetic_value) AS fact_value FROM dbo.synthetic_table t",
        "union": (
            "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t UNION ALL SELECT 1"
        ),
        "neq": (
            "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key <> :syntheticKey"
        ),
        "self_cmp": (
            "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t "
            "WHERE :syntheticKey = :syntheticKey"
        ),
        "arith": (
            "SELECT t.synthetic_value + t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey"
        ),
        "distinct": (
            "SELECT DISTINCT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey"
        ),
        "cte": (
            "WITH cte AS (SELECT synthetic_value FROM dbo.synthetic_table) "
            "SELECT cte.synthetic_value AS fact_value FROM cte"
        ),
        "extra_pred": (
            "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey AND t.synthetic_value > 0"
        ),
        "like": (
            "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey AND t.synthetic_key LIKE '%a%'"
        ),
        "cast": (
            "SELECT CAST(t.synthetic_value AS varchar(50)) AS fact_value "
            "FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey"
        ),
        "abs": (
            "SELECT ABS(t.synthetic_value) AS fact_value "
            "FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey"
        ),
        "order_by": (
            "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey ORDER BY t.synthetic_key"
        ),
        "row_number": (
            "SELECT ROW_NUMBER() OVER (ORDER BY t.synthetic_key) AS fact_value "
            "FROM dbo.synthetic_table t WHERE t.synthetic_key = :syntheticKey"
        ),
        "dup_binding": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey AND t.synthetic_value = :syntheticKey"
        ),
        "unqualified_col": (
            "SELECT synthetic_value AS fact_value "
            "FROM dbo.synthetic_table WHERE synthetic_key = :syntheticKey"
        ),
        "wrong_qualifier": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE other_alias.synthetic_key = :syntheticKey"
        ),
        "case_mismatch_where": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.SYNTHETIC_KEY = :syntheticKey"
        ),
        "three_part_projection": (
            "SELECT dbo.synthetic_table.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table WHERE synthetic_key=:syntheticKey"
        ),
        "desens_marker": (
            "SELECT marker_db.marker_table.MARKER_COLUMN AS fact_value "
            "FROM dbo.synthetic_table WHERE synthetic_key=:syntheticKey"
        ),
        "dup_same_col": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey AND t.synthetic_key = :syntheticKey"
        ),
        "dup_three_way": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey AND t.synthetic_value = :syntheticKey "
            "AND t.synthetic_key = :syntheticKey"
        ),
    }
    return mapping[sql]


def _recompute_hash(wire: dict[str, Any]) -> dict[str, Any]:
    """Recompute contentSha256 for a self-consistent candidate wire."""
    candidate = SqlTemplateCandidateV3.model_validate(wire)
    wire["contentSha256"] = canonical_content_sha256(candidate)
    return wire


def _build_validation_request(
    candidate: SqlTemplateCandidateV3 | None = None,
    tamper: str | None = None,
) -> ValidateSqlCandidateRequestV3:
    """Build a validation request, optionally tampering with the candidate's SQL."""
    if candidate is None:
        candidate = _build_valid_candidate()

    if tamper and tamper != "valid":
        if tamper == "content_hash":
            # Don't recompute hash - we want it wrong
            wire = candidate.model_dump(by_alias=True, mode="json")
            wire["contentSha256"] = "f" * 64
            candidate = SqlTemplateCandidateV3.model_validate(wire)
        elif tamper == "condition_path_tamper":
            wire = candidate.model_dump(by_alias=True, mode="json")
            wire["declaredUsageCoverage"][0]["conditionPath"] = "/TAMPERED"
            candidate = SqlTemplateCandidateV3.model_validate(wire)
            wire = candidate.model_dump(by_alias=True, mode="json")
            candidate = SqlTemplateCandidateV3.model_validate(_recompute_hash(wire))
        else:
            wire = candidate.model_dump(by_alias=True, mode="json")
            wire["sqlTemplate"] = _tamper_sql(tamper)
            candidate = SqlTemplateCandidateV3.model_validate(_recompute_hash(wire))

    report_wire = _build_resolution_report_wire()
    req_wire = valid_resolve_metadata_request_v3_wire()
    closure = _build_closure_wire()
    req_wire["handoffClosure"]["batchSha256"] = closure["batchSha256"]

    return ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": {
                "schemaVersion": "1.0.0",
                "resolutionRequest": req_wire,
                "resolutionReport": report_wire,
            },
            "candidate": candidate.model_dump(by_alias=True, mode="json"),
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_candidate_passes():
    """Valid synthetic candidate passes static validation."""
    request = _build_validation_request()
    report = validate_sql_candidate_v3(request)

    assert report.status == "passed"
    assert report.executable is False
    assert len(report.issues) == 0
    assert report.inspection is not None
    assert report.inspection.statement_count == 1


def test_candidate_hash_tampering_parser_zero_calls():
    """Tampered candidate content hash: parser not called."""
    request = _build_validation_request(tamper="content_hash")
    report = validate_sql_candidate_v3(request)

    assert report.status == "blocked"
    assert report.inspection is None  # Parser NOT called
    codes = {i.code for i in report.issues}
    assert "CAND_HASH" in codes


def test_missing_predicate_blocked():
    """Missing WHERE clause is blocked at AST level."""
    request = _build_validation_request(tamper="drop_predicate")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None  # Parser WAS called
    codes = {i.code for i in report.issues}
    assert "SQL_KEY_MISSING" in codes


def test_wrong_param_blocked():
    """Using undeclared parameter name is blocked."""
    request = _build_validation_request(tamper="wrong_param")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_PARAM_MISMATCH" in codes or "SQL_KEY_MISSING" in codes


def test_literal_blocked():
    """Using literal value instead of parameter is blocked."""
    request = _build_validation_request(tamper="literal")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_LITERAL" in codes


def test_second_statement_blocked():
    """Multiple statements blocked."""
    request = _build_validation_request(tamper="second_stmt")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    codes = {i.code for i in report.issues}
    assert "SQL_STMT_COUNT" in codes


def test_select_into_blocked():
    request = _build_validation_request(tamper="select_into")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert any(c.startswith("SQL_NODE_FORBIDDEN") or c == "SQL_NODE_FORBIDDEN" for c in codes)


def test_dml_blocked():
    request = _build_validation_request(tamper="dml")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    codes = {i.code for i in report.issues}
    assert "SQL_ROOT" in codes or "SQL_NODE_FORBIDDEN" in codes


def test_exec_blocked():
    request = _build_validation_request(tamper="exec")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    codes = {i.code for i in report.issues}
    assert "SQL_ROOT" in codes or "SQL_NODE_FORBIDDEN" in codes


def test_temp_table_blocked():
    request = _build_validation_request(tamper="temp")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    # #tmp may fail parsing or be rejected as unknown/forbidden object


def test_cross_database_blocked():
    request = _build_validation_request(tamper="cross_db")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"


def test_wrong_fact_value_source_blocked():
    request = _build_validation_request(tamper="wrong_source")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_KEY_WRONG" in codes or "SQL_RES_SRC" in codes


def test_star_blocked():
    request = _build_validation_request(tamper="star")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert any(c.startswith("SQL_NODE_FORBIDDEN") or c == "SQL_NODE_FORBIDDEN" for c in codes)


def test_multi_column_blocked():
    request = _build_validation_request(tamper="multi_col")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_RES_SHAPE" in codes or "SQL_NODE_FORBIDDEN" in codes


def test_join_blocked():
    request = _build_validation_request(tamper="join")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_JOIN" in codes


def test_subquery_blocked():
    request = _build_validation_request(tamper="subquery")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_SUBQUERY" in codes


def test_aggregate_blocked():
    request = _build_validation_request(tamper="agg")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_AGG" in codes


def test_union_blocked():
    request = _build_validation_request(tamper="union")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"


# --- New P0-3 whitelist negative cases ---


def test_neq_blocked():
    request = _build_validation_request(tamper="neq")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_CMP_OP" in codes


def test_self_cmp_blocked():
    request = _build_validation_request(tamper="self_cmp")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_SELF_CMP" in codes or "SQL_CMP_SHAPE" in codes


def test_arithmetic_blocked():
    request = _build_validation_request(tamper="arith")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_ARITH" in codes


def test_distinct_blocked():
    request = _build_validation_request(tamper="distinct")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert any(c.startswith("SQL_NODE_FORBIDDEN") or c == "SQL_NODE_FORBIDDEN" for c in codes)


def test_cte_blocked():
    request = _build_validation_request(tamper="cte")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_CTE" in codes


def test_extra_predicate_blocked():
    """Extra non-entity-key predicate is blocked."""
    request = _build_validation_request(tamper="extra_pred")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert any(
        c in codes
        for c in [
            "SQL_PREDICATE",
            "SQL_KEY_EXTRA",
            "SQL_NODE_FORBIDDEN",
            "SQL_FUNCTION",
            "SQL_LITERAL",
            "SQL_BAD_FEAT",
        ]
    )


def test_like_blocked():
    request = _build_validation_request(tamper="like")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_KEY_EXTRA" in codes or "SQL_NODE_FORBIDDEN" in codes


def test_cast_blocked():
    request = _build_validation_request(tamper="cast")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert any("FORBIDDEN" in c or c.startswith("forbidden") for c in codes)


def test_abs_blocked():
    request = _build_validation_request(tamper="abs")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert any("FORBIDDEN" in c or c.startswith("forbidden") for c in codes)


def test_order_by_blocked():
    request = _build_validation_request(tamper="order_by")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert any(c.startswith("SQL_NODE_FORBIDDEN") or c == "SQL_NODE_FORBIDDEN" for c in codes)


def test_row_number_blocked():
    request = _build_validation_request(tamper="row_number")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert any("FORBIDDEN" in c or c.startswith("forbidden") for c in codes)


# --- Coverage / reference integrity tests ---


def test_condition_path_tamper_blocked():
    """Tampering only conditionPath in coverage is blocked before parser."""
    request = _build_validation_request(tamper="condition_path_tamper")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is None
    codes = {i.code for i in report.issues}
    assert "USAGE_COVERAGE" in codes


def test_extra_usage_blocked():
    """Candidate with extra usage not in request is blocked."""
    candidate = _build_valid_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    second = deepcopy(wire["declaredUsageCoverage"][0])
    second["conditionPath"] = "/rule/stages/eligibility/2"
    wire["declaredUsageCoverage"].append(second)
    candidate = SqlTemplateCandidateV3.model_validate(_recompute_hash(wire))
    request = _build_validation_request(candidate)
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    codes = {i.code for i in report.issues}
    assert "USAGE_COVERAGE" in codes


def test_report_references_correct():
    """Report contains correct traceability references."""
    request = _build_validation_request()
    report = validate_sql_candidate_v3(request)
    assert report.status == "passed"
    assert report.usage_traceability_sha256
    assert report.parser_ref.name == "sqlglot"
    assert report.parser_ref.dialect == "tsql"


def test_preview_output_validates(tmp_path):
    """Generate candidate + report in tmp_path and validate round-trip."""
    import asyncio

    from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3
    from release_sql_bot.application.ports.approval_records_v3 import (
        InMemoryApprovalRecordPortV3,
    )
    from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3

    batch = _build_handoff_batch()

    class _FakeRepo:
        async def get_batch_by_rule_version(self, rv):
            return batch

    req_wire = valid_resolve_metadata_request_v3_wire()
    closure = _build_closure_wire()
    req_wire["handoffClosure"]["batchSha256"] = closure["batchSha256"]
    approval = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    approval_port = InMemoryApprovalRecordPortV3(records={approval.approval_id: approval})

    from release_sql_bot.domain.sql_candidates_v3 import GenerateSqlCandidateRequestV3

    gen_req = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": req_wire,
            "resolutionReport": _build_resolution_report_wire(),
        }
    )

    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-001",
                model="fixed-model-v3",
                content=_synthetic_provider_content(),
            )
        ]
    )

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=gen_req,
            handoff_repository=_FakeRepo(),
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    # Validate
    val_req = ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": gen_req.model_dump(by_alias=True, mode="json"),
            "candidate": candidate.model_dump(by_alias=True, mode="json"),
        }
    )
    report = validate_sql_candidate_v3(val_req)

    # Write outputs
    candidate_path = tmp_path / "v3-candidate.json"
    report_path = tmp_path / "v3-report.json"
    candidate_path.write_text(candidate.model_dump_json(by_alias=True), encoding="utf-8")
    report_path.write_text(report.model_dump_json(by_alias=True), encoding="utf-8")

    # Round-trip validation
    c = SqlTemplateCandidateV3.model_validate_json(candidate_path.read_text(encoding="utf-8"))
    r = SqlStaticValidationReportV3.model_validate_json(report_path.read_text(encoding="utf-8"))

    assert c.status == "candidate"
    assert c.executable is False
    assert r.status == "passed"
    assert r.executable is False
    assert c.content_sha256 == canonical_content_sha256(c)


# ---------------------------------------------------------------------------
# Defect 1: Result projection must be direct column
# ---------------------------------------------------------------------------


def test_eq_projection_blocked():
    """EQ in result projection must be blocked (not a direct column)."""
    candidate = _build_valid_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["sqlTemplate"] = (
        "SELECT (t.synthetic_value = t.synthetic_value) AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )
    wire = _recompute_hash(wire)
    tampered = SqlTemplateCandidateV3.model_validate(wire)
    request = _build_validation_request(tampered)
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    codes = {i.code for i in report.issues}
    assert "SQL_RES_SRC" in codes or "SQL_RES_SHAPE" in codes


# ---------------------------------------------------------------------------
# Defect 2: AND must not be misidentified as function
# ---------------------------------------------------------------------------


def test_and_not_flagged_as_function_inspector():
    """Inspector-level: AND in WHERE must not be flagged as function."""
    from release_sql_bot.application.ports.sql_ast_v3 import (
        OfflineRelationV3,
        SqlGatePolicyV3,
        SqlInspectionRequestV3,
    )
    from release_sql_bot.infrastructure.sql.sqlglot_tsql_v3 import SqlglotTsqlInspectorV3

    schema = (
        OfflineRelationV3(
            "dbo",
            "synthetic_table",
            (
                type("C", (), {"name": "synthetic_value", "sql_type": "int"})(),
                type("C", (), {"name": "synthetic_key", "sql_type": "nvarchar(100)"})(),
                type("C", (), {"name": "second_key", "sql_type": "nvarchar(100)"})(),
            ),
        ),
    )
    req = SqlInspectionRequestV3(
        sql="SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey AND t.second_key = :secondKey",
        dialect="tsql",
        identifier_case_sensitivity="insensitive",
        offline_schema=schema,
        gate_policy=SqlGatePolicyV3(),
    )
    inspector = SqlglotTsqlInspectorV3()
    result = inspector.inspect(req)
    codes = {i.code for i in result.issues}
    assert "SQL_FUNCTION" not in codes, f"AND misidentified: {codes}"
    # Both comparisons must be extracted
    assert len(result.summary.comparisons) == 2, (
        f"Expected 2 comparisons, got {len(result.summary.comparisons)}"
    )


def test_quoted_alias_with_dots_pass():
    """Quoted alias [t.x].column has 2 parts and should be allowed."""
    from release_sql_bot.application.ports.sql_ast_v3 import (
        OfflineRelationV3,
        SqlGatePolicyV3,
        SqlInspectionRequestV3,
    )
    from release_sql_bot.infrastructure.sql.sqlglot_tsql_v3 import SqlglotTsqlInspectorV3

    schema = (
        OfflineRelationV3(
            "dbo",
            "synthetic_table",
            (
                type("C", (), {"name": "synthetic_value", "sql_type": "int"})(),
                type("C", (), {"name": "synthetic_key", "sql_type": "nvarchar(100)"})(),
            ),
        ),
    )
    req = SqlInspectionRequestV3(
        sql="SELECT [t.x].synthetic_value AS fact_value "
        "FROM dbo.synthetic_table AS [t.x] "
        "WHERE [t.x].synthetic_key = :syntheticKey",
        dialect="tsql",
        identifier_case_sensitivity="insensitive",
        offline_schema=schema,
        gate_policy=SqlGatePolicyV3(),
    )
    inspector = SqlglotTsqlInspectorV3()
    result = inspector.inspect(req)
    codes = {i.code for i in result.issues}
    assert "SQL_NODE_FORBIDDEN" not in codes, f"Quoted alias wrongly blocked: {codes}"
    assert len(result.summary.comparisons) == 1


def test_multi_part_qualifier_blocked_inspector():
    """Inspector-level: db.table.column (3+ parts) must be blocked."""
    from release_sql_bot.application.ports.sql_ast_v3 import (
        OfflineRelationV3,
        SqlGatePolicyV3,
        SqlInspectionRequestV3,
    )
    from release_sql_bot.infrastructure.sql.sqlglot_tsql_v3 import SqlglotTsqlInspectorV3

    schema = (
        OfflineRelationV3(
            "dbo",
            "synthetic_table",
            (
                type("C", (), {"name": "synthetic_value", "sql_type": "int"})(),
                type("C", (), {"name": "synthetic_key", "sql_type": "nvarchar(100)"})(),
            ),
        ),
    )
    req = SqlInspectionRequestV3(
        sql="SELECT dbo.t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey",
        dialect="tsql",
        identifier_case_sensitivity="insensitive",
        offline_schema=schema,
        gate_policy=SqlGatePolicyV3(),
    )
    inspector = SqlglotTsqlInspectorV3()
    result = inspector.inspect(req)
    assert not any(rc.source_column for rc in result.summary.result_columns), (
        f"Multi-part qualifier should not resolve: {result.summary.result_columns}"
    )


def test_full_v3_two_entity_keys_and_pass():
    """Full V3 entry: two entity keys with AND should pass."""
    import asyncio

    # Start from a valid request wire
    import json
    from datetime import datetime

    from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3
    from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3
    from release_sql_bot.application.ports.approval_records_v3 import InMemoryApprovalRecordPortV3
    from release_sql_bot.application.ports.candidates import CandidateModelResponse
    from release_sql_bot.domain.fact_binding_handoffs_v3 import StoredFactBindingHandoffBatchV3
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
    from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3
    from release_sql_bot.domain.sql_candidates_v3 import GenerateSqlCandidateRequestV3
    from release_sql_bot.domain.sql_validation_v3 import ValidateSqlCandidateRequestV3
    from tests.fakes import FixedCandidateModelProvider
    from tests.v3_metadata_support import _reclose_all_hashes_from_wire

    req_wire = json.loads(json.dumps(_build_two_key_request_wire()))
    binding_req = req_wire["bindingRequest"]

    # Build closure from the actual binding request FIRST
    binding_model = FactBindingRequestV3.model_validate(binding_req)
    payload_hash = canonical_sha256(binding_model)
    closure_wire = {
        "schemaVersion": "1.0.0",
        "ruleVersion": binding_req["ruleRef"]["ruleVersion"],
        "requestId": binding_req["requestId"],
        "factCode": binding_req["fact"]["factCode"],
        "payloadSha256": payload_hash,
        "batchSha256": canonical_sha256(
            [{"requestId": binding_req["requestId"], "payloadSha256": payload_hash}]
        ),
        "contractSchemaId": "urn:rulereader:fact-binding-request:3.0.0",
        "contractSchemaSha256": "2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566",
        "intakeStatus": "readyForMetadataResolution",
        "payload": binding_req,
    }
    req_wire["handoffClosure"] = closure_wire

    # Now reclose all hashes (closure is already updated)
    request = _reclose_all_hashes_from_wire(req_wire)
    report = resolve_metadata_v3(request)
    assert report.status == "metadataResolved", f"M2 failed: {[i.code for i in report.issues]}"

    # Build provider payload
    fact = binding_req["fact"]
    parameters = [
        {
            "name": p["name"],
            "dataType": p["dataType"],
            "required": p["required"],
            "source": f"fact.parameters.{p['name']}",
        }
        for p in fact["parameters"]
    ]
    declared_usage = [
        {
            "stage": u["stage"],
            "ruleCode": u["ruleCode"],
            "priority": u["priority"],
            "conditionId": u["conditionId"],
            "conditionPath": u["conditionPath"],
            "outcome": u["outcome"],
        }
        for u in binding_req["usages"]
    ]
    payload = {
        "templateCode": "SYNTHETIC_V2KEY",
        "sqlTemplate": (
            "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t "
            "WHERE t.synthetic_key = :syntheticKey AND t.second_key = :secondKey"
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
        "declaredUsageCoverage": declared_usage,
        "assumptions": [],
        "warnings": [],
    }
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)
    request_dict = {
        "request_id": closure_wire["requestId"],
        "rule_version": closure_wire["ruleVersion"],
        "fact_code": closure_wire["factCode"],
        "contract_version": "3.0.0",
        "payload_sha256": closure_wire["payloadSha256"],
        "created_at": now,
        "payload": binding_model.model_dump(by_alias=True, mode="json"),
    }
    batch = StoredFactBindingHandoffBatchV3.model_validate(
        {
            "_id": closure_wire["ruleVersion"],
            "rule_version": closure_wire["ruleVersion"],
            "contract_version": "3.0.0",
            "request_count": 1,
            "request_ids": [closure_wire["requestId"]],
            "batch_sha256": closure_wire["batchSha256"],
            "created_at": now,
            "requests": [request_dict],
        }
    )

    class _FakeRepo:
        async def get_batch_by_rule_version(self, rv):
            return batch

    approval_rec = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    approval_port = InMemoryApprovalRecordPortV3(records={approval_rec.approval_id: approval_rec})
    import json as _json

    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-001",
                model="fixed-model-v3",
                content=_json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        ]
    )
    gen_req = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": req_wire,
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=gen_req,
            handoff_repository=_FakeRepo(),
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    # Validate
    val_req = ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": gen_req.model_dump(by_alias=True, mode="json"),
            "candidate": candidate.model_dump(by_alias=True, mode="json"),
        }
    )
    val_report = validate_sql_candidate_v3(val_req)

    assert val_report.status == "passed", (
        f"Expected passed, got {val_report.status}: "
        f"{[(i.code, i.message) for i in val_report.issues]}"
    )
    assert len(val_report.issues) == 0
    assert len(val_report.inspection.comparisons) == 2
    params_bound = {c.right_parameter for c in val_report.inspection.comparisons}
    assert params_bound == {"syntheticKey", "secondKey"}


# ---------------------------------------------------------------------------
# WHERE structure + column qualifier regression tests
# ---------------------------------------------------------------------------


def test_where_bare_column_blocked():
    """WHERE key=:p AND t.synthetic_value (bare column) blocked."""
    candidate = _build_valid_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["sqlTemplate"] = (
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey AND t.synthetic_value"
    )
    wire = _recompute_hash(wire)
    tampered = SqlTemplateCandidateV3.model_validate(wire)
    request = _build_validation_request(tampered)
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_WHERE_INVALID" in codes


def test_where_bare_placeholder_blocked():
    """WHERE key=:p AND :p (bare placeholder) blocked."""
    candidate = _build_valid_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["sqlTemplate"] = (
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey AND :syntheticKey"
    )
    wire = _recompute_hash(wire)
    tampered = SqlTemplateCandidateV3.model_validate(wire)
    request = _build_validation_request(tampered)
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_WHERE_INVALID" in codes


def test_where_bare_column_with_parens_blocked():
    """WHERE key=:p AND (t.synthetic_value) (parens) blocked."""
    candidate = _build_valid_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["sqlTemplate"] = (
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey AND (t.synthetic_value)"
    )
    wire = _recompute_hash(wire)
    tampered = SqlTemplateCandidateV3.model_validate(wire)
    request = _build_validation_request(tampered)
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_WHERE_INVALID" in codes


def test_where_cross_db_projection_blocked():
    """Projection with db.prefix column reference blocked."""
    candidate = _build_valid_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["sqlTemplate"] = (
        "SELECT other_db.dbo.t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )
    wire = _recompute_hash(wire)
    tampered = SqlTemplateCandidateV3.model_validate(wire)
    request = _build_validation_request(tampered)
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None


def test_where_db_qualified_column_blocked():
    """WHERE column with dbo.prefix blocked."""
    candidate = _build_valid_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["sqlTemplate"] = (
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE dbo.t.synthetic_key = :syntheticKey"
    )
    wire = _recompute_hash(wire)
    tampered = SqlTemplateCandidateV3.model_validate(wire)
    request = _build_validation_request(tampered)
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None


def test_three_part_projection_blocked():
    """Three-part projection dbo.table.column must be blocked via full V3."""
    candidate = _build_valid_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["sqlTemplate"] = (
        "SELECT dbo.synthetic_table.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table WHERE synthetic_key=:syntheticKey"
    )
    wire = _recompute_hash(wire)
    tampered = SqlTemplateCandidateV3.model_validate(wire)
    request = _build_validation_request(tampered)
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_COLUMN_QUALIFIER" in codes


def test_quoted_alias_full_v3_pass():
    """Quoted alias [t.x].column should pass via full V3."""
    candidate = _build_valid_candidate()
    wire = candidate.model_dump(by_alias=True, mode="json")
    wire["sqlTemplate"] = (
        "SELECT [t.x].synthetic_value AS fact_value "
        "FROM dbo.synthetic_table AS [t.x] "
        "WHERE [t.x].synthetic_key = :syntheticKey"
    )
    wire = _recompute_hash(wire)
    tampered = SqlTemplateCandidateV3.model_validate(wire)
    request = _build_validation_request(tampered)
    report = validate_sql_candidate_v3(request)
    assert report.status == "passed", (
        f"Expected passed, got {report.status}: {[(i.code, i.message) for i in report.issues]}"
    )
    assert len(report.issues) == 0
    # Verify projection source
    assert len(report.inspection.result_columns) == 1
    rc = report.inspection.result_columns[0]
    assert rc.alias == "fact_value"
    assert rc.source_column == "synthetic_value"
    # Verify entity key binding
    assert len(report.inspection.comparisons) == 1
    comp = report.inspection.comparisons[0]
    assert comp.right_parameter == "syntheticKey"
    assert comp.left_column == "synthetic_key"


# ---------------------------------------------------------------------------
# M4 wrap-up: duplicate binding, case sensitivity, unqualified/wrong-qualifier
# ---------------------------------------------------------------------------


def test_duplicate_param_binding_blocked():
    """Same parameter bound to two different columns is blocked."""
    request = _build_validation_request(tamper="dup_binding")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_KEY_DUPLICATE" in codes


def test_single_table_unqualified_column_pass():
    """Single-table query with unqualified column name passes (insensitive)."""
    request = _build_validation_request(tamper="unqualified_col")
    report = validate_sql_candidate_v3(request)
    assert report.status == "passed", (
        f"Expected passed, got {report.status}: {[(i.code, i.message) for i in report.issues]}"
    )
    assert len(report.issues) == 0
    assert len(report.inspection.comparisons) == 1
    comp = report.inspection.comparisons[0]
    assert comp.left_column == "synthetic_key"
    assert comp.right_parameter == "syntheticKey"


def test_wrong_qualifier_blocked():
    """Column qualified with wrong alias (not matching FROM) is blocked."""
    request = _build_validation_request(tamper="wrong_qualifier")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    # The wrong qualifier cannot resolve, so the comparison shape is invalid
    assert any(c in codes for c in ["SQL_KEY_WRONG", "SQL_KEY_MISSING", "SQL_CMP_SHAPE"])


def test_case_insensitive_mixed_case_where_pass():
    """Insensitive mode: uppercase column in WHERE still resolves."""
    request = _build_validation_request(tamper="case_mismatch_where")
    report = validate_sql_candidate_v3(request)
    assert report.status == "passed", (
        f"Expected passed, got {report.status}: {[(i.code, i.message) for i in report.issues]}"
    )
    assert len(report.issues) == 0


def test_case_sensitive_mismatch_blocked():
    """Sensitive mode: uppercase column in WHERE must not match lowercase snapshot.

    Inspector-level test: the physical column resolution uses
    identifier_case_sensitivity from the inspection request, so a sensitive
    mismatch means the column cannot resolve to the physical triple.
    """
    from release_sql_bot.application.ports.sql_ast_v3 import (
        OfflineRelationV3,
        SqlGatePolicyV3,
        SqlInspectionRequestV3,
    )
    from release_sql_bot.infrastructure.sql.sqlglot_tsql_v3 import SqlglotTsqlInspectorV3

    schema = (
        OfflineRelationV3(
            "dbo",
            "synthetic_table",
            (
                type("C", (), {"name": "synthetic_value", "sql_type": "int"})(),
                type("C", (), {"name": "synthetic_key", "sql_type": "nvarchar(100)"})(),
            ),
        ),
    )
    req = SqlInspectionRequestV3(
        sql="SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.SYNTHETIC_KEY = :syntheticKey",
        dialect="tsql",
        identifier_case_sensitivity="sensitive",
        offline_schema=schema,
        gate_policy=SqlGatePolicyV3(),
    )
    inspector = SqlglotTsqlInspectorV3()
    result = inspector.inspect(req)
    # In sensitive mode, t.SYNTHETIC_KEY does not match snapshot column synthetic_key
    resolved_cols = [c.left_column for c in result.summary.comparisons if c.left_column]
    assert "synthetic_key" not in resolved_cols, (
        f"Sensitive mode should not resolve SYNTHETIC_KEY, got comparisons: "
        f"{[(c.left_column, c.right_parameter) for c in result.summary.comparisons]}"
    )


# ---------------------------------------------------------------------------
# M4 wrap-up: full V3 desensitization regression
# ---------------------------------------------------------------------------


def test_desensitization_independent_markers_full_v3(caplog):
    """Independent synthetic markers that appear ONLY in the illegal SQL column
    reference (not in the authorized snapshot) must not leak into issues,
    the full serialized report, or captured log output.

    Uses a multi-part qualifier with marker names (marker_db, marker_table,
    MARKER_COLUMN) that never appear in the fixture's authorized snapshot.
    After full V3 entry with recomputed candidate hash, asserts blocked,
    SQL_COLUMN_QUALIFIER, and inspection exists; then asserts the markers
    are absent from issues, the complete serialized report, and logs.
    """
    import logging

    # Independent markers — only in the illegal SQL, never in the snapshot
    markers = ("marker_db", "marker_table", "MARKER_COLUMN")

    request = _build_validation_request(tamper="desens_marker")
    report = validate_sql_candidate_v3(request)

    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_COLUMN_QUALIFIER" in codes

    # 1. Issue messages and normalized_identifier must not contain markers
    for issue in report.issues:
        for marker in markers:
            assert marker not in issue.message, (
                f"Marker {marker!r} leaked into issue message: {issue.message}"
            )
            if issue.normalized_identifier:
                assert marker not in issue.normalized_identifier, (
                    f"Marker {marker!r} leaked into normalized_identifier"
                )

    # 2. Full serialized report must not contain markers
    report_json = report.model_dump_json(by_alias=True)
    for marker in markers:
        assert marker not in report_json, f"Marker {marker!r} leaked into serialized report"

    # 3. Captured log must not contain markers
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        _ = validate_sql_candidate_v3(request)

    for record in caplog.records:
        msg = record.getMessage()
        for marker in markers:
            assert marker not in msg, f"Marker {marker!r} leaked into log: {msg}"
            assert marker not in str(record.args), f"Marker {marker!r} leaked into log args"


def test_column_qualifier_message_is_neutral():
    """SQL_COLUMN_QUALIFIER message must be neutral — no col.sql() dump,
    no raw column reference, no input identifier."""
    request = _build_validation_request(tamper="desens_marker")
    report = validate_sql_candidate_v3(request)
    qual_issues = [i for i in report.issues if i.code == "SQL_COLUMN_QUALIFIER"]
    assert qual_issues, "Expected SQL_COLUMN_QUALIFIER issue"
    msg = qual_issues[0].message
    # Must not contain the raw column reference or any input identifier
    assert "marker_db" not in msg
    assert "marker_table" not in msg
    assert "MARKER_COLUMN" not in msg
    assert "dbo" not in msg  # no authorized name leak either
    # Must contain stable neutral explanation
    assert "限定符" in msg or "alias.column" in msg


def test_duplicate_same_column_blocked():
    """Same parameter bound to the same column twice is blocked."""
    request = _build_validation_request(tamper="dup_same_col")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_KEY_DUPLICATE" in codes


def test_duplicate_three_way_blocked():
    """key=:p AND value=:p AND key=:p — three conditions, one param repeated."""
    request = _build_validation_request(tamper="dup_three_way")
    report = validate_sql_candidate_v3(request)
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_KEY_DUPLICATE" in codes


def test_case_sensitive_full_v3_blocked():
    """Full V3 entry: sensitive mode with mixed-case WHERE column is blocked.

    Constructs a fully self-consistent sensitive-case request: rebuilds the
    snapshot, approval, M2 report, and candidate references so that the
    request passes reference/hash gates and enters the parser. The WHERE
    column (SYNTHETIC_KEY) does not match the lowercase snapshot column
    (synthetic_key) in sensitive mode, so the comparison cannot resolve.
    """
    import asyncio
    import json
    from datetime import datetime

    from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3
    from release_sql_bot.application.ports.approval_records_v3 import (
        InMemoryApprovalRecordPortV3,
    )
    from release_sql_bot.application.ports.candidates import CandidateModelResponse
    from release_sql_bot.domain.fact_binding_handoffs_v3 import (
        StoredFactBindingHandoffBatchV3,
    )
    from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
    from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3
    from release_sql_bot.domain.sql_candidates_v3 import GenerateSqlCandidateRequestV3
    from release_sql_bot.domain.sql_validation_v3 import ValidateSqlCandidateRequestV3
    from tests.fakes import FixedCandidateModelProvider
    from tests.v3_metadata_support import _reclose_all_hashes_from_wire

    # Build a sensitive-case variant of the standard fixture
    req_wire = json.loads(json.dumps(valid_resolve_metadata_request_v3_wire()))
    req_wire["metadataSnapshot"]["identifierCaseSensitivity"] = "sensitive"
    _reclose_all_hashes_from_wire(req_wire)

    binding_req = req_wire["bindingRequest"]

    # Build closure from the binding request
    binding_model = FactBindingRequestV3.model_validate(binding_req)
    payload_hash = canonical_sha256(binding_model)
    closure_wire = {
        "schemaVersion": "1.0.0",
        "ruleVersion": binding_req["ruleRef"]["ruleVersion"],
        "requestId": binding_req["requestId"],
        "factCode": binding_req["fact"]["factCode"],
        "payloadSha256": payload_hash,
        "batchSha256": canonical_sha256(
            [{"requestId": binding_req["requestId"], "payloadSha256": payload_hash}]
        ),
        "contractSchemaId": "urn:rulereader:fact-binding-request:3.0.0",
        "contractSchemaSha256": "2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566",
        "intakeStatus": "readyForMetadataResolution",
        "payload": binding_req,
    }
    req_wire["handoffClosure"] = closure_wire

    # Resolve M2 against the sensitive fixture
    request = _reclose_all_hashes_from_wire(req_wire)
    report = resolve_metadata_v3(request)
    assert report.status == "metadataResolved", (
        f"Sensitive fixture M2 failed: {[i.code for i in report.issues]}"
    )

    # Build provider payload with mixed-case WHERE column
    fact = binding_req["fact"]
    parameters = [
        {
            "name": p["name"],
            "dataType": p["dataType"],
            "required": p["required"],
            "source": f"fact.parameters.{p['name']}",
        }
        for p in fact["parameters"]
    ]
    declared_usage = [
        {
            "stage": u["stage"],
            "ruleCode": u["ruleCode"],
            "priority": u["priority"],
            "conditionId": u["conditionId"],
            "conditionPath": u["conditionPath"],
            "outcome": u["outcome"],
        }
        for u in binding_req["usages"]
    ]
    payload = {
        "templateCode": "SYNTHETIC_SENSITIVE",
        "sqlTemplate": (
            "SELECT t.synthetic_value AS fact_value "
            "FROM dbo.synthetic_table t "
            "WHERE t.SYNTHETIC_KEY = :syntheticKey"
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
        "declaredUsageCoverage": declared_usage,
        "assumptions": [],
        "warnings": [],
    }

    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)
    request_dict = {
        "request_id": closure_wire["requestId"],
        "rule_version": closure_wire["ruleVersion"],
        "fact_code": closure_wire["factCode"],
        "contract_version": "3.0.0",
        "payload_sha256": closure_wire["payloadSha256"],
        "created_at": now,
        "payload": binding_model.model_dump(by_alias=True, mode="json"),
    }
    batch = StoredFactBindingHandoffBatchV3.model_validate(
        {
            "_id": closure_wire["ruleVersion"],
            "rule_version": closure_wire["ruleVersion"],
            "contract_version": "3.0.0",
            "request_count": 1,
            "request_ids": [closure_wire["requestId"]],
            "batch_sha256": closure_wire["batchSha256"],
            "created_at": now,
            "requests": [request_dict],
        }
    )

    class _FakeRepo:
        async def get_batch_by_rule_version(self, rv):
            return batch

    approval_rec = ApprovalRecordV3.model_validate(req_wire["approvalRecord"])
    approval_port = InMemoryApprovalRecordPortV3(records={approval_rec.approval_id: approval_rec})

    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-001",
                model="fixed-model-v3",
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        ]
    )

    gen_req = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": req_wire,
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )
    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=gen_req,
            handoff_repository=_FakeRepo(),
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    # Validate through the full V3 entry
    val_req = ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": gen_req.model_dump(by_alias=True, mode="json"),
            "candidate": candidate.model_dump(by_alias=True, mode="json"),
        }
    )
    val_report = validate_sql_candidate_v3(val_req)

    assert val_report.status == "blocked", (
        f"Expected blocked (sensitive mismatch), got {val_report.status}: "
        f"{[(i.code, i.message) for i in val_report.issues]}"
    )
    assert val_report.inspection is not None, "Parser must have been called"
    codes = {i.code for i in val_report.issues}
    # The WHERE column SYNTHETIC_KEY cannot resolve in sensitive mode,
    # so the entity key comparison is missing or wrong
    assert any(c in codes for c in ["SQL_KEY_MISSING", "SQL_KEY_WRONG", "SQL_CMP_SHAPE"]), (
        f"Expected a key resolution issue, got: {codes}"
    )
