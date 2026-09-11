"""V3 metadata-resolution physical-reference helpers (M2 subtasks 5–11).

Internal helpers that re-validate input consistency and resolve physical
references for a ``ResolveMetadataRequestV3`` without accessing any repository,
provider, SQL Server, or environment.

Implemented capabilities:

* Input gate ``_validate_resolution_input_v3``: structural re-validation plus
  six-group content/scope checks (DEV §5.3.0).
* Single column-grant resolution ``_resolve_column_grant_v3``: column grant
  → relation grant → snapshot relation → snapshot column (DEV §5.3.1).
* Field and entity-key binding closure ``_resolve_fields_and_entity_keys_v3``:
  explicit field-authorization chain and entity-key authorization closure
  (DEV §5.3.2).
* Filter resolution ``_resolve_filters_v3``: maps each filter item to its
  authorized physical column reference (via the already-resolved field
  authorization) and verifies each item's ``evidenceIds`` against the
  request's top-level evidence (DEV §5.3.3).  This helper only completes
  field physical mapping and evidence-reference checking; it does NOT prove
  filter semantics correct, SQL executable, or complete M2 finished.
* Aggregation resolution ``_resolve_aggregation_v3``: verifies that every
  field referenced by the aggregation declaration (inputFieldIds +
  groupByFieldIds) is covered by an authorized field result, then copies
  the six declared fields verbatim into a new ``ResolvedAggregationV3``
  (DEV §5.3.4).  This helper only proves field-reference authorization
  closure and declaration preservation; it does NOT compute aggregation,
  generate SQL, or prove aggregation results or type correctness.
* Time-range resolution ``_resolve_time_range_v3``: for ``none`` mode
  returns null physical identifiers; for ``asOf``/``between`` maps the
  time field to its authorized physical column reference (DEV §5.3.5).
  This helper only proves the time-field authorization mapping; it does
  NOT validate date semantics, SQL executability, or time-range correctness.
* Join-grant resolution ``_resolve_join_grant_v3``: validates a specified
  join grant, resolves both column grants, and verifies exactly one
  snapshot relationship edge matches (undirected) (DEV §5.3.6).  This
  helper only proves the specified grant's physical closure; it does NOT
  select join paths, generate SQL, or prove join correctness.

Success proves only that the input is internally consistent and that the
referenced physical objects exist in the carried snapshot; it does NOT prove
approval truthiness, repository attestation, or SQL executability.
The public ``resolve_metadata_v3`` orchestrator and full report assembly
are NOT implemented yet.  Future application service must independently
re-run these checks and complete the eight deterministic resolution steps
(DEV §5.3).

Pure computation: no repository, provider, SQL, or environment access.
"""

from __future__ import annotations

from re import fullmatch
from typing import Any

from release_sql_bot.application.canonical import canonical_json_bytes
from release_sql_bot.application.validate_approval_closure_v3 import (
    validate_approval_closure_v3,
)
from release_sql_bot.application.validate_handoff_closure_v3 import (
    validate_handoff_closure_v3,
)
from release_sql_bot.domain.handoff_closure_v3 import (
    HandoffClosureValidationErrorV3,
)
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalClosureValidationErrorV3,
    ColumnGrantV3,
    EntityKeyAuthorizationV3,
    FieldBindingAuthorizationV3,
    GovernedColumnV3,
    GovernedMetadataSnapshotV3,
    GovernedRelationshipV3,
    GovernedRelationV3,
    JoinGrantV3,
    JoinTypeV3,
    PhysicalColumnRefV3,
    RelationGrantV3,
    ResolvedAggregationV3,
    ResolvedEntityKeyV3,
    ResolvedFieldV3,
    ResolvedFilterV3,
    ResolvedTimeRangeV3,
    ResolveMetadataRequestV3,
)

# ---------------------------------------------------------------------------
# Shared stable-ID pattern (mirrors ColumnGrantV3.grantId)
# ---------------------------------------------------------------------------

_GRANT_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"

# ---------------------------------------------------------------------------
# Neutral error codes (DEV §5.3.0)
# ---------------------------------------------------------------------------

_METADATA_RESOLUTION_INPUT_CODES: frozenset[str] = frozenset(
    {
        "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID",
        "HANDOFF_BINDING_REQUEST_MISMATCH",
        "PROJECT_REF_MISMATCH",
        "RULE_REF_MISMATCH",
        "REQUEST_NOT_IN_CONTEXT",
    }
)

# Existing closure-validation codes preserved verbatim (not rewritten)
_HANDOFF_CODES: frozenset[str] = frozenset(
    {
        "HANDOFF_STRUCTURE_INVALID",
        "HANDOFF_SCHEMA_SOURCE_INVALID",
        "HANDOFF_SCHEMA_REF_MISMATCH",
        "HANDOFF_PAYLOAD_SCHEMA_INVALID",
        "HANDOFF_IDENTITY_MISMATCH",
        "HANDOFF_PAYLOAD_HASH_MISMATCH",
    }
)

_APPROVAL_CODES: frozenset[str] = frozenset(
    {
        "APPROVAL_ID_MISMATCH",
        "APPROVAL_POLICY_MISMATCH",
        "APPROVAL_TIME_MISMATCH",
        "APPROVAL_CONTEXT_REF_MISMATCH",
        "APPROVAL_SNAPSHOT_REF_MISMATCH",
        "APPROVAL_CONTENT_HASH_MISMATCH",
        "APPROVAL_CONTEXT_NOT_APPROVED",
        "APPROVAL_SNAPSHOT_NOT_APPROVED",
        "APPROVAL_SNAPSHOT_BINDING_MISMATCH",
    }
)


