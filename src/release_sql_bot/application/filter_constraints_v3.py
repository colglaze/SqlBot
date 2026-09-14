"""Pure-computation V3 filter-constraint qualification helpers.

Determines whether a V3 source request's ``filters.items`` are strict
entity-key equality filters that the current M3/M4 slice can support.

A filter is qualified only when **all** of the following hold:

1. ``operator = eq``
2. ``value.kind = parameter`` with a non-null ``parameterName``
3. ``required = true``
4. ``nullPolicy = error``
5. The named parameter exists in ``fact.parameters`` with
   ``role = entityKey`` and ``required = true``
6. The filter's ``fieldId`` equals the ``fieldId`` of the entity-key
   authorization for the current request and that parameter
7. The M2 ``resolvedEntityKey`` and ``resolvedFilter`` for that parameter
   resolve to the **same** ``(schema, relation, column)``
8. That physical column is explicitly ``nullable = false`` in the approved
   snapshot

The helpers here perform only in-memory checks against already-validated,
re-computed M2 results. They do not access repositories, providers, SQL, or
the network.
"""

from __future__ import annotations

from typing import Any

from release_sql_bot.domain.fact_bindings_v3 import FilterRequirementV3
from release_sql_bot.domain.project_bindings_v3 import (
    BindingResolutionReportV3,
)
from release_sql_bot.domain.sql_candidates_v3 import GenerateSqlCandidateRequestV3


def qualified_filter_param_names(
    payload: GenerateSqlCandidateRequestV3,
) -> frozenset[str]:
    """Return the parameter names whose filters are fully qualified.

    A parameter appears in the returned set only when **every** filter that
    references it (by parameter name) passes all qualification criteria and
    there is at least one such filter. Returns an empty set when the request
    has no filters or when any filter fails qualification.
    """
    request = payload.resolution_request.binding_request
    report = payload.resolution_report

    if not request.query_requirements.filters.items:
        return frozenset()

    # Build lookup structures once.
    entity_key_param = _entity_key_param_map(payload)
    entity_key_col = _entity_key_column_map(report)
    filter_col = _filter_column_map(report)
    snap_cols = _snapshot_columns(payload)

    qualified: set[str] = set()

    for filt in request.query_requirements.filters.items:
        param_name = _qualified_single_filter(
            filt=filt,
            entity_key_param=entity_key_param,
            entity_key_col=entity_key_col,
            filter_col=filter_col,
            snap_cols=snap_cols,
        )
        if param_name is None:
            # One unqualified filter invalidates the whole set.
            return frozenset()
        qualified.add(param_name)

    return frozenset(qualified)


def _entity_key_param_map(
    payload: GenerateSqlCandidateRequestV3,
) -> dict[str, dict[str, Any]]:
    """Map parameterName → {field_id, role, required} for entity-key params."""
    result = {}
    for param in payload.resolution_request.binding_request.fact.parameters:
        if str(param.role) == "entityKey" and param.required:
            result[param.name] = {
                "field_id": _lookup_entity_key_field_id(payload, param.name),
                "role": str(param.role),
                "required": param.required,
            }
    return result


def _lookup_entity_key_field_id(
    payload: GenerateSqlCandidateRequestV3,
    parameter_name: str,
) -> str | None:
    """Return the fieldId authorized for this entity-key parameter."""
    request_id = payload.resolution_request.binding_request.request_id
    for eka in payload.resolution_request.project_context.entity_key_authorizations:
        if eka.request_id == request_id and eka.parameter_name == parameter_name:
            return eka.field_id
    return None


def _entity_key_column_map(
    report: BindingResolutionReportV3,
) -> dict[str, tuple[str, str, str]]:
    """Map parameterName → (schema, relation, column) from resolved keys."""
    return {
        ek.parameter_name: (ek.schema_name, ek.relation_name, ek.column_name)
        for ek in report.resolved_entity_keys
    }


def _filter_column_map(
    report: BindingResolutionReportV3,
) -> dict[str, tuple[str, str, str]]:
    """Map parameterName → (schema, relation, column) from resolved filters.

    The parameter name is derived from the filter's input declaration, which
    the resolver does not directly expose; we match by fieldId → entity-key
    parameter instead. This map is keyed by fieldId.
    """
    return {
        f.field_id: (f.schema_name, f.relation_name, f.column_name) for f in report.resolved_filters
    }


def _snapshot_columns(
    payload: GenerateSqlCandidateRequestV3,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Map (schema, relation, column) → column metadata from the snapshot."""
    result = {}
    for rel in payload.resolution_request.metadata_snapshot.relations:
        for col in rel.columns:
            result[(rel.schema_name, rel.relation_name, col.column_name)] = {
                "nullable": col.nullable,
            }
    return result


def _qualified_single_filter(
    filt: FilterRequirementV3,
    entity_key_param: dict[str, dict[str, Any]],
    entity_key_col: dict[str, tuple[str, str, str]],
    filter_col: dict[str, tuple[str, str, str]],
    snap_cols: dict[tuple[str, str, str], dict[str, Any]],
) -> str | None:
    """Check one filter. Returns the qualified param name or None."""

    # 1. operator must be eq
    if str(filt.operator) != "eq":
        return None

    # 2. value must be a parameter reference
    if filt.value is None or str(filt.value.kind) != "parameter":
        return None
    param_name = filt.value.parameter_name
    if param_name is None:
        return None

    # 3. required must be true
    if not filt.required:
        return None

    # 4. nullPolicy must be error
    if str(filt.null_policy) != "error":
        return None

    # 5. parameter must be a declared entity-key parameter
    ek_param = entity_key_param.get(param_name)
    if ek_param is None:
        return None

    # 6. filter.fieldId must match the entity-key authorization fieldId
    authorized_field_id = ek_param["field_id"]
    if authorized_field_id is None or filt.field_id != authorized_field_id:
        return None

    # 7. resolvedEntityKey and resolvedFilter must agree on the column
    ek_tuple = entity_key_col.get(param_name)
    if ek_tuple is None:
        return None
    # Match resolved filter by fieldId
    filt_tuple = filter_col.get(filt.field_id)
    if filt_tuple is None:
        return None
    if filt_tuple != ek_tuple:
        return None

    # 8. column must be explicitly nullable=false in the snapshot
    col_meta = snap_cols.get(ek_tuple)
    if col_meta is None:
        return None
    if col_meta["nullable"] is not False:
        return None

    return param_name
