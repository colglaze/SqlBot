"""V3 evidence pack contract (M6 first slice, review fix).

The evidence pack is the immutable audit record of a single offline V3
evidence loop run. It captures the outcome of each pipeline stage
(generate -> store -> static validate) as reference hashes and status
flags, without carrying any sensitive content.

Explicitly forbidden: SQL text, parameter values, object/field lists,
connection strings, URIs, API keys, provider raw fixes applied:

- Nullable downstream fields (``candidateContentSha256``,
  ``storeOutcome``, ``staticStatus``, ``staticReportSha256``,
  ``provider``, ``model``, ``promptVersion``) are *required* to appear
  in the payload (``...``) but their value may be ``null``. There is no
  ``default=None``, so callers must always pass them explicitly.
- ``storeOutcome`` is restricted to ``stored``/``duplicate``/
  ``unavailable``/``failed`` via ``Literal``.
- A stage-consistency ``model_validator`` rejects any combination of
  stage and downstream fields that cannot arise from the pipeline.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field, StrictBool, model_validator

from release_sql_bot.domain.fact_bindings_v3 import V3ReportModel

_SHA256_PATTERN = r"^[a-f0-9]{64}$"
_RUN_ID_PATTERN = r"^[a-f0-9]{32}$"
_RUN_ID_RE = re.compile(_RUN_ID_PATTERN)

_STORE_OUTCOME_VALUES = Literal["stored", "duplicate", "unavailable", "failed"]


class EvidencePackV3(V3ReportModel):
    """Immutable V3 evidence pack assembled by the evidence loop or CLI.

    ``schemaVersion="1.0.0"`` is the offline-loop contract (no online
    authorization record). ``schemaVersion="1.1.0"`` is the CLI generate-v3
    contract and requires ``runId`` plus ``authorizedOnlineProvider=true``.
    Distinct from the candidate's ``schemaVersion="3.0.0"``. camelCase /
    extra=forbid / frozen (inherited from ``V3ReportModel``).
    """

    schema_version: Literal["1.0.0", "1.1.0"] = Field(default="1.0.0", alias="schemaVersion")
    stage: Literal[
        "blockedUpstream",
        "candidateGenerated",
        "candidateStored",
        "evidenceComplete",
    ]
    rule_version: str = Field(..., alias="ruleVersion", min_length=1, max_length=260)
    request_id: str = Field(..., alias="requestId", min_length=3, max_length=420)
    batch_sha256: str = Field(..., alias="batchSha256", pattern=_SHA256_PATTERN)
    payload_sha256: str = Field(..., alias="payloadSha256", pattern=_SHA256_PATTERN)
    repository_verification_status: Literal["verified", "failed", "unavailable"] = Field(
        ..., alias="repositoryVerificationStatus"
    )
    context_sha256: str = Field(..., alias="contextSha256", pattern=_SHA256_PATTERN)
    snapshot_sha256: str = Field(..., alias="snapshotSha256", pattern=_SHA256_PATTERN)
    resolution_report_sha256: str = Field(
        ..., alias="resolutionReportSha256", pattern=_SHA256_PATTERN
    )
    # Nullable but required: must always appear, value may be null.
    candidate_content_sha256: str | None = Field(
        ..., alias="candidateContentSha256", pattern=_SHA256_PATTERN
    )
    store_outcome: _STORE_OUTCOME_VALUES | None = Field(..., alias="storeOutcome")
    static_status: Literal["passed", "blocked"] | None = Field(..., alias="staticStatus")
    static_report_sha256: str | None = Field(
        ..., alias="staticReportSha256", pattern=_SHA256_PATTERN
    )
    provider: str | None = Field(..., alias="provider", max_length=120)
    model: str | None = Field(..., alias="model", max_length=160)
    prompt_version: str | None = Field(..., alias="promptVersion", max_length=160)
    attempt_count: int = Field(default=0, alias="attemptCount", ge=0, le=6)
    issue_codes: tuple[str, ...] = Field(default=(), alias="issueCodes")
    started_at: datetime = Field(..., alias="startedAt")
    ended_at: datetime = Field(..., alias="endedAt")
    # Optional on 1.0.0 (missing/null). Required and constrained on 1.1.0.
    # Defaults are null — never auto-generate a runId or imply authorization.
    # StrictBool rejects string/number coercion; 1.1.0 still requires True.
    run_id: str | None = Field(default=None, alias="runId", max_length=32)
    authorized_online_provider: StrictBool | None = Field(
        default=None, alias="authorizedOnlineProvider"
    )
    executable: Literal[False] = False

    @model_validator(mode="after")
    def _validate_audit_schema(self) -> EvidencePackV3:
        """Enforce 1.0.0 / 1.1.0 authorization-audit compatibility."""
        if self.schema_version == "1.1.0":
            if self.run_id is None or _RUN_ID_RE.fullmatch(self.run_id) is None:
                raise ValueError("schemaVersion 1.1.0 requires a valid runId")
            if self.authorized_online_provider is not True:
                raise ValueError("schemaVersion 1.1.0 requires authorizedOnlineProvider=true")
            return self
        if self.run_id is not None:
            raise ValueError("schemaVersion 1.0.0 cannot carry runId")
        if self.authorized_online_provider is not None:
            raise ValueError("schemaVersion 1.0.0 cannot carry authorizedOnlineProvider")
        return self

    @model_validator(mode="after")
    def _validate_stage_consistency(self) -> EvidencePackV3:
        """Reject stage/downstream-field combinations the pipeline cannot produce."""
        if self.stage == "blockedUpstream":
            if self.candidate_content_sha256 is not None:
                raise ValueError("blockedUpstream requires candidateContentSha256=null")
            if self.store_outcome is not None:
                raise ValueError("blockedUpstream requires storeOutcome=null")
            if self.static_status is not None:
                raise ValueError("blockedUpstream requires staticStatus=null")
            if self.static_report_sha256 is not None:
                raise ValueError("blockedUpstream requires staticReportSha256=null")
            # provider identity is untrusted; never copied on blockedUpstream
            if self.provider is not None:
                raise ValueError("blockedUpstream requires provider=null")
            # When the provider was never called, model/promptVersion must
            # be null. When it was called, safe metadata may be present.
            if self.attempt_count == 0:
                if self.model is not None:
                    raise ValueError("blockedUpstream attemptCount=0 requires model=null")
                if self.prompt_version is not None:
                    raise ValueError("blockedUpstream attemptCount=0 requires promptVersion=null")
            return self

        if self.stage == "candidateGenerated":
            if self.candidate_content_sha256 is None:
                raise ValueError("candidateGenerated requires candidateContentSha256")
            if self.store_outcome not in ("failed", "unavailable"):
                raise ValueError("candidateGenerated requires storeOutcome=failed|unavailable")
            if self.static_status is not None:
                raise ValueError("candidateGenerated requires staticStatus=null")
            if self.static_report_sha256 is not None:
                raise ValueError("candidateGenerated requires staticReportSha256=null")
            return self

        if self.stage in ("candidateStored", "evidenceComplete"):
            if self.candidate_content_sha256 is None:
                raise ValueError(f"{self.stage} requires candidateContentSha256")
            if self.store_outcome not in ("stored", "duplicate"):
                raise ValueError(f"{self.stage} requires storeOutcome=stored|duplicate")
            if self.static_report_sha256 is None:
                raise ValueError(f"{self.stage} requires staticReportSha256")

            expected_status = "blocked" if self.stage == "candidateStored" else "passed"
            if self.static_status != expected_status:
                raise ValueError(f"{self.stage} requires staticStatus={expected_status}")

        return self
