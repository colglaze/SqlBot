from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from release_sql_bot.application.complete_delivery_intake_v31 import (
    CANDIDATE_SCHEMA_ID_V31,
    CANDIDATE_SCHEMA_SHA256_V31,
    CATALOG_SCHEMA_ID_V3,
    CATALOG_SCHEMA_SHA256_V3,
    FACT_BINDING_SCHEMA_ID_V31,
    FACT_BINDING_SCHEMA_SHA256_V31,
    RESULT_SCHEMA_ID_V31,
    RESULT_SCHEMA_SHA256_V31,
    CompleteDeliveryInvalidV31Error,
    CompleteDeliveryNotConsumableV31Error,
    CompleteDeliveryNotFoundV31Error,
    load_candidate_schema_v31,
    load_catalog_schema_v3,
    load_fact_binding_schema_v31,
    load_result_schema_v31,
    select_complete_delivery_v31,
)
from release_sql_bot.application.composition_plan_v31 import (
    ViewShapedSqlCompilationBlockedV31,
    build_view_shaped_sql_composition_plan_v31,
    compile_view_shaped_sql_v31,
)
from release_sql_bot.application.ports.in_memory_complete_delivery_v31 import (
    InMemoryCompleteDeliverySourceV31,
)
from release_sql_bot.domain.complete_delivery_v31 import StoredCompleteDeliveryV31
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.fact_bindings_v31 import FactBindingRequestV31
from release_sql_bot.domain.purpose_v31 import (
    HISTORICAL_REPORT_RELEASE_V3_RULE_VERSION,
    DeliveryPurposeV31,
)
from release_sql_bot.domain.rule_structure_v31 import STAGE_ORDER_V31
from tests.v31_delivery_support import build_synthetic_complete_delivery


def _select(stored: StoredCompleteDeliveryV31, purpose: DeliveryPurposeV31):
    source = InMemoryCompleteDeliverySourceV31({stored.rule_version: stored})
    return asyncio.run(
        select_complete_delivery_v31(
            source,
            purpose=purpose,
            rule_version=stored.rule_version,
        )
    )


def test_checked_in_v31_schema_hashes_are_frozen() -> None:
    assert load_fact_binding_schema_v31()["$id"] == FACT_BINDING_SCHEMA_ID_V31
    assert load_candidate_schema_v31()["$id"] == CANDIDATE_SCHEMA_ID_V31
    assert load_result_schema_v31()["$id"] == RESULT_SCHEMA_ID_V31
    assert load_catalog_schema_v3()["$id"] == CATALOG_SCHEMA_ID_V3
    assert FACT_BINDING_SCHEMA_SHA256_V31 == (
        "9102fa3fe2e67e7e266dcd41fc40c7f00a629158fb013f9e61ef739d11a270fc"
    )
    assert CANDIDATE_SCHEMA_SHA256_V31 == (
        "cdd416b23c49e254ca12606f27f4db32ab17c9afe00de26a8d2beb9a83f531c2"
    )
    assert RESULT_SCHEMA_SHA256_V31 == (
        "4e5774c559e770182b6de964324016309941f9f8292395cee2512f5e2048948f"
    )
    assert CATALOG_SCHEMA_SHA256_V3 == (
        "bb2446f2112f073967358c7b0c36a9bab4565ca30d35dbce24142191b50f450c"
    )


def test_synthetic_report_version_returns_five_stage_tree() -> None:
    stored, meta = build_synthetic_complete_delivery()

    delivery = _select(stored, DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION)

    assert delivery.consumable is True
    assert delivery.missing == ()
    assert delivery.stage_names == STAGE_ORDER_V31
    assert delivery.request_count == 2
    assert delivery.executable is False
    assert delivery.schema_version == "3.1.0"
    assert meta["ruleVersion"] == delivery.rule_version
    assert [stage.stage for stage in delivery.candidate.stages] == list(STAGE_ORDER_V31)
    set_facts = [
        item.payload.query_requirements.result.cardinality for item in delivery.batch.requests
    ]
    assert "set" in set_facts
    assert "scalar" in set_facts


def test_historical_version_sql_compilation_fails_closed() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    source = InMemoryCompleteDeliverySourceV31({stored.rule_version: stored})

    with pytest.raises(CompleteDeliveryInvalidV31Error, match="requested purpose"):
        asyncio.run(
            select_complete_delivery_v31(
                source,
                purpose=DeliveryPurposeV31.SQL_COMPILATION,
                rule_version=HISTORICAL_REPORT_RELEASE_V3_RULE_VERSION,
            )
        )


def test_historical_version_optimization_plan_generation_fails_closed() -> None:
    source = InMemoryCompleteDeliverySourceV31()
    with pytest.raises(CompleteDeliveryInvalidV31Error, match="requested purpose"):
        asyncio.run(
            select_complete_delivery_v31(
                source,
                purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
                rule_version=HISTORICAL_REPORT_RELEASE_V3_RULE_VERSION,
            )
        )


