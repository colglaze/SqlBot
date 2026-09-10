"""Contract tests for V3 handoff closure and repository-verified result.

M1 scope only: shape, field, nesting, type isolation, schema-version constants,
and the non-instantiability of RepositoryVerifiedHandoffV3. Does NOT test
M2 content-closure hash re-verification or cross-field identity.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

# Frozen Schema-identity constants — canonical source is the intake module.
from release_sql_bot.application.handoff_intake_v3 import (
    FACT_BINDING_SCHEMA_ID_V3,
    FACT_BINDING_SCHEMA_SHA256_V3,
)
from release_sql_bot.domain.fact_bindings_v3 import FactBindingRequestV3
from release_sql_bot.domain.handoff_closure_v3 import (
    HandoffClosureV3,
    RepositoryVerifiedHandoffV3,
)

# ---------------------------------------------------------------------------
# Synthetic fixture builders
# ---------------------------------------------------------------------------

_VALID_SHA = "a" * 64


def _load_synthetic_v3_payload() -> dict[str, object]:
    """Load the full synthetic FactBindingRequestV3 fixture."""
    fixture_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "fact-binding-request-3.0.0.synthetic-ready.json"
    )
    return json.loads(fixture_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _load_synthetic_v2_payload() -> dict[str, object]:
    """Load the complete synthetic FactBindingRequestV2 ready fixture."""
    fixture_path = (
        Path(__file__).resolve().parent.parent
        / "fixtures"
        / "fact-binding-request-2.0.0.synthetic-ready.json"
    )
    return json.loads(fixture_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _build_closure_wire(
    *,
    rule_version: str | None = None,
    request_id: str | None = None,
    fact_code: str | None = None,
    payload_sha256: str | None = None,
    batch_sha256: str | None = None,
    contract_schema_id: str | None = None,
    contract_schema_sha256: str | None = None,
    intake_status: str | None = None,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build a valid HandoffClosureV3 wire payload with optional overrides."""
    pl = payload if payload is not None else _load_synthetic_v3_payload()
    rv = rule_version if rule_version is not None else pl["ruleRef"]["ruleVersion"]
    fc = fact_code if fact_code is not None else pl["fact"]["factCode"]
    rid = request_id if request_id is not None else pl["requestId"]
    return {
        "schemaVersion": "1.0.0",
        "ruleVersion": rv,
        "requestId": rid,
        "factCode": fc,
        "payloadSha256": payload_sha256 if payload_sha256 is not None else _VALID_SHA,
        "batchSha256": batch_sha256 if batch_sha256 is not None else _VALID_SHA,
        "contractSchemaId": (
            contract_schema_id if contract_schema_id is not None else FACT_BINDING_SCHEMA_ID_V3
        ),
        "contractSchemaSha256": (
            contract_schema_sha256
            if contract_schema_sha256 is not None
            else FACT_BINDING_SCHEMA_SHA256_V3
        ),
        "intakeStatus": (
            intake_status if intake_status is not None else "readyForMetadataResolution"
        ),
        "payload": pl,
    }


def _make_closure(**overrides: object) -> HandoffClosureV3:
    wire = _build_closure_wire(**overrides)  # type: ignore[arg-type]
    return HandoffClosureV3.model_validate(wire)


# ===================================================================
# Section 1: Valid payload round-trip and content preservation
# ===================================================================


def test_valid_closure_round_trip() -> None:
    wire = _build_closure_wire()
    closure = HandoffClosureV3.model_validate(wire)
    dumped = closure.model_dump(by_alias=True, mode="json")
    assert dumped == wire


def test_closure_schema_version_is_1_0_0() -> None:
    closure = _make_closure()
    assert closure.schema_version == "1.0.0"
    wire = closure.model_dump(by_alias=True, mode="json")
    assert wire["schemaVersion"] == "1.0.0"


def test_closure_preserves_full_payload() -> None:
    """Payload is preserved verbatim: evidence, examples, uncertainties, queryRequirements."""
    closure = _make_closure()
    payload_wire = closure.payload.model_dump(by_alias=True, mode="json")
    original = _load_synthetic_v3_payload()
    assert payload_wire == original


