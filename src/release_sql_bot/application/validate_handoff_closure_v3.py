"""V3 handoff content-closure pure-validation function (M2 第一子任务).

Re-validates the structural, identity, hash and frozen-schema references of a
``HandoffClosureV3`` without accessing any repository, provider, SQL Server or
environment. Success proves only that the closure is internally consistent;
it does NOT prove that the referenced batch exists in MongoDB, that the
``intakeStatus`` came from a real intake, or that the payload belongs to a
currently approved live run.

Six ordered fail-fast checks (first failure wins):

1. ``HANDOFF_STRUCTURE_INVALID`` — re-validate the complete closure against the
   ``HandoffClosureV3`` contract (including the full nested
   ``FactBindingRequestV3``). Guards against objects mutated after construction.
2. ``HANDOFF_SCHEMA_SOURCE_INVALID`` — the frozen Schema loader cannot load or
   source-check the packaged Schema.
3. ``HANDOFF_SCHEMA_REF_MISMATCH`` — ``contractSchemaId`` /
   ``contractSchemaSha256`` differ from the loader constants.
4. ``HANDOFF_PAYLOAD_SCHEMA_INVALID`` — the nested payload fails the packaged
   frozen JSON Schema.
5. ``HANDOFF_IDENTITY_MISMATCH`` — closure identity fields disagree with the
   nested payload.
6. ``HANDOFF_PAYLOAD_HASH_MISMATCH`` — ``canonical_sha256(payload)`` differs
   from ``payloadSha256``.

The function is pure computation: no repository/provider/SQL/environment.
"""

from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.application.handoff_intake_v3 import (
    FACT_BINDING_SCHEMA_ID_V3,
    FACT_BINDING_SCHEMA_SHA256_V3,
    FactBindingHandoffBatchInvalidErrorV3,
    load_fact_binding_schema_v3,
)
from release_sql_bot.domain.handoff_closure_v3 import (
    HandoffClosureV3,
    HandoffClosureValidationErrorV3,
)


def _revalidate_structure(closure: object) -> HandoffClosureV3:
    """Re-validate closure shape and return an independent verified copy.

    Non-``HandoffClosureV3`` root objects (dicts, V1/V2 models, etc.) map to
    ``HANDOFF_STRUCTURE_INVALID``. Serialization uses ``warnings="error"`` so
    that non-serializable content or type warnings are caught and mapped to the
    same neutral code. Returns a freshly validated model for downstream use.
    """
    if not isinstance(closure, HandoffClosureV3):
        raise HandoffClosureValidationErrorV3("HANDOFF_STRUCTURE_INVALID")
    try:
        wire = closure.model_dump(by_alias=True, mode="json", warnings="error")
        verified = HandoffClosureV3.model_validate(wire)
    except Exception:  # noqa: BLE001 — map any structural failure to neutral code
        raise HandoffClosureValidationErrorV3("HANDOFF_STRUCTURE_INVALID") from None
    return verified


def _load_frozen_schema() -> dict[str, Any]:
    """Load the packaged frozen Schema, mapping failures to a neutral code."""
    try:
        return load_fact_binding_schema_v3()
    except FactBindingHandoffBatchInvalidErrorV3:
        raise HandoffClosureValidationErrorV3("HANDOFF_SCHEMA_SOURCE_INVALID") from None


def _check_schema_ref(closure: HandoffClosureV3) -> None:
    if closure.contract_schema_id != FACT_BINDING_SCHEMA_ID_V3:
        raise HandoffClosureValidationErrorV3("HANDOFF_SCHEMA_REF_MISMATCH")
    if closure.contract_schema_sha256 != FACT_BINDING_SCHEMA_SHA256_V3:
        raise HandoffClosureValidationErrorV3("HANDOFF_SCHEMA_REF_MISMATCH")


def _check_payload_schema(
    closure: HandoffClosureV3,
    schema: dict[str, Any],
) -> None:
    payload_wire = closure.payload.model_dump(by_alias=True, mode="json")
    try:
        Draft202012Validator(schema).validate(payload_wire)
    except JsonSchemaValidationError:
        raise HandoffClosureValidationErrorV3("HANDOFF_PAYLOAD_SCHEMA_INVALID") from None


def _check_identity(closure: HandoffClosureV3) -> None:
    payload = closure.payload
    if closure.rule_version != payload.rule_ref.rule_version:
        raise HandoffClosureValidationErrorV3("HANDOFF_IDENTITY_MISMATCH")
    if closure.request_id != payload.request_id:
        raise HandoffClosureValidationErrorV3("HANDOFF_IDENTITY_MISMATCH")
    if closure.fact_code != payload.fact.fact_code:
        raise HandoffClosureValidationErrorV3("HANDOFF_IDENTITY_MISMATCH")
    expected_request_id = f"{closure.rule_version}#{closure.fact_code}"
    if closure.request_id != expected_request_id:
        raise HandoffClosureValidationErrorV3("HANDOFF_IDENTITY_MISMATCH")


def _check_payload_hash(closure: HandoffClosureV3) -> None:
    recomputed = canonical_sha256(closure.payload)
    if recomputed != closure.payload_sha256:
        raise HandoffClosureValidationErrorV3("HANDOFF_PAYLOAD_HASH_MISMATCH")


def validate_handoff_closure_v3(closure: HandoffClosureV3) -> None:
    """Validate V3 handoff content-closure consistency (fail-fast).

    Args:
        closure: V3 handoff closure to validate.

    Returns:
        None on success.

    Raises:
        HandoffClosureValidationErrorV3: on the first failing check.
    """
    # 1. Re-validate structure; use the verified copy for all later checks
    verified = _revalidate_structure(closure)

    # 2. Frozen Schema loader must succeed
    schema = _load_frozen_schema()

    # 3. Schema reference must match loader constants
    _check_schema_ref(verified)

    # 4. Payload must pass the frozen JSON Schema
    _check_payload_schema(verified, schema)

    # 5. Identity closure
    _check_identity(verified)

    # 6. Payload content hash
    _check_payload_hash(verified)
