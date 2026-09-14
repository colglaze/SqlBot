"""M3/M6 V3 filter-support regression tests: strict entity-key eq filters.

Verifies that the M3 scope check and full M6 pipeline handle entity-key
eq filters correctly — both the success paths and the rejection of
out-of-scope filter shapes.

All tests use synthetic fixtures from test_v3_filter_support.
No network, no .env, no MongoDB/SQL Server/online model.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from release_sql_bot.application.candidates_v3 import (
    CandidateScopeErrorV3,
    _check_m3_scope,
)
from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.filter_constraints_v3 import (
    qualified_filter_param_names,
)
from release_sql_bot.application.ports.candidate_store_v3 import (
    CandidateStoreV3Outcome,
    CandidateStoreV3Status,
)
from release_sql_bot.application.ports.candidates import CandidateModelResponse
from release_sql_bot.domain.fact_binding_handoffs_v3 import (
    StoredFactBindingHandoffBatchV3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.project_bindings_v3 import (
    ResolveMetadataRequestV3,
)
from release_sql_bot.domain.sql_candidates_v3 import (
    GenerateSqlCandidateRequestV3,
)
from tests.fakes import FixedCandidateModelProvider
from tests.unit.test_candidates_v3 import (
    _build_synthetic_approval_port,
    _InMemoryHandoffRepository,
)
from tests.unit.test_v3_filter_support import (
    _base_request_wire,
    _rebuild_snapshot_and_approval,
    build_filter_request,
    make_filter_item,
)


class _CountingStore:
    def __init__(self, status: CandidateStoreV3Status = CandidateStoreV3Status.STORED) -> None:
        self._status = status
        self.save_count = 0
        self.closed = False
        self.saved_refs: list = []
        self.snapshots: list[dict[str, Any]] = []

    async def initialize(self) -> None:
        return None

    async def save(self, candidate) -> CandidateStoreV3Outcome:
        self.save_count += 1
        import copy

        self.saved_refs.append(candidate)
        self.snapshots.append(copy.deepcopy(candidate.model_dump(by_alias=True, mode="json")))
        return CandidateStoreV3Outcome(
            status=self._status,
            content_sha256=candidate.content_sha256,
        )

    async def close(self) -> None:
        self.closed = True


def _provider_for_filter(content: str | None = None) -> FixedCandidateModelProvider:
    """Build a fixed provider returning a valid filtered candidate."""
    if content is None:
        content = _synthetic_filtered_content()
    return FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="fixed-offline-v3",
                request_id="req-filter",
                model="fixed-model-v3",
                content=content,
            )
        ]
    )


def _synthetic_filtered_content() -> str:
    """Build synthetic model output for a filtered request."""
    import json

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
# 1. Empty filters (old path) still passes
# ---------------------------------------------------------------------------


def test_empty_filters_still_pass_scope_check() -> None:
    """A request with no filters continues to pass the scope check."""
    payload, _ = build_filter_request([])
    _check_m3_scope(payload)


# ---------------------------------------------------------------------------
# 2. Single valid entity-key eq filter
# ---------------------------------------------------------------------------


def test_single_valid_filter_passes_scope() -> None:
    """A single strict entity-key eq filter passes the scope check."""
    payload, _ = build_filter_request([make_filter_item()])
    _check_m3_scope(payload)
    assert qualified_filter_param_names(payload) == frozenset({"syntheticKey"})


# ---------------------------------------------------------------------------
# 3. Multiple valid entity-key filters (same key, different filterIds)
# ---------------------------------------------------------------------------


def test_same_key_two_filter_ids_both_qualified() -> None:
    """Two equivalent filters on the same key are both qualified."""
    items = [
        make_filter_item(filter_id="filter-a"),
        make_filter_item(filter_id="filter-b"),
    ]
    payload, _ = build_filter_request(items)
    _check_m3_scope(payload)
    assert qualified_filter_param_names(payload) == frozenset({"syntheticKey"})


# ---------------------------------------------------------------------------
# 4. Mixed valid + invalid filters → blocked
# ---------------------------------------------------------------------------


def test_mixed_valid_and_invalid_filters_blocked() -> None:
    """When any filter is unqualified, the whole request is blocked."""
    items = [
        make_filter_item(filter_id="valid"),
        make_filter_item(filter_id="invalid", operator="gte"),
    ]
    payload, _ = build_filter_request(items)
    with pytest.raises(CandidateScopeErrorV3, match="M3_SCOPE_UNSUPPORTED_FILTERS"):
        _check_m3_scope(payload)


# ---------------------------------------------------------------------------
# 5. Parameterized invalid filter shapes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"operator": "gte"},
        {"operator": "gt"},
        {"operator": "lt"},
        {"operator": "ne"},
        {"value_kind": "literal"},
        {"required": False},
        {"null_policy": "fail"},
        {"null_policy": "pass"},
    ],
)
def test_invalid_filter_shapes_blocked(overrides: dict[str, Any]) -> None:
    """Various out-of-scope filter shapes are rejected."""
    item = make_filter_item(**overrides)
    payload, _ = build_filter_request([item])
    with pytest.raises(CandidateScopeErrorV3, match="M3_SCOPE_UNSUPPORTED_FILTERS"):
        _check_m3_scope(payload)


# ---------------------------------------------------------------------------
# 6. Non-eq operators and literal values
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "operator",
    ["gt", "gte", "lt", "lte", "ne", "in", "not_in"],
)
def test_non_eq_operators_blocked(operator: str) -> None:
    """Non-eq operators are rejected."""
    payload, _ = build_filter_request([make_filter_item(operator=operator)])
    with pytest.raises(CandidateScopeErrorV3, match="M3_SCOPE_UNSUPPORTED_FILTERS"):
        _check_m3_scope(payload)


def test_literal_filter_blocked() -> None:
    """Literal value filters are rejected."""
    payload, _ = build_filter_request([make_filter_item(value_kind="literal")])
    with pytest.raises(CandidateScopeErrorV3, match="M3_SCOPE_UNSUPPORTED_FILTERS"):
        _check_m3_scope(payload)


# ---------------------------------------------------------------------------
# 7. Full M3 generation with valid filter
# ---------------------------------------------------------------------------


def test_m3_generation_with_valid_filter_provider_one_save_one() -> None:
    """A valid filter request generates exactly one candidate."""
    from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3

    payload, handoff_repo = build_filter_request([make_filter_item()])
    provider = _provider_for_filter()
    approval_port = _build_synthetic_approval_port()

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert candidate.status == "candidate"
    assert candidate.executable is False
    assert candidate.review_status == "pending"
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# 8. Full M6 end-to-end with valid filter → evidenceComplete
# ---------------------------------------------------------------------------


def test_m6_e2e_with_valid_filter_evidence_complete() -> None:
    """A valid entity-key eq filter runs through M3/M4/M6 to evidenceComplete."""
    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    payload, handoff_repo = build_filter_request([make_filter_item()])
    provider = _provider_for_filter()
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    asyncio.run(store.initialize())
    try:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    finally:
        asyncio.run(store.close())

    assert pack.stage == "evidenceComplete"
    assert pack.store_outcome == "stored"
    assert pack.static_status == "passed"
    assert pack.attempt_count == 1
    assert pack.executable is False
    assert pack.prompt_version == "sqlserver-fact-candidate-v3.1"
    assert store.save_count == 1
    assert store.closed is True


# ---------------------------------------------------------------------------
# 9. M6 with invalid filter → blockedUpstream, zero provider/save
# ---------------------------------------------------------------------------


def test_m6_with_invalid_filter_blocked_zero_side_effects() -> None:
    """An invalid filter blocks upstream with zero provider/save/static."""
    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    items = [
        make_filter_item(filter_id="valid"),
        make_filter_item(filter_id="invalid", operator="gte"),
    ]
    payload, handoff_repo = build_filter_request(items)
    provider = _provider_for_filter()
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert pack.stage == "blockedUpstream"
    assert pack.attempt_count == 0
    assert len(provider.calls) == 0
    assert store.save_count == 0


# ---------------------------------------------------------------------------
# Two different entity keys, each with a valid eq filter (M6 success)
# ---------------------------------------------------------------------------


def test_m6_two_different_entity_keys_both_covered() -> None:
    """Two different entity-key params/fields/columns each with an eq filter pass M6."""
    import json

    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop
    from release_sql_bot.application.ports.approval_records_v3 import (
        InMemoryApprovalRecordPortV3,
    )
    from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3

    extra_keys = [
        {
            "parameter_name": "secondKey",
            "field_id": "secondKey",
            "column_name": "second_key",
        }
    ]
    items = [
        make_filter_item(filter_id="filter-first", field_id="syntheticKey"),
        make_filter_item(
            filter_id="filter-second",
            field_id="secondKey",
            parameter_name="secondKey",
        ),
    ]
    payload, handoff_repo = build_filter_request(
        items,
        extra_entity_keys=extra_keys,
    )

    # Build provider content with both WHERE predicates.
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
        "WHERE t.synthetic_key = :syntheticKey "
        "AND t.second_key = :secondKey"
    )
    content = json.dumps(
        {
            "templateCode": "SYNTHETIC_V3_TWO_KEYS",
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

    provider = _provider_for_filter(content)
    store = _CountingStore()

    # Build approval port from the modified wire (extra keys changed hashes).
    wire = payload.resolution_request.model_dump(by_alias=True, mode="json")
    approval = ApprovalRecordV3.model_validate(wire["approvalRecord"])
    approval_port = InMemoryApprovalRecordPortV3(
        records={approval.approval_id: approval},
    )

    from release_sql_bot.application.prompts_v3 import build_sqlserver_candidate_prompt_v3

    prompt = build_sqlserver_candidate_prompt_v3(payload)
    user = json.loads(prompt.user)
    fc = user["filterConstraints"]
    assert len(fc) == 2
    fc_by_id = {f["filterId"]: f for f in fc}
    assert fc_by_id["filter-first"]["parameterName"] == "syntheticKey"
    assert fc_by_id["filter-first"]["columnName"] == "synthetic_key"
    assert fc_by_id["filter-second"]["parameterName"] == "secondKey"
    assert fc_by_id["filter-second"]["columnName"] == "second_key"

    asyncio.run(store.initialize())
    try:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    finally:
        asyncio.run(store.close())

    assert pack.stage == "evidenceComplete"
    assert pack.store_outcome == "stored"
    assert pack.static_status == "passed"
    assert store.save_count == 1


def test_m6_two_entity_keys_delete_one_predicate_m4_blocks() -> None:
    """Deleting one of the two WHERE predicates blocks M4."""
    import json

    from release_sql_bot.application.sql_validation_v3 import validate_sql_candidate_v3
    from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3
    from release_sql_bot.domain.sql_validation_v3 import ValidateSqlCandidateRequestV3
    from release_sql_bot.infrastructure.sql.sqlglot_tsql_v3 import SqlglotTsqlInspectorV3

    extra_keys = [
        {
            "parameter_name": "secondKey",
            "field_id": "secondKey",
            "column_name": "second_key",
        }
    ]
    items = [
        make_filter_item(filter_id="filter-first", field_id="syntheticKey"),
        make_filter_item(
            filter_id="filter-second",
            field_id="secondKey",
            parameter_name="secondKey",
        ),
    ]
    payload, handoff_repo = build_filter_request(
        items,
        extra_entity_keys=extra_keys,
    )

    from release_sql_bot.application.candidates_v3 import generate_sql_candidate_v3

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
    valid_sql = (
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey "
        "AND t.second_key = :secondKey"
    )
    valid_content = json.dumps(
        {
            "templateCode": "SYNTHETIC_V3_TWO_KEYS",
            "sqlTemplate": valid_sql,
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

    from release_sql_bot.application.ports.approval_records_v3 import (
        InMemoryApprovalRecordPortV3,
    )
    from release_sql_bot.application.ports.candidates import CandidateModelResponse
    from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3
    from tests.fakes import FixedCandidateModelProvider

    # Build approval port from the modified wire (extra keys changed hashes).
    wire = payload.resolution_request.model_dump(by_alias=True, mode="json")
    approval = ApprovalRecordV3.model_validate(wire["approvalRecord"])
    approval_port = InMemoryApprovalRecordPortV3(
        records={approval.approval_id: approval},
    )

    candidate = asyncio.run(
        generate_sql_candidate_v3(
            provider=FixedCandidateModelProvider(
                [
                    CandidateModelResponse(
                        provider="fixed-offline-v3",
                        request_id="r",
                        model="fixed-model-v3",
                        content=valid_content,
                    )
                ]
            ),
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    tampered_sql = (
        "SELECT t.synthetic_value AS fact_value "
        "FROM dbo.synthetic_table t "
        "WHERE t.synthetic_key = :syntheticKey"
    )
    cand_wire = candidate.model_dump(by_alias=True, mode="json")
    cand_wire["sqlTemplate"] = tampered_sql
    cand_obj = SqlTemplateCandidateV3.model_validate(cand_wire)
    cand_wire["contentSha256"] = canonical_content_sha256(cand_obj)

    request = ValidateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": payload.model_dump(by_alias=True, mode="json"),
            "candidate": cand_wire,
        }
    )

    report = validate_sql_candidate_v3(request, SqlglotTsqlInspectorV3())
    assert report.status == "blocked"
    assert report.inspection is not None
    codes = {i.code for i in report.issues}
    assert "SQL_FILTER_MISSING" in codes


# ---------------------------------------------------------------------------
# Authorization boundary failures via real M3/M6 entry
# ---------------------------------------------------------------------------


def test_m6_boundary_nullable_column_blocked() -> None:
    """Entity-key column nullable=true blocks via M3 scope."""
    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    payload, handoff_repo = build_filter_request(
        [make_filter_item()],
        make_nullable={"synthetic_key"},
    )
    provider = _provider_for_filter()
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert pack.stage == "blockedUpstream"
    assert pack.attempt_count == 0
    assert len(provider.calls) == 0
    assert store.save_count == 0


def test_m6_boundary_filter_field_not_entity_key_blocked() -> None:
    """Filter field that is not the param's entity-key authorization blocks."""
    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    extra_keys = [
        {
            "parameter_name": "otherParam",
            "field_id": "otherField",
            "column_name": "other_column",
        }
    ]
    items = [
        make_filter_item(
            filter_id="f1",
            field_id="otherField",
            parameter_name="syntheticKey",
        ),
    ]
    payload, handoff_repo = build_filter_request(
        items,
        extra_entity_keys=extra_keys,
    )
    provider = _provider_for_filter()
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert pack.stage == "blockedUpstream"
    assert pack.attempt_count == 0
    assert len(provider.calls) == 0
    assert store.save_count == 0


