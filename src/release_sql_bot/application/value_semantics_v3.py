"""Resolve approved value-encoding and result-semantics bindings (V3).

Pure computation. These bindings are governed context inputs, not Prompt
or model claims. Missing encodings for non-empty allowedValues fail closed.
"""

from __future__ import annotations

from release_sql_bot.domain.project_bindings_v3 import (
    ResolvedFieldV3,
    ResolvedResultSemanticsV3,
    ResolvedValueEncodingV3,
    ResolveMetadataRequestV3,
    ResultSemanticsBindingV3,
    ValueEncodingBindingV3,
)

_VALUE_SEMANTICS_CODES: frozenset[str] = frozenset(
    {
        "VALUE_ENCODING_REQUIRED",
        "VALUE_ENCODING_FIELD_MISMATCH",
        "VALUE_ENCODING_COLUMN_MISMATCH",
        "VALUE_ENCODING_VALUES_INVALID",
        "VALUE_ENCODING_DUPLICATE",
        "RESULT_SEMANTICS_DUPLICATE",
        "RESULT_SEMANTICS_REQUEST_MISMATCH",
    }
)


class MetadataValueSemanticsErrorV3(Exception):
    """Stable neutral error for encoding / result-semantics resolution."""

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in _VALUE_SEMANTICS_CODES:
            raise ValueError("unknown value-semantics issue code")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


def allowed_values_as_strings(values: list[object]) -> tuple[str, ...]:
    """Return allowedValues as strings, or raise if any item is not a scalar string."""

    result: list[str] = []
    for item in values:
        if not isinstance(item, str) or item.strip() != item or not item:
            raise MetadataValueSemanticsErrorV3("VALUE_ENCODING_VALUES_INVALID")
        result.append(item)
    if len(result) != len(set(result)):
        raise MetadataValueSemanticsErrorV3("VALUE_ENCODING_VALUES_INVALID")
    return tuple(result)


def _current_encodings(
    request: ResolveMetadataRequestV3,
) -> tuple[ValueEncodingBindingV3, ...]:
    request_id = request.binding_request.request_id
    matched = tuple(
        item
        for item in request.project_context.value_encoding_bindings
        if item.request_id == request_id
    )
    if len(matched) > 1:
        raise MetadataValueSemanticsErrorV3("VALUE_ENCODING_DUPLICATE")
    return matched


def _current_result_semantics(
    request: ResolveMetadataRequestV3,
) -> ResultSemanticsBindingV3 | None:
    request_id = request.binding_request.request_id
    matched = [
        item
        for item in request.project_context.result_semantics_bindings
        if item.request_id == request_id
    ]
    if len(matched) > 1:
        raise MetadataValueSemanticsErrorV3("RESULT_SEMANTICS_DUPLICATE")
    return matched[0] if matched else None


def resolve_value_encodings_v3(
    request: ResolveMetadataRequestV3,
    field_by_id: dict[str, ResolvedFieldV3],
) -> tuple[ResolvedValueEncodingV3, ...]:
    """Resolve encoding bindings for the current request's factValue field."""

    fact = request.binding_request.fact
    allowed = allowed_values_as_strings(list(fact.allowed_values))
    matched = _current_encodings(request)

    if allowed and not matched:
        raise MetadataValueSemanticsErrorV3("VALUE_ENCODING_REQUIRED")
    if not matched:
        return ()

    binding = matched[0]
    if binding.field_id != "factValue":
        raise MetadataValueSemanticsErrorV3("VALUE_ENCODING_FIELD_MISMATCH")

    fact_value = field_by_id.get("factValue")
    if fact_value is None:
        raise MetadataValueSemanticsErrorV3("VALUE_ENCODING_FIELD_MISMATCH")
    if binding.column_grant_id != fact_value.column_grant_id:
        raise MetadataValueSemanticsErrorV3("VALUE_ENCODING_COLUMN_MISMATCH")

    logical_values = [item.logical_value for item in binding.entries]
    if any(not item or item.strip() != item for item in logical_values):
        raise MetadataValueSemanticsErrorV3("VALUE_ENCODING_VALUES_INVALID")
    if binding.projection_kind == "mappedCase":
        if not allowed or any(value not in allowed for value in logical_values):
            raise MetadataValueSemanticsErrorV3("VALUE_ENCODING_VALUES_INVALID")
    else:
        if tuple(sorted(logical_values)) != tuple(sorted(allowed)):
            raise MetadataValueSemanticsErrorV3("VALUE_ENCODING_VALUES_INVALID")

    resolved = ResolvedValueEncodingV3.model_validate(
        {
            "bindingId": binding.binding_id,
            "requestId": binding.request_id,
            "fieldId": binding.field_id,
            "columnGrantId": binding.column_grant_id,
            "schemaName": fact_value.schema_name,
            "relationName": fact_value.relation_name,
            "columnName": fact_value.column_name,
            "projectionKind": binding.projection_kind,
            "comparisonKind": binding.comparison_kind,
            "nullInput": binding.null_input,
            "unknownPhysical": binding.unknown_physical,
            "entries": [item.model_dump(by_alias=True, mode="json") for item in binding.entries],
            "sourceKind": binding.source_kind,
            "sourceSha256": binding.source_sha256,
        }
    )
    return (resolved,)


def resolve_result_semantics_v3(
    request: ResolveMetadataRequestV3,
) -> ResolvedResultSemanticsV3 | None:
    """Resolve the optional rowset overlay for the current request."""

    binding = _current_result_semantics(request)
    if binding is None:
        return None
    if binding.request_id != request.binding_request.request_id:
        raise MetadataValueSemanticsErrorV3("RESULT_SEMANTICS_REQUEST_MISMATCH")
    return ResolvedResultSemanticsV3.model_validate(binding.model_dump(by_alias=True, mode="json"))


def expected_candidate_result_fields(
    request, consumer_cardinality: str | None
) -> dict[str, object]:
    """Effective candidate result contract. Does not mutate the FBR."""

    result = request.binding_request.query_requirements.result
    cardinality = consumer_cardinality or str(result.cardinality)
    return {
        "columnName": str(result.column_name),
        "dataType": str(result.data_type),
        "cardinality": cardinality,
        "nullable": result.nullable,
        "nullPolicy": str(result.null_policy),
        "unit": result.unit,
    }