class MetadataResolutionInputErrorV3(Exception):
    """Stable neutral error for V3 metadata-resolution input gating.

    Carries only the issue code. Never carries raw IDs, hashes, private
    objects, or original payloads. Accepts the five new gate codes plus
    the existing handoff and approval closure codes.
    """

    _ALLOWED_CODES: frozenset[str] = (
        _METADATA_RESOLUTION_INPUT_CODES | _HANDOFF_CODES | _APPROVAL_CODES
    )

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in self._ALLOWED_CODES:
            raise ValueError("unknown metadata-resolution input issue code")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


def _revalidate_request_structure(value: Any) -> ResolveMetadataRequestV3:
    """Re-validate request shape and return an independent verified copy.

    Non-``ResolveMetadataRequestV3`` root objects (dicts, V2 models, etc.) map to
    ``METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID``. Serialization uses
    ``warnings="error"`` so that non-serializable content or type warnings are
    caught and mapped to the same neutral code. Returns a freshly validated model
    for downstream use.

    Only structural errors are caught here; business-logic errors from later
    steps propagate as their own typed exceptions.
    """
    if not isinstance(value, ResolveMetadataRequestV3):
        raise MetadataResolutionInputErrorV3("METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID")
    try:
        wire = value.model_dump(by_alias=True, mode="json", warnings="error")
        verified = ResolveMetadataRequestV3.model_validate(wire)
    except Exception:  # noqa: BLE001 — map any structural failure to neutral code
        raise MetadataResolutionInputErrorV3(
            "METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID"
        ) from None
    return verified


def _check_binding_request_matches_payload(request: ResolveMetadataRequestV3) -> None:
    """bindingRequest must be byte-identical to closure.payload (canonical JSON)."""
    closure_bytes = canonical_json_bytes(request.handoff_closure.payload)
    request_bytes = canonical_json_bytes(request.binding_request)
    if closure_bytes != request_bytes:
        raise MetadataResolutionInputErrorV3("HANDOFF_BINDING_REQUEST_MISMATCH")


def _check_project_ref(request: ResolveMetadataRequestV3) -> None:
    """request.projectRef must equal projectContext.projectRef (all fields)."""
    if request.project_ref != request.project_context.project_ref:
        raise MetadataResolutionInputErrorV3("PROJECT_REF_MISMATCH")


def _check_rule_ref(request: ResolveMetadataRequestV3) -> None:
    """projectContext.ruleRef must equal bindingRequest.ruleRef (all fields)."""
    if request.project_context.rule_ref != request.binding_request.rule_ref:
        raise MetadataResolutionInputErrorV3("RULE_REF_MISMATCH")


def _check_request_in_context(request: ResolveMetadataRequestV3) -> None:
    """bindingRequest.requestId must be in projectContext.requestIds."""
    if request.binding_request.request_id not in request.project_context.request_ids:
        raise MetadataResolutionInputErrorV3("REQUEST_NOT_IN_CONTEXT")


def _validate_resolution_input_v3(
    request: ResolveMetadataRequestV3,
) -> ResolveMetadataRequestV3:
    """Validate V3 metadata-resolution input consistency (fail-fast).

    Re-validates the request structure, handoff closure, binding-request
    consistency, approval closure, and project/rule/request scope. Returns an
    independent verified copy on success; never mutates the original input.

    Args:
        request: V3 metadata-resolution request to validate.

    Returns:
        A freshly validated ``ResolveMetadataRequestV3`` copy.

    Raises:
        MetadataResolutionInputErrorV3: on the first failing check.
    """
    # 0. Re-validate structure; use the verified copy for all later steps
    verified = _revalidate_request_structure(request)

    # 1. Handoff content closure (6-group, preserves original codes)
    try:
        validate_handoff_closure_v3(verified.handoff_closure)
    except HandoffClosureValidationErrorV3 as exc:
        raise MetadataResolutionInputErrorV3(exc.code) from None

    # 2. bindingRequest must match closure.payload byte-for-byte
    _check_binding_request_matches_payload(verified)

    # 3. Approval closure (9-group, preserves original codes)
    try:
        validate_approval_closure_v3(
            verified.project_context,
            verified.metadata_snapshot,
            verified.approval_record,
        )
    except ApprovalClosureValidationErrorV3 as exc:
        raise MetadataResolutionInputErrorV3(exc.code) from None

    # 4. Project scope
    _check_project_ref(verified)

    # 5. Rule scope
    _check_rule_ref(verified)

    # 6. Current request within context scope
    _check_request_in_context(verified)

    return verified


# ---------------------------------------------------------------------------
# Column-grant physical-reference resolution (DEV §5.3.1)
# ---------------------------------------------------------------------------

_COLUMN_RESOLUTION_CODES: frozenset[str] = frozenset(
    {
        "COLUMN_GRANT_ID_INVALID",
        "SNAPSHOT_RELATION_AMBIGUOUS",
        "SNAPSHOT_COLUMN_AMBIGUOUS",
        "COLUMN_GRANT_NOT_FOUND",
        "RELATION_GRANT_NOT_FOUND",
        "RELATION_NOT_IN_SNAPSHOT",
        "COLUMN_NOT_IN_SNAPSHOT",
    }
)


