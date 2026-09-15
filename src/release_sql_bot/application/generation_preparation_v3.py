"""Repository-backed preparation of a V3 metadata-resolution request.

This service is the first half of the MongoDB generation entry.  It accepts a
strict package containing only an exact rule/request identity and the approved
metadataReview materials.  It reads and intakes the immutable V3 handoff,
derives the handoff closure from that trusted result, checks the currently
active approval through the read-only approval port, and finally runs the
existing pure M2 resolver.

The returned request is intentionally not a repository attestation.  The
candidate-generation service must re-read the handoff and approval again in
its own call before invoking a provider or writing a candidate.
"""

from __future__ import annotations

from typing import Any

from release_sql_bot.application.canonical import canonical_json_bytes
from release_sql_bot.application.handoff_intake_v3 import (
    FactBindingHandoffBatchInvalidErrorV3,
    FactBindingHandoffBatchNotFoundErrorV3,
    intake_fact_binding_handoffs_v3,
)
from release_sql_bot.application.metadata_resolution_v3 import (
    MetadataResolutionStructureErrorV3,
    resolve_metadata_v3,
)
from release_sql_bot.application.ports.approval_records_v3 import (
    ApprovalRecordLookupError,
    ApprovalRecordPortV3,
)
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffBatchDocumentInvalidV3Error,
    FactBindingHandoffBatchRepositoryV3,
    FactBindingHandoffBatchRepositoryV3UnavailableError,
)
from release_sql_bot.domain.fact_binding_handoffs_v3 import (
    FactBindingHandoffIntakeBatchV3,
    FactBindingHandoffIntakeRequestV3,
)
from release_sql_bot.domain.generation_preparation_v3 import (
    GenerationPreparationErrorV3,
    PrepareGenerationRequestV3,
)
from release_sql_bot.domain.handoff_closure_v3 import HandoffClosureV3
from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3


def _revalidate_prepare_request(value: Any) -> PrepareGenerationRequestV3:
    """Rebuild an independent strict copy of the preparation input."""

    if not isinstance(value, PrepareGenerationRequestV3):
        raise GenerationPreparationErrorV3("PREPARE_REQUEST_STRUCTURE_INVALID")
    try:
        wire = value.model_dump(by_alias=True, mode="json", warnings="error")
        return PrepareGenerationRequestV3.model_validate(wire)
    except Exception:  # noqa: BLE001 - expose only the stable code
        raise GenerationPreparationErrorV3("PREPARE_REQUEST_STRUCTURE_INVALID") from None


def _map_handoff_error(error: Exception) -> GenerationPreparationErrorV3:
    """Map intake/repository failures to neutral preparation codes."""

    if isinstance(error, FactBindingHandoffBatchRepositoryV3UnavailableError):
        return GenerationPreparationErrorV3("PREPARE_HANDOFF_REPOSITORY_UNAVAILABLE")
    if isinstance(error, FactBindingHandoffBatchNotFoundErrorV3):
        return GenerationPreparationErrorV3("PREPARE_HANDOFF_BATCH_NOT_FOUND")
    if isinstance(
        error,
        (
            FactBindingHandoffBatchInvalidErrorV3,
            FactBindingHandoffBatchDocumentInvalidV3Error,
        ),
    ):
        return GenerationPreparationErrorV3("PREPARE_HANDOFF_BATCH_INVALID")
    return GenerationPreparationErrorV3("PREPARE_HANDOFF_BATCH_INVALID")


def _build_handoff_closure(
    *,
    intake_batch: FactBindingHandoffIntakeBatchV3,
    intake_request: FactBindingHandoffIntakeRequestV3,
) -> HandoffClosureV3:
    """Build the wire closure solely from the validated intake result."""

    return HandoffClosureV3.model_validate(
        {
            "schemaVersion": "1.0.0",
            "ruleVersion": intake_batch.rule_version,
            "requestId": intake_request.request_id,
            "factCode": intake_request.fact_code,
            "payloadSha256": intake_request.payload_sha256,
            "batchSha256": intake_batch.batch_sha256,
            "contractSchemaId": intake_batch.contract_schema_id,
            "contractSchemaSha256": intake_batch.contract_schema_sha256,
            "intakeStatus": intake_batch.status,
            "payload": intake_request.payload.model_dump(by_alias=True, mode="json"),
        }
    )


def _select_intake_request(
    *,
    intake_batch: FactBindingHandoffIntakeBatchV3,
    request_id: str,
) -> FactBindingHandoffIntakeRequestV3:
    """Select exactly one request from an already validated intake batch."""

    matching = [item for item in intake_batch.requests if item.request_id == request_id]
    if len(matching) != 1:
        raise GenerationPreparationErrorV3("PREPARE_HANDOFF_REQUEST_NOT_FOUND")
    return matching[0]


