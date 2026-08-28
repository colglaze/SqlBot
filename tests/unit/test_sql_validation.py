from __future__ import annotations

import json
from copy import deepcopy

from release_sql_bot.application.sql_validation import validate_sql_candidate_v2
from release_sql_bot.domain.sql_validation import ValidateSqlCandidateRequestV2
from release_sql_bot.infrastructure.sql.sqlglot_tsql import SqlglotTsqlInspector
from tests.fakes import FixedSqlDialectInspector
from tests.phase4_support import (
    VALID_JOIN_SQL,
    VALID_SQL,
    reseal_candidate,
    valid_validation_request,
    validation_payload,
)


def _codes(request: ValidateSqlCandidateRequestV2) -> set[str]:
    report = validate_sql_candidate_v2(SqlglotTsqlInspector(), request)
    return {item.code for item in report.issues}


def test_exact_v2_candidate_passes_but_report_remains_non_executable() -> None:
    request = valid_validation_request()
    inspector = SqlglotTsqlInspector()

    first = validate_sql_candidate_v2(inspector, request)
    second = validate_sql_candidate_v2(inspector, request)

    assert first == second
    assert first.status == "passed"
    assert first.executable is False
    assert first.issues == ()
    assert first.parser_ref is not None
    assert first.parser_ref.exact_version == "30.17.0"
    assert [item.condition_id for item in first.usage_coverage] == ["amount-positive"]
    serialized = json.dumps(first.model_dump(by_alias=True, mode="json"), ensure_ascii=False)
    assert VALID_SQL not in serialized
    assert '"sqlTemplate":' not in serialized


def test_candidate_content_tamper_blocks_before_inspector() -> None:
    payload = validation_payload()
    payload["candidate"]["sqlTemplate"] += " ORDER BY amounts.total_amount"
    request = ValidateSqlCandidateRequestV2.model_validate(payload)
    inspector = FixedSqlDialectInspector([])

    report = validate_sql_candidate_v2(inspector, request)

    assert report.status == "blocked"
    assert report.parser_ref.exact_version == "30.17.0-test-double"
    assert inspector.calls == []
    assert "CANDIDATE_HASH_MISMATCH" in {item.code for item in report.issues}


def test_resolution_report_tamper_blocks_before_inspector() -> None:
    payload = validation_payload()
    payload["generationRequest"]["resolutionReport"]["authorizationPolicyVersion"] = (
        "tampered-policy"
    )
    request = ValidateSqlCandidateRequestV2.model_validate(payload)
    inspector = FixedSqlDialectInspector([])

    report = validate_sql_candidate_v2(inspector, request)

    assert inspector.calls == []
    assert "REFERENCE_MISMATCH" in {item.code for item in report.issues}


def test_context_snapshot_and_generation_ref_tamper_all_skip_parser() -> None:
    payloads = [validation_payload() for _ in range(3)]
    payloads[0]["generationRequest"]["resolutionRequest"]["projectContext"]["approvalRef"][
        "approvalId"
    ] = "approval.synthetic.context.tampered"
    payloads[1]["generationRequest"]["resolutionRequest"]["metadataSnapshot"]["capturedAt"] = (
        "2026-08-28T00:00:01Z"
    )
    payloads[2]["candidate"]["generationInputSha256"] = "0" * 64
    reseal_candidate(payloads[2])

    for payload in payloads:
        inspector = FixedSqlDialectInspector([])
        report = validate_sql_candidate_v2(
            inspector,
            ValidateSqlCandidateRequestV2.model_validate(payload),
        )

        assert report.status == "blocked"
        assert inspector.calls == []
        assert {item.code for item in report.issues} & {
            "REFERENCE_MISMATCH",
            "RESOLUTION_NOT_READY",
        }


def test_resealed_usage_claim_tamper_is_not_promoted_to_coverage() -> None:
    payload = validation_payload()
    payload["candidate"]["declaredUsageCoverage"] = ["invented-condition"]
    reseal_candidate(payload)
    request = ValidateSqlCandidateRequestV2.model_validate(payload)
    inspector = FixedSqlDialectInspector([])

    report = validate_sql_candidate_v2(inspector, request)

    assert inspector.calls == []
    assert report.usage_coverage == ()
    assert "SQL_USAGE_COVERAGE_MISMATCH" in {item.code for item in report.issues}


def test_ast_object_set_must_match_authorized_and_declared_relations() -> None:
    request = valid_validation_request(
        sql=(
            "SELECT x.total_amount AS fact_value "
            "FROM reporting.not_authorized AS x "
            "WHERE x.project_id = :projectId"
        )
    )

    codes = _codes(request)

    assert "SQL_OBJECT_NOT_ALLOWED" in codes
    assert "SQL_OBJECT_CLAIM_MISMATCH" in codes


def test_snapshot_column_without_phase2g_authority_is_blocked() -> None:
    sql = (
        "SELECT CASE WHEN projects.project_code IS NULL "
        "THEN amounts.total_amount ELSE amounts.total_amount END AS fact_value "
        "FROM reporting.synthetic_report_amounts AS amounts "
        "INNER JOIN reporting.synthetic_projects AS projects "
        "ON amounts.project_id = projects.project_id "
        "WHERE projects.project_id = :projectId"
    )
    request = valid_validation_request(sql=sql, second_relation=True)

    codes = _codes(request)

    assert "SQL_COLUMN_NOT_ALLOWED" in codes
    assert "SQL_RESULT_SOURCE_UNPROVEN" in codes


def test_declared_relation_not_used_by_ast_is_reported_as_claim_mismatch() -> None:
    request = valid_validation_request(sql=VALID_SQL, second_relation=True)

    assert "SQL_OBJECT_CLAIM_MISMATCH" in _codes(request)