class MetadataColumnResolutionErrorV3(Exception):
    """Stable neutral error for V3 column-grant physical-reference resolution.

    Carries only the issue code. Never carries raw grant IDs, object names,
    payloads, or original exceptions.
    """

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in _COLUMN_RESOLUTION_CODES:
            raise ValueError("unknown column-resolution issue code")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


def _normalize_identifier(value: str, case_sensitive: bool) -> str:
    """Normalize a physical identifier for comparison.

    sensitive: exact code-point comparison.
    insensitive: Unicode casefold.
    """
    return value if case_sensitive else value.casefold()


def _check_grant_id_format(column_grant_id: str) -> None:
    """Validate column_grant_id against ColumnGrantV3.grantId format."""
    if not isinstance(column_grant_id, str):
        raise MetadataColumnResolutionErrorV3("COLUMN_GRANT_ID_INVALID")
    if not (1 <= len(column_grant_id) <= 200):
        raise MetadataColumnResolutionErrorV3("COLUMN_GRANT_ID_INVALID")
    if fullmatch(_GRANT_ID_PATTERN, column_grant_id) is None:
        raise MetadataColumnResolutionErrorV3("COLUMN_GRANT_ID_INVALID")


def _build_snapshot_index(
    snapshot: GovernedMetadataSnapshotV3,
) -> tuple[dict[tuple[str, str], GovernedRelationV3], dict[tuple[str, str, str], GovernedColumnV3]]:
    """Build unique physical-identifier indexes for snapshot relations and columns.

    Two independent phases:
    1. All relations first — any duplicate relation key raises SNAPSHOT_RELATION_AMBIGUOUS.
    2. All columns second — any duplicate column key raises SNAPSHOT_COLUMN_AMBIGUOUS.

    This ensures relation ambiguity is always detected before column ambiguity,
    regardless of the order relations appear in the snapshot.

    Raises on ambiguity (duplicate keys), per DEV §5.3.1 step 2.
    Returns (relation_index, column_index).
    """
    case_sensitive = snapshot.identifier_case_sensitivity == "sensitive"
    relation_index: dict[tuple[str, str], GovernedRelationV3] = {}
    column_index: dict[tuple[str, str, str], GovernedColumnV3] = {}

    # Phase 1: check ALL relations before any columns
    for relation in snapshot.relations:
        rel_key = (
            _normalize_identifier(relation.schema_name, case_sensitive),
            _normalize_identifier(relation.relation_name, case_sensitive),
        )
        if rel_key in relation_index:
            raise MetadataColumnResolutionErrorV3("SNAPSHOT_RELATION_AMBIGUOUS")
        relation_index[rel_key] = relation

    # Phase 2: only after all relations pass, check ALL columns
    for relation in snapshot.relations:
        for column in relation.columns:
            col_key = (
                _normalize_identifier(relation.schema_name, case_sensitive),
                _normalize_identifier(relation.relation_name, case_sensitive),
                _normalize_identifier(column.column_name, case_sensitive),
            )
            if col_key in column_index:
                raise MetadataColumnResolutionErrorV3("SNAPSHOT_COLUMN_AMBIGUOUS")
            column_index[col_key] = column

    return relation_index, column_index


def _resolve_column_grant_v3(
    request: ResolveMetadataRequestV3,
    column_grant_id: str,
) -> PhysicalColumnRefV3:
    """Resolve a single column grant to its physical reference (DEV §5.3.1).

    Re-validates input consistency, then walks:
    column grant → relation grant → snapshot relation → snapshot column.

    Args:
        request: V3 metadata-resolution request.
        column_grant_id: The grant ID to resolve.

    Returns:
        A new PhysicalColumnRefV3 with exact snapshot spelling.

    Raises:
        MetadataResolutionInputErrorV3: on input-gate failure (propagated verbatim).
        MetadataColumnResolutionErrorV3: on the first failing physical-reference check.
    """
    # 0. Input gate — use the verified copy for all later steps
    verified = _validate_resolution_input_v3(request)

    # 1. Validate column_grant_id format
    _check_grant_id_format(column_grant_id)

    # 2. Build snapshot physical-identifier uniqueness index
    snapshot = verified.metadata_snapshot
    relation_index, column_index = _build_snapshot_index(snapshot)

    # 3. Find column grant by exact grantId
    column_grant: ColumnGrantV3 | None = None
    for cg in verified.project_context.column_grants:
        if cg.grant_id == column_grant_id:
            column_grant = cg
            break
    if column_grant is None:
        raise MetadataColumnResolutionErrorV3("COLUMN_GRANT_NOT_FOUND")

    # 4. Find relation grant by exact grantId
    relation_grant: RelationGrantV3 | None = None
    for rg in verified.project_context.relation_grants:
        if rg.grant_id == column_grant.relation_grant_id:
            relation_grant = rg
            break
    if relation_grant is None:
        raise MetadataColumnResolutionErrorV3("RELATION_GRANT_NOT_FOUND")

    # 5. Find snapshot relation by physical identifiers
    case_sensitive = snapshot.identifier_case_sensitivity == "sensitive"
    rel_key = (
        _normalize_identifier(relation_grant.schema_name, case_sensitive),
        _normalize_identifier(relation_grant.relation_name, case_sensitive),
    )
    matched_relation = relation_index.get(rel_key)
    if matched_relation is None:
        raise MetadataColumnResolutionErrorV3("RELATION_NOT_IN_SNAPSHOT")

    # 6. Find snapshot column by physical identifier
    col_key = (
        _normalize_identifier(relation_grant.schema_name, case_sensitive),
        _normalize_identifier(relation_grant.relation_name, case_sensitive),
        _normalize_identifier(column_grant.column_name, case_sensitive),
    )
    matched_column = column_index.get(col_key)
    if matched_column is None:
        raise MetadataColumnResolutionErrorV3("COLUMN_NOT_IN_SNAPSHOT")

    # 7. Return new PhysicalColumnRefV3 with exact snapshot spelling
    return PhysicalColumnRefV3.model_validate(
        {
            "schemaName": matched_relation.schema_name,
            "relationName": matched_relation.relation_name,
            "columnName": matched_column.column_name,
        }
    )


