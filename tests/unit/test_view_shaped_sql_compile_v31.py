from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from release_sql_bot.application.complete_delivery_intake_v31 import select_complete_delivery_v31
from release_sql_bot.application.ports.in_memory_complete_delivery_v31 import (
    InMemoryCompleteDeliverySourceV31,
)
from release_sql_bot.application.ports.sql_ast_v3 import (
    OfflineColumnV3,
    OfflineRelationV3,
    SqlGatePolicyV3,
    SqlInspectionRequestV3,
)
from release_sql_bot.application.view_shaped_compile_v31 import (
    ViewShapedSqlCompilationBlockedV31,
    compile_view_shaped_sql_v31,
)
from release_sql_bot.application.view_shaped_sql_validation_v31 import (
    compile_and_validate_view_shaped_sql_v31,
    mapping_offline_relations_v31,
    validate_view_shaped_sql_candidate_v31,
)
from release_sql_bot.domain.complete_delivery_v31 import CompleteDeliveryV31
from release_sql_bot.domain.fact_bindings_v3 import AggregationRequirementV3, FactKindV3
from release_sql_bot.domain.purpose_v31 import DeliveryPurposeV31
from release_sql_bot.domain.view_shaped_sql_v31 import FactMappingGrantV31, KeyBindingV31
from release_sql_bot.infrastructure.sql.sqlglot_tsql_v3 import SqlglotTsqlInspectorV3
from release_sql_bot.infrastructure.sql.sqlglot_tsql_v31_view import inspect_view_shaped_sql_v31
from tests.v31_delivery_support import (
    build_synthetic_complete_delivery,
    build_synthetic_mapping_bundle,
)


def _delivery():
    stored, _meta = build_synthetic_complete_delivery()
    source = InMemoryCompleteDeliverySourceV31({stored.rule_version: stored})
    return asyncio.run(
        select_complete_delivery_v31(
            source,
            purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
            rule_version=stored.rule_version,
        )
    )


def test_synthetic_mapping_compiles_parameterized_view_shaped_sql() -> None:
    delivery = _delivery()
    mapping = build_synthetic_mapping_bundle()

    candidate = compile_and_validate_view_shaped_sql_v31(delivery, mapping)

    assert candidate.sql_generated is True
    assert candidate.executable is False
    assert candidate.static_status == "passed"
    assert candidate.static_issue_codes == ()
    sql = candidate.sql_template
    assert "SELECT" in sql
    assert "OUTER APPLY" in sql
    assert "NOT EXISTS" in sql
    assert ":taskId" in sql
    assert ":evaluationDate" in sql
    assert ":rawDataReleasedCutoffDate" in sql
    assert "Synthetic condition" not in sql
    assert "Synthetic ready compare" not in sql
    assert "eligible" not in sql.lower()
    assert "#" not in sql
    assert "N'READY'" in sql
    assert "N'STATE_INVALID'" in sql
    assert "CONCAT_WS" in sql
    assert "THEN 1 WHEN NOT (" in sql
    assert "ELSE NULL END" in sql
    assert "FACT_VALUE_MISSING_OR_INVALID" in sql
    assert candidate.bound_literals
    assert all(
        not str(value).startswith("Synthetic") for value in candidate.bound_literals.values()
    )


def test_missing_mapping_stays_metadata_review() -> None:
    delivery = _delivery()
    with pytest.raises(ViewShapedSqlCompilationBlockedV31) as error:
        compile_view_shaped_sql_v31(delivery)
    assert "METADATA_REVIEW" in error.value.blockers
    assert "SQL_NOT_COMPILED" in error.value.blockers


def test_partial_mapping_stays_metadata_review() -> None:
    delivery = _delivery()
    mapping = build_synthetic_mapping_bundle()
    mapping = mapping.model_copy(update={"grants": mapping.grants[:1]})
    with pytest.raises(ViewShapedSqlCompilationBlockedV31) as error:
        compile_view_shaped_sql_v31(delivery, mapping)
    assert error.value.blockers == ("METADATA_REVIEW",)