def test_closure_preserves_usages_complete_sextuplet() -> None:
    closure = _make_closure()
    usages = closure.payload.usages
    assert len(usages) >= 1
    for usage in usages:
        assert usage.stage
        assert usage.rule_code
        assert usage.priority >= 1
        assert usage.condition_id
        assert usage.condition_path.startswith("/")
        assert usage.outcome


def test_closure_preserves_evidence() -> None:
    closure = _make_closure()
    evidence_ids = [ev.evidence_id for ev in closure.payload.evidence]
    assert "ev-fact-declaration" in evidence_ids
    assert "ev-condition-usage" in evidence_ids
    assert "ev-query-requirement" in evidence_ids
    assert "ev-example" in evidence_ids


def test_closure_preserves_examples_expected_outcome() -> None:
    closure = _make_closure()
    assert len(closure.payload.examples) >= 1
    assert closure.payload.examples[0].expected_outcome


def test_closure_preserves_warning_uncertainties() -> None:
    closure = _make_closure()
    assert len(closure.payload.uncertainties) >= 1
    assert closure.payload.uncertainties[0].impact == "warning"


# ===================================================================
# Section 2: camelCase only — snake_case and extra fields rejected
# ===================================================================


def test_closure_rejects_snake_case_top_level() -> None:
    wire = _build_closure_wire()
    wire["rule_version"] = wire.pop("ruleVersion")
    with pytest.raises(ValidationError, match="snake_case"):
        HandoffClosureV3.model_validate(wire)


def test_closure_rejects_snake_case_nested_in_payload() -> None:
    wire = _build_closure_wire()
    wire["payload"]["rule_ref"] = wire["payload"].pop("ruleRef")
    with pytest.raises(ValidationError, match="snake_case"):
        HandoffClosureV3.model_validate(wire)


def test_closure_rejects_extra_top_level_field() -> None:
    wire = _build_closure_wire()
    wire["inventedField"] = "extra"
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


def test_closure_rejects_extra_nested_field() -> None:
    wire = _build_closure_wire()
    wire["payload"]["ruleRef"]["invented"] = True
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


# ===================================================================
# Section 3: Missing required fields
# ===================================================================


@pytest.mark.parametrize(
    "missing_field",
    [
        "ruleVersion",
        "requestId",
        "factCode",
        "payloadSha256",
        "batchSha256",
        "contractSchemaId",
        "contractSchemaSha256",
        "intakeStatus",
        "payload",
    ],
)
def test_closure_rejects_missing_required_field(missing_field: str) -> None:
    wire = _build_closure_wire()
    del wire[missing_field]
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


# ===================================================================
# Section 4: Type coercion rejection (strict)
# ===================================================================


def test_closure_rejects_integer_coercion_for_string() -> None:
    wire = _build_closure_wire()
    wire["ruleVersion"] = 12345
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


def test_closure_rejects_string_coercion_for_nested_integer() -> None:
    wire = _build_closure_wire()
    wire["payload"]["usages"][0]["priority"] = "1"
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


# ===================================================================
# Section 5: schemaVersion and intakeStatus constraints
# ===================================================================


def test_closure_rejects_wrong_schema_version() -> None:
    wire = _build_closure_wire()
    wire["schemaVersion"] = "2.0.0"
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


def test_closure_rejects_wrong_intake_status() -> None:
    wire = _build_closure_wire()
    wire["intakeStatus"] = "metadataResolved"
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


# ===================================================================
# Section 6: Field boundaries — parameterized
# ===================================================================


# -- ruleVersion boundary --
def test_closure_accepts_rule_version_at_min_length() -> None:
    """ruleVersion length 1 is accepted (min_length=1)."""
    closure = _make_closure(rule_version="R")
    assert closure.rule_version == "R"


def test_closure_accepts_rule_version_at_max_length() -> None:
    long_rv = "R" + "x" * 259  # 260 chars total
    closure = _make_closure(rule_version=long_rv)
    assert len(closure.rule_version) == 260


def test_closure_rejects_empty_rule_version() -> None:
    """ruleVersion empty string is rejected (min_length=1)."""
    wire = _build_closure_wire(rule_version="")
    with pytest.raises(ValidationError) as exc_info:
        HandoffClosureV3.model_validate(wire)
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("ruleVersion",) for err in errors)