# ---------------------------------------------------------------------------
# Field and entity-key binding resolution (DEV §5.3.2)
# ---------------------------------------------------------------------------

_BINDING_RESOLUTION_CODES: frozenset[str] = frozenset(
    {
        "FIELD_AUTHORIZATION_MISSING",
        "ENTITY_KEY_AUTHORIZATION_MISSING",
        "ENTITY_KEY_AUTHORIZATION_AMBIGUOUS",
        "ENTITY_KEY_FIELD_NOT_FOUND",
        "ENTITY_KEY_FIELD_ROLE_MISMATCH",
        "ENTITY_KEY_COLUMN_GRANT_MISMATCH",
    }
)


class MetadataBindingResolutionErrorV3(Exception):
    """Stable neutral error for V3 field/entity-key binding resolution.

    Carries only the issue code. Never carries raw IDs, object names,
    payloads, or original exceptions.
    """

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in _BINDING_RESOLUTION_CODES:
            raise ValueError("unknown binding-resolution issue code")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


def _resolve_fields_and_entity_keys_v3(
    request: ResolveMetadataRequestV3,
) -> tuple[tuple[ResolvedFieldV3, ...], tuple[ResolvedEntityKeyV3, ...]]:
    """Resolve field bindings and entity-key authorizations (DEV §5.3.2).

    Re-validates input consistency, then resolves each field's explicit
    authorization chain and each entity-key's authorization closure.

    Args:
        request: V3 metadata-resolution request.

    Returns:
        A tuple of (resolved_fields, resolved_entity_keys), each as an
        immutable tuple preserving the original field/key order.

    Raises:
        MetadataResolutionInputErrorV3: on input-gate failure (propagated verbatim).
        MetadataColumnResolutionErrorV3: on column-grant resolution failure (propagated).
        MetadataBindingResolutionErrorV3: on the first failing binding check.
    """
    # 0. Input gate — use the verified copy for all later steps
    verified = _validate_resolution_input_v3(request)

    binding_request = verified.binding_request
    project_context = verified.project_context

    # -------------------------------------------------------------------------
    # 1. Resolve all fields in order
    # -------------------------------------------------------------------------
    resolved_fields: list[ResolvedFieldV3] = []

    for field in binding_request.query_requirements.fields:
        # Find exact match: requestId + fieldId + role
        matched_auth: FieldBindingAuthorizationV3 | None = None
        for auth in project_context.field_binding_authorizations:
            if (
                auth.request_id == binding_request.request_id
                and auth.field_id == field.field_id
                and auth.role == field.role
            ):
                matched_auth = auth
                break

        if matched_auth is None:
            raise MetadataBindingResolutionErrorV3("FIELD_AUTHORIZATION_MISSING")

        # Resolve the column grant to get physical reference
        physical_ref = _resolve_column_grant_v3(verified, matched_auth.column_grant_id)

        # Construct the resolved field
        resolved_field = ResolvedFieldV3.model_validate(
            {
                "fieldId": field.field_id,
                "role": field.role,
                "authorizationId": matched_auth.authorization_id,
                "columnGrantId": matched_auth.column_grant_id,
                "schemaName": physical_ref.schema_name,
                "relationName": physical_ref.relation_name,
                "columnName": physical_ref.column_name,
                "evidenceIds": list(field.evidence_ids),  # deep copy
            }
        )
        resolved_fields.append(resolved_field)

    # -------------------------------------------------------------------------
    # 2. Resolve all entity keys in order
    # -------------------------------------------------------------------------
    resolved_entity_keys: list[ResolvedEntityKeyV3] = []

    # Build a lookup from fieldId to resolved field for entity-key resolution
    field_by_id = {rf.field_id: rf for rf in resolved_fields}

    # Build a lookup from (requestId, parameterName) to entity-key authorizations
    entity_key_auths_by_param: dict[tuple[str, str], list[EntityKeyAuthorizationV3]] = {}
    for eka in project_context.entity_key_authorizations:
        if eka.request_id == binding_request.request_id:
            key = (eka.request_id, eka.parameter_name)
            entity_key_auths_by_param.setdefault(key, []).append(eka)

    # Build a lookup from parameter name to fact parameter
    param_by_name = {
        param.name: param for param in binding_request.fact.parameters if param.role == "entityKey"
    }

    for key_param_name in binding_request.query_requirements.entity.key_parameters:
        # Find the fact parameter
        fact_param = param_by_name.get(key_param_name)
        if fact_param is None:
            # This should not happen due to consumer validation, but be explicit
            raise MetadataBindingResolutionErrorV3("ENTITY_KEY_AUTHORIZATION_MISSING")

        # Find entity-key authorizations for this request + parameter
        auths = entity_key_auths_by_param.get((binding_request.request_id, key_param_name), [])

        if len(auths) == 0:
            raise MetadataBindingResolutionErrorV3("ENTITY_KEY_AUTHORIZATION_MISSING")
        if len(auths) > 1:
            raise MetadataBindingResolutionErrorV3("ENTITY_KEY_AUTHORIZATION_AMBIGUOUS")

        eka = auths[0]

        # The authorization's fieldId must exist in the request's fields
        resolved_field_for_key = field_by_id.get(eka.field_id)
        if resolved_field_for_key is None:
            raise MetadataBindingResolutionErrorV3("ENTITY_KEY_FIELD_NOT_FOUND")

        # The field must have role=entityKey
        if resolved_field_for_key.role != "entityKey":
            raise MetadataBindingResolutionErrorV3("ENTITY_KEY_FIELD_ROLE_MISMATCH")

        # The column grant must match
        if eka.column_grant_id != resolved_field_for_key.column_grant_id:
            raise MetadataBindingResolutionErrorV3("ENTITY_KEY_COLUMN_GRANT_MISMATCH")

        # Construct the resolved entity key
        resolved_ek = ResolvedEntityKeyV3.model_validate(
            {
                "parameterName": key_param_name,
                "fieldId": eka.field_id,
                "authorizationId": eka.authorization_id,
                "columnGrantId": eka.column_grant_id,
                "schemaName": resolved_field_for_key.schema_name,
                "relationName": resolved_field_for_key.relation_name,
                "columnName": resolved_field_for_key.column_name,
                "evidenceIds": list(binding_request.query_requirements.entity.evidence_ids),
            }
        )
        resolved_entity_keys.append(resolved_ek)

    return tuple(resolved_fields), tuple(resolved_entity_keys)


