"""延后初始化候选存储，直到生成门禁通过并取得候选。"""

from __future__ import annotations

from release_sql_bot.application.ports.candidate_store_v3 import (
    CandidateStoreV3Outcome,
    CandidateStoreV3Status,
    CandidateTemplateStoreV3,
)
from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3


class DeferredCandidateStoreV3:
    def __init__(self, delegate: CandidateTemplateStoreV3) -> None:
        self.delegate = delegate
        self._initialized = False

    async def initialize(self) -> None:
        return None

    async def save(self, candidate: SqlTemplateCandidateV3) -> CandidateStoreV3Outcome:
        if not self._initialized:
            try:
                await self.delegate.initialize()
            except Exception:  # noqa: BLE001 - store initialization must degrade to an outcome
                return CandidateStoreV3Outcome(
                    status=CandidateStoreV3Status.UNAVAILABLE,
                    content_sha256=candidate.content_sha256,
                )
            self._initialized = True
        return await self.delegate.save(candidate)

    async def close(self) -> None:
        await self.delegate.close()
        self._initialized = False