def test_closure_rejects_rule_version_exceeds_max_length() -> None:
    wire = _build_closure_wire(rule_version="x" * 261)
    with pytest.raises(ValidationError) as exc_info:
        HandoffClosureV3.model_validate(wire)
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("ruleVersion",) for err in errors)


# -- requestId boundary --
def test_closure_accepts_request_id_at_min_length() -> None:
    """requestId length 3 is accepted (min_length=3)."""
    closure = _make_closure(request_id="a#b")
    assert closure.request_id == "a#b"


@pytest.mark.parametrize("bad_len", [0, 1, 2])
def test_closure_rejects_request_id_below_min_length(bad_len: int) -> None:
    """requestId length 0, 1, 2 are rejected (min_length=3)."""
    wire = _build_closure_wire(request_id="x" * bad_len)
    with pytest.raises(ValidationError) as exc_info:
        HandoffClosureV3.model_validate(wire)
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("requestId",) for err in errors)


def test_closure_accepts_request_id_at_420_boundary() -> None:
    """requestId at exactly 420 chars is accepted at the outer field level.

    The outer ``requestId`` field has ``max_length=420``. The nested payload
    has its own ``requestId`` (capped at 420 as well). We test the outer
    field's own boundary by providing a payload whose ``requestId`` is also
    at 420 and whose ``ruleRef.ruleVersion`` is within its own 260 limit.
    """
    # ruleVersion max=260, factCode max=160 → requestId max = 260+1+160 = 421
    # factCode pattern: ^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$ (dot-separated)
    # ruleVersion must start with "ruleSetId@" per FactBindingRequestV3
    # Use ruleVersion=259 chars + "#" + factCode=160 chars → 420 total
    rv = "R@" + "x" * 257  # 259 chars, starts with "R@"
    fc = "ab" + "c" * 59 + ".de" + "f" * 96  # 160 chars, valid pattern
    rid = f"{rv}#{fc}"  # 420 chars
    pl = _load_synthetic_v3_payload()
    pl["requestId"] = rid
    pl["ruleRef"]["ruleVersion"] = rv
    pl["ruleRef"]["ruleSetId"] = "R"  # must be prefix of ruleVersion
    pl["fact"]["factCode"] = fc
    pl["mappingCandidate"]["factCode"] = fc
    for field in pl["queryRequirements"]["fields"]:
        if field["fieldId"] == "factValue":
            field["logicalName"] = fc
    wire = _build_closure_wire(request_id=rid, payload=pl)
    closure = HandoffClosureV3.model_validate(wire)
    assert len(closure.request_id) == 420


def test_closure_rejects_request_id_at_421() -> None:
    wire = _build_closure_wire(request_id="x" * 421)
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


# -- factCode format --
def test_closure_accepts_fact_code_min_format() -> None:
    """factCode 'a.b' (minimal valid format) is accepted."""
    closure = _make_closure(fact_code="a.b")
    assert closure.fact_code == "a.b"


def test_closure_accepts_fact_code_at_max_length() -> None:
    """factCode at 160 chars (max_length) is accepted."""
    fc = "ab" + "c" * 59 + ".de" + "f" * 96  # 160 chars, valid pattern
    closure = _make_closure(fact_code=fc)
    assert len(closure.fact_code) == 160


def test_closure_rejects_empty_fact_code() -> None:
    """factCode empty string is rejected."""
    wire = _build_closure_wire(fact_code="")
    with pytest.raises(ValidationError) as exc_info:
        HandoffClosureV3.model_validate(wire)
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("factCode",) for err in errors)


def test_closure_rejects_fact_code_exceeds_max_length() -> None:
    """factCode at 161 chars is rejected (max_length=160)."""
    fc = "ab" + "c" * 60 + ".de" + "f" * 96  # 161 chars
    wire = _build_closure_wire(fact_code=fc)
    with pytest.raises(ValidationError) as exc_info:
        HandoffClosureV3.model_validate(wire)
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("factCode",) for err in errors)


def test_closure_rejects_fact_code_invalid_format() -> None:
    wire = _build_closure_wire(fact_code="Not.A.Valid.Format")
    with pytest.raises(ValidationError) as exc_info:
        HandoffClosureV3.model_validate(wire)
    errors = exc_info.value.errors()
    assert any(err["loc"] == ("factCode",) for err in errors)