# ---------------------------------------------------------------------------
# Filter resolution (DEV §5.3.3)
# ---------------------------------------------------------------------------

_FILTER_RESOLUTION_CODES: frozenset[str] = frozenset(
    {
        "FILTER_EVIDENCE_REFERENCE_INVALID",
    }
)


class MetadataFilterResolutionErrorV3(Exception):
    """Stable neutral error for V3 filter resolution.

    Carries only the issue code. Never carries raw IDs, object names,
    payloads, or original exceptions.
    """

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in _FILTER_RESOLUTION_CODES:
            raise ValueError("unknown filter-resolution issue code")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


def _resolve_filters_v3(
    request: ResolveMetadataRequestV3,
) -> tuple[ResolvedFilterV3, ...]:
    """Resolve filter items to physical field references (DEV §5.3.3).

    Re-validates input consistency, resolves field authorizations, then
    maps each filter item to its authorized physical column reference.
    Uses the field's actual declared role for authorization — a filter
    reference does not force ``role="filter"``.

    Args:
        request: V3 metadata-resolution request.

    Returns:
        A tuple of ResolvedFilterV3 in the original filters.items order.
        Returns an empty tuple when filters.items is empty (after the input
        and field/entity-key gates have passed).

    Raises:
        MetadataResolutionInputErrorV3: on input-gate failure (propagated).
        MetadataBindingResolutionErrorV3: on field/entity-key failure
            (propagated).
        MetadataColumnResolutionErrorV3: on column-grant failure
            (propagated).
        MetadataFilterResolutionErrorV3: when a filter item references an
            evidence ID that does not exist in the request's top-level
            evidence. Code is always ``FILTER_EVIDENCE_REFERENCE_INVALID``.
    """
    # 0. Input gate — use the verified copy for all later steps
    verified = _validate_resolution_input_v3(request)

    # 1. Resolve fields and entity keys (re-validates + authorizes)
    resolved_fields, _ = _resolve_fields_and_entity_keys_v3(verified)

    # Build a lookup: field_id -> ResolvedFieldV3
    field_by_id = {field.field_id: field for field in resolved_fields}

    # Build the set of valid top-level evidence IDs
    valid_evidence_ids = {evidence.evidence_id for evidence in verified.binding_request.evidence}

    # 2. Map each filter item in original order
    resolved_filters: list[ResolvedFilterV3] = []

    for item in verified.binding_request.query_requirements.filters.items:
        # Verify each filter item's evidence references exist at top level
        for evidence_id in item.evidence_ids:
            if evidence_id not in valid_evidence_ids:
                raise MetadataFilterResolutionErrorV3("FILTER_EVIDENCE_REFERENCE_INVALID")

        # Find the authorized field for this filter item by field_id only.
        # Authorization already used the field's actual role; a filter
        # reference does not require role="filter".
        resolved_field = field_by_id.get(item.field_id)
        if resolved_field is None:
            # Defensive: consumer reference closure guarantees this field
            # exists and was resolved; treat absence as a field error.
            raise MetadataBindingResolutionErrorV3("FIELD_AUTHORIZATION_MISSING")

        # Build the resolved filter using the field's physical spelling
        resolved_filter = ResolvedFilterV3.model_validate(
            {
                "filterId": item.filter_id,
                "fieldId": item.field_id,
                "schemaName": resolved_field.schema_name,
                "relationName": resolved_field.relation_name,
                "columnName": resolved_field.column_name,
                "evidenceIds": list(item.evidence_ids),  # copy from filter item
            }
        )
        resolved_filters.append(resolved_filter)

    return tuple(resolved_filters)


