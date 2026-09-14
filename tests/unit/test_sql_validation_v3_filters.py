"""M4 V3 filter AST validation regression tests.

Independently verifies that the M4 static gate validates filter
constraints against the AST using the real SQLGlot adapter. Tampering
with the SQL after generation must be detected.

All tests use synthetic fixtures from test_v3_filter_support.
No network, no .env, no MongoDB/SQL Server/online model.
"""

from __future__ import annotations

import json

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.filter_constraints_v3 import (
    qualified_filter_param_names,
)
from release_sql_bot.application.sql_validation_v3 import validate_sql_candidate_v3
from release_sql_bot.domain.sql_validation_v3 import ValidateSqlCandidateRequestV3
from release_sql_bot.infrastructure.sql.sqlglot_tsql_v3 import SqlglotTsqlInspectorV3
from tests.unit.test_v3_filter_support import build_filter_request, make_filter_item


def _build_validated_request_with_filter() -> ValidateSqlCandidateRequestV3:
    """Build a M4 validation request with a valid entity-key eq filter."""
    payload, handoff_repo = build_filter_request([make_filter_item()])

    # Generate a valid candidate through M3.
    import asyncio

    from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3
    from release_sql_bot.application.ports.candidates import CandidateModelResponse
    from tests.fakes import FixedCandidateModelProvider
    from tests.unit.test_candidates_v3 import _build_synthetic_approval_port

    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req",
                model="fixed-model-v3",
                content=_filtered_sql_content(),
            )
        ]
    )

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=_build_synthetic_approval_port(),
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    return ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": payload.model_dump(by_alias=True, mode="json"),
            "candidate": candidate.model_dump(by_alias=True, mode="json"),
        }
    )