# -- SHA-256 fields: valid, length, case, non-hex --
@pytest.mark.parametrize(
    "param_name",
    ["payload_sha256", "batch_sha256", "contract_schema_sha256"],
)
def test_closure_accepts_valid_sha256(param_name: str) -> None:
    """A valid 64-char lowercase hex SHA-256 is accepted."""
    wire = _build_closure_wire(**{param_name: "0" * 64})  # type: ignore[arg-type]
    closure = HandoffClosureV3.model_validate(wire)
    # The wire dict carries the param_name; verify the dumped value matches.
    camel_key = "".join(
        word.capitalize() if i > 0 else word for i, word in enumerate(param_name.split("_"))
    )
    assert closure.model_dump(by_alias=True, mode="json")[camel_key] == "0" * 64


@pytest.mark.parametrize(
    "param_name",
    ["payload_sha256", "batch_sha256", "contract_schema_sha256"],
)
def test_closure_rejects_sha256_too_short(param_name: str) -> None:
    wire = _build_closure_wire(**{param_name: "a" * 63})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


@pytest.mark.parametrize(
    "param_name",
    ["payload_sha256", "batch_sha256", "contract_schema_sha256"],
)
def test_closure_rejects_sha256_too_long(param_name: str) -> None:
    """SHA-256 at 65 chars is rejected (must be exactly 64)."""
    wire = _build_closure_wire(**{param_name: "a" * 65})  # type: ignore[arg-type]
    with pytest.raises(ValidationError) as exc_info:
        HandoffClosureV3.model_validate(wire)
    errors = exc_info.value.errors()
    # The error must come from the target SHA-256 field, not from the payload
    camel_field = "".join(
        word.capitalize() if i > 0 else word for i, word in enumerate(param_name.split("_"))
    )
    assert any(camel_field in str(err["loc"]) for err in errors)


@pytest.mark.parametrize(
    "param_name",
    ["payload_sha256", "batch_sha256", "contract_schema_sha256"],
)
def test_closure_rejects_sha256_uppercase(param_name: str) -> None:
    wire = _build_closure_wire(**{param_name: "A" * 64})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


@pytest.mark.parametrize(
    "param_name",
    ["payload_sha256", "batch_sha256", "contract_schema_sha256"],
)
def test_closure_rejects_sha256_non_hex(param_name: str) -> None:
    wire = _build_closure_wire(**{param_name: "g" * 64})  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


# ===================================================================
# Section 7: V2 payload rejected — using the complete V2 fixture
# ===================================================================


def test_v2_fixture_passes_v2_consumer() -> None:
    """Precondition: the synthetic V2 ready fixture is a valid V2 request."""
    from release_sql_bot.domain.fact_bindings_v2 import FactBindingRequestV2

    v2_payload = _load_synthetic_v2_payload()
    v2_model = FactBindingRequestV2.model_validate(v2_payload)
    assert v2_model.contract_version == "2.0.0"


def test_closure_rejects_complete_v2_fixture() -> None:
    """A structurally valid V2 request is rejected by the V3 closure.

    The V2 payload has an incompatible shape (no ``evidence``, no
    ``uncertainties``, no ``requiresMetadataSnapshot``, etc.), so the nested
    ``FactBindingRequestV3`` validation fails.
    """
    v2_payload = _load_synthetic_v2_payload()
    wire = _build_closure_wire(payload=v2_payload)
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


# ===================================================================
# Section 8: Input payload not mutated by construction
# ===================================================================


def test_closure_does_not_mutate_input_wire() -> None:
    wire = _build_closure_wire()
    original = deepcopy(wire)
    HandoffClosureV3.model_validate(wire)
    assert wire == original


# ===================================================================
# Section 9: M1 stage boundary — structurally valid closure constructs,
# but M2 identity/closure checks are NOT performed
# ===================================================================


def test_closure_constructs_with_mismatched_rule_version_in_m1() -> None:
    """M1 does NOT compare closure.ruleVersion against payload.ruleRef.ruleVersion.

    A closure with a ruleVersion that differs from the payload's own ruleVersion
    can still be constructed (the payload itself is internally valid). This
    proves M1 does not implement M2 cross-field identity checks.
    """
    pl = _load_synthetic_v3_payload()
    wire = _build_closure_wire(rule_version="DIFFERENT@v1", payload=pl)
    closure = HandoffClosureV3.model_validate(wire)
    # Constructed successfully — M1 only checks the closure's own field format
    assert closure.rule_version == "DIFFERENT@v1"
    # The payload's own ruleVersion is unchanged
    assert closure.payload.rule_ref.rule_version != "DIFFERENT@v1"


