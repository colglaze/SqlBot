from __future__ import annotations

from typing import Any

from release_sql_bot.application.ports.sqlserver_validation import (
    CatalogFactsResult,
    ColumnFacts,
    DescribeColumn,
    DescribeResult,
    ObjectRef,
    PermissionAttestationResult,
    RelationFacts,
    TargetIdentityEvidence,
)
from release_sql_bot.application.sql_validation import validate_sql_candidate_v2
from release_sql_bot.domain.sql_validation import ValidateSqlCandidateRequestV2
from release_sql_bot.domain.sqlserver_validation import ValidateSqlServerRequestV2
from release_sql_bot.infrastructure.sql.sqlglot_tsql import SqlglotTsqlInspector
from release_sql_bot.infrastructure.sqlserver import identity_fingerprinter_for
from tests.fakes import FixedSqlServerValidationSession
from tests.phase4_support import validation_payload

VALIDATION_PROFILE_ID = "validation-profile-synthetic-01"
VALIDATION_HOST = "synthetic-host"
VALIDATION_DATABASE = "synthetic_reporting"
VALIDATION_USERNAME = "synthetic_validation_user"
HMAC_KEY = "0123456789abcdef-synthetic"
AMOUNTS = ObjectRef("reporting", "synthetic_report_amounts")


def sqlserver_validation_payload(
    *,
    sql: str | None = None,
    project_id_value: Any = 42,
) -> dict[str, Any]:
    """Build a complete, self-consistent Phase 5A request payload."""

    static_payload = validation_payload() if sql is None else validation_payload(sql=sql)
    static_request = ValidateSqlCandidateRequestV2.model_validate(static_payload)
    static_report = validate_sql_candidate_v2(SqlglotTsqlInspector(), static_request)
    if static_report.status != "passed":
        raise AssertionError("fixture static validation must pass")
    return {
        "schemaVersion": "1.0.0",
        "mode": "describeOnly",
        "validationProfileId": VALIDATION_PROFILE_ID,
        "staticValidationRequest": static_payload,
        "staticValidationReport": static_report.model_dump(by_alias=True, mode="json"),
        "validationCase": {
            "caseId": "case.synthetic.001",
            "dataClassification": "synthetic",
            "parameterBindings": [
                {
                    "name": "projectId",
                    "dataType": "integer",
                    "value": project_id_value,
                    "source": "validationCase.case.synthetic.001",
                }
            ],
        },
    }


def valid_sqlserver_request(
    *,
    sql: str | None = None,
    project_id_value: Any = 42,
) -> ValidateSqlServerRequestV2:
    return ValidateSqlServerRequestV2.model_validate(
        sqlserver_validation_payload(sql=sql, project_id_value=project_id_value)
    )


def synthetic_profile(**overrides: Any):
    """Return the default safe synthetic validation profile."""

    from release_sql_bot.application.sqlserver_validation import SqlServerValidationProfile

    values = {
        "profile_id": VALIDATION_PROFILE_ID,
        "enabled": True,
        "environment_class": "development",
        "allowed_modes": ("describeOnly",),
        "connect_timeout_seconds": 10,
        "command_timeout_seconds": 30,
        "lock_timeout_milliseconds": 2000,
        "max_describe_rows": 1000,
        "max_describe_bytes": 1_000_000,
        "supported_major_versions": (16, 17),
        "encrypt": True,
        "trust_server_certificate": False,
        "read_only": True,
        "application_intent": "ReadOnly",
        "host": VALIDATION_HOST,
        "database": VALIDATION_DATABASE,
    }
    values.update(overrides)
    return SqlServerValidationProfile(**values)


def default_identity(**overrides: Any) -> TargetIdentityEvidence:
    fingerprint = identity_fingerprinter_for(HMAC_KEY)
    values = {
        "server_major_version": 16,
        "engine_edition": 3,
        "compatibility_level": 160,
        "server_fingerprint": fingerprint(f"server:{VALIDATION_HOST}"),
        "database_fingerprint": fingerprint(f"database:{VALIDATION_DATABASE}"),
        "identity_matched": True,
        "version_supported": True,
    }
    values.update(overrides)
    return TargetIdentityEvidence(**values)


def default_permissions(**overrides: Any) -> PermissionAttestationResult:
    values = {
        "determined": True,
        "select_granted": (AMOUNTS,),
        "select_denied": (),
        "forbidden_capabilities": (),
        "server_role_write_detected": False,
        "database_role_write_detected": False,
    }
    values.update(overrides)
    return PermissionAttestationResult(**values)


def default_facts(**overrides: Any) -> CatalogFactsResult:
    columns = (
        ColumnFacts(
            column_name="total_amount",
            system_type_name="decimal",
            is_user_defined_type=False,
            max_length=9,
            precision=18,
            scale=2,
            is_nullable=False,
            is_computed=False,
        ),
        ColumnFacts(
            column_name="project_id",
            system_type_name="int",
            is_user_defined_type=False,
            max_length=4,
            precision=10,
            scale=0,
            is_nullable=False,
            is_computed=False,
        ),
    )
    values = {
        "relations": (
            RelationFacts(
                schema_name="reporting",
                relation_name="synthetic_report_amounts",
                object_type="V",
                modified_after_capture=False,
                columns=columns,
            ),
        ),
        "undetermined": (),
    }
    values.update(overrides)
    return CatalogFactsResult(**values)


def default_describe(**overrides: Any) -> DescribeResult:
    values = {
        "columns": (
            DescribeColumn(name="fact_value", system_type_name="decimal(18,2)", is_nullable=False),
        ),
        "response_rows": 1,
        "response_bytes": 120,
    }
    values.update(overrides)
    return DescribeResult(**values)


def default_session(**overrides: Any) -> FixedSqlServerValidationSession:
    return FixedSqlServerValidationSession(
        fingerprint=identity_fingerprinter_for(HMAC_KEY),
        identity=default_identity(**overrides.pop("identity", {})),
        permissions=default_permissions(**overrides.pop("permissions", {})),
        facts=default_facts(**overrides.pop("facts", {})),
        describe=default_describe(**overrides.pop("describe", {})),
        fail_on=overrides.pop("fail_on", {}),
    )
