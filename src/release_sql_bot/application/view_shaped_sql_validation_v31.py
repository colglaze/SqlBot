"""Independent static validation for compiled view-shaped SQL."""

from __future__ import annotations

from release_sql_bot.application.view_shaped_compile_v31 import compile_view_shaped_sql_v31
from release_sql_bot.domain.complete_delivery_v31 import CompleteDeliveryV31
from release_sql_bot.domain.view_shaped_sql_v31 import (
    ViewShapedMappingBundleV31,
    ViewShapedSqlCandidateV31,
)
from release_sql_bot.infrastructure.sql.sqlglot_tsql_v31_view import (
    ViewShapedRelationV31,
    inspect_view_shaped_sql_v31,
)


def mapping_offline_relations_v31(
    mapping: ViewShapedMappingBundleV31,
) -> tuple[ViewShapedRelationV31, ...]:
    grouped: dict[tuple[str, str], set[str]] = {}
    subject_columns = {item.column_name for item in mapping.subject.key_bindings}
    grouped[(mapping.subject.schema_name, mapping.subject.relation_name)] = set(subject_columns)
    for grant in mapping.grants:
        columns = grouped.setdefault((grant.schema_name, grant.relation_name), set())
        columns.add(grant.column_name)
        for binding in grant.key_bindings:
            columns.add(binding.column_name)
    return tuple(
        ViewShapedRelationV31(
            schema_name=schema,
            relation_name=relation,
            columns=tuple(sorted(cols)),
        )
        for (schema, relation), cols in grouped.items()
    )


def compile_and_validate_view_shaped_sql_v31(
    delivery: CompleteDeliveryV31,
    mapping: ViewShapedMappingBundleV31,
) -> ViewShapedSqlCandidateV31:
    candidate = compile_view_shaped_sql_v31(delivery, mapping)
    return validate_view_shaped_sql_candidate_v31(candidate, mapping)


def validate_view_shaped_sql_candidate_v31(
    candidate: ViewShapedSqlCandidateV31,
    mapping: ViewShapedMappingBundleV31,
) -> ViewShapedSqlCandidateV31:
    inspection = inspect_view_shaped_sql_v31(
        candidate.sql_template,
        relations=mapping_offline_relations_v31(mapping),
        allowed_parameters=frozenset(candidate.parameter_names),
    )
    passed = not inspection.issue_codes
    return candidate.model_copy(
        update={
            "static_status": "passed" if passed else "blocked",
            "static_issue_codes": inspection.issue_codes,
        }
    )
