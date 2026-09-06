from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.application.ports.sql_parameter_binding import (
    BoundSqlV2,
    SqlParameterBinder,
    SqlParameterBindingError,
)
from release_sql_bot.application.ports.sqlserver_validation import (
    ColumnFacts,
    DescribeColumn,
    RelationFacts,
    SqlServerProbeUndeterminedError,
    SqlServerTimeoutError,
    SqlServerUnavailableError,
)
from release_sql_bot.application.sqlserver_validation import validate_sqlserver_candidate_v2
from release_sql_bot.domain.sqlserver_validation import ValidateSqlServerRequestV2
from release_sql_bot.infrastructure.sql.sqlglot_tsql import SqlglotTsqlInspector
from release_sql_bot.infrastructure.sql.sqlserver_parameter_binder import (
    SqlServerTokenParameterBinder,
)
from release_sql_bot.infrastructure.sqlserver import (
    identity_fingerprinter_for,
    value_fingerprinter_for,
)
from tests.fakes import FixedSqlServerValidationSession, FixedSqlServerValidator
from tests.phase5_support import (
    AMOUNTS,
    HMAC_KEY,
    VALIDATION_DATABASE,
    VALIDATION_HOST,
    VALIDATION_USERNAME,
    default_facts,
    default_session,
    synthetic_profile,
    valid_sqlserver_request,
)

RUN_ID = "run-synthetic-0001"
STARTED_AT = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)


class FailingBinder(SqlParameterBinder):
    def bind(self, sql: str) -> BoundSqlV2:
        raise SqlParameterBindingError("injected binder failure")


def run_validation(
    *,
    request: ValidateSqlServerRequestV2 | None = None,
    profile=None,
    session: FixedSqlServerValidationSession | None = None,
    validator: FixedSqlServerValidator | None = None,
    binder: SqlParameterBinder | None = None,
    **session_overrides,
):
    request = request if request is not None else valid_sqlserver_request()
    profile = profile if profile is not None else synthetic_profile()
    session = session if session is not None else default_session(**session_overrides)
    validator = validator if validator is not None else FixedSqlServerValidator(session)
    report = validate_sqlserver_candidate_v2(
        request=request,
        profile=profile,
        inspector=SqlglotTsqlInspector(),
        binder=binder if binder is not None else SqlServerTokenParameterBinder(),
        validator_port=validator,
        value_fingerprinter=value_fingerprinter_for(HMAC_KEY),
        identity_fingerprinter=identity_fingerprinter_for(HMAC_KEY),
        run_id=RUN_ID,
        started_at=STARTED_AT,
    )
    return report, session, validator


def tampered_request(mutate) -> ValidateSqlServerRequestV2:
    payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
    mutate(payload)
    return ValidateSqlServerRequestV2.model_validate(payload)


def issue_codes(report) -> list[str]:
    return [issue.code for issue in report.issues]