# ---------------------------------------------------------------------------
# Aggregation resolution (DEV §5.3.4)
# ---------------------------------------------------------------------------


def _resolve_aggregation_v3(
    request: ResolveMetadataRequestV3,
) -> ResolvedAggregationV3:
    """Resolve aggregation field references (DEV §5.3.4).

    Re-validates input consistency, resolves field authorizations, then
    verifies that every field referenced by the aggregation declaration is
    covered by an authorized field result.  Copies the aggregation
    declaration verbatim into a new ``ResolvedAggregationV3``.

    Args:
        request: V3 metadata-resolution request.

    Returns:
        A new ResolvedAggregationV3 with the six declared fields copied
        (mode, function, inputFieldIds, groupByFieldIds, distinct,
        evidenceIds).  Lists are copied; they do not share mutable
        references with the input.

    Raises:
        MetadataResolutionInputErrorV3: on input-gate failure (propagated).
        MetadataBindingResolutionErrorV3: on field/entity-key failure
            (propagated) or when a referenced field is not authorized.
        MetadataColumnResolutionErrorV3: on column-grant failure
            (propagated via field resolution).
    """
    # 0. Input gate — use the verified copy for all later steps
    verified = _validate_resolution_input_v3(request)

    # 1. Resolve fields and entity keys (re-validates + authorizes)
    resolved_fields, _ = _resolve_fields_and_entity_keys_v3(verified)

    # Build a lookup: field_id -> ResolvedFieldV3
    field_by_id = {field.field_id: field for field in resolved_fields}

    aggregation = verified.binding_request.query_requirements.aggregation

    # 2. Verify every referenced field is authorized.
    # Authorization uses each field's actual declared role; a reference
    # does not force role="value" or "groupBy".
    referenced_ids = [*aggregation.input_field_ids, *aggregation.group_by_field_ids]
    for field_id in referenced_ids:
        if field_by_id.get(field_id) is None:
            raise MetadataBindingResolutionErrorV3("FIELD_AUTHORIZATION_MISSING")

    # 3. Build the result, copying the six declared fields verbatim.
    return ResolvedAggregationV3.model_validate(
        {
            "mode": str(aggregation.mode),
            "function": aggregation.function,
            "inputFieldIds": list(aggregation.input_field_ids),
            "groupByFieldIds": list(aggregation.group_by_field_ids),
            "distinct": aggregation.distinct,
            "evidenceIds": list(aggregation.evidence_ids),
        }
    )


# ---------------------------------------------------------------------------
# Time-range resolution (DEV §5.3.5)
# ---------------------------------------------------------------------------


def _resolve_time_range_v3(
    request: ResolveMetadataRequestV3,
) -> ResolvedTimeRangeV3:
    """Resolve the time-range field reference (DEV §5.3.5).

    Re-validates input consistency, resolves field authorizations, then
    maps the time-range declaration to its authorized physical column
    reference.  For ``none`` mode all physical identifiers are null; for
    ``asOf``/``between`` the physical names come from the authorized field.

    Args:
        request: V3 metadata-resolution request.

    Returns:
        A new ResolvedTimeRangeV3.  For ``none`` mode: mode="none" with
        all physical identifiers null.  For ``asOf``/``between``: mode and
        timeFieldId copied from the declaration, physical names from the
        authorized field.  evidenceIds are copied from the declaration.

    Raises:
        MetadataResolutionInputErrorV3: on input-gate failure (propagated).
        MetadataBindingResolutionErrorV3: on field/entity-key failure
            (propagated) or when the time field is not authorized.
        MetadataColumnResolutionErrorV3: on column-grant failure
            (propagated via field resolution).
    """
    # 0. Input gate — use the verified copy for all later steps
    verified = _validate_resolution_input_v3(request)

    # 1. Resolve fields and entity keys (re-validates + authorizes)
    resolved_fields, _ = _resolve_fields_and_entity_keys_v3(verified)

    # Build a lookup: field_id -> ResolvedFieldV3
    field_by_id = {field.field_id: field for field in resolved_fields}

    time_range = verified.binding_request.query_requirements.time_range

    # 2. For none mode, return null physical identifiers after gates pass.
    if str(time_range.mode) == "none":
        return ResolvedTimeRangeV3.model_validate(
            {
                "mode": "none",
                "timeFieldId": None,
                "timeSchemaName": None,
                "timeRelationName": None,
                "timeColumnName": None,
                "evidenceIds": list(time_range.evidence_ids),
            }
        )

    # 3. asOf / between: resolve the time field's physical reference.
    # Authorization uses the field's actual declared role; a time-range
    # reference does not force role="time".
    resolved_field = field_by_id.get(time_range.time_field_id)
    if resolved_field is None:
        raise MetadataBindingResolutionErrorV3("FIELD_AUTHORIZATION_MISSING")

    return ResolvedTimeRangeV3.model_validate(
        {
            "mode": str(time_range.mode),
            "timeFieldId": time_range.time_field_id,
            "timeSchemaName": resolved_field.schema_name,
            "timeRelationName": resolved_field.relation_name,
            "timeColumnName": resolved_field.column_name,
            "evidenceIds": list(time_range.evidence_ids),
        }
    )


