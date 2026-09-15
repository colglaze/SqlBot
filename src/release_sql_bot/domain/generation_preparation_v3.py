"""Strict input contract for preparing a V3 generation request.

The preparation step is deliberately separate from candidate generation.  A
caller supplies the exact RuleReader version/request identity and the
metadataReview-approved context, snapshot, and approval record.  The
repository-backed application service supplies the handoff payload and its
content closure after running the V3 intake; this input model never accepts a
caller-provided payload or a repository-verification flag.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from release_sql_bot.domain.fact_bindings_v3 import V3ConsumerModel
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalRecordV3,
    GovernedMetadataSnapshotV3,
    ProjectBindingContextV3,
)


def _reject_nested_snake_case_keys(value: Any) -> None:
    """Reject snake_case keys before nested models can normalize them."""

    if isinstance(value, dict):
        for key, nested in value.items():
            if isinstance(key, str) and "_" in key:
                raise ValueError(f"snake_case key is not accepted: {key}")
            _reject_nested_snake_case_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_nested_snake_case_keys(nested)


class _PrepareConsumerModel(V3ConsumerModel):
    """Strict camelCase model for the local preparation input."""

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
        _reject_nested_snake_case_keys(value)
        return value


class PrepareGenerationRequestV3(_PrepareConsumerModel):
    """Strict package consumed by the repository-backed prepare service.

    ``ruleVersion`` and ``requestId`` are exact identities.  The handoff
    payload, ``HandoffClosureV3``, and ``projectRef`` are intentionally absent:
    all three are derived from the repository intake and the approved context
    by the application service.
    """

    schema_version: Literal["1.0.0"]
    rule_version: str = Field(min_length=1, max_length=260)
    request_id: str = Field(min_length=3, max_length=420)
    project_context: ProjectBindingContextV3
    metadata_snapshot: GovernedMetadataSnapshotV3
    approval_record: ApprovalRecordV3

    @field_validator("rule_version", "request_id")
    @classmethod
    def reject_latest_selector(cls, value: str) -> str:
        """Require a concrete identity instead of a moving ``latest`` selector."""

        if value.strip().casefold() == "latest":
            raise ValueError("latest is not an exact rule/request identity")
        return value


PREPARATION_ERROR_CODES: frozenset[str] = frozenset(
    {
        "PREPARE_REQUEST_STRUCTURE_INVALID",
        "PREPARE_HANDOFF_REPOSITORY_UNAVAILABLE",
        "PREPARE_HANDOFF_BATCH_NOT_FOUND",
        "PREPARE_HANDOFF_BATCH_INVALID",
        "PREPARE_HANDOFF_REQUEST_NOT_FOUND",
        "PREPARE_APPROVAL_PORT_UNAVAILABLE",
        "PREPARE_APPROVAL_RECORD_NOT_FOUND",
        "PREPARE_APPROVAL_RECORD_MISMATCH",
        "PREPARE_M2_BLOCKED",
        "PREPARE_M2_STRUCTURE_INVALID",
    }
)


class GenerationPreparationErrorV3(RuntimeError):
    """Neutral preparation failure carrying only stable issue codes.

    The exception never stores the request, repository document, approval
    record, SQL, or provider data.  ``issue_codes`` is intended for safe
    reporting of deterministic M2 blocker codes only.
    """

    def __init__(
        self,
        code: str,
        *,
        issue_codes: tuple[str, ...] = (),
    ) -> None:
        if code not in PREPARATION_ERROR_CODES:
            raise ValueError("unknown generation-preparation issue code")
        if any(not isinstance(item, str) or not item for item in issue_codes):
            raise ValueError("generation-preparation issue codes must be non-empty strings")
        self.code = code
        self.issue_codes = tuple(issue_codes)
        super().__init__(code)

    def __str__(self) -> str:
        return self.code

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, issue_codes={self.issue_codes!r})"
