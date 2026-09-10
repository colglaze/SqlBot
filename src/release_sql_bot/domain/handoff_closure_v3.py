"""V3 handoff dual-layer contract: content closure and repository-verified result.

Two distinct concepts (DEV §5.1, frozen 2026-09-06 second review):

1. ``HandoffClosureV3`` — 内容闭包。严格 camelCase、可序列化的 wire 契约。
   携带身份字段（``ruleVersion``、``requestId``、``factCode``）、内容哈希
   （``payloadSha256``、``batchSha256``）、冻结 Schema 身份
   （``contractSchemaId``、``contractSchemaSha256``）、``intakeStatus`` 快照
   和完整的 ``FactBindingRequestV3`` payload。

   M1 只验证结构：schemaVersion 常量、strict、camelCase、extra=forbid、
   字段格式/长度、固定 intakeStatus、嵌套 payload 约束。
   **不**证明内容闭包哈希/引用内部一致（那是 M2 范围）；
   **不**证明 batch 真实存在于 MongoDB、由 RuleReader 写入、
   ``intakeStatus`` 来自真实 intake 或载荷属于当前批准的真实运行。
   从 HTTP/CLI JSON 反序列化的 ``HandoffClosureV3`` 只是内容闭包，
   永远不得称为 repository-verified attestation。

2. ``RepositoryVerifiedHandoffV3`` — the **repository-attested result**. An
   internal type that a trusted application service constructs **within a single
   application call** after re-reading the batch from the repository by exact
   ruleVersion, re-running V3 intake, selecting the unique request, and comparing
   it field-by-field and hash-by-hash against the carried closure/context/
   snapshot. It is not a wire DTO: it has no ``schemaVersion``, cannot be
   constructed from a plain dict/JSON, and exposes no public conversion entry
   that upgrades an ordinary closure to a verified one. A caller-supplied
   ``repositoryVerified=true`` flag is not accepted as proof.

M1 scope (this module): define the **shapes and structural constraints only**.

- ``HandoffClosureV3``: schema-version constant, strict camelCase,
  ``extra=forbid``, field formats/lengths, fixed ``intakeStatus``, and the
  complete nested ``FactBindingRequestV3`` payload constraint. **No** cross-field
  identity verification (closure-vs-payload identity comparison, payload-hash
  recomputation, or frozen-Source-Schema re-verification) — those are M2.
- ``RepositoryVerifiedHandoffV3``: declare the internal-result type. Its
  constructor is **blocked** (raises ``TypeError``) because M1 implements no
  repository access, intake orchestration, or authenticated factory. The
  trusted-construction mechanism is deferred to the repository-backed
  application-service phase.

This module imports neither application/infrastructure code, nor MongoDB/SQL
drivers, web frameworks, LLM SDKs, or V2 contracts.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from release_sql_bot.domain.fact_bindings_v3 import (
    FactBindingRequestV3,
    V3ConsumerModel,
)

_SHA256_PATTERN = r"^[a-f0-9]{64}$"


def _reject_snake_case_keys(value: Any) -> None:
    """Recursively reject any snake_case key at any nesting level."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and "_" in key:
                raise ValueError(f"snake_case key is not accepted: {key}")
            _reject_snake_case_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_snake_case_keys(nested)


class _ClosureBase(V3ConsumerModel):
    """Strict wire model: camelCase aliases only, extra=forbid, strict, no snake_case."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )

    @model_validator(mode="before")
    @classmethod
    def reject_nested_snake_case_fallback(cls, value: Any) -> Any:
        _reject_snake_case_keys(value)
        return value


class HandoffClosureV3(_ClosureBase):
    """V3 handoff content closure (DEV §5.1).

    Pure-computation, strictly camelCase, serializable wire contract. M1 proves
    only wire-shape legality (schemaVersion constant, strict typing, field
    formats/lengths, fixed intakeStatus, complete nested payload). It does
    **not** verify cross-field identity against the carried payload — that is
    M2 content-closure scope.
    """

    schema_version: Literal["1.0.0"]
    rule_version: str = Field(min_length=1, max_length=260)
    request_id: str = Field(min_length=3, max_length=420)
    fact_code: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    batch_sha256: str = Field(pattern=_SHA256_PATTERN)
    contract_schema_id: str = Field(min_length=1, max_length=200)
    contract_schema_sha256: str = Field(pattern=_SHA256_PATTERN)
    intake_status: Literal["readyForMetadataResolution"]
    payload: FactBindingRequestV3


_HANDOFF_CLOSURE_CODES: frozenset[str] = frozenset(
    {
        "HANDOFF_STRUCTURE_INVALID",
        "HANDOFF_SCHEMA_SOURCE_INVALID",
        "HANDOFF_SCHEMA_REF_MISMATCH",
        "HANDOFF_PAYLOAD_SCHEMA_INVALID",
        "HANDOFF_IDENTITY_MISMATCH",
        "HANDOFF_PAYLOAD_HASH_MISMATCH",
    }
)


class HandoffClosureValidationErrorV3(Exception):
    """Stable neutral error for V3 handoff content-closure validation.

    Carries only the issue code. Never carries raw IDs, hashes, private
    objects, or original payloads. Only accepts codes from the frozen
    whitelist.
    """

    def __init__(self, code: str) -> None:
        if not isinstance(code, str) or code not in _HANDOFF_CLOSURE_CODES:
            raise ValueError("unknown handoff-closure issue code")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class RepositoryVerifiedHandoffV3:
    """Internal repository-attested result — NOT a wire DTO, NOT constructible in M1.

    Constructed ONLY by a trusted application service within a single
    application call after: re-reading the batch from the repository by exact
    ruleVersion, re-running V3 intake, selecting the unique request, and
    comparing it field-by-field and hash-by-hash against the carried closure.

    This type is intentionally NOT a pydantic model:
    - No ``schemaVersion`` wire field.
    - Cannot be built from a dict/JSON (no ``model_validate``/``parse_obj``).
    - The constructor raises ``TypeError`` because M1 implements no
      repository access, intake orchestration, or authenticated factory.
    - Caller-supplied ``repositoryVerified`` flags are not accepted.
    """

    __slots__ = ()

    def __init__(self, **kwargs: Any) -> None:
        """Block construction — repository attestation is not implemented in M1.

        Every normal construction path fails: keyword args from a dict
        (``**dict``), from JSON (``**json.loads(...)``), from a closure's
        fields, with or without a ``repositoryVerified`` flag, and with a
        plain-dict or parsed payload. The trusted-construction mechanism is
        deferred to the repository-backed application-service phase.
        """
        raise TypeError(
            "RepositoryVerifiedHandoffV3 cannot be constructed in M1: "
            "no repository access, intake orchestration, or authenticated "
            "factory is implemented. Trusted construction is deferred "
            "to the repository-backed application-service phase."
        )