def test_valid_authorized_join_can_pass_the_same_offline_gate() -> None:
    report = validate_sql_candidate_v2(
        SqlglotTsqlInspector(),
        valid_validation_request(sql=VALID_JOIN_SQL, second_relation=True),
    )

    assert report.status == "passed"
    assert report.issues == ()
    assert report.inspection is not None
    assert report.inspection.join_count == 1


def test_join_type_and_endpoints_cannot_be_recombined_from_individually_allowed_columns() -> None:
    wrong_type = VALID_JOIN_SQL.replace("INNER JOIN", "LEFT JOIN")
    wrong_pair = VALID_JOIN_SQL.replace(
        "amounts.project_id = projects.project_id",
        "amounts.total_amount = projects.project_id",
    )

    assert "SQL_JOIN_NOT_ALLOWED" in _codes(
        valid_validation_request(sql=wrong_type, second_relation=True)
    )
    assert "SQL_JOIN_NOT_ALLOWED" in _codes(
        valid_validation_request(sql=wrong_pair, second_relation=True)
    )


def test_missing_extra_and_misplaced_parameters_are_independent_issues() -> None:
    missing = valid_validation_request(
        sql=(
            "SELECT amounts.total_amount AS fact_value "
            "FROM reporting.synthetic_report_amounts AS amounts"
        )
    )
    extra = valid_validation_request(
        sql=(
            "SELECT amounts.total_amount AS fact_value "
            "FROM reporting.synthetic_report_amounts AS amounts "
            "WHERE amounts.project_id = :projectId AND :extra = 1"
        )
    )
    misplaced = valid_validation_request(
        sql=(
            "SELECT amounts.total_amount + :projectId AS fact_value "
            "FROM reporting.synthetic_report_amounts AS amounts"
        )
    )

    assert "SQL_PARAMETER_MISSING" in _codes(missing)
    assert "SQL_PARAMETER_UNDECLARED" in _codes(extra)
    assert "SQL_PARAMETER_POSITION" in _codes(misplaced)


def test_constant_fact_value_cannot_prove_result_source_or_usage() -> None:
    request = valid_validation_request(
        sql=(
            "SELECT 1 AS fact_value "
            "FROM reporting.synthetic_report_amounts AS amounts "
            "WHERE amounts.project_id = :projectId"
        )
    )
    report = validate_sql_candidate_v2(SqlglotTsqlInspector(), request)

    assert "SQL_RESULT_SOURCE_UNPROVEN" in {item.code for item in report.issues}
    assert "SQL_USAGE_SOURCE_UNPROVEN" in {item.code for item in report.issues}
    assert report.usage_coverage == ()


def test_cte_lineage_can_prove_the_same_authorized_result_source() -> None:
    sql = (
        "WITH scoped AS ("
        "SELECT amounts.total_amount, amounts.project_id "
        "FROM reporting.synthetic_report_amounts AS amounts"
        ") "
        "SELECT scoped.total_amount AS fact_value FROM scoped "
        "WHERE scoped.project_id = :projectId"
    )

    report = validate_sql_candidate_v2(
        SqlglotTsqlInspector(),
        valid_validation_request(sql=sql),
    )

    assert report.status == "passed"


def test_computed_aggregation_uses_all_phase2g_result_inputs() -> None:
    sql = (
        "SELECT SUM(amounts.total_amount) AS fact_value "
        "FROM reporting.synthetic_report_amounts AS amounts "
        "WHERE amounts.project_id = :projectId"
    )

    report = validate_sql_candidate_v2(
        SqlglotTsqlInspector(),
        valid_validation_request(sql=sql, result_mode="aggregation"),
    )

    assert report.status == "passed"
    assert report.inspection is not None
    assert report.inspection.result_columns[0].source_columns[0].column_name == ("total_amount")


def test_exists_result_requires_an_authorized_subquery_dependency() -> None:
    sql = (
        "SELECT CASE WHEN EXISTS ("
        "SELECT 1 FROM reporting.synthetic_report_amounts AS amounts "
        "WHERE amounts.project_id = :projectId"
        ") THEN CAST(1 AS bit) ELSE CAST(0 AS bit) END AS fact_value"
    )

    report = validate_sql_candidate_v2(
        SqlglotTsqlInspector(),
        valid_validation_request(sql=sql, result_mode="exists"),
    )

    assert report.status == "passed"
    assert report.candidate_ref.candidate_content_sha256
    assert report.usage_coverage[0].condition_id == "amount-positive"


def test_report_issue_order_is_stable_and_does_not_echo_sql() -> None:
    sql = (
        "SELECT 1 AS wrong FROM reporting.synthetic_report_amounts AS amounts "
        "WHERE amounts.project_id = @projectId"
    )
    request = valid_validation_request(sql=sql)

    report = validate_sql_candidate_v2(SqlglotTsqlInspector(), request)
    sort_keys = [
        (item.gate_order, item.code, item.field_path, item.normalized_identifier or "")
        for item in report.issues
    ]

    assert sort_keys == sorted(sort_keys)
    serialized = json.dumps(report.model_dump(by_alias=True, mode="json"))
    assert sql not in serialized


def test_candidate_lifecycle_fields_are_not_changed_by_validation() -> None:
    request = valid_validation_request()
    original = deepcopy(request.candidate)

    validate_sql_candidate_v2(SqlglotTsqlInspector(), request)

    assert request.candidate == original
    assert request.candidate.status == "candidate"
    assert request.candidate.review_status == "pending"
    assert request.candidate.executable is False