def test_m6_boundary_param_not_required_blocked() -> None:
    """Entity-key parameter with required=false blocks."""
    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    wire = _base_request_wire()
    wire["bindingRequest"]["queryRequirements"]["filters"]["items"] = [
        make_filter_item(),
    ]
    for p in wire["bindingRequest"]["fact"]["parameters"]:
        if p["name"] == "syntheticKey":
            p["required"] = False

    wire["handoffClosure"]["payload"] = wire["bindingRequest"]
    wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire["bindingRequest"])
    )
    _rebuild_snapshot_and_approval(wire)

    closure = wire["handoffClosure"]
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)
    batch_request_id = closure["requestId"]
    batch_rule_version = closure["ruleVersion"]
    request_dict = {
        "request_id": batch_request_id,
        "rule_version": batch_rule_version,
        "fact_code": closure["factCode"],
        "contract_version": "3.0.0",
        "payload_sha256": closure["payloadSha256"],
        "created_at": now,
        "payload": wire["bindingRequest"],
    }
    batch_sha256 = canonical_sha256(
        [{"requestId": batch_request_id, "payloadSha256": closure["payloadSha256"]}]
    )
    batch = StoredFactBindingHandoffBatchV3.model_validate(
        {
            "_id": batch_rule_version,
            "rule_version": batch_rule_version,
            "contract_version": "3.0.0",
            "request_count": 1,
            "request_ids": [batch_request_id],
            "batch_sha256": batch_sha256,
            "created_at": now,
            "requests": [request_dict],
        }
    )
    handoff_repo = _InMemoryHandoffRepository(batch)
    wire["handoffClosure"]["batchSha256"] = batch_sha256

    request_inner = ResolveMetadataRequestV3.model_validate(wire)
    from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3

    report = resolve_metadata_v3(request_inner)
    payload = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": wire,
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )

    provider = _provider_for_filter()
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert pack.stage == "blockedUpstream"
    assert pack.attempt_count == 0
    assert len(provider.calls) == 0
    assert store.save_count == 0


