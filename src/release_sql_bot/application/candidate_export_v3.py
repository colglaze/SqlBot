"""保留同次生成候选，供本地人工复核；不改变存储和审核状态。"""

from __future__ import annotations

from typing import Any

from release_sql_bot.application.canonical import canonical_content_sha256, canonical_sha256
from release_sql_bot.application.ports.candidate_store_v3 import (
    CandidateStoreV3Outcome,
    CandidateTemplateStoreV3,
)
from release_sql_bot.application.sql_validation_v3 import validate_sql_candidate_v3
from release_sql_bot.domain.evidence_pack_v3 import EvidencePackV3
from release_sql_bot.domain.sql_candidates_v3 import (
    GenerateSqlCandidateRequestV3,
    SqlTemplateCandidateV3,
)
from release_sql_bot.domain.sql_validation_v3 import ValidateSqlCandidateRequestV3


class CapturingCandidateStoreV3:
    def __init__(self, delegate: CandidateTemplateStoreV3) -> None:
        self.delegate = delegate
        self.candidate: SqlTemplateCandidateV3 | None = None

    async def initialize(self) -> None:
        await self.delegate.initialize()

    async def close(self) -> None:
        await self.delegate.close()

    async def save(self, candidate: SqlTemplateCandidateV3) -> CandidateStoreV3Outcome:
        self.candidate = SqlTemplateCandidateV3.model_validate(
            candidate.model_dump(by_alias=True, mode="json")
        )
        return await self.delegate.save(candidate)


def build_candidate_export_v3(
    *,
    candidate: SqlTemplateCandidateV3,
    payload: GenerateSqlCandidateRequestV3,
    pack: EvidencePackV3,
) -> dict[str, Any]:
    """仅导出与证据包相符的候选及确定性静态报告。"""
    rebuilt = SqlTemplateCandidateV3.model_validate(
        candidate.model_dump(by_alias=True, mode="json")
    )
    digest = canonical_content_sha256(rebuilt)
    if digest != rebuilt.content_sha256 or digest != pack.candidate_content_sha256:
        raise ValueError("CANDIDATE_EXPORT_HASH_MISMATCH")
    report = None
    if pack.static_report_sha256 is not None:
        report = validate_sql_candidate_v3(
            ValidateSqlCandidateRequestV3.model_validate(
                {
                    "schemaVersion": "1.0.0",
                    "generationRequest": payload.model_dump(by_alias=True, mode="json"),
                    "candidate": rebuilt.model_dump(by_alias=True, mode="json"),
                }
            )
        )
        if (
            canonical_sha256(report) != pack.static_report_sha256
            or report.status != pack.static_status
        ):
            raise ValueError("CANDIDATE_EXPORT_STATIC_MISMATCH")
    elif pack.static_status is not None:
        raise ValueError("CANDIDATE_EXPORT_STATIC_MISMATCH")
    return {
        "schemaVersion": "1.0.0",
        "artifactKind": "candidateReviewExportV3",
        "runId": pack.run_id,
        "evidencePackSha256": canonical_sha256(pack),
        "candidate": rebuilt.model_dump(by_alias=True, mode="json"),
        "staticReport": report.model_dump(by_alias=True, mode="json") if report else None,
    }