def _filtered_sql_content() -> str:
    """Build synthetic model output with the entity-key WHERE clause."""
    payload, _ = build_filter_request([make_filter_item()])
    binding = payload.resolution_request.binding_request
    fact = binding.fact
    usages = binding.usages

    parameters = [
        {
            "name": p.name,
            "dataType": str(p.data_type),
            "required": p.required,
            "source": f"fact.parameters.{p.name}",
        }
        for p in sorted(fact.parameters, key=lambda x: x.name)
    ]

    declared_usage_coverage = [
        {
            "stage": str(u.stage),
            "ruleCode": u.rule_code,
            "priority": u.priority,
            "conditionId": u.condition_id,
            "conditionPath": u.condition_path,
            "outcome": str(u.outcome),
        }
        for u in usages
    ]

    sql = (
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )

    return json.dumps(
        {
            "templateCode": "SYNTHETIC_V3_FILTERED",
            "sqlTemplate": sql,
            "parameters": parameters,
            "result": {
                "columnName": "fact_value",
                "dataType": str(fact.data_type),
                "cardinality": "scalar",
                "nullable": fact.nullable,
                "nullPolicy": str(fact.null_policy),
                "unit": fact.unit,
            },
            "declaredObjects": [{"schemaName": "dbo", "relationName": "synthetic_table"}],
            "declaredUsageCoverage": declared_usage_coverage,
            "assumptions": [],
            "warnings": [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _tamper_sql(
    request: ValidateSqlCandidateRequestV3,
    new_sql: str,
) -> ValidateSqlCandidateRequestV3:
    """Return a new request with the candidate SQL replaced and hash recomputed."""

    wire = request.model_dump(by_alias=True, mode="json")
    wire["candidate"]["sqlTemplate"] = new_sql
    # Recompute the candidate self-hash.
    from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3

    cand = SqlTemplateCandidateV3.model_validate(wire["candidate"])
    wire["candidate"]["contentSha256"] = canonical_content_sha256(cand)
    return ValidateSqlCandidateRequestV3.model_validate(wire)


# ---------------------------------------------------------------------------
# Helper qualification tests
# ---------------------------------------------------------------------------


def test_no_filters_returns_empty() -> None:
    """A request with no filters returns an empty qualified set."""
    payload, _ = build_filter_request([])
    assert qualified_filter_param_names(payload) == frozenset()


def test_single_valid_filter_qualified() -> None:
    """A single valid entity-key eq filter is qualified."""
    payload, _ = build_filter_request([make_filter_item()])
    assert qualified_filter_param_names(payload) == frozenset({"syntheticKey"})


def test_invalid_filter_not_qualified() -> None:
    """An invalid filter (wrong operator) is not qualified."""
    payload, _ = build_filter_request([make_filter_item(operator="gte")])
    assert qualified_filter_param_names(payload) == frozenset()


# ---------------------------------------------------------------------------
# M4: Valid filter + valid SQL → passed
# ---------------------------------------------------------------------------


def test_m4_valid_filter_valid_sql_passed() -> None:
    """A valid filter with correct SQL passes M4."""
    request = _build_validated_request_with_filter()
    report = validate_sql_candidate_v3(request, SqlglotTsqlInspectorV3())
    assert report.status == "passed"


# ---------------------------------------------------------------------------
# M4: Tampered SQL → blocked (each case)
# ---------------------------------------------------------------------------


def test_m4_tampered_remove_where_blocked() -> None:
    """Removing the WHERE filter blocks M4."""
    request = _build_validated_request_with_filter()
    tampered = _tamper_sql(
        request,
        "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t",
    )
    report = validate_sql_candidate_v3(tampered, SqlglotTsqlInspectorV3())
    assert report.status == "blocked"
    assert any(i.code == "SQL_FILTER_MISSING" for i in report.issues)


def test_m4_tampered_wrong_column_blocked() -> None:
    """Using the wrong column blocks M4."""
    request = _build_validated_request_with_filter()
    tampered = _tamper_sql(
        request,
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.wrong_column = :syntheticKey",
    )
    report = validate_sql_candidate_v3(tampered, SqlglotTsqlInspectorV3())
    assert report.status == "blocked"


def test_m4_tampered_wrong_parameter_blocked() -> None:
    """Using the wrong parameter blocks M4."""
    request = _build_validated_request_with_filter()
    tampered = _tamper_sql(
        request,
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :wrongParam",
    )
    report = validate_sql_candidate_v3(tampered, SqlglotTsqlInspectorV3())
    assert report.status == "blocked"


def test_m4_tampered_eq_to_gte_blocked() -> None:
    """Changing eq to gte blocks M4."""
    request = _build_validated_request_with_filter()
    tampered = _tamper_sql(
        request,
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key >= :syntheticKey",
    )
    report = validate_sql_candidate_v3(tampered, SqlglotTsqlInspectorV3())
    assert report.status == "blocked"


def test_m4_tampered_add_or_blocked() -> None:
    """Adding OR blocks M4."""
    request = _build_validated_request_with_filter()
    tampered = _tamper_sql(
        request,
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey OR t.synthetic_value > 0",
    )
    report = validate_sql_candidate_v3(tampered, SqlglotTsqlInspectorV3())
    assert report.status == "blocked"


def test_m4_tampered_add_not_blocked() -> None:
    """Adding NOT blocks M4."""
    request = _build_validated_request_with_filter()
    tampered = _tamper_sql(
        request,
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE NOT t.synthetic_key = :syntheticKey",
    )
    report = validate_sql_candidate_v3(tampered, SqlglotTsqlInspectorV3())
    assert report.status == "blocked"


def test_m4_tampered_literal_value_blocked() -> None:
    """Replacing parameter with literal blocks M4."""
    request = _build_validated_request_with_filter()
    tampered = _tamper_sql(
        request,
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = 'hardcoded'",
    )
    report = validate_sql_candidate_v3(tampered, SqlglotTsqlInspectorV3())
    assert report.status == "blocked"


def test_m4_tampered_duplicate_predicate_blocked() -> None:
    """Duplicating the entity-key predicate blocks M4."""
    request = _build_validated_request_with_filter()
    tampered = _tamper_sql(
        request,
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey AND t.synthetic_key = :syntheticKey",
    )
    report = validate_sql_candidate_v3(tampered, SqlglotTsqlInspectorV3())
    assert report.status == "blocked"


# ---------------------------------------------------------------------------
# M4: Unsupported filter → blocked
# ---------------------------------------------------------------------------


def test_m4_unsupported_filter_blocked() -> None:
    """M4 independently blocks a request with an unsupported filter.

    Builds a fully self-consistent request with a gte filter (which M3
    would reject) and a valid SQL candidate, then verifies M4 blocks it
    via the filter qualification gate — not via reference mismatch.
    """
    # Build a complete, self-consistent request with a gte filter.
    filter_item = make_filter_item(operator="gte")
    gte_payload, _ = build_filter_request([filter_item])

    # Generate a valid candidate through the real M2/M4 path.
    # We need a candidate whose SQL is valid (eq) so that only the
    # input filter qualification fails.
    import asyncio

    from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3
    from release_sql_bot.application.ports.candidates import CandidateModelResponse
    from tests.fakes import FixedCandidateModelProvider
    from tests.unit.test_candidates_v3 import _build_synthetic_approval_port

    # Use a valid SQL content (eq) — the candidate itself is fine.
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req",
                model="fixed-model-v3",
                content=_valid_eq_sql_content(),
            )
        ]
    )

    # We can't run M3 because it rejects gte. Instead, build the
    # candidate from a valid eq request, then transplant it into the
    # gte request's validation payload.
    eq_payload, eq_handoff = build_filter_request([make_filter_item()])
    eq_candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=eq_payload,
            handoff_repository=eq_handoff,
            approval_port=_build_synthetic_approval_port(),
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    # Build the M4 validation request using the gte payload but the
    # valid eq candidate. Recompute all affected hashes.
    cand_wire = eq_candidate.model_dump(by_alias=True, mode="json")
    cand_wire["generationInputSha256"] = canonical_sha256(gte_payload)
    cand_wire["requestRef"]["payloadSha256"] = canonical_sha256(
        gte_payload.resolution_request.binding_request
    )
    cand_wire["resolutionRef"]["reportSha256"] = canonical_sha256(gte_payload.resolution_report)
    cand_wire["handoffRefs"] = {
        "batchSha256": gte_payload.resolution_report.handoff_refs.batch_sha256,
        "payloadSha256": gte_payload.resolution_report.handoff_refs.payload_sha256,
        "contractSchemaId": gte_payload.resolution_report.handoff_refs.contract_schema_id,
        "contractSchemaSha256": gte_payload.resolution_report.handoff_refs.contract_schema_sha256,
    }
    # Compute content hash AFTER all other fields are set.
    from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3

    cand_obj = SqlTemplateCandidateV3.model_validate(cand_wire)
    cand_wire["contentSha256"] = canonical_content_sha256(cand_obj)

    request = ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": gte_payload.model_dump(by_alias=True, mode="json"),
            "candidate": cand_wire,
        }
    )

    report = validate_sql_candidate_v3(request, SqlglotTsqlInspectorV3())
    assert report.status == "blocked"
    assert report.inspection is not None  # AST was inspected
    codes = {i.code for i in report.issues}
    assert "SQL_FILTER_UNSUPPORTED" in codes
    # Must NOT be blocked due to reference/hash/resolution mismatch.
    assert "REF_MISMATCH" not in codes
    assert "CAND_HASH" not in codes
    assert "RESOLUTION_NOT_READY" not in codes


