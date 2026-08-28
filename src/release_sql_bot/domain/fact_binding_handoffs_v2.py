"""Contracts for RuleReader-owned immutable fact binding handoff records."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from release_sql_bot.domain.fact_bindings_v2 import (
    BindingGapReport,
    FactBindingRequestV2,
    ReportModel,
)


class HandoffDocumentModel(BaseModel):
    """Strict model for the snake_case RuleReader MongoDB wrapper."""

    model_config = ConfigDict(
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class StoredFactBindingHandoffV2(HandoffDocumentModel):
    mongo_id: str = Field(alias="_id", min_length=3, max_length=384)
    request_id: str = Field(min_length=3, max_length=384)
    rule_version: str = Field(min_length=1, max_length=220)
    fact_code: str = Field(
        pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$",
        max_length=160,
    )
    contract_version: Literal["2.0.0"]
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    payload: FactBindingRequestV2

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


class FactBindingHandoffIntakeRecordV2(ReportModel):
    request_id: str = Field(min_length=3, max_length=384)
    fact_code: str = Field(min_length=1, max_length=160)
    payload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    created_at: datetime
    payload: FactBindingRequestV2
    gap_report: BindingGapReport


class FactBindingHandoffIntakeBatchV2(ReportModel):
    rule_version: str = Field(min_length=1, max_length=220)
    contract_version: Literal["2.0.0"] = "2.0.0"
    contract_schema_id: str = Field(min_length=1, max_length=200)
    contract_schema_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    status: Literal["blocked", "readyForMetadataResolution"]
    executable: Literal[False] = False
    record_count: int = Field(ge=1)
    blocking_request_count: int = Field(ge=0)
    records: tuple[FactBindingHandoffIntakeRecordV2, ...]

    @field_validator("records")
    @classmethod
    def require_records(
        cls,
        value: tuple[FactBindingHandoffIntakeRecordV2, ...],
    ) -> tuple[FactBindingHandoffIntakeRecordV2, ...]:
        if not value:
            raise ValueError("records cannot be empty")
        return value

    def model_post_init(self, __context: object) -> None:
        if self.record_count != len(self.records):
            raise ValueError("recordCount must match records")
        actual_blocking = sum(record.gap_report.status == "blocked" for record in self.records)
        if self.blocking_request_count != actual_blocking:
            raise ValueError("blockingRequestCount must match records")
        expected_status = "blocked" if actual_blocking else "readyForMetadataResolution"
        if self.status != expected_status:
            raise ValueError("batch status must match blocking records")