def test_closure_constructs_with_mismatched_request_id_in_m1() -> None:
    """M1 does NOT compare closure.requestId against payload.requestId."""
    pl = _load_synthetic_v3_payload()
    wire = _build_closure_wire(request_id="DIFFERENT@v1#fact.one", payload=pl)
    closure = HandoffClosureV3.model_validate(wire)
    assert closure.request_id == "DIFFERENT@v1#fact.one"


def test_closure_rejects_invalid_nested_payload() -> None:
    """A structurally invalid nested payload is still rejected by M1."""
    pl = _load_synthetic_v3_payload()
    del pl["evidence"]  # FactBindingRequestV3 requires evidence
    wire = _build_closure_wire(payload=pl)
    with pytest.raises(ValidationError):
        HandoffClosureV3.model_validate(wire)


# ===================================================================
# Section 10: Duplicate conditionId usages preserved (not merged)
# ===================================================================


def test_closure_preserves_duplicate_condition_id_different_stage() -> None:
    """Same conditionId in different stages must be preserved, not merged."""
    pl = _load_synthetic_v3_payload()
    second_usage = dict(pl["usages"][0])
    second_usage["stage"] = "exclusions"
    pl["usages"] = pl["usages"] + [second_usage]
    wire = _build_closure_wire(payload=pl)
    closure = HandoffClosureV3.model_validate(wire)
    stages = [u.stage for u in closure.payload.usages]
    condition_ids = [u.condition_id for u in closure.payload.usages]
    assert len(closure.payload.usages) == len(pl["usages"])
    assert "eligibility" in stages
    assert "exclusions" in stages
    assert condition_ids.count("cond-synthetic-001") == 2


# ===================================================================
# Section 11: RepositoryVerifiedHandoffV3 — non-instantiable
# ===================================================================


def test_repository_verified_has_no_schema_version() -> None:
    """RepositoryVerifiedHandoffV3 is not a wire DTO — no schemaVersion field."""
    assert not hasattr(RepositoryVerifiedHandoffV3, "schema_version")
    assert not hasattr(RepositoryVerifiedHandoffV3, "schemaVersion")


def test_repository_verified_cannot_be_built_from_dict() -> None:
    """RepositoryVerifiedHandoffV3 has no model_validate / parse_obj."""
    assert not hasattr(RepositoryVerifiedHandoffV3, "model_validate")
    assert not hasattr(RepositoryVerifiedHandoffV3, "parse_obj")


def test_repository_verified_rejects_dict_unpacking_with_real_payload() -> None:
    """**dict with correct snake_case keys + real parsed payload still fails."""
    payload = FactBindingRequestV3.model_validate(_load_synthetic_v3_payload())
    params = {
        "rule_version": payload.rule_ref.rule_version,
        "request_id": payload.request_id,
        "fact_code": payload.fact.fact_code,
        "payload": payload,
    }
    with pytest.raises(TypeError, match="cannot be constructed in M1"):
        RepositoryVerifiedHandoffV3(**params)


def test_repository_verified_rejects_json_loads_dict() -> None:
    """json.dumps/json.loads 整个字典后直接解包，仍被构造器拒绝。

    使用完整合成 V3 fixture，构造包含 rule_version、request_id、
    fact_code、payload 的完整字典，对整个字典执行序列化与反序列化，
    不在 json.loads 后重新塞入模型对象。
    """
    pl = _load_synthetic_v3_payload()
    # 构造完整字典（identity 参数从 fixture 提取，payload 保持普通 dict）
    full_dict = {
        "rule_version": pl["ruleRef"]["ruleVersion"],
        "request_id": pl["requestId"],
        "fact_code": pl["fact"]["factCode"],
        "payload": pl,  # 普通 dict，未经 FactBindingRequestV3 解析
    }
    # 对整个字典执行序列化与反序列化
    deserialized = json.loads(json.dumps(full_dict, sort_keys=True))
    with pytest.raises(TypeError, match="cannot be constructed in M1"):
        RepositoryVerifiedHandoffV3(**deserialized)


