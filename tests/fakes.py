from __future__ import annotations

from collections.abc import Iterable

from release_sql_bot.application.ports.candidates import (
    CandidateModelRequest,
    CandidateModelResponse,
)
from release_sql_bot.application.ports.sql_ast import (
    SqlInspectionRequest,
    SqlInspectionResult,
)
from release_sql_bot.domain.sql_validation import SqlParserRefV2


class FixedCandidateModelProvider:
    def __init__(
        self,
        responses: Iterable[CandidateModelResponse | Exception],
    ) -> None:
        self._responses = iter(responses)
        self.calls: list[CandidateModelRequest] = []

    async def generate(self, request: CandidateModelRequest) -> CandidateModelResponse:
        self.calls.append(request)
        try:
            response = next(self._responses)
        except StopIteration:
            raise AssertionError(
                "Fixed candidate provider response sequence was exhausted"
            ) from None
        if isinstance(response, Exception):
            raise response
        return response


class FixedSqlDialectInspector:
    def __init__(self, responses: Iterable[SqlInspectionResult | Exception]) -> None:
        self._responses = iter(responses)
        self.calls: list[SqlInspectionRequest] = []

    @property
    def parser_ref(self) -> SqlParserRefV2:
        return SqlParserRefV2(
            name="sqlglot",
            exact_version="30.17.0-test-double",
            dialect="tsql",
            gate_version="sqlserver-ast-safety-v1",
        )

    def inspect(self, request: SqlInspectionRequest) -> SqlInspectionResult:
        self.calls.append(request)
        try:
            response = next(self._responses)
        except StopIteration:
            raise AssertionError("Fixed SQL inspector response sequence was exhausted") from None
        if isinstance(response, Exception):
            raise response
        return response
