"""Selected 3.1.0 complete delivery: tree + catalog + result + batch."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from release_sql_bot.domain.fact_bindings_v3 import V3ReportModel
from release_sql_bot.domain.fact_bindings_v31 import FactBindingRequestV31
from release_sql_bot.domain.rule_structure_v31 import (
    STAGE_ORDER_V31,
    BusinessConfirmedFactCatalogV31,
    RuleParseResultV31,
    RuleStructureCandidateV31,
)

_SHA256_PATTERN = r"^[a-f0-9]{64}$"


class StoredFactBindingHandoffRequestV31(V3ReportModel):
    request_id: str = Field(min_length=3, max_length=420)
    rule_version: str = Field(min_length=1, max_length=260)
    fact_code: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    contract_version: Literal["3.1.0"]
    payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    created_at: datetime
    payload: FactBindingRequestV31

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


class StoredFactBindingHandoffBatchV31(V3ReportModel):
    mongo_id: str = Field(alias="_id", min_length=1, max_length=260)
    rule_version: str = Field(min_length=1, max_length=260)
    contract_version: Literal["3.1.0"]
    request_count: int = Field(ge=1)
    request_ids: list[str] = Field(min_length=1)
    batch_sha256: str = Field(pattern=_SHA256_PATTERN)
    created_at: datetime
    requests: list[StoredFactBindingHandoffRequestV31] = Field(min_length=1)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


class StoredCompleteDeliveryV31(V3ReportModel):
    """Untrusted stored envelope. Missing payloads stay None until intake."""

    rule_version: str = Field(min_length=1, max_length=260)
    schema_version: str = Field(min_length=1, max_length=20)
    purpose: str = Field(min_length=1, max_length=80)
    status: str = Field(min_length=1, max_length=40)
    executable: bool
    source_file_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    parse_input_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    catalog_digest: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    catalog_payload: dict[str, object] | None = None
    candidate_payload: dict[str, object] | None = None
    result_payload: dict[str, object] | None = None
    batch: StoredFactBindingHandoffBatchV31 | None = None


class CompleteDeliveryV31(V3ReportModel):
    rule_version: str
    purpose: str
    schema_version: Literal["3.1.0"]
    consumable: bool
    missing: tuple[str, ...]
    executable: Literal[False] = False
    status: Literal["draft"]
    delivery_purpose: Literal["optimization-plan-generation"]
    source_file_sha256: str = Field(pattern=_SHA256_PATTERN)
    parse_input_sha256: str = Field(pattern=_SHA256_PATTERN)
    catalog_digest: str = Field(pattern=_SHA256_PATTERN)
    catalog_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    candidate_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    result_payload_sha256: str = Field(pattern=_SHA256_PATTERN)
    batch_sha256: str = Field(pattern=_SHA256_PATTERN)
    request_count: int = Field(ge=1)
    stage_names: tuple[str, ...]
    catalog: BusinessConfirmedFactCatalogV31
    candidate: RuleStructureCandidateV31
    result: RuleParseResultV31
    batch: StoredFactBindingHandoffBatchV31

    @field_validator("stage_names")
    @classmethod
    def require_five_stages(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value != STAGE_ORDER_V31:
            raise ValueError("complete delivery must expose the frozen five-stage tree")
        return value