def test_m6_boundary_param_role_not_entity_key_blocked() -> None:
    """Filter referencing a non-entityKey parameter role blocks."""
    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    wire = _base_request_wire()
    # Add a non-entityKey parameter and reference it in the filter.
    wire["bindingRequest"]["fact"]["parameters"].append(
        {
            "name": "filterParam",
            "role": "filter",
            "dataType": "string",
            "required": True,
            "description": "Non-entity-key parameter.",
        }
    )
    wire["bindingRequest"]["queryRequirements"]["filters"]["items"] = [
        make_filter_item(parameter_name="filterParam", field_id="syntheticKey"),
    ]

    wire["handoffClosure"]["payload"] = wire["bindingRequest"]
    wire["handoffClosure"]["payloadSha256"] = canonical_sha256(
        FactBindingRequestV3.model_validate(wire["bindingRequest"])
    )
    _rebuild_snapshot_and_approval(wire)

    closure = wire["handoffClosure"]
    now = datetime(2026, 9, 9, 0, 0, 0, tzinfo=UTC)
    batch_request_id = closure["requestId"]
    batch_rule_version = closure["ruleVersion"]
    request_dict = {
        "request_id": batch_request_id,
        "rule_version": batch_rule_version,
        "fact_code": closure["factCode"],
        "contract_version": "3.0.0",
        "payload_sha256": closure["payloadSha256"],
        "created_at": now,
        "payload": wire["bindingRequest"],
    }
    batch_sha256 = canonical_sha256(
        [{"requestId": batch_request_id, "payloadSha256": closure["payloadSha256"]}]
    )
    batch = StoredFactBindingHandoffBatchV3.model_validate(
        {
            "_id": batch_rule_version,
            "rule_version": batch_rule_version,
            "contract_version": "3.0.0",
            "request_count": 1,
            "request_ids": [batch_request_id],
            "batch_sha256": batch_sha256,
            "created_at": now,
            "requests": [request_dict],
        }
    )
    handoff_repo = _InMemoryHandoffRepository(batch)
    wire["handoffClosure"]["batchSha256"] = batch_sha256

    request_inner = ResolveMetadataRequestV3.model_validate(wire)
    from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3

    report = resolve_metadata_v3(request_inner)
    payload = GenerateSqlCandidateRequestV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "resolutionRequest": wire,
            "resolutionReport": report.model_dump(by_alias=True, mode="json"),
        }
    )

    provider = _provider_for_filter()
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert pack.stage == "blockedUpstream"
    assert pack.attempt_count == 0
    assert len(provider.calls) == 0
    assert store.save_count == 0