# ---------------------------------------------------------------------------
# Join-grant physical-reference resolution (DEV §5.3.6)
# ---------------------------------------------------------------------------

_JOIN_RESOLUTION_CODES: frozenset[str] = frozenset(
    {
        "JOIN_GRANT_ID_INVALID",
        "JOIN_GRANT_NOT_FOUND",
        "JOIN_ENDPOINTS_IDENTICAL",
        "JOIN_RELATIONSHIP_NOT_FOUND",
        "JOIN_RELATIONSHIP_AMBIGUOUS",
    }
)


class MetadataJoinResolutionErrorV3(Exception):
    """Stable neutral error for V3 join-grant physical-reference resolution.

    Carries only the issue code. Never carries raw grant IDs, object names,
    payloads, or original exceptions.
    """

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in _JOIN_RESOLUTION_CODES:
            raise ValueError("unknown join-resolution issue code")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


def _check_join_grant_id_format(join_grant_id: str) -> None:
    """Validate join_grant_id against JoinGrantV3.grantId format."""
    if not isinstance(join_grant_id, str):
        raise MetadataJoinResolutionErrorV3("JOIN_GRANT_ID_INVALID")
    if not (1 <= len(join_grant_id) <= 200):
        raise MetadataJoinResolutionErrorV3("JOIN_GRANT_ID_INVALID")
    if fullmatch(_GRANT_ID_PATTERN, join_grant_id) is None:
        raise MetadataJoinResolutionErrorV3("JOIN_GRANT_ID_INVALID")


def _resolve_join_grant_v3(
    request: ResolveMetadataRequestV3,
    join_grant_id: str,
) -> tuple[PhysicalColumnRefV3, PhysicalColumnRefV3, JoinTypeV3]:
    """Resolve a specified join grant to its physical endpoints (DEV §5.3.6).

    Validates the join grant identifier, resolves both column grants
    through the existing column-resolution chain, confirms the two
    endpoints are distinct, and verifies exactly one snapshot
    relationship edge matches (undirected).

    Args:
        request: V3 metadata-resolution request.
        join_grant_id: The join grant ID to resolve.

    Returns:
        A tuple of (left_physical, right_physical, join_type).  Physical
        names use exact snapshot spelling; left/right direction follows
        the join grant.

    Raises:
        MetadataResolutionInputErrorV3: on input-gate failure (propagated).
        MetadataColumnResolutionErrorV3: on column-resolution failure
            (propagated).
        MetadataJoinResolutionErrorV3: on join-specific failures.
    """
    # 0. Input gate — use the verified copy for all later steps
    verified = _validate_resolution_input_v3(request)

    # 1. Validate join_grant_id format
    _check_join_grant_id_format(join_grant_id)

    # 2. Find the join grant by exact grantId
    join_grant: JoinGrantV3 | None = None
    for jg in verified.project_context.join_grants:
        if jg.grant_id == join_grant_id:
            join_grant = jg
            break
    if join_grant is None:
        raise MetadataJoinResolutionErrorV3("JOIN_GRANT_NOT_FOUND")

    # 3. Resolve both column grants via the existing helper.
    # Input/column failures propagate with their original codes.
    left_physical = _resolve_column_grant_v3(verified, join_grant.left_column_grant_id)
    right_physical = _resolve_column_grant_v3(verified, join_grant.right_column_grant_id)

    # 4. Normalize both endpoints and reject identical endpoints.
    snapshot = verified.metadata_snapshot
    case_sensitive = snapshot.identifier_case_sensitivity == "sensitive"

    left_key = (
        _normalize_identifier(left_physical.schema_name, case_sensitive),
        _normalize_identifier(left_physical.relation_name, case_sensitive),
        _normalize_identifier(left_physical.column_name, case_sensitive),
    )
    right_key = (
        _normalize_identifier(right_physical.schema_name, case_sensitive),
        _normalize_identifier(right_physical.relation_name, case_sensitive),
        _normalize_identifier(right_physical.column_name, case_sensitive),
    )
    if left_key == right_key:
        raise MetadataJoinResolutionErrorV3("JOIN_ENDPOINTS_IDENTICAL")

    # 5. Find matching snapshot relationship edges (undirected).
    # A–B and B–A are the same physical relationship.
    matching_edges: list[GovernedRelationshipV3] = []
    for rel in snapshot.relationships:
        rel_left_key = (
            _normalize_identifier(rel.left_column.schema_name, case_sensitive),
            _normalize_identifier(rel.left_column.relation_name, case_sensitive),
            _normalize_identifier(rel.left_column.column_name, case_sensitive),
        )
        rel_right_key = (
            _normalize_identifier(rel.right_column.schema_name, case_sensitive),
            _normalize_identifier(rel.right_column.relation_name, case_sensitive),
            _normalize_identifier(rel.right_column.column_name, case_sensitive),
        )
        # Undirected match: (left,right) or (right,left)
        if (left_key == rel_left_key and right_key == rel_right_key) or (
            left_key == rel_right_key and right_key == rel_left_key
        ):
            matching_edges.append(rel)

    if len(matching_edges) == 0:
        raise MetadataJoinResolutionErrorV3("JOIN_RELATIONSHIP_NOT_FOUND")
    if len(matching_edges) > 1:
        raise MetadataJoinResolutionErrorV3("JOIN_RELATIONSHIP_AMBIGUOUS")

    # 6. Return left, right, join_type (direction preserved from the grant).
    return left_physical, right_physical, JoinTypeV3(join_grant.join_type)


