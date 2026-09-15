"""Tests for the repository-backed V3 generation preparation slice."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from release_sql_bot.application.generation_preparation_v3 import (
    prepare_generation_request_v3,
)
from release_sql_bot.application.ports.approval_records_v3 import (
    ApprovalRecordLookupError,
    InMemoryApprovalRecordPortV3,
)
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffBatchRepositoryV3,
    FactBindingHandoffBatchRepositoryV3UnavailableError,
)
from release_sql_bot.domain.generation_preparation_v3 import (
    GenerationPreparationErrorV3,
    PrepareGenerationRequestV3,
)
from release_sql_bot.domain.project_bindings_v3 import (
    BindingResolutionReportV3,
    ResolveMetadataRequestV3,
)
from tests.unit.test_candidates_v3 import (
    _build_synthetic_approval_port,
    _build_synthetic_handoff_batch,
)
from tests.v3_metadata_support import (
    valid_blocked_report_v3_wire,
    valid_resolve_metadata_request_v3_wire,
)


class _InMemoryHandoffRepository(FactBindingHandoffBatchRepositoryV3):
    def __init__(self, batch: Any | None) -> None:
        self.batch = batch
        self.calls: list[str] = []

    async def get_batch_by_rule_version(self, rule_version: str):
        self.calls.append(rule_version)
        if self.batch is None or self.batch.rule_version != rule_version:
            return None
        return self.batch


class _UnavailableHandoffRepository(FactBindingHandoffBatchRepositoryV3):
    async def get_batch_by_rule_version(self, rule_version: str):
        del rule_version
        raise FactBindingHandoffBatchRepositoryV3UnavailableError("MARKER_REPOSITORY")


class _FailingApprovalPort(InMemoryApprovalRecordPortV3):
    async def get_by_approval_id(self, approval_id: str):
        del approval_id
        raise ApprovalRecordLookupError("MARKER_APPROVAL")


class _CountingApprovalPort(InMemoryApprovalRecordPortV3):
    def __init__(self, records: dict[str, Any]) -> None:
        super().__init__(records=records)
        self.calls: list[str] = []

    async def get_by_approval_id(self, approval_id: str):
        self.calls.append(approval_id)
        return await super().get_by_approval_id(approval_id)


def _valid_prepare_wire() -> dict[str, Any]:
    wire = valid_resolve_metadata_request_v3_wire()
    closure = wire["handoffClosure"]
    return {
        "schemaVersion": "1.0.0",
        "ruleVersion": closure["ruleVersion"],
        "requestId": closure["requestId"],
        "projectContext": deepcopy(wire["projectContext"]),
        "metadataSnapshot": deepcopy(wire["metadataSnapshot"]),
        "approvalRecord": deepcopy(wire["approvalRecord"]),
    }


def _valid_prepare_request() -> PrepareGenerationRequestV3:
    return PrepareGenerationRequestV3.model_validate(_valid_prepare_wire())


def _prepare(
    request: PrepareGenerationRequestV3 | Any,
    *,
    handoff_repository: FactBindingHandoffBatchRepositoryV3 | None = None,
    approval_port: Any | None = None,
) -> ResolveMetadataRequestV3:
    return asyncio.run(
        prepare_generation_request_v3(
            request=request,
            handoff_repository=(
                handoff_repository
                if handoff_repository is not None
                else _InMemoryHandoffRepository(_build_synthetic_handoff_batch())
            ),
            approval_port=(
                approval_port if approval_port is not None else _build_synthetic_approval_port()
            ),
        )
    )


def test_contract_contains_only_exact_identity_and_approved_material() -> None:
    request = _valid_prepare_request()
    dumped = request.model_dump(by_alias=True, mode="json")

    assert set(dumped) == {
        "schemaVersion",
        "ruleVersion",
        "requestId",
        "projectContext",
        "metadataSnapshot",
        "approvalRecord",
    }
    assert "latest" not in str(dumped).lower()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda wire: wire.update({"ruleId": "latest"}),
        lambda wire: wire.update({"repositoryVerified": True}),
        lambda wire: wire.update({"bindingRequest": {}}),
        lambda wire: wire.update({"rule_version": wire.pop("ruleVersion")}),
    ],
)
def test_contract_rejects_latest_payload_and_repository_claims(mutation) -> None:
    wire = _valid_prepare_wire()
    mutation(wire)
    with pytest.raises(ValidationError):
        PrepareGenerationRequestV3.model_validate(wire)


def test_contract_rejects_nested_snake_case() -> None:
    wire = _valid_prepare_wire()
    wire["projectContext"]["project_ref"] = wire["projectContext"].pop("projectRef")
    with pytest.raises(ValidationError, match="snake_case"):
        PrepareGenerationRequestV3.model_validate(wire)


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("ruleVersion", "latest"),
        ("ruleVersion", "LATEST"),
        ("ruleVersion", "  latest  "),
        ("requestId", "latest"),
        ("requestId", " Latest "),
    ],
)
def test_contract_rejects_latest_identity_selectors(field_name: str, value: str) -> None:
    wire = _valid_prepare_wire()
    wire[field_name] = value

    with pytest.raises(ValidationError):
        PrepareGenerationRequestV3.model_validate(wire)


def test_happy_path_derives_closure_and_payload_from_intake() -> None:
    request = _valid_prepare_request()
    repository = _InMemoryHandoffRepository(_build_synthetic_handoff_batch())
    approval_port = _CountingApprovalPort(
        {request.approval_record.approval_id: request.approval_record}
    )

    result = _prepare(
        request,
        handoff_repository=repository,
        approval_port=approval_port,
    )

    assert isinstance(result, ResolveMetadataRequestV3)
    assert result.project_ref == request.project_context.project_ref
    assert result.binding_request.request_id == request.request_id
    assert result.handoff_closure.request_id == request.request_id
    assert result.handoff_closure.payload == result.binding_request
    assert result.handoff_closure.payload_sha256
    assert result.handoff_closure.batch_sha256 == _build_synthetic_handoff_batch().batch_sha256
    assert repository.calls == [request.rule_version]
    assert approval_port.calls == [request.approval_record.approval_id]


def test_handoff_is_read_by_exact_rule_version_and_request_id() -> None:
    request = _valid_prepare_request()
    repository = _InMemoryHandoffRepository(_build_synthetic_handoff_batch())
    mutated = request.model_copy(update={"rule_version": "OTHER@VERSION"})

    with pytest.raises(GenerationPreparationErrorV3) as error:
        _prepare(mutated, handoff_repository=repository)

    assert error.value.code == "PREPARE_HANDOFF_BATCH_NOT_FOUND"
    assert repository.calls == ["OTHER@VERSION"]

    mismatched = request.model_copy(update={"request_id": "missing-request"})
    with pytest.raises(GenerationPreparationErrorV3) as error:
        _prepare(mismatched)
    assert error.value.code == "PREPARE_HANDOFF_REQUEST_NOT_FOUND"


def test_missing_or_unavailable_batch_is_neutral_and_approval_is_not_called() -> None:
    request = _valid_prepare_request()
    approval_port = _CountingApprovalPort(
        {request.approval_record.approval_id: request.approval_record}
    )

    with pytest.raises(GenerationPreparationErrorV3) as missing:
        _prepare(
            request,
            handoff_repository=_InMemoryHandoffRepository(None),
            approval_port=approval_port,
        )
    assert missing.value.code == "PREPARE_HANDOFF_BATCH_NOT_FOUND"
    assert approval_port.calls == []

    with pytest.raises(GenerationPreparationErrorV3) as unavailable:
        _prepare(
            request,
            handoff_repository=_UnavailableHandoffRepository(),
            approval_port=approval_port,
        )
    assert unavailable.value.code == "PREPARE_HANDOFF_REPOSITORY_UNAVAILABLE"
    assert approval_port.calls == []


def test_missing_inactive_and_mismatched_approval_are_blocked_before_m2() -> None:
    request = _valid_prepare_request()
    repository = _InMemoryHandoffRepository(_build_synthetic_handoff_batch())

    with pytest.raises(GenerationPreparationErrorV3) as missing:
        _prepare(
            request,
            handoff_repository=repository,
            approval_port=InMemoryApprovalRecordPortV3(),
        )
    assert missing.value.code == "PREPARE_APPROVAL_RECORD_NOT_FOUND"

    inactive = InMemoryApprovalRecordPortV3(
        records={request.approval_record.approval_id: request.approval_record},
        inactive_ids=frozenset({request.approval_record.approval_id}),
    )
    with pytest.raises(GenerationPreparationErrorV3) as inactive_error:
        _prepare(request, handoff_repository=repository, approval_port=inactive)
    assert inactive_error.value.code == "PREPARE_APPROVAL_RECORD_NOT_FOUND"

    altered = request.approval_record.model_copy(update={"actor_ref": "other-actor"})
    mismatched = InMemoryApprovalRecordPortV3(
        records={request.approval_record.approval_id: altered}
    )
    with pytest.raises(GenerationPreparationErrorV3) as mismatch:
        _prepare(request, handoff_repository=repository, approval_port=mismatched)
    assert mismatch.value.code == "PREPARE_APPROVAL_RECORD_MISMATCH"


def test_approval_lookup_failure_does_not_leak_adapter_text() -> None:
    request = _valid_prepare_request()
    with pytest.raises(GenerationPreparationErrorV3) as error:
        _prepare(
            request,
            handoff_repository=_InMemoryHandoffRepository(_build_synthetic_handoff_batch()),
            approval_port=_FailingApprovalPort(),
        )

    assert error.value.code == "PREPARE_APPROVAL_PORT_UNAVAILABLE"
    assert "MARKER_APPROVAL" not in str(error.value)
    assert "MARKER_APPROVAL" not in repr(error.value)


def test_m2_blocker_is_returned_as_fixed_code_without_input_content(monkeypatch) -> None:
    request = _valid_prepare_request()
    blocked = BindingResolutionReportV3.model_validate(valid_blocked_report_v3_wire())

    def blocked_resolution(_request):
        return blocked

    monkeypatch.setattr(
        "release_sql_bot.application.generation_preparation_v3.resolve_metadata_v3",
        blocked_resolution,
    )

    with pytest.raises(GenerationPreparationErrorV3) as error:
        _prepare(request)

    assert error.value.code == "PREPARE_M2_BLOCKED"
    assert error.value.issue_codes == tuple(issue.code for issue in blocked.issues)
    assert request.request_id not in str(error.value)
    assert request.approval_record.approval_id not in str(error.value)


def test_m2_unexpected_failure_maps_to_neutral_structure_error(monkeypatch) -> None:
    request = _valid_prepare_request()

    def broken_resolution(_request):
        raise RuntimeError("MARKER_M2_INTERNAL")

    monkeypatch.setattr(
        "release_sql_bot.application.generation_preparation_v3.resolve_metadata_v3",
        broken_resolution,
    )

    with pytest.raises(GenerationPreparationErrorV3) as error:
        _prepare(request)

    assert error.value.code == "PREPARE_M2_STRUCTURE_INVALID"
    assert "MARKER_M2_INTERNAL" not in str(error.value)
    assert "MARKER_M2_INTERNAL" not in repr(error.value)


def test_non_model_input_is_rejected_before_repository_access() -> None:
    repository = _InMemoryHandoffRepository(_build_synthetic_handoff_batch())
    with pytest.raises(GenerationPreparationErrorV3) as error:
        _prepare(
            {"schemaVersion": "1.0.0"},
            handoff_repository=repository,
        )

    assert error.value.code == "PREPARE_REQUEST_STRUCTURE_INVALID"
    assert repository.calls == []
