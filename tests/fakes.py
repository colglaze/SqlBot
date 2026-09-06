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
from release_sql_bot.application.ports.sqlserver_validation import (
    CatalogFactsRequest,
    CatalogFactsResult,
    DescribeRequest,
    DescribeResult,
    PermissionAttestationRequest,
    PermissionAttestationResult,
    SessionLimits,
    SessionPolicy,
    SqlServerValidationSession,
    TargetAttestationExpectation,
    TargetIdentityEvidence,
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


class FixedSqlServerValidationSession(SqlServerValidationSession):
    """Scripted session double that records every port call."""

    def __init__(
        self,
        *,
        fingerprint=None,
        identity: TargetIdentityEvidence | None = None,
        permissions: PermissionAttestationResult | None = None,
        facts: CatalogFactsResult | None = None,
        describe: DescribeResult | None = None,
        fail_on: dict[str, Exception] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.policies: list[SessionPolicy] = []
        self.expectations: list[TargetAttestationExpectation] = []
        self.permission_requests: list[PermissionAttestationRequest] = []
        self.catalog_requests: list[CatalogFactsRequest] = []
        self.describe_requests: list[DescribeRequest] = []
        self.rollback_count = 0
        self.close_count = 0
        self._fingerprint = fingerprint or (lambda value: "fp:" + value)
        self._identity = identity
        self._permissions = permissions
        self._facts = facts
        self._describe = describe
        self._fail_on = fail_on or {}

    def _maybe_fail(self, stage: str) -> None:
        failure = self._fail_on.get(stage)
        if failure is not None:
            raise failure

    def apply_session_policy(self, policy: SessionPolicy) -> None:
        self.calls.append("apply_session_policy")
        self.policies.append(policy)
        self._maybe_fail("apply_session_policy")

    def attest_target(
        self,
        expectation: TargetAttestationExpectation,
    ) -> TargetIdentityEvidence:
        self.calls.append("attest_target")
        self.expectations.append(expectation)
        self._maybe_fail("attest_target")
        assert self._identity is not None
        return self._identity

    def attest_permissions(
        self,
        request: PermissionAttestationRequest,
    ) -> PermissionAttestationResult:
        self.calls.append("attest_permissions")
        self.permission_requests.append(request)
        self._maybe_fail("attest_permissions")
        assert self._permissions is not None
        return self._permissions

    def read_catalog_facts(self, request: CatalogFactsRequest) -> CatalogFactsResult:
        self.calls.append("read_catalog_facts")
        self.catalog_requests.append(request)
        self._maybe_fail("read_catalog_facts")
        assert self._facts is not None
        return self._facts

    def describe_first_result_set(self, request: DescribeRequest) -> DescribeResult:
        self.calls.append("describe_first_result_set")
        self.describe_requests.append(request)
        self._maybe_fail("describe_first_result_set")
        assert self._describe is not None
        return self._describe

    def rollback_safely(self) -> None:
        self.calls.append("rollback_safely")
        self.rollback_count += 1

    def close(self) -> None:
        self.calls.append("close")
        self.close_count += 1


class FixedSqlServerValidator:
    """Port double recording open_session calls and handing out scripted sessions."""

    def __init__(self, session: FixedSqlServerValidationSession) -> None:
        self._session = session
        self.limits: list[SessionLimits] = []
        self.open_count = 0
        self.open_failure: Exception | None = None

    def open_session(self, limits: SessionLimits) -> FixedSqlServerValidationSession:
        self.open_count += 1
        self.limits.append(limits)
        if self.open_failure is not None:
            raise self.open_failure
        return self._session