def test_m6_boundary_missing_resolved_filter_blocked() -> None:
    """Tampered report missing resolvedFilter blocks via reference check."""
    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    payload, handoff_repo = build_filter_request([make_filter_item()])

    report_wire = payload.resolution_report.model_dump(by_alias=True, mode="json")
    report_wire["resolvedFilters"] = []
    from release_sql_bot.domain.project_bindings_v3 import BindingResolutionReportV3

    tampered_report = BindingResolutionReportV3.model_validate(report_wire)

    payload_wire = payload.model_dump(by_alias=True, mode="json")
    payload_wire["resolutionReport"] = tampered_report.model_dump(by_alias=True, mode="json")
    tampered_payload = GenerateSqlCandidateRequestV3.model_validate(payload_wire)

    provider = _provider_for_filter()
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=tampered_payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert pack.stage == "blockedUpstream"
    assert pack.attempt_count == 0
    assert len(provider.calls) == 0
    assert store.save_count == 0


# ---------------------------------------------------------------------------
# 10. Input wire unchanged after M6 call
# ---------------------------------------------------------------------------


def test_input_request_unchanged_after_m6_call() -> None:
    """Original request wire is unchanged after the evidence loop call."""
    import json

    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    payload, handoff_repo = build_filter_request([make_filter_item()])
    provider = _provider_for_filter()
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    original_wire = payload.model_dump(by_alias=True, mode="json")
    original_json = json.dumps(original_wire, sort_keys=True)

    asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    after_wire = payload.model_dump(by_alias=True, mode="json")
    after_json = json.dumps(after_wire, sort_keys=True)
    assert after_json == original_json


