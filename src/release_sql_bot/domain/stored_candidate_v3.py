"""V3 stored candidate document wrapper contract.

The wrapper is the MongoDB document envelope persisted by the V3 candidate
store. It carries its own ``schemaVersion="1.0.0"`` (document wrapper version,
following the V2 stored document convention) while the embedded candidate
retains its own ``schemaVersion="3.0.0"`` (candidate contract version).

The outer ``contentSha256`` must always equal the candidate's
``contentSha256``; any mismatch means the document is corrupt and must be
rejected on read.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from release_sql_bot.domain.fact_bindings_v3 import V3ConsumerModel
from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3


class _StoredV3Base(V3ConsumerModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_alias=True,
        validate_by_name=False,
        serialize_by_alias=True,
        extra="forbid",
        strict=True,
    )


class StoredCandidateV3(_StoredV3Base):
    """MongoDB document envelope for a stored V3 candidate."""

    schema_version: Literal["1.0.0"] = Field(default="1.0.0", alias="schemaVersion")
    content_sha256: str = Field(
        ...,
        alias="contentSha256",
        min_length=64,
        max_length=64,
    )
    stored_at_utc: str = Field(..., alias="storedAtUtc")
    candidate: SqlTemplateCandidateV3

    @model_validator(mode="after")
    def _verify_content_sha256_matches_candidate(self) -> StoredCandidateV3:
        if self.candidate.content_sha256 != self.content_sha256:
            raise ValueError("wrapper contentSha256 does not match candidate contentSha256")
        return self

    def to_wire(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, mode="json")