def test_missing_catalog_payload_is_not_consumable_and_does_not_fallback() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    stored_missing = stored.model_copy(update={"catalog_payload": None})
    source = InMemoryCompleteDeliverySourceV31({stored.rule_version: stored_missing})

    with pytest.raises(CompleteDeliveryNotConsumableV31Error) as error:
        asyncio.run(
            select_complete_delivery_v31(
                source,
                purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
                rule_version=stored.rule_version,
            )
        )
    assert error.value.missing == ("catalog",)
    assert source.calls == [stored.rule_version]


def test_three_one_version_sql_compilation_fails_closed() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    with pytest.raises(CompleteDeliveryInvalidV31Error, match="requested purpose"):
        _select(stored, DeliveryPurposeV31.SQL_COMPILATION)


def test_missing_result_or_batch_is_not_consumable() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    stored_missing = stored.model_copy(update={"result_payload": None, "batch": None})
    source = InMemoryCompleteDeliverySourceV31({stored.rule_version: stored_missing})

    with pytest.raises(CompleteDeliveryNotConsumableV31Error) as error:
        asyncio.run(
            select_complete_delivery_v31(
                source,
                purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
                rule_version=stored.rule_version,
            )
        )
    assert error.value.missing == ("result", "batch")


def test_missing_candidate_payload_is_not_consumable() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    stored_missing = stored.model_copy(update={"candidate_payload": None})
    source = InMemoryCompleteDeliverySourceV31({stored.rule_version: stored_missing})

    with pytest.raises(CompleteDeliveryNotConsumableV31Error) as error:
        asyncio.run(
            select_complete_delivery_v31(
                source,
                purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
                rule_version=stored.rule_version,
            )
        )
    assert "candidate" in error.value.missing


def test_formula_tree_300_is_rejected() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    stored_old = stored.model_copy(update={"schema_version": "3.0.0"})
    with pytest.raises(CompleteDeliveryInvalidV31Error, match="3.0.0"):
        _select(stored_old, DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION)


def test_executable_delivery_is_rejected() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    stored_exec = stored.model_copy(update={"executable": True})
    with pytest.raises(CompleteDeliveryInvalidV31Error, match="non-executable"):
        _select(stored_exec, DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION)


def test_hash_mismatch_fails_closed() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    payload = dict(stored.candidate_payload or {})
    payload["title"] = "tampered synthetic title"
    stored_bad = stored.model_copy(update={"candidate_payload": payload})
    with pytest.raises(CompleteDeliveryInvalidV31Error, match="hash"):
        _select(stored_bad, DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION)


def test_missing_delivery_is_not_found() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    source = InMemoryCompleteDeliverySourceV31()
    with pytest.raises(CompleteDeliveryNotFoundV31Error):
        asyncio.run(
            select_complete_delivery_v31(
                source,
                purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
                rule_version=stored.rule_version,
            )
        )


def test_v3_consumer_rejects_v31_payload() -> None:
    _stored, meta = build_synthetic_complete_delivery()
    with pytest.raises(ValidationError):
        FactBindingRequestV3.model_validate(meta["statusRequest"])


def test_v31_allows_extra_unused_evidence_rows() -> None:
    _stored, meta = build_synthetic_complete_delivery()
    payload = dict(meta["statusRequest"])
    evidence = list(payload["evidence"])
    evidence.append(
        {
            "evidenceId": "fact.synthetic.unused",
            "kind": "factDeclaration",
            "sourceDocument": "ruleResult",
            "sourcePath": "/factDeclarations/0",
        }
    )
    payload["evidence"] = evidence
    parsed = FactBindingRequestV31.model_validate(payload)
    assert parsed.contract_version == "3.1.0"


def test_composition_plan_is_frozen_and_not_compiled() -> None:
    stored, _meta = build_synthetic_complete_delivery()
    delivery = _select(stored, DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION)
    plan = build_view_shaped_sql_composition_plan_v31(delivery)

    assert plan.sql_generated is False
    assert plan.executable is False
    assert plan.description_is_not_sql is True
    assert plan.result.matches_current_release_view_or_branches is False
    assert plan.combination == "rule-tree-over-fact-queries"
    assert all(item.sql_parameter and not item.database_column for item in plan.runtime_parameters)
    assert plan.member_sets
    assert all(item.member_eligible_boolean_column is False for item in plan.member_sets)
    assert "METADATA_REVIEW" in plan.compilation_blockers
    assert "SQL_NOT_COMPILED" in plan.compilation_blockers
    assert "JOIN" not in plan.compilation_blockers
    assert "WHERE_OR_NOT" not in plan.compilation_blockers
    with pytest.raises(ViewShapedSqlCompilationBlockedV31, match="not compiled") as error:
        compile_view_shaped_sql_v31(delivery)
    assert "METADATA_REVIEW" in error.value.blockers