# ---------------------------------------------------------------------------
# Happy path and evidence
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_passed_report_with_single_probe_cycle(self) -> None:
        report, session, validator = run_validation()
        assert report.status == "passed"
        assert report.executable is False
        assert validator.open_count == 1
        assert session.calls == [
            "apply_session_policy",
            "attest_target",
            "attest_permissions",
            "read_catalog_facts",
            "describe_first_result_set",
            "rollback_safely",
            "close",
        ]
        assert session.describe_requests[0].parameter_declaration == "@p0 int"
        assert session.describe_requests[0].tsql == (
            "SELECT amounts.total_amount AS fact_value "
            "FROM reporting.synthetic_report_amounts AS amounts "
            "WHERE amounts.project_id = @p0"
        )
        assert report.target_evidence is not None
        assert report.target_evidence.identity_matched is True
        assert report.permission_evidence is not None
        assert report.permission_evidence.select_granted_objects == 1
        assert report.snapshot_drift_evidence is not None
        assert report.snapshot_drift_evidence.status == "matched"
        assert report.describe_evidence is not None
        assert report.describe_evidence.described is True
        assert report.describe_evidence.column_name == "fact_value"
        assert report.describe_evidence.sql_server_type_family == "decimal"
        assert report.bound_sql_ref is not None
        assert report.bound_sql_ref.ordered_parameter_names == ("projectId",)
        assert report.parameter_evidence[0].value_hmac_sha256 != ""

    def test_report_hash_is_canonical_and_reproducible(self) -> None:
        report, _session, _validator = run_validation()
        payload = report.model_dump(by_alias=True, mode="json")
        expected = payload.pop("reportSha256")
        assert canonical_sha256(payload) == expected

    def test_session_policy_uses_request_tightened_limits(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        payload["validationCase"]["requestedLimits"] = {
            "lockTimeoutMilliseconds": 500,
            "commandTimeoutSeconds": 10,
        }
        request = ValidateSqlServerRequestV2.model_validate(payload)
        report, session, validator = run_validation(request=request)
        assert report.status == "passed"
        assert session.policies[0].lock_timeout_milliseconds == 500
        assert session.policies[0].command_timeout_seconds == 10
        assert validator.limits[0].command_timeout_seconds == 10


# ---------------------------------------------------------------------------
# Preflight: profile/config gates keep adapter calls at zero
# ---------------------------------------------------------------------------


class TestProfileGates:
    def test_disabled_profile_blocks_before_any_work(self) -> None:
        report, session, validator = run_validation(profile=synthetic_profile(enabled=False))
        assert report.status == "blocked"
        assert issue_codes(report) == ["VALIDATION_DISABLED"]
        assert validator.open_count == 0
        assert session.calls == []

    def test_unknown_profile_reference_blocks(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        payload["validationProfileId"] = "validation-profile-other"
        request = ValidateSqlServerRequestV2.model_validate(payload)
        report, session, validator = run_validation(request=request)
        assert issue_codes(report) == ["PROFILE_NOT_FOUND"]
        assert validator.open_count == 0
        assert session.calls == []

    def test_production_profile_blocks(self) -> None:
        report, _session, validator = run_validation(
            profile=synthetic_profile(environment_class="production")
        )
        assert issue_codes(report) == ["PRODUCTION_TARGET_FORBIDDEN"]
        assert validator.open_count == 0

    def test_insecure_tls_profile_blocks(self) -> None:
        report, _session, validator = run_validation(
            profile=synthetic_profile(trust_server_certificate=True)
        )
        assert issue_codes(report) == ["TLS_POLICY_INVALID"]
        assert validator.open_count == 0

    def test_mode_not_allowed_blocks(self) -> None:
        report, _session, validator = run_validation(profile=synthetic_profile(allowed_modes=()))
        assert issue_codes(report) == ["MODE_NOT_ALLOWED"]
        assert validator.open_count == 0

    def test_relaxed_requested_limits_block(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        payload["validationCase"]["requestedLimits"] = {
            "lockTimeoutMilliseconds": 999_999,
        }
        request = ValidateSqlServerRequestV2.model_validate(payload)
        report, _session, validator = run_validation(request=request)
        assert issue_codes(report) == ["REQUESTED_LIMIT_VIOLATION"]
        assert validator.open_count == 0


# ---------------------------------------------------------------------------
# Preflight: reference closure tampering keeps adapter calls at zero
# ---------------------------------------------------------------------------


class TestReferenceGates:
    def test_carried_static_report_tampering_blocks(self) -> None:
        def mutate(payload: dict[str, Any]) -> None:
            payload["staticValidationReport"]["usageCoverage"] = []

        request = tampered_request(mutate)
        report, _session, validator = run_validation(request=request)
        assert "STATIC_REPORT_MISMATCH" in issue_codes(report)
        assert validator.open_count == 0

    def test_candidate_sql_tampering_blocks_before_adapter(self) -> None:
        def mutate(payload: dict[str, Any]) -> None:
            payload["staticValidationRequest"]["candidate"]["sqlTemplate"] = (
                "SELECT amounts.total_amount AS fact_value "
                "FROM reporting.synthetic_report_amounts AS amounts "
                "WHERE amounts.project_id = :otherId"
            )

        request = tampered_request(mutate)
        report, _session, validator = run_validation(request=request)
        assert "STATIC_GATE_NOT_PASSED" in issue_codes(report)
        assert "INPUT_REFERENCE_MISMATCH" in issue_codes(report)
        assert validator.open_count == 0

    def test_candidate_hash_tampering_blocks(self) -> None:
        def mutate(payload: dict[str, Any]) -> None:
            payload["staticValidationRequest"]["candidate"]["contentSha256"] = "0" * 64

        request = tampered_request(mutate)
        report, _session, validator = run_validation(request=request)
        assert "INPUT_REFERENCE_MISMATCH" in issue_codes(report)
        assert validator.open_count == 0

    def test_forged_passed_status_blocks(self) -> None:
        def mutate(payload: dict[str, Any]) -> None:
            payload["staticValidationRequest"]["candidate"]["sqlTemplate"] = (
                "SELECT amounts.total_amount AS fact_value "
                "FROM reporting.synthetic_report_amounts AS amounts "
                "WHERE amounts.project_id = :otherId"
            )
            payload["staticValidationReport"]["status"] = "passed"

        request = tampered_request(mutate)
        report, _session, validator = run_validation(request=request)
        assert "STATIC_GATE_NOT_PASSED" in issue_codes(report)
        assert validator.open_count == 0

    def test_blocking_uncertainty_blocks_before_adapter(self) -> None:
        def mutate(payload: dict[str, Any]) -> None:
            payload["staticValidationRequest"]["generationRequest"]["resolutionRequest"][
                "bindingRequest"
            ]["uncertainties"].append(
                {
                    "uncertaintyId": "u.synthetic.blocking",
                    "code": "TIME_SEMANTIC_UNRESOLVED",
                    "category": "timeRange",
                    "fieldPath": "/queryRequirements/timeRange",
                    "impact": "blocking",
                    "reason": "合成阻断不确定性。",
                    "resolutionHint": "合成解决提示。",
                    "evidenceIds": ["e1"],
                }
            )

        request = tampered_request(mutate)
        report, _session, validator = run_validation(request=request)
        assert "INPUT_REFERENCE_MISMATCH" in issue_codes(report)
        assert validator.open_count == 0


# ---------------------------------------------------------------------------
# Preflight: parameter policy
# ---------------------------------------------------------------------------


class TestParameterGates:
    def test_missing_parameter_binding_blocks(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        payload["validationCase"]["parameterBindings"] = []
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_extra_parameter_binding_blocks(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        payload["validationCase"]["parameterBindings"].append(
            {
                "name": "extraParam",
                "dataType": "integer",
                "value": 1,
                "source": "validationCase.case.synthetic.001",
            }
        )
        request = ValidateSqlServerRequestV2.model_validate(payload)
        report, _session, validator = run_validation(request=request)
        assert "PARAMETER_UNDECLARED" in issue_codes(report)
        assert validator.open_count == 0

    def test_type_mismatch_blocks(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        payload["validationCase"]["parameterBindings"][0]["dataType"] = "string"
        request = ValidateSqlServerRequestV2.model_validate(payload)
        report, _session, validator = run_validation(request=request)
        assert "PARAMETER_TYPE_MISMATCH" in issue_codes(report)
        assert validator.open_count == 0

    def test_non_integer_value_blocks(self) -> None:
        request = valid_sqlserver_request(project_id_value="not-an-int")
        report, _session, validator = run_validation(request=request)
        assert "PARAMETER_TYPE_MISMATCH" in issue_codes(report) or (
            "PARAMETER_VALUE_REJECTED" in issue_codes(report)
        )
        assert validator.open_count == 0

    def test_list_data_type_blocks(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        binding = payload["validationCase"]["parameterBindings"][0]
        binding["dataType"] = "list"
        binding["value"] = 1
        request = ValidateSqlServerRequestV2.model_validate(payload)
        report, _session, validator = run_validation(request=request)
        assert "PARAMETER_TYPE_MISMATCH" in issue_codes(report)
        assert validator.open_count == 0

    def test_structured_list_value_rejected_on_wire(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        payload["validationCase"]["parameterBindings"][0]["value"] = [1, 2]
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_nan_value_blocks(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        payload["validationCase"]["parameterBindings"][0]["value"] = float("nan")
        request = ValidateSqlServerRequestV2.model_validate(payload)
        report, _session, validator = run_validation(request=request)
        assert "PARAMETER_VALUE_REJECTED" in issue_codes(report)
        assert validator.open_count == 0

    def test_infinity_value_blocks(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        payload["validationCase"]["parameterBindings"][0]["value"] = float("inf")
        request = ValidateSqlServerRequestV2.model_validate(payload)
        report, _session, validator = run_validation(request=request)
        assert "PARAMETER_VALUE_REJECTED" in issue_codes(report)
        assert validator.open_count == 0

    def test_binder_failure_blocks_with_zero_adapter_calls(self) -> None:
        report, _session, validator = run_validation(binder=FailingBinder())
        assert "PARAMETER_BIND_FAILED" in issue_codes(report)
        assert validator.open_count == 0


# ---------------------------------------------------------------------------
# Database stage gates
# ---------------------------------------------------------------------------


class TestTargetStage:
    def test_target_identity_mismatch_blocks_and_closes(self) -> None:
        report, session, _validator = run_validation(identity={"identity_matched": False})
        assert issue_codes(report) == ["TARGET_IDENTITY_MISMATCH"]
        assert report.status == "blocked"
        assert session.rollback_count == 1
        assert session.close_count == 1
        assert "describe_first_result_set" not in session.calls

    def test_unsupported_server_version_blocks_and_closes(self) -> None:
        report, session, _validator = run_validation(identity={"version_supported": False})
        assert issue_codes(report) == ["SERVER_VERSION_UNSUPPORTED"]
        assert session.close_count == 1

    def test_session_policy_failure_blocks_and_closes(self) -> None:
        report, session, _validator = run_validation(
            fail_on={
                "apply_session_policy": SqlServerProbeUndeterminedError("sessionPolicy"),
            }
        )
        assert issue_codes(report) == ["SESSION_POLICY_FAILED"]
        assert report.status == "blocked"
        assert session.rollback_count == 1
        assert session.close_count == 1


class TestPermissionStage:
    def test_missing_select_blocks(self) -> None:
        report, session, _validator = run_validation(
            permissions={"select_granted": (), "select_denied": (AMOUNTS,)}
        )
        assert "SELECT_PERMISSION_MISSING" in issue_codes(report)
        assert session.close_count == 1
        assert "read_catalog_facts" not in session.calls

    def test_object_level_write_capability_blocks(self) -> None:
        report, _session, validator = run_validation(
            permissions={"forbidden_capabilities": ("object:UPDATE",)}
        )
        assert "WRITE_PERMISSION_PRESENT" in issue_codes(report)
        assert validator.open_count == 1

    def test_high_role_membership_blocks(self) -> None:
        report, _session, _validator = run_validation(
            permissions={"database_role_write_detected": True}
        )
        assert "WRITE_PERMISSION_PRESENT" in issue_codes(report)

    def test_undetermined_permissions_block(self) -> None:
        report, _session, _validator = run_validation(permissions={"determined": False})
        assert "PERMISSION_UNDETERMINED" in issue_codes(report)
        assert report.status == "blocked"

    def test_permission_probe_failure_blocks(self) -> None:
        report, session, _validator = run_validation(
            fail_on={"attest_permissions": SqlServerProbeUndeterminedError("permission.object")}
        )
        assert "PERMISSION_UNDETERMINED" in issue_codes(report)
        assert session.close_count == 1


class TestSnapshotDriftStage:
    def test_missing_live_object_blocks(self) -> None:
        report, _session, _validator = run_validation(facts={"relations": ()})
        assert "SNAPSHOT_OBJECT_MISSING" in issue_codes(report)
        assert report.snapshot_drift_evidence.status == "drifted"

    def test_missing_live_column_blocks(self) -> None:
        facts = default_facts()
        relation = facts.relations[0]
        narrowed = RelationFacts(
            schema_name=relation.schema_name,
            relation_name=relation.relation_name,
            object_type=relation.object_type,
            modified_after_capture=False,
            columns=(relation.columns[1],),
        )
        report, _session, _validator = run_validation(facts={"relations": (narrowed,)})
        assert "SNAPSHOT_COLUMN_MISSING" in issue_codes(report)

    def test_type_drift_blocks(self) -> None:
        facts = default_facts()
        relation = facts.relations[0]
        drifted_column = ColumnFacts(
            column_name="total_amount",
            system_type_name="decimal",
            is_user_defined_type=False,
            max_length=9,
            precision=10,
            scale=2,
            is_nullable=False,
            is_computed=False,
        )
        drifted = RelationFacts(
            schema_name=relation.schema_name,
            relation_name=relation.relation_name,
            object_type=relation.object_type,
            modified_after_capture=False,
            columns=(drifted_column, relation.columns[1]),
        )
        report, _session, _validator = run_validation(facts={"relations": (drifted,)})
        assert "SNAPSHOT_TYPE_DRIFT" in issue_codes(report)

    def test_alias_type_blocks(self) -> None:
        facts = default_facts()
        relation = facts.relations[0]
        alias_column = ColumnFacts(
            column_name="total_amount",
            system_type_name=None,
            is_user_defined_type=True,
            max_length=9,
            precision=18,
            scale=2,
            is_nullable=False,
            is_computed=False,
        )
        drifted = RelationFacts(
            schema_name=relation.schema_name,
            relation_name=relation.relation_name,
            object_type=relation.object_type,
            modified_after_capture=False,
            columns=(alias_column, relation.columns[1]),
        )
        report, _session, _validator = run_validation(facts={"relations": (drifted,)})
        assert "SNAPSHOT_TYPE_DRIFT" in issue_codes(report)

    def test_nullability_widening_blocks(self) -> None:
        facts = default_facts()
        relation = facts.relations[0]
        nullable_column = ColumnFacts(
            column_name="total_amount",
            system_type_name="decimal",
            is_user_defined_type=False,
            max_length=9,
            precision=18,
            scale=2,
            is_nullable=True,
            is_computed=False,
        )
        drifted = RelationFacts(
            schema_name=relation.schema_name,
            relation_name=relation.relation_name,
            object_type=relation.object_type,
            modified_after_capture=False,
            columns=(nullable_column, relation.columns[1]),
        )
        report, _session, _validator = run_validation(facts={"relations": (drifted,)})
        assert "SNAPSHOT_NULLABILITY_DRIFT" in issue_codes(report)

    def test_object_type_drift_blocks(self) -> None:
        facts = default_facts()
        relation = facts.relations[0]
        drifted = RelationFacts(
            schema_name=relation.schema_name,
            relation_name=relation.relation_name,
            object_type="U",
            modified_after_capture=False,
            columns=relation.columns,
        )
        report, _session, _validator = run_validation(facts={"relations": (drifted,)})
        assert "SNAPSHOT_TYPE_DRIFT" in issue_codes(report)

    def test_modify_after_capture_is_warning_only(self) -> None:
        facts = default_facts()
        relation = facts.relations[0]
        touched = RelationFacts(
            schema_name=relation.schema_name,
            relation_name=relation.relation_name,
            object_type=relation.object_type,
            modified_after_capture=True,
            columns=relation.columns,
        )
        report, _session, _validator = run_validation(facts={"relations": (touched,)})
        assert report.status == "passed"
        assert [warning.code for warning in report.warnings] == ["SNAPSHOT_DEFINITION_DRIFT"]

    def test_extra_live_column_never_expands_authorization(self) -> None:
        facts = default_facts()
        relation = facts.relations[0]
        extra_column = ColumnFacts(
            column_name="extra_live_column",
            system_type_name="int",
            is_user_defined_type=False,
            max_length=4,
            precision=10,
            scale=0,
            is_nullable=True,
            is_computed=False,
        )
        widened = RelationFacts(
            schema_name=relation.schema_name,
            relation_name=relation.relation_name,
            object_type=relation.object_type,
            modified_after_capture=False,
            columns=relation.columns + (extra_column,),
        )
        report, _session, _validator = run_validation(facts={"relations": (widened,)})
        assert report.status == "passed"
        extra = [
            item
            for item in report.snapshot_drift_evidence.drifts
            if item.drift_type == "extraColumn"
        ]
        assert len(extra) == 1
        assert "extra_live_column" not in extra[0].safe_identifier

    def test_catalog_undetermined_blocks(self) -> None:
        report, _session, _validator = run_validation(facts={"undetermined": (AMOUNTS,)})
        assert "SNAPSHOT_PROBE_FAILED" in issue_codes(report)


class TestDescribeStage:
    def test_zero_result_columns_block(self) -> None:
        report, _session, _validator = run_validation(
            describe={"columns": (), "response_rows": 0, "response_bytes": 0}
        )
        assert "RESULT_SHAPE_MISMATCH" in issue_codes(report)

    def test_two_result_columns_block(self) -> None:
        report, _session, _validator = run_validation(
            describe={
                "columns": (
                    DescribeColumn("fact_value", "decimal(18,2)", False),
                    DescribeColumn("extra", "int", False),
                ),
                "response_rows": 2,
                "response_bytes": 200,
            }
        )
        assert "RESULT_SHAPE_MISMATCH" in issue_codes(report)

    def test_wrong_column_alias_blocks(self) -> None:
        report, _session, _validator = run_validation(
            describe={
                "columns": (DescribeColumn("value", "decimal(18,2)", False),),
            }
        )
        assert "RESULT_SHAPE_MISMATCH" in issue_codes(report)

    def test_incompatible_result_type_blocks(self) -> None:
        report, _session, _validator = run_validation(
            describe={
                "columns": (DescribeColumn("fact_value", "int", False),),
            }
        )
        assert "RESULT_TYPE_MISMATCH" in issue_codes(report)

    def test_wider_nullability_blocks(self) -> None:
        report, _session, _validator = run_validation(
            describe={
                "columns": (DescribeColumn("fact_value", "decimal(18,2)", True),),
            }
        )
        assert "RESULT_NULLABILITY_MISMATCH" in issue_codes(report)

    def test_describe_rejection_maps_to_blocked_issue(self) -> None:
        report, session, _validator = run_validation(
            fail_on={"describe_first_result_set": SqlServerProbeUndeterminedError("describe")}
        )
        assert "DESCRIBE_REJECTED" in issue_codes(report) or (
            "SNAPSHOT_PROBE_FAILED" in issue_codes(report)
        )
        assert session.close_count == 1

    def test_describe_size_limit_blocks(self) -> None:
        report, _session, _validator = run_validation(
            describe={"response_rows": 1001, "response_bytes": 120}
        )
        assert "DESCRIBE_SIZE_LIMIT" in issue_codes(report)

    def test_describe_timeout_is_inconclusive_and_cleans_up(self) -> None:
        report, session, _validator = run_validation(
            fail_on={
                "describe_first_result_set": SqlServerTimeoutError("describe", "HYT00"),
            }
        )
        assert report.status == "inconclusive"
        assert "CONNECTION_UNAVAILABLE" in issue_codes(report)
        assert session.rollback_count == 1
        assert session.close_count == 1

    def test_connection_failure_is_inconclusive(self) -> None:
        validator = FixedSqlServerValidator(default_session())
        validator.open_failure = SqlServerUnavailableError("open", "08001")
        report, session, _ = run_validation(validator=validator)
        assert report.status == "inconclusive"
        assert "CONNECTION_UNAVAILABLE" in issue_codes(report)
        assert session.calls == []

    def test_driver_missing_maps_to_driver_unavailable(self) -> None:
        validator = FixedSqlServerValidator(default_session())
        validator.open_failure = SqlServerUnavailableError("open", "IM002")
        report, _session, _ = run_validation(validator=validator)
        assert report.status == "inconclusive"
        assert "DRIVER_UNAVAILABLE" in issue_codes(report)


# ---------------------------------------------------------------------------
# Sensitive data and lifecycle invariants
# ---------------------------------------------------------------------------


class TestSensitiveOutputs:
    def test_report_never_contains_sql_targets_or_values(self) -> None:
        report, _session, _validator = run_validation(
            profile=synthetic_profile(host=VALIDATION_HOST, database=VALIDATION_DATABASE)
        )
        text = report.model_dump_json(by_alias=True)
        assert "SELECT" not in text
        assert "total_amount" not in text
        assert "synthetic_report_amounts" not in text
        assert VALIDATION_HOST not in text
        assert VALIDATION_DATABASE not in text
        assert VALIDATION_USERNAME not in text
        assert '"value"' not in text
        assert '"sql"' not in text
        assert report.executable is False

    def test_report_status_blocked_when_preflight_fails_stays_not_executable(self) -> None:
        report, _session, _validator = run_validation(profile=synthetic_profile(enabled=False))
        assert report.executable is False
        assert report.target_evidence is None
        assert report.describe_evidence is None

    def test_issue_ordering_is_stable(self) -> None:
        report, _session, _validator = run_validation(permissions={"determined": False})
        codes = issue_codes(report)
        assert codes == sorted(codes)

    def test_tampered_case_source_blocks(self) -> None:
        payload = valid_sqlserver_request().model_dump(by_alias=True, mode="json")
        payload["validationCase"]["parameterBindings"][0]["source"] = "validationCase.otherCase"
        request = ValidateSqlServerRequestV2.model_validate(payload)
        report, _session, validator = run_validation(request=request)
        assert "PARAMETER_VALUE_REJECTED" in issue_codes(report)
        assert validator.open_count == 0

    def test_repeated_run_with_identical_inputs_is_stable(self) -> None:
        first, _, _ = run_validation()
        second, _, _ = run_validation()
        assert first.validation_run_id == second.validation_run_id
        assert first.status == second.status == "passed"
        assert first.candidate_ref == second.candidate_ref
        assert first.bound_sql_ref == second.bound_sql_ref
        assert (
            first.describe_evidence.sql_server_type_family
            == second.describe_evidence.sql_server_type_family
        )