def _valid_eq_sql_content() -> str:
    """Build synthetic model output with a valid eq entity-key WHERE clause."""
    payload, _ = build_filter_request([make_filter_item()])
    binding = payload.resolution_request.binding_request
    fact = binding.fact
    usages = binding.usages

    parameters = [
        {
            "name": p.name,
            "dataType": str(p.data_type),
            "required": p.required,
            "source": f"fact.parameters.{p.name}",
        }
        for p in sorted(fact.parameters, key=lambda x: x.name)
    ]

    declared_usage_coverage = [
        {
            "stage": str(u.stage),
            "ruleCode": u.rule_code,
            "priority": u.priority,
            "conditionId": u.condition_id,
            "conditionPath": u.condition_path,
            "outcome": str(u.outcome),
        }
        for u in usages
    ]

    sql = (
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )

    return json.dumps(
        {
            "templateCode": "SYNTHETIC_V3_FILTERED",
            "sqlTemplate": sql,
            "parameters": parameters,
            "result": {
                "columnName": "fact_value",
                "dataType": str(fact.data_type),
                "cardinality": "scalar",
                "nullable": fact.nullable,
                "nullPolicy": str(fact.null_policy),
                "unit": fact.unit,
            },
            "declaredObjects": [{"schemaName": "dbo", "relationName": "synthetic_table"}],
            "declaredUsageCoverage": declared_usage_coverage,
            "assumptions": [],
            "warnings": [],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


# ---------------------------------------------------------------------------
# M4: No-filters old path still passes
# ---------------------------------------------------------------------------


def test_m4_no_filters_old_path_passed() -> None:
    """A request with no filters passes M4 (old path)."""
    from tests.unit.test_candidates_v3 import _build_generation_request

    payload = _build_generation_request()

    import asyncio

    from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3
    from tests.unit.test_candidates_v3 import (
        _build_synthetic_approval_port,
        _build_synthetic_handoff_repository,
        _valid_provider,
    )

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=_valid_provider(),
            payload=payload,
            handoff_repository=_build_synthetic_handoff_repository(),
            approval_port=_build_synthetic_approval_port(),
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    request = ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": payload.model_dump(by_alias=True, mode="json"),
            "candidate": candidate.model_dump(by_alias=True, mode="json"),
        }
    )

    report = validate_sql_candidate_v3(request, SqlglotTsqlInspectorV3())
    assert report.status == "passed"
