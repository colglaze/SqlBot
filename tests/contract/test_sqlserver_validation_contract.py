from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from release_sql_bot.domain.sqlserver_validation import (
    FactDataTypeV2,
    SqlServerValidationIssueV2,
    SqlServerValidationReportV2,
    ValidateSqlServerRequestV2,
    live_column_compatible,
    parameter_declaration_is_bounded,
    parse_sql_server_type,
    result_family_allowed,
)
from tests.phase5_support import sqlserver_validation_payload


def valid_issue(**overrides):
    values = {
        "stage_order": 3,
        "code": "STATIC_GATE_NOT_PASSED",
        "field_path": "/staticValidationReport/status",
        "message": "静态门禁未通过。",
    }
    values.update(overrides)
    return SqlServerValidationIssueV2(**values)


def valid_report_payload() -> dict:
    return {
        "schemaVersion": "1.0.0",
        "validationPolicyVersion": "sqlserver-validation-v1",
        "mode": "describeOnly",
        "status": "blocked",
        "executable": False,
        "validationRunId": "run-0001",
        "startedAt": "2026-09-05T00:00:00.000Z",
        "completedAt": "2026-09-05T00:00:01.000Z",
        "durationMs": 1000,
        "candidateRef": {
            "contentSha256": "a" * 64,
            "sqlTemplateSha256": "b" * 64,
            "generationInputSha256": "c" * 64,
            "resolutionReportSha256": "d" * 64,
            "contextSha256": "e" * 64,
            "snapshotSha256": "f" * 64,
        },
        "staticReportSha256": "0" * 64,
        "issues": [valid_issue().model_dump(by_alias=True, mode="json")],
        "reportSha256": "1" * 64,
    }


class TestSqlServerRequestContract:
    def test_accepts_complete_camel_case_payload(self) -> None:
        request = ValidateSqlServerRequestV2.model_validate(sqlserver_validation_payload())
        assert request.mode == "describeOnly"
        assert request.validation_case.parameter_bindings[0].name == "projectId"

    def test_serializes_camel_case_only(self) -> None:
        request = ValidateSqlServerRequestV2.model_validate(sqlserver_validation_payload())
        dumped = request.model_dump(by_alias=True, mode="json")
        assert "validationProfileId" in dumped
        assert "validation_profile_id" not in dumped
        assert "staticValidationRequest" in dumped
        assert "validationCase" in dumped

    def test_rejects_unknown_top_level_field(self) -> None:
        payload = sqlserver_validation_payload()
        payload["host"] = "synthetic-host"
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_rejects_snake_case_top_level_key(self) -> None:
        payload = sqlserver_validation_payload()
        payload["validation_profile_id"] = payload.pop("validationProfileId")
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_rejects_snake_case_nested_key(self) -> None:
        payload = sqlserver_validation_payload()
        case = deepcopy(payload["validationCase"])
        case["data_classification"] = case.pop("dataClassification")
        payload["validationCase"] = case
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_rejects_partial_nested_static_request(self) -> None:
        payload = sqlserver_validation_payload()
        payload["staticValidationRequest"]["candidate"].pop("templateCode")
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_rejects_unsupported_schema_version(self) -> None:
        payload = sqlserver_validation_payload()
        payload["schemaVersion"] = "1.0.1"
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_rejects_unknown_mode(self) -> None:
        payload = sqlserver_validation_payload()
        payload["mode"] = "boundedExecution"
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_rejects_duplicate_parameter_bindings(self) -> None:
        payload = sqlserver_validation_payload()
        bindings = payload["validationCase"]["parameterBindings"]
        bindings.append(deepcopy(bindings[0]))
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_rejects_structured_parameter_value(self) -> None:
        payload = sqlserver_validation_payload()
        payload["validationCase"]["parameterBindings"][0]["value"] = {"a": 1}
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_rejects_parameter_source_with_invalid_shape(self) -> None:
        payload = sqlserver_validation_payload()
        binding = payload["validationCase"]["parameterBindings"][0]
        binding["source"] = "notACaseReference"
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)

    def test_rejects_requested_limits_relaxation_shape(self) -> None:
        payload = sqlserver_validation_payload()
        payload["validationCase"]["requestedLimits"] = {
            "lockTimeoutMilliseconds": 0,
        }
        with pytest.raises(ValidationError):
            ValidateSqlServerRequestV2.model_validate(payload)


