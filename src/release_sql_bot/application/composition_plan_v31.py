"""Freeze the one-view-shaped SQL combination plan. Compile lives elsewhere."""

from __future__ import annotations

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.application.view_shaped_compile_v31 import (
    ViewShapedSqlCompilationBlockedV31,
    compile_blockers_v31,
    compile_view_shaped_sql_v31,
)
from release_sql_bot.domain.complete_delivery_v31 import CompleteDeliveryV31
from release_sql_bot.domain.composition_plan_v31 import (
    FactQueryRefV31,
    MemberSetQueryV31,
    RuntimeParameterBindingV31,
    ViewShapedResultContractV31,
    ViewShapedSqlCompositionPlanV31,
)
from release_sql_bot.domain.rule_structure_v31 import RuleStructureCandidateV31

__all__ = [
    "ViewShapedSqlCompilationBlockedV31",
    "build_view_shaped_sql_composition_plan_v31",
    "compile_view_shaped_sql_v31",
]


def build_view_shaped_sql_composition_plan_v31(
    delivery: CompleteDeliveryV31,
) -> ViewShapedSqlCompositionPlanV31:
    if not delivery.consumable:
        raise ViewShapedSqlCompilationBlockedV31(("METADATA_REVIEW", "SQL_NOT_COMPILED"))

    candidate = delivery.candidate
    fact_queries = tuple(
        FactQueryRefV31(
            fact_code=request.payload.fact.fact_code,
            cardinality=request.payload.query_requirements.result.cardinality,
            grain=request.payload.fact.grain,
        )
        for request in delivery.batch.requests
    )
    entity_keys = tuple(
        dict.fromkeys(
            parameter
            for request in delivery.batch.requests
            for parameter in request.payload.query_requirements.entity.key_parameters
        )
    )
    grain = delivery.batch.requests[0].payload.fact.grain
    runtime_parameters = tuple(
        RuntimeParameterBindingV31(
            name=parameter.name,
            role=parameter.role.value,
            data_type=parameter.data_type.value,
            sql_parameter=True,
        )
        for parameter in candidate.runtime_parameters
    )
    member_sets = tuple(
        MemberSetQueryV31(collection_fact_code=code)
        for code in sorted(_collection_fact_codes(candidate))
    )
    return ViewShapedSqlCompositionPlanV31(
        schema_version="1.0.0",
        rule_version=delivery.rule_version,
        catalog_digest=delivery.catalog_digest,
        candidate_payload_sha256=canonical_sha256(candidate),
        stage_order=delivery.stage_names,
        eligibility_on_hit="firstMatchThenContinue",
        other_stages_on_hit="terminate",
        combination="rule-tree-over-fact-queries",
        result=ViewShapedResultContractV31(
            grain=grain,
            entity_key_parameters=entity_keys,
            columns=("entity_keys", "outcome", "reasonCode", "matchedRuleCodes"),
        ),
        fact_queries=fact_queries,
        runtime_parameters=runtime_parameters,
        member_sets=member_sets,
        compilation_blockers=compile_blockers_v31(delivery),
    )


def _collection_fact_codes(candidate: RuleStructureCandidateV31) -> set[str]:
    codes: set[str] = set()
    for stage in candidate.stages:
        for rule in stage.rules:
            if rule.when is not None:
                codes |= rule.when.collection_fact_codes()
    return codes
