"""Unit tests for V3 handoff content-closure validation (M2 第一子任务).

All tests use synthetic data. No repository, provider, SQL or environment access.
"""

from __future__ import annotations

import json
import warnings
from copy import deepcopy
from unittest.mock import patch

import pytest

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.application.handoff_intake_v3 import (
    FACT_BINDING_SCHEMA_ID_V3,
    FACT_BINDING_SCHEMA_SHA256_V3,
    FactBindingHandoffBatchInvalidErrorV3,
)
from release_sql_bot.application.validate_handoff_closure_v3 import (
    validate_handoff_closure_v3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.handoff_closure_v3 import (
    HandoffClosureV3,
    HandoffClosureValidationErrorV3,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _load_synthetic_payload() -> dict[str, object]:
    """Load the full synthetic FactBindingRequestV3 fixture."""
    from pathlib import Path

    fixture_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "fact-binding-request-3.0.0.synthetic-ready.json"
    )
    return json.loads(fixture_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _make_closure_wire(**overrides: object) -> dict[str, object]:
    """Build a valid HandoffClosureV3 wire payload with real canonical hash.

    If ``payload`` is provided in ``overrides``, it is used as the actual payload
    AND for hash computation, so the returned wire is self-consistent.
    """
    if "payload" in overrides:
        pl = deepcopy(overrides["payload"])
    else:
        pl = deepcopy(_load_synthetic_payload())
    rv = overrides.get("rule_version", pl["ruleRef"]["ruleVersion"])
    fc = overrides.get("fact_code", pl["fact"]["factCode"])
    rid = overrides.get("request_id", pl["requestId"])
    real_hash = canonical_sha256(FactBindingRequestV3.model_validate(pl))
    return {
        "schemaVersion": "1.0.0",
        "ruleVersion": rv,
        "requestId": rid,
        "factCode": fc,
        "payloadSha256": real_hash,
        "batchSha256": "a" * 64,
        "contractSchemaId": FACT_BINDING_SCHEMA_ID_V3,
        "contractSchemaSha256": FACT_BINDING_SCHEMA_SHA256_V3,
        "intakeStatus": "readyForMetadataResolution",
        "payload": pl,
    }


def _make_closure(**overrides: object) -> HandoffClosureV3:
    wire = _make_closure_wire(**overrides)
    return HandoffClosureV3.model_validate(wire)


# ===================================================================
# Section 1: Success path
# ===================================================================


def test_valid_closure_returns_none() -> None:
    """A self-consistent closure passes all six checks."""
    closure = _make_closure()
    assert validate_handoff_closure_v3(closure) is None


def test_valid_closure_with_real_canonical_hash() -> None:
    """payloadSha256 is computed using the real canonical_sha256 algorithm."""
    pl = _load_synthetic_payload()
    real_hash = canonical_sha256(FactBindingRequestV3.model_validate(pl))
    closure = _make_closure()
    # Verify the hash was set correctly
    assert closure.payload_sha256 == real_hash
    assert validate_handoff_closure_v3(closure) is None


# ===================================================================
# Section 2: Structure re-validation — defect regressions
# ===================================================================


def test_structure_invalid_root_is_dict() -> None:
    """Root object is a plain dict, not a HandoffClosureV3."""
    wire = _make_closure_wire()
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(wire)
    assert exc_info.value.code == "HANDOFF_STRUCTURE_INVALID"


def test_structure_invalid_root_is_v2_model() -> None:
    """Root object is a valid V1/V2 model — must not convert to V3."""
    from pathlib import Path

    from release_sql_bot.domain.fact_bindings_v2 import FactBindingRequestV2

    v2_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "fact-binding-request-2.0.0.synthetic-ready.json"
    )
    v2_payload = json.loads(v2_path.read_text(encoding="utf-8"))
    v2_model = FactBindingRequestV2.model_validate(v2_payload)
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(v2_model)
    assert exc_info.value.code == "HANDOFF_STRUCTURE_INVALID"


def test_structure_invalid_model_copy_plain_dict_payload() -> None:
    """model_copy injecting a plain dict payload (bypasses nested validation)."""
    closure = _make_closure()
    plain_dict = {"requestId": "x#y"}  # not a valid FactBindingRequestV3
    mutated = closure.model_copy(update={"payload": plain_dict})
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(mutated)
    assert exc_info.value.code == "HANDOFF_STRUCTURE_INVALID"


def test_structure_invalid_model_copy_unserializable_payload() -> None:
    """model_copy injecting a non-serializable payload triggers serialization error."""
    closure = _make_closure()

    class _Unserializable:
        pass

    mutated = closure.model_copy(update={"payload": _Unserializable()})
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(mutated)
    assert exc_info.value.code == "HANDOFF_STRUCTURE_INVALID"


def test_structure_invalid_rule_version_list_with_sensitive_marker() -> None:
    """rule_version changed to a list containing a synthetic sensitive marker.

    Verifies that no warning or error leaks the marker, and that the input
    object is not mutated.
    """
    closure = _make_closure()
    sensitive = "SENSITIVE-MARKER-XYZ"
    mutated = closure.model_copy(update={"rule_version": [sensitive, "extra"]})
    before_dump = closure.model_dump(by_alias=True, mode="json", warnings="error")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
            validate_handoff_closure_v3(mutated)
    assert exc_info.value.code == "HANDOFF_STRUCTURE_INVALID"
    # Error text must not contain the sensitive marker
    assert sensitive not in str(exc_info.value)
    assert sensitive not in repr(exc_info.value)
    assert sensitive not in exc_info.value.args
    # No warning should contain the sensitive marker
    for w in caught:
        assert sensitive not in str(w.message)
    # Original object is unchanged
    after_dump = closure.model_dump(by_alias=True, mode="json", warnings="error")
    assert before_dump == after_dump


# ===================================================================
# Section 3: Schema source
# ===================================================================


def test_schema_source_invalid_when_loader_fails() -> None:
    """Frozen Schema loader failure maps to HANDOFF_SCHEMA_SOURCE_INVALID."""
    closure = _make_closure()
    with patch(
        "release_sql_bot.application.validate_handoff_closure_v3.load_fact_binding_schema_v3",
        side_effect=FactBindingHandoffBatchInvalidErrorV3("Schema could not be loaded"),
    ):
        with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
            validate_handoff_closure_v3(closure)
    assert exc_info.value.code == "HANDOFF_SCHEMA_SOURCE_INVALID"


def test_schema_source_invalid_sanitizes_sensitive_marker() -> None:
    """Schema loader failure with a sensitive marker in the cause stays neutral."""
    closure = _make_closure()
    sensitive = "SENSITIVE-LOADER-MARKER"
    with patch(
        "release_sql_bot.application.validate_handoff_closure_v3.load_fact_binding_schema_v3",
        side_effect=FactBindingHandoffBatchInvalidErrorV3(sensitive),
    ):
        with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
            validate_handoff_closure_v3(closure)
    assert exc_info.value.code == "HANDOFF_SCHEMA_SOURCE_INVALID"
    assert sensitive not in str(exc_info.value)
    assert sensitive not in repr(exc_info.value)


# ===================================================================
# Section 4: Schema reference mismatch
# ===================================================================


def test_schema_id_mismatch() -> None:
    """contractSchemaId differs from loader constant."""
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    wire["contractSchemaId"] = "wrong-schema-id"
    bad = HandoffClosureV3.model_validate(wire)
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_SCHEMA_REF_MISMATCH"


def test_schema_hash_mismatch() -> None:
    """contractSchemaSha256 differs from loader constant."""
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    wire["contractSchemaSha256"] = "b" * 64
    bad = HandoffClosureV3.model_validate(wire)
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_SCHEMA_REF_MISMATCH"


# ===================================================================
# Section 5: Payload JSON Schema
# ===================================================================


def test_payload_schema_invalid() -> None:
    """Payload fails the frozen JSON Schema.

    Uses a mock to simulate a JSON Schema validation failure, since the
    Pydantic consumer is stricter than the JSON Schema and catches most
    violations first.
    """
    from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

    closure = _make_closure()
    with patch(
        "release_sql_bot.application.validate_handoff_closure_v3.Draft202012Validator"
    ) as mock_validator:
        mock_validator.return_value.validate.side_effect = JsonSchemaValidationError(
            "Schema validation failed"
        )
        with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
            validate_handoff_closure_v3(closure)
    assert exc_info.value.code == "HANDOFF_PAYLOAD_SCHEMA_INVALID"


def test_payload_schema_invalid_sanitizes_sensitive_marker() -> None:
    """JSON Schema failure with a sensitive marker in the cause stays neutral."""
    from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

    closure = _make_closure()
    sensitive = "SENSITIVE-SCHEMA-MARKER"
    with patch(
        "release_sql_bot.application.validate_handoff_closure_v3.Draft202012Validator"
    ) as mock_validator:
        mock_validator.return_value.validate.side_effect = JsonSchemaValidationError(sensitive)
        with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
            validate_handoff_closure_v3(closure)
    assert exc_info.value.code == "HANDOFF_PAYLOAD_SCHEMA_INVALID"
    assert sensitive not in str(exc_info.value)
    assert sensitive not in repr(exc_info.value)


# ===================================================================
# Section 6: Identity mismatch
# ===================================================================


def test_identity_mismatch_rule_version() -> None:
    """closure.ruleVersion != payload.ruleRef.ruleVersion."""
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    wire["ruleVersion"] = "WRONG@v1"
    bad = HandoffClosureV3.model_validate(wire)
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_IDENTITY_MISMATCH"


def test_identity_mismatch_request_id() -> None:
    """closure.requestId != payload.requestId."""
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    wire["requestId"] = "WRONG@v1#fact.one"
    bad = HandoffClosureV3.model_validate(wire)
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_IDENTITY_MISMATCH"


def test_identity_mismatch_fact_code() -> None:
    """closure.factCode != payload.fact.factCode."""
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    wire["factCode"] = "wrong.fact_code"
    bad = HandoffClosureV3.model_validate(wire)
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_IDENTITY_MISMATCH"


def test_identity_mismatch_request_id_format() -> None:
    """closure.requestId != ruleVersion#factCode."""
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    rv = wire["ruleVersion"]
    wire["requestId"] = f"{rv}#WRONG"  # valid format but wrong factCode
    bad = HandoffClosureV3.model_validate(wire)
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_IDENTITY_MISMATCH"


# ===================================================================
# Section 7: Payload hash mismatch
# ===================================================================


def test_payload_hash_mismatch() -> None:
    """Modify payload content but keep old hash."""
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    wire["payload"]["fact"]["name"] = "Tampered name"
    bad = HandoffClosureV3.model_validate(wire)
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_PAYLOAD_HASH_MISMATCH"


# ===================================================================
# Section 8: Ordering — first failure wins (real double-fault)
# ===================================================================


def test_ordering_structure_before_identity() -> None:
    """Structure (check 1) fires before identity (check 5).

    Contains BOTH a structural fault (empty rule_version) and an identity
    fault (ruleVersion != payload.ruleRef.ruleVersion).
    """
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    wire["ruleVersion"] = "STRUCT@v1"  # valid format but != payload (identity fail)
    wire["requestId"] = "STRUCT@v1#fact.one"  # matches ruleVersion#factCode
    partial = HandoffClosureV3.model_validate(wire)
    # model_copy bypasses validation, introducing a structural fault
    bad = partial.model_copy(update={"rule_version": ""})
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_STRUCTURE_INVALID"


def test_ordering_schema_ref_before_payload_schema() -> None:
    """Schema ref (check 3) fires before payload schema (check 4).

    Contains BOTH a schema ref fault and a payload schema fault.
    """
    from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    wire["contractSchemaId"] = "wrong-id"  # schema ref fault
    bad = HandoffClosureV3.model_validate(wire)
    with patch(
        "release_sql_bot.application.validate_handoff_closure_v3.Draft202012Validator"
    ) as mock_validator:
        mock_validator.return_value.validate.side_effect = JsonSchemaValidationError("mock")
        with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
            validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_SCHEMA_REF_MISMATCH"
    # Verify the payload schema validator was NOT called (schema ref failed first)
    mock_validator.return_value.validate.assert_not_called()


def test_ordering_schema_ref_removed_payload_schema_triggers() -> None:
    """With schema ref fixed, payload schema fault triggers HANDOFF_PAYLOAD_SCHEMA_INVALID."""
    from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

    closure = _make_closure()  # schema ref is correct
    with patch(
        "release_sql_bot.application.validate_handoff_closure_v3.Draft202012Validator"
    ) as mock_validator:
        mock_validator.return_value.validate.side_effect = JsonSchemaValidationError("mock")
        with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
            validate_handoff_closure_v3(closure)
    assert exc_info.value.code == "HANDOFF_PAYLOAD_SCHEMA_INVALID"


def test_ordering_identity_before_hash() -> None:
    """Identity (check 5) fires before hash (check 6).

    Contains BOTH an identity fault and a hash fault (wrong payloadSha256).
    """
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    wire["ruleVersion"] = "WRONG@v1"  # identity fault
    wire["payloadSha256"] = "f" * 64  # hash fault (wrong but format-valid)
    bad = HandoffClosureV3.model_validate(wire)
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_IDENTITY_MISMATCH"


def test_ordering_identity_fixed_hash_triggers() -> None:
    """With identity fixed, hash fault triggers HANDOFF_PAYLOAD_HASH_MISMATCH."""
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    # Identity matches: use payload's actual values
    wire["ruleVersion"] = wire["payload"]["ruleRef"]["ruleVersion"]
    wire["requestId"] = wire["payload"]["requestId"]
    wire["factCode"] = wire["payload"]["fact"]["factCode"]
    wire["payloadSha256"] = "f" * 64  # hash fault (wrong but format-valid)
    bad = HandoffClosureV3.model_validate(wire)
    with pytest.raises(HandoffClosureValidationErrorV3) as exc_info:
        validate_handoff_closure_v3(bad)
    assert exc_info.value.code == "HANDOFF_PAYLOAD_HASH_MISMATCH"


# ===================================================================
# Section 9: Immutability — input not modified
# ===================================================================


def test_validation_does_not_modify_input_on_success() -> None:
    """Closure serialization is unchanged after successful validation."""
    closure = _make_closure()
    before = closure.model_dump(by_alias=True, mode="json")
    validate_handoff_closure_v3(closure)
    after = closure.model_dump(by_alias=True, mode="json")
    assert before == after


def test_validation_does_not_modify_input_on_failure() -> None:
    """Closure serialization is unchanged after failed validation."""
    closure = _make_closure()
    wire = closure.model_dump(by_alias=True, mode="json")
    wire["ruleVersion"] = "WRONG@v1"
    bad = HandoffClosureV3.model_validate(wire)
    before = bad.model_dump(by_alias=True, mode="json")
    with pytest.raises(HandoffClosureValidationErrorV3):
        validate_handoff_closure_v3(bad)
    after = bad.model_dump(by_alias=True, mode="json")
    assert before == after


# ===================================================================
# Section 10: Error code whitelist and sanitization
# ===================================================================


@pytest.mark.parametrize(
    "code",
    [
        "HANDOFF_STRUCTURE_INVALID",
        "HANDOFF_SCHEMA_SOURCE_INVALID",
        "HANDOFF_SCHEMA_REF_MISMATCH",
        "HANDOFF_PAYLOAD_SCHEMA_INVALID",
        "HANDOFF_IDENTITY_MISMATCH",
        "HANDOFF_PAYLOAD_HASH_MISMATCH",
    ],
)
def test_error_accepts_all_six_valid_codes(code: str) -> None:
    err = HandoffClosureValidationErrorV3(code)
    assert err.code == code
    assert str(err) == code
    assert code in repr(err)


def test_error_rejects_invalid_code() -> None:
    with pytest.raises(ValueError, match="unknown"):
        HandoffClosureValidationErrorV3("SOME_INVALID_CODE")


def test_error_does_not_leak_input_data() -> None:
    sensitive = "SENSITIVE-MARKER-12345"
    err = HandoffClosureValidationErrorV3("HANDOFF_IDENTITY_MISMATCH")
    assert sensitive not in err.code
    assert sensitive not in str(err)
    assert sensitive not in repr(err)
    assert sensitive not in err.args


# ===================================================================
# Section 11: Success is content validation, NOT repository attestation
# ===================================================================


def test_success_does_not_construct_repository_verified() -> None:
    """Validation success does NOT yield a RepositoryVerifiedHandoffV3."""
    from release_sql_bot.domain.handoff_closure_v3 import RepositoryVerifiedHandoffV3

    closure = _make_closure()
    result = validate_handoff_closure_v3(closure)
    assert result is None
    # No verified object is returned
    with pytest.raises(TypeError):
        RepositoryVerifiedHandoffV3(
            rule_version=closure.rule_version,
            request_id=closure.request_id,
            fact_code=closure.fact_code,
            payload=closure.payload,
        )


def test_batch_sha256_not_verified() -> None:
    """Different but format-valid batchSha256 values all pass — not verified."""
    closure1 = _make_closure()
    wire = closure1.model_dump(by_alias=True, mode="json")
    wire["batchSha256"] = "b" * 64
    closure2 = HandoffClosureV3.model_validate(wire)
    # Both pass — batchSha256 is not independently verified
    assert validate_handoff_closure_v3(closure1) is None
    assert validate_handoff_closure_v3(closure2) is None


# ===================================================================
# Section 12: No infrastructure dependencies
# ===================================================================


def test_validator_does_not_import_v2_or_infrastructure() -> None:
    from pathlib import Path

    import release_sql_bot.application.validate_handoff_closure_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "fact_bindings_v2" not in source
    assert "project_bindings_v2" not in source
    assert "mongodb" not in source
    assert "pymongo" not in source
    assert "os.environ" not in source
    assert "getenv" not in source