async def _verify_active_approval(
    *,
    request: PrepareGenerationRequestV3,
    approval_port: ApprovalRecordPortV3,
) -> None:
    """Require an active, exact canonical copy of the carried approval."""

    approval_id = request.approval_record.approval_id
    try:
        live_record = await approval_port.get_by_approval_id(approval_id)
    except ApprovalRecordLookupError:
        raise GenerationPreparationErrorV3("PREPARE_APPROVAL_PORT_UNAVAILABLE") from None
    except Exception:  # noqa: BLE001 - do not expose adapter/provider details
        raise GenerationPreparationErrorV3("PREPARE_APPROVAL_PORT_UNAVAILABLE") from None

    if live_record is None:
        # The port contract returns None for missing, revoked, superseded, or
        # otherwise non-active approval records.
        raise GenerationPreparationErrorV3("PREPARE_APPROVAL_RECORD_NOT_FOUND")

    # Do not accept a caller-controlled mapping or a structurally different
    # object as approval truth, even if its JSON happens to compare equal.
    from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3

    if not isinstance(live_record, ApprovalRecordV3):
        raise GenerationPreparationErrorV3("PREPARE_APPROVAL_RECORD_MISMATCH")
    if canonical_json_bytes(live_record) != canonical_json_bytes(request.approval_record):
        raise GenerationPreparationErrorV3("PREPARE_APPROVAL_RECORD_MISMATCH")


async def prepare_generation_request_v3(
    *,
    request: PrepareGenerationRequestV3,
    handoff_repository: FactBindingHandoffBatchRepositoryV3,
    approval_port: ApprovalRecordPortV3,
) -> ResolveMetadataRequestV3:
    """Prepare a V3 ``ResolveMetadataRequestV3`` from exact repository input.

    The service has no provider, candidate-store, SQL Server, or persistence
    dependency.  It never writes data.  The returned request is a fresh
    serializable request that the M3 generation service will revalidate and
    re-attest in the later generation call.

    Raises:
        GenerationPreparationErrorV3: for any structural, repository,
            approval, or M2 blocker.  The exception carries stable codes only.
    """

    verified_input = _revalidate_prepare_request(request)
    if handoff_repository is None:
        raise GenerationPreparationErrorV3("PREPARE_HANDOFF_REPOSITORY_UNAVAILABLE")
    if approval_port is None:
        raise GenerationPreparationErrorV3("PREPARE_APPROVAL_PORT_UNAVAILABLE")

    # Read and validate the exact immutable batch.  No caller payload or hash
    # participates in constructing the handoff closure.
    try:
        intake_batch = await intake_fact_binding_handoffs_v3(
            handoff_repository,
            verified_input.rule_version,
        )
    except Exception as error:  # noqa: BLE001 - map all repository failures
        raise _map_handoff_error(error) from None

    intake_request = _select_intake_request(
        intake_batch=intake_batch,
        request_id=verified_input.request_id,
    )
    try:
        handoff_closure = _build_handoff_closure(
            intake_batch=intake_batch,
            intake_request=intake_request,
        )
    except Exception:  # noqa: BLE001 - intake result must never leak details
        raise GenerationPreparationErrorV3("PREPARE_HANDOFF_BATCH_INVALID") from None

    # Verify the current active pointer before running M2.  M2 then checks the
    # context/snapshot/approval content closure and statuses deterministically.
    await _verify_active_approval(request=verified_input, approval_port=approval_port)

    try:
        resolution_request = ResolveMetadataRequestV3.model_validate(
            {
                "schemaVersion": "1.0.0",
                "projectRef": verified_input.project_context.project_ref.model_dump(
                    by_alias=True,
                    mode="json",
                ),
                "handoffClosure": handoff_closure.model_dump(by_alias=True, mode="json"),
                "bindingRequest": intake_request.payload.model_dump(
                    by_alias=True,
                    mode="json",
                ),
                "projectContext": verified_input.project_context.model_dump(
                    by_alias=True,
                    mode="json",
                ),
                "metadataSnapshot": verified_input.metadata_snapshot.model_dump(
                    by_alias=True,
                    mode="json",
                ),
                "approvalRecord": verified_input.approval_record.model_dump(
                    by_alias=True,
                    mode="json",
                ),
            }
        )
    except Exception:  # noqa: BLE001 - expose only the stable code
        raise GenerationPreparationErrorV3("PREPARE_REQUEST_STRUCTURE_INVALID") from None

    try:
        report = resolve_metadata_v3(resolution_request)
    except MetadataResolutionStructureErrorV3:
        raise GenerationPreparationErrorV3("PREPARE_M2_STRUCTURE_INVALID") from None
    except Exception:  # noqa: BLE001 - M2 failure is fail-closed and neutral
        raise GenerationPreparationErrorV3("PREPARE_M2_STRUCTURE_INVALID") from None

    if report.status != "metadataResolved":
        issue_codes = tuple(issue.code for issue in report.issues)
        raise GenerationPreparationErrorV3(
            "PREPARE_M2_BLOCKED",
            issue_codes=issue_codes or ("PREPARE_M2_BLOCKED",),
        )
    return resolution_request


__all__ = [
    "GenerationPreparationErrorV3",
    "PrepareGenerationRequestV3",
    "prepare_generation_request_v3",
]