# ---------------------------------------------------------------------------
# 11. Prompt includes filterConstraints for qualified filters
# ---------------------------------------------------------------------------


def test_prompt_includes_filter_constraints() -> None:
    """The v3.1 prompt includes filterConstraints for qualified filters."""
    import json

    from release_sql_bot.application.prompts_v3 import build_sqlserver_candidate_prompt_v3

    payload, _ = build_filter_request([make_filter_item()])
    prompt = build_sqlserver_candidate_prompt_v3(payload)
    assert prompt.version == "sqlserver-fact-candidate-v3.1"

    user = json.loads(prompt.user)
    assert "filterConstraints" in user
    fc = user["filterConstraints"]
    assert len(fc) == 1
    assert fc[0]["filterId"] == "filter-1"
    assert fc[0]["parameterName"] == "syntheticKey"
    assert fc[0]["schemaName"] == "dbo"
    assert fc[0]["columnName"] == "synthetic_key"


# ---------------------------------------------------------------------------
# 12. Two filterIds on same key both appear in prompt
# ---------------------------------------------------------------------------


def test_two_filter_ids_both_in_prompt() -> None:
    """Two equivalent filters on the same key both appear in filterConstraints."""
    import json

    from release_sql_bot.application.prompts_v3 import build_sqlserver_candidate_prompt_v3

    items = [
        make_filter_item(filter_id="filter-a"),
        make_filter_item(filter_id="filter-b"),
    ]
    payload, _ = build_filter_request(items)
    prompt = build_sqlserver_candidate_prompt_v3(payload)
    user = json.loads(prompt.user)
    fc = user["filterConstraints"]
    assert len(fc) == 2
    assert {f["filterId"] for f in fc} == {"filter-a", "filter-b"}


