"""Contracts for RuleReader-owned immutable V3 fact binding handoff batches.

The upstream Schema v5 collection ``fact_binding_handoff_batches_v3`` stores one
atomic single-document batch per exact rule version. This module mirrors that
frozen wrapper without importing RuleReader; wrappers are untrusted input and
every field is validated before intake trusts it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from release_sql_bot.domain.fact_bindings_v3 import (
    FactBindingRequestV3,
    V3ReportModel,
)


class V3HandoffDocumentModel(BaseModel):
    """Strict model for the snake_case RuleReader MongoDB batch wrapper."""

    model_config = ConfigDict(
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class StoredFactBindingHandoffRequestV3(V3HandoffDocumentModel):
    request_id: str = Field(min_length=3, max_length=420)
    rule_version: str = Field(min_length=1, max_length=260)
    fact_code: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    contract_version: Literal["3.0.0"]
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    payload: FactBindingRequestV3

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


class StoredFactBindingHandoffBatchV3(V3HandoffDocumentModel):
    mongo_id: str = Field(alias="_id", min_length=1, max_length=260)
    rule_version: str = Field(min_length=1, max_length=260)
    contract_version: Literal["3.0.0"]
    request_count: int = Field(ge=1)
    request_ids: list[str] = Field(min_length=1)
    batch_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    requests: list[StoredFactBindingHandoffRequestV3] = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


class FactBindingHandoffIntakeRequestV3(V3ReportModel):
    request_id: str = Field(min_length=3, max_length=420)
    fact_code: str = Field(min_length=1, max_length=160)
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    payload: FactBindingRequestV3


class FactBindingHandoffIntakeBatchV3(V3ReportModel):
    rule_version: str = Field(min_length=1, max_length=260)
    contract_version: Literal["3.0.0"] = "3.0.0"
    contract_schema_id: str = Field(min_length=1, max_length=200)
    contract_schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    batch_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["readyForMetadataResolution"] = "readyForMetadataResolution"
    executable: Literal[False] = False
    request_count: int = Field(ge=1)
    requests: tuple[FactBindingHandoffIntakeRequestV3, ...]

    @field_validator("requests")
    @classmethod
    def require_requests(
        cls,
        value: tuple[FactBindingHandoffIntakeRequestV3, ...],
    ) -> tuple[FactBindingHandoffIntakeRequestV3, ...]:
        if not value:
            raise ValueError("requests cannot be empty")
        return value

    def model_post_init(self, __context: object) -> None:
        if self.request_count != len(self.requests):
            raise ValueError("requestCount must match requests")