class TestSqlServerReportContract:
    def test_report_keeps_executable_false_and_status_enum(self) -> None:
        report = SqlServerValidationReportV2.model_validate(valid_report_payload())
        assert report.executable is False
        assert report.status == "blocked"

    def test_report_rejects_executable_true(self) -> None:
        payload = valid_report_payload()
        payload["executable"] = True
        with pytest.raises(ValidationError):
            SqlServerValidationReportV2.model_validate(payload)

    def test_report_rejects_unknown_status(self) -> None:
        payload = valid_report_payload()
        payload["status"] = "ready"
        with pytest.raises(ValidationError):
            SqlServerValidationReportV2.model_validate(payload)

    def test_report_rejects_extra_field(self) -> None:
        payload = valid_report_payload()
        payload["sql"] = "SELECT 1"
        with pytest.raises(ValidationError):
            SqlServerValidationReportV2.model_validate(payload)

    def test_issue_requires_stable_code_shape(self) -> None:
        with pytest.raises(ValidationError):
            valid_issue(code="not_upper")

    def test_issues_sort_by_stage_then_code(self) -> None:
        late = valid_issue(stage_order=11, code="RESULT_SHAPE_MISMATCH")
        early = valid_issue(stage_order=2, code="PROFILE_NOT_FOUND")
        ordered = sorted([late, early], key=lambda item: (item.stage_order, item.code))
        assert [item.stage_order for item in ordered] == [2, 11]


class TestSqlServerTypePolicy:
    def test_parse_scalar_types(self) -> None:
        decimal_spec = parse_sql_server_type("decimal(18,2)")
        assert decimal_spec is not None
        assert decimal_spec.family == "decimal"
        assert decimal_spec.precision == 18
        assert decimal_spec.scale == 2

        nvarchar_spec = parse_sql_server_type("nvarchar(40)")
        assert nvarchar_spec is not None
        assert nvarchar_spec.family == "nvarchar"
        assert nvarchar_spec.length == 40

        assert parse_sql_server_type("int") is not None
        max_spec = parse_sql_server_type("varchar(max)")
        assert max_spec is not None
        assert max_spec.is_max

    def test_parse_rejects_unknown_or_malformed(self) -> None:
        assert parse_sql_server_type("hierarchyid") is None
        assert parse_sql_server_type("decimal(18,2;") is None
        assert parse_sql_server_type("int(5)") is None
        assert parse_sql_server_type("") is None

    def test_unbounded_and_lob_parameters_rejected(self) -> None:
        assert not parameter_declaration_is_bounded(parse_sql_server_type("varchar(max)"))
        assert not parameter_declaration_is_bounded(parse_sql_server_type("text"))
        assert parameter_declaration_is_bounded(parse_sql_server_type("nvarchar(40)"))

    def test_result_family_matrix(self) -> None:
        assert result_family_allowed(FactDataTypeV2.MONEY, "decimal")
        assert result_family_allowed(FactDataTypeV2.MONEY, "money")
        assert not result_family_allowed(FactDataTypeV2.MONEY, "float")
        assert result_family_allowed(FactDataTypeV2.INTEGER, "int")
        assert not result_family_allowed(FactDataTypeV2.INTEGER, "decimal")
        assert not result_family_allowed(FactDataTypeV2.LIST, "int")

    def test_live_column_compatibility_directions(self) -> None:
        approved = parse_sql_server_type("nvarchar(40)")
        assert approved is not None
        assert (
            live_column_compatible(
                "nvarchar",
                approved,
                live_family="nvarchar",
                live_max_length=80,
                live_precision=0,
                live_scale=0,
            )
            == "compatible"
        )
        assert (
            live_column_compatible(
                "nvarchar",
                approved,
                live_family="nvarchar",
                live_max_length=20,
                live_precision=0,
                live_scale=0,
            )
            == "typeDrift"
        )
        assert (
            live_column_compatible(
                "nvarchar",
                approved,
                live_family="varchar",
                live_max_length=80,
                live_precision=0,
                live_scale=0,
            )
            == "typeDrift"
        )
        assert (
            live_column_compatible(
                "nvarchar",
                approved,
                live_family="nvarchar",
                live_max_length=80,
                live_precision=0,
                live_scale=0,
            )
            == "compatible"
        )