# ---------------------------------------------------------------------------
# Join-closure selection (DEV §5.3.6)
# ---------------------------------------------------------------------------

_JOIN_CLOSURE_CODES: frozenset[str] = frozenset(
    {
        "JOIN_CLOSURE_DISCONNECTED",
        "JOIN_CLOSURE_AMBIGUOUS",
    }
)


class MetadataJoinClosureErrorV3(Exception):
    """Stable neutral error for V3 join-closure selection.

    Carries only the issue code. Never carries raw grant IDs, object names,
    payloads, or original exceptions.
    """

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in _JOIN_CLOSURE_CODES:
            raise ValueError("unknown join-closure issue code")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


def _select_join_closure_v3(
    request: ResolveMetadataRequestV3,
) -> tuple[str, ...]:
    """Select the authorized join closure (DEV §5.3.6).

    Determines the set of join grants that connect all relations required
    by the resolved fields.  Follows the deterministic connectivity
    principles of the existing V2 algorithm.

    Args:
        request: V3 metadata-resolution request.

    Returns:
        A tuple of selected join grant IDs sorted in ascending
        lexicographic order.  Returns an empty tuple when there is
        only one (or zero) required relation.

    Raises:
        MetadataResolutionInputErrorV3: on input-gate failure (propagated).
        MetadataBindingResolutionErrorV3: on field/entity-key failure
            (propagated).
        MetadataColumnResolutionErrorV3: on column-resolution failure
            (propagated).
        MetadataJoinResolutionErrorV3: when any individual join grant
            fails validation (propagated).
        MetadataJoinClosureErrorV3: when the required relations cannot
            be connected (``JOIN_CLOSURE_DISCONNECTED``) or when multiple
            valid closures exist (``JOIN_CLOSURE_AMBIGUOUS``).
    """
    # 0. Input gate — use the verified copy for all later steps
    verified = _validate_resolution_input_v3(request)

    # 1. Resolve fields and entity keys to collect required relations.
    resolved_fields, _ = _resolve_fields_and_entity_keys_v3(verified)

    snapshot = verified.metadata_snapshot
    case_sensitive = snapshot.identifier_case_sensitivity == "sensitive"

    # Collect required relations from all resolved field physical references.
    required_relations: set[tuple[str, str]] = set()
    for field in resolved_fields:
        required_relations.add(
            (
                _normalize_identifier(field.schema_name, case_sensitive),
                _normalize_identifier(field.relation_name, case_sensitive),
            )
        )

    # 2. Validate every join grant in deterministic (grantId ascending) order.
    # Any failure propagates with its original code — no grant is skipped.
    # This gate runs BEFORE the single-relation early return so that all
    # grants are always validated regardless of the required-relation count.
    join_grants_by_id = sorted(verified.project_context.join_grants, key=lambda g: g.grant_id)
    validated: list[tuple[JoinGrantV3, tuple[str, str], tuple[str, str]]] = []
    for jg in join_grants_by_id:
        left_phys, right_phys, _ = _resolve_join_grant_v3(verified, jg.grant_id)
        left_relation = (
            _normalize_identifier(left_phys.schema_name, case_sensitive),
            _normalize_identifier(left_phys.relation_name, case_sensitive),
        )
        right_relation = (
            _normalize_identifier(right_phys.schema_name, case_sensitive),
            _normalize_identifier(right_phys.relation_name, case_sensitive),
        )
        validated.append((jg, left_relation, right_relation))

    # 3. Single relation (or none) needs no joins.
    # Placed AFTER grant validation so every grant is always checked.
    if len(required_relations) <= 1:
        return ()

    # 4. Keep only candidates that connect two distinct required relations.
    candidates: list[JoinGrantV3] = []
    for jg, left_relation, right_relation in validated:
        if (
            left_relation != right_relation
            and left_relation in required_relations
            and right_relation in required_relations
        ):
            candidates.append(jg)

    # 5. Build an undirected connectivity graph over required relations.
    adjacency: dict[tuple[str, str], set[tuple[str, str]]] = {
        relation: set() for relation in required_relations
    }
    for jg, left_relation, right_relation in validated:
        if jg in candidates:
            adjacency[left_relation].add(right_relation)
            adjacency[right_relation].add(left_relation)

    # 6. Check connectivity via traversal.
    start = min(required_relations)
    seen: set[tuple[str, str]] = set()
    pending = [start]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(sorted(adjacency[current] - seen))

    if seen != required_relations:
        raise MetadataJoinClosureErrorV3("JOIN_CLOSURE_DISCONNECTED")

    # 7. Check for ambiguity: edges must equal relations - 1.
    if len(candidates) != len(required_relations) - 1:
        raise MetadataJoinClosureErrorV3("JOIN_CLOSURE_AMBIGUOUS")

    # 8. Return selected grant IDs in ascending order.
    return tuple(sorted(jg.grant_id for jg in candidates))