# ---------------------------------------------------------------------------
# 13. Store immutability on static-blocked filter request
# ---------------------------------------------------------------------------


def test_store_immutability_on_blocked_filter_request() -> None:
    """When static validation blocks, the stored candidate wire is unchanged."""
    import json

    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    # Build a request with a valid filter but use a provider that returns
    # SQL missing the WHERE clause → M4 blocks.
    bad_sql = "SELECT t.synthetic_value AS fact_value FROM dbo.synthetic_table t WHERE 1=1"
    good_content = _synthetic_filtered_content()
    content = json.loads(good_content)
    content["sqlTemplate"] = bad_sql
    bad_content = json.dumps(content, ensure_ascii=False, sort_keys=True)

    payload, handoff_repo = build_filter_request([make_filter_item()])
    provider = _provider_for_filter(bad_content)
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    asyncio.run(store.initialize())
    try:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="fixed-model-v3",
                max_retries=0,
            )
        )
    finally:
        asyncio.run(store.close())

    # Static should be blocked.
    assert pack.static_status == "blocked"
    assert pack.stage == "candidateStored"
    # Store was called once.
    assert store.save_count == 1
    assert len(store.snapshots) == 1
    # The saved snapshot (taken at save time) must match the final candidate wire.
    final_wire = store.saved_refs[0].model_dump(by_alias=True, mode="json")
    assert final_wire == store.snapshots[0]
    # Lifecycle fields unchanged.
    cand = store.saved_refs[0]
    assert cand.status == "candidate"
    assert cand.executable is False
    assert cand.review_status == "pending"


# ---------------------------------------------------------------------------
# 14. Two filterIds on same key: single WHERE covers both (M6)
# ---------------------------------------------------------------------------


def test_m6_single_where_covers_two_filter_ids() -> None:
    """A single eq WHERE predicate can satisfy two filterIds on the same key."""

    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    items = [
        make_filter_item(filter_id="filter-a"),
        make_filter_item(filter_id="filter-b"),
    ]
    payload, handoff_repo = build_filter_request(items)
    provider = _provider_for_filter()
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    asyncio.run(store.initialize())
    try:
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repo,
                approval_port=approval_port,
                store=store,
                model="model-v3",
                max_retries=0,
            )
        )
    finally:
        asyncio.run(store.close())

    assert pack.stage == "evidenceComplete"
    assert pack.static_status == "passed"
    assert store.save_count == 1


# ---------------------------------------------------------------------------
# 15. Parameterized scope failures via full M6 entry
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filter_overrides",
    [
        {"operator": "gte"},
        {"operator": "gt"},
        {"operator": "lt"},
        {"operator": "ne"},
        {"value_kind": "literal"},
        {"required": False},
        {"null_policy": "fail"},
        {"null_policy": "pass"},
    ],
)
def test_m6_scope_failures_full_entry_blocked(
    filter_overrides: dict[str, Any],
) -> None:
    """Scope-level filter failures block via full M6 with zero side effects."""
    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    item = make_filter_item(**filter_overrides)
    payload, handoff_repo = build_filter_request([item])
    provider = _provider_for_filter()
    store = _CountingStore()
    approval_port = _build_synthetic_approval_port()

    pack = asyncio.run(
        run_offline_v3_evidence_loop(
            provider=provider,
            payload=payload,
            handoff_repository=handoff_repo,
            approval_port=approval_port,
            store=store,
            model="fixed-model-v3",
            max_retries=0,
        )
    )

    assert pack.stage == "blockedUpstream"
    assert pack.attempt_count == 0
    assert len(provider.calls) == 0
    assert store.save_count == 0
