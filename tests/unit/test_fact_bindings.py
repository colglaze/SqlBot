from __future__ import annotations

from release_sql_bot.application.bindings import validate_binding_readiness
from release_sql_bot.domain.fact_bindings import ValidateFactBindingRequest
from tests.support import valid_binding_payload


def _validate(payload: dict[str, object]):
    request = ValidateFactBindingRequest.model_validate(payload)
    return validate_binding_readiness(request)


def test_rule_reader_fact_binding_is_ready_for_generation_stage() -> None:
    result = _validate(valid_binding_payload())

    assert result.status == "ready"
    assert result.fact_code == "task.settlement_fee"
    assert result.issues == []


def test_schema_v1_and_derived_fact_are_blocked() -> None:
    payload = valid_binding_payload()
    payload["bindingRequest"]["ruleRef"]["schemaVersion"] = "1.0.0"
    payload["bindingRequest"]["fact"]["factKind"] = "derived"

    result = _validate(payload)

    assert result.status == "blocked"
    assert {issue.code for issue in result.issues} == {
        "RULE_SCHEMA_UNSUPPORTED",
        "DERIVED_FACT_NOT_SQL_BOUND",
    }


def test_missing_parameters_and_metadata_scope_are_blocked() -> None:
    payload = valid_binding_payload()
    payload["bindingRequest"]["fact"]["parameters"] = []
    payload["context"]["entityKeys"] = []
    payload["context"]["allowedRelations"] = []

    result = _validate(payload)

    assert result.status == "blocked"
    assert {issue.code for issue in result.issues} == {
        "FACT_PARAMETERS_MISSING",
        "ENTITY_KEY_MISSING",
        "ALLOWED_RELATIONS_MISSING",
    }


def test_temp_tables_are_blocked_even_when_context_reports_capability() -> None:
    payload = valid_binding_payload()
    payload["bindingRequest"]["tempTableAllowed"] = True
    payload["context"]["capabilities"]["sessionTempTable"] = True
    payload["context"]["tempTableAllowed"] = True

    result = _validate(payload)

    assert result.status == "blocked"
    assert [issue.code for issue in result.issues] == ["TEMP_TABLE_DISABLED"]


def test_parameter_and_mapping_must_be_covered_by_context() -> None:
    payload = valid_binding_payload()
    payload["context"]["entityKeys"][0]["parameterName"] = "projectId"
    payload["context"]["allowedRelations"][0]["relationName"] = "v_Other"

    result = _validate(payload)

    assert result.status == "blocked"
    assert {issue.code for issue in result.issues} == {
        "PARAMETER_SOURCE_MISSING",
        "ENTITY_KEY_RELATION_NOT_ALLOWED",
        "MAPPING_RELATION_NOT_ALLOWED",
    }