def test_repository_verified_rejects_closure_fields() -> None:
    """Extracting fields from a valid closure still fails to construct."""
    closure = _make_closure()
    with pytest.raises(TypeError, match="cannot be constructed in M1"):
        RepositoryVerifiedHandoffV3(
            rule_version=closure.rule_version,
            request_id=closure.request_id,
            fact_code=closure.fact_code,
            payload=closure.payload,
        )


def test_repository_verified_rejects_self_reported_flag() -> None:
    """repositoryVerified=true is not accepted as proof."""
    payload = FactBindingRequestV3.model_validate(_load_synthetic_v3_payload())
    with pytest.raises(TypeError, match="cannot be constructed in M1"):
        RepositoryVerifiedHandoffV3(
            rule_version=payload.rule_ref.rule_version,
            request_id=payload.request_id,
            fact_code=payload.fact.fact_code,
            payload=payload,
            repositoryVerified=True,
        )


def test_repository_verified_rejects_dict_payload() -> None:
    """使用完整 V3 合成 fixture 的普通 dict payload 仍被拒绝。

    身份参数从完整 fixture 提取，payload 保持普通 dict（未经
    FactBindingRequestV3 解析），断言相同的 M1 构造器错误信息。
    """
    pl = _load_synthetic_v3_payload()
    with pytest.raises(TypeError, match="cannot be constructed in M1"):
        RepositoryVerifiedHandoffV3(
            rule_version=pl["ruleRef"]["ruleVersion"],
            request_id=pl["requestId"],
            fact_code=pl["fact"]["factCode"],
            payload=pl,  # type: ignore[arg-type]
        )


def test_repository_verified_rejects_parsed_fact_binding_payload() -> None:
    """Even with a fully parsed FactBindingRequestV3, construction fails."""
    payload = FactBindingRequestV3.model_validate(_load_synthetic_v3_payload())
    with pytest.raises(TypeError, match="cannot be constructed in M1"):
        RepositoryVerifiedHandoffV3(
            rule_version=payload.rule_ref.rule_version,
            request_id=payload.request_id,
            fact_code=payload.fact.fact_code,
            payload=payload,
        )


def test_repository_verified_rejects_no_args() -> None:
    """Even a no-argument construction attempt fails with the same M1 message."""
    with pytest.raises(TypeError, match="cannot be constructed in M1"):
        RepositoryVerifiedHandoffV3()


# ===================================================================
# Section 12: No V2 / infrastructure dependencies in domain module
# ===================================================================


def test_handoff_closure_module_does_not_import_v2_contracts() -> None:
    import release_sql_bot.domain.handoff_closure_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "fact_bindings_v2" not in source
    assert "from release_sql_bot.domain.fact_bindings_v2" not in source


def test_handoff_closure_module_does_not_import_infrastructure() -> None:
    import release_sql_bot.domain.handoff_closure_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "mongodb" not in source
    assert "pymongo" not in source
    assert "os.environ" not in source
    assert "getenv" not in source


def test_handoff_closure_module_does_not_import_application() -> None:
    import release_sql_bot.domain.handoff_closure_v3 as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "from release_sql_bot.application" not in source
    assert "handoff_intake_v3" not in source


# ===================================================================
# Section 13: Closure identity fields match intake output shape
# ===================================================================


def test_closure_contract_schema_id_matches_intake_constant() -> None:
    """The closure carries the exact frozen Schema identity from intake."""
    closure = _make_closure()
    assert closure.contract_schema_id == FACT_BINDING_SCHEMA_ID_V3
    assert closure.contract_schema_sha256 == FACT_BINDING_SCHEMA_SHA256_V3
    assert closure.contract_schema_id == "urn:rulereader:fact-binding-request:3.0.0"


def test_closure_intake_status_matches_intake_output() -> None:
    closure = _make_closure()
    assert closure.intake_status == "readyForMetadataResolution"


def test_closure_payload_is_full_fact_binding_request_v3() -> None:
    closure = _make_closure()
    assert isinstance(closure.payload, FactBindingRequestV3)
    assert closure.payload.contract_version == "3.0.0"
    assert closure.payload.target_dialect == "sqlserver"
    assert closure.payload.query_requirements is not None
    assert closure.payload.evidence is not None