def test_descriptions_and_java_do_not_enter_sql() -> None:
    delivery = _delivery()
    sql = compile_and_validate_view_shaped_sql_v31(
        delivery, build_synthetic_mapping_bundle()
    ).sql_template
    for stage in delivery.candidate.stages:
        for rule in stage.rules:
            assert rule.title not in sql
            assert rule.failure_reason not in sql
            if rule.when is not None:
                assert rule.when.description not in sql


def test_v31_inspector_rejects_dml_and_star() -> None:
    relations = mapping_offline_relations_v31(build_synthetic_mapping_bundle())
    dml = inspect_view_shaped_sql_v31(
        "DELETE FROM synth.synth_task WHERE taskId = :taskId",
        relations=relations,
        allowed_parameters=frozenset({"taskId"}),
    )
    assert "SQL_ROOT_NOT_SELECT" in dml.issue_codes or "SQL_DML_DDL" in dml.issue_codes
    star = inspect_view_shaped_sql_v31(
        "SELECT * FROM synth.synth_task AS subject WHERE subject.taskId = :taskId",
        relations=relations,
        allowed_parameters=frozenset({"taskId"}),
    )
    assert "SQL_STAR" in star.issue_codes


def test_v3_join_gate_remains_closed() -> None:
    inspector = SqlglotTsqlInspectorV3()
    result = inspector.inspect(
        SqlInspectionRequestV3(
            sql=(
                "SELECT a.status_code AS fact_value "
                "FROM synth.synth_task_status AS a "
                "JOIN synth.synth_task AS b ON a.taskId = b.taskId "
                "WHERE a.taskId = :taskId"
            ),
            dialect="tsql",
            identifier_case_sensitivity="sensitive",
            offline_schema=(
                OfflineRelationV3(
                    schema_name="synth",
                    relation_name="synth_task_status",
                    columns=(
                        OfflineColumnV3(name="taskId", sql_type="nvarchar"),
                        OfflineColumnV3(name="status_code", sql_type="int"),
                    ),
                ),
                OfflineRelationV3(
                    schema_name="synth",
                    relation_name="synth_task",
                    columns=(OfflineColumnV3(name="taskId", sql_type="nvarchar"),),
                ),
            ),
            gate_policy=SqlGatePolicyV3(),
        )
    )
    assert "SQL_JOIN" in {issue.code for issue in result.issues}


def test_temp_relation_is_rejected_by_mapping_contract() -> None:
    with pytest.raises(ValidationError):
        FactMappingGrantV31(
            fact_code="task.status_code",
            schema_name="synth",
            relation_name="#temp_status",
            column_name="status_code",
            cardinality="scalar",
            key_bindings=(KeyBindingV31(parameter_name="taskId", column_name="taskId"),),
        )
    relations = mapping_offline_relations_v31(build_synthetic_mapping_bundle())
    temp = inspect_view_shaped_sql_v31(
        "SELECT subject.taskId FROM synth.#tmp AS subject WHERE subject.taskId = :taskId",
        relations=relations,
        allowed_parameters=frozenset({"taskId"}),
    )
    assert "SQL_TEMP_OBJECT" in temp.issue_codes or "SQL_OBJECT_NOT_ALLOWED" in temp.issue_codes


def _aggregation(**payload: object) -> AggregationRequirementV3:
    return AggregationRequirementV3.model_validate(payload)


def _with_status_aggregation(
    delivery: CompleteDeliveryV31,
    *,
    fact_kind: FactKindV3,
    aggregation: AggregationRequirementV3,
) -> CompleteDeliveryV31:
    requests = []
    for wrapper in delivery.batch.requests:
        if wrapper.fact_code != "task.status_code":
            requests.append(wrapper)
            continue
        payload = wrapper.payload.model_copy(
            update={
                "fact": wrapper.payload.fact.model_copy(update={"fact_kind": fact_kind}),
                "query_requirements": wrapper.payload.query_requirements.model_copy(
                    update={"aggregation": aggregation}
                ),
            }
        )
        requests.append(wrapper.model_copy(update={"payload": payload}))
    return delivery.model_copy(
        update={"batch": delivery.batch.model_copy(update={"requests": requests})}
    )


def test_count_shape_compiles_count_star() -> None:
    delivery = _with_status_aggregation(
        _delivery(),
        fact_kind=FactKindV3.AGGREGATE,
        aggregation=_aggregation(
            mode="compute",
            function="count",
            inputFieldIds=["factValue"],
            groupByFieldIds=[],
            distinct=False,
            evidenceIds=["query.task.status_code"],
        ),
    )
    candidate = compile_and_validate_view_shaped_sql_v31(delivery, build_synthetic_mapping_bundle())
    assert "COUNT(*)" in candidate.sql_template
    assert candidate.static_status == "passed"
    assert candidate.executable is False


def test_exists_shape_compiles_exists_apply() -> None:
    delivery = _with_status_aggregation(
        _delivery(),
        fact_kind=FactKindV3.EXISTS,
        aggregation=_aggregation(
            mode="exists",
            function=None,
            inputFieldIds=[],
            groupByFieldIds=[],
            distinct=None,
            evidenceIds=["query.task.status_code"],
        ),
    )
    candidate = compile_and_validate_view_shaped_sql_v31(delivery, build_synthetic_mapping_bundle())
    assert "CASE WHEN EXISTS (" in candidate.sql_template
    assert "SELECT 1" in candidate.sql_template
    assert candidate.static_status == "passed"


def test_sum_and_grouped_count_remain_blocked() -> None:
    mapping = build_synthetic_mapping_bundle()
    summed = _with_status_aggregation(
        _delivery(),
        fact_kind=FactKindV3.AGGREGATE,
        aggregation=_aggregation(
            mode="compute",
            function="sum",
            inputFieldIds=["factValue"],
            groupByFieldIds=[],
            distinct=False,
            evidenceIds=["query.task.status_code"],
        ),
    )
    grouped = _with_status_aggregation(
        _delivery(),
        fact_kind=FactKindV3.AGGREGATE,
        aggregation=_aggregation(
            mode="compute",
            function="count",
            inputFieldIds=["factValue"],
            groupByFieldIds=["factValue"],
            distinct=False,
            evidenceIds=["query.task.status_code"],
        ),
    )
    with pytest.raises(ViewShapedSqlCompilationBlockedV31) as sum_error:
        compile_view_shaped_sql_v31(summed, mapping)
    with pytest.raises(ViewShapedSqlCompilationBlockedV31) as group_error:
        compile_view_shaped_sql_v31(grouped, mapping)
    assert sum_error.value.blockers == ("AGGREGATION",)
    assert group_error.value.blockers == ("AGGREGATION",)


def test_unresolved_mapping_candidate_is_not_a_compile_grant() -> None:
    delivery = _delivery()
    assert all(item.payload.mapping_is_unresolved() for item in delivery.batch.requests)
    with pytest.raises(ViewShapedSqlCompilationBlockedV31) as error:
        compile_view_shaped_sql_v31(delivery)
    assert "METADATA_REVIEW" in error.value.blockers


def test_validation_can_block_tampered_sql() -> None:
    delivery = _delivery()
    mapping = build_synthetic_mapping_bundle()
    candidate = compile_view_shaped_sql_v31(delivery, mapping)
    tampered = candidate.model_copy(update={"sql_template": candidate.sql_template + "; SELECT 1"})
    report = validate_view_shaped_sql_candidate_v31(tampered, mapping)
    assert report.static_status == "blocked"
    assert report.static_issue_codes
    assert report.executable is False
