"""V3 approval closure content-validation pure function.

Recomputes canonical content hashes and compares identity/policy/time
references across ProjectBindingContextV3, GovernedMetadataMetadataSnapshotV3
and ApprovalRecordV3. Success proves only that the three payloads are
internally consistent; it does NOT prove that the approval is real,
currently effective, backed by a repository, or sufficient for live
M3/M6 calls.
"""

from __future__ import annotations

from release_sql_bot.application.canonical import canonical_content_sha256
from release_sql_bot.domain.project_bindings_v3 import (
    ApprovalClosureValidationErrorV3,
    ApprovalRecordV3,
    GovernedMetadataSnapshotV3,
    ProjectBindingContextV3,
)


def validate_approval_closure_v3(
    project_context: ProjectBindingContextV3,
    metadata_snapshot: GovernedMetadataSnapshotV3,
    approval_record: ApprovalRecordV3,
) -> None:
    """Validate V3 approval closure content consistency (fail-fast).

    Args:
        project_context: V3 project binding context.
        metadata_snapshot: V3 governed metadata snapshot.
        approval_record: V3 approval record.

    Returns:
        None on success.

    Raises:
        ApprovalClosureValidationErrorV3: on the first failing check.
    """
    # 1. APPROVAL_ID_MISMATCH
    if project_context.approval_ref.approval_id != approval_record.approval_id:
        raise ApprovalClosureValidationErrorV3("APPROVAL_ID_MISMATCH")
    if metadata_snapshot.approval_ref.approval_id != approval_record.approval_id:
        raise ApprovalClosureValidationErrorV3("APPROVAL_ID_MISMATCH")

    # 2. APPROVAL_POLICY_MISMATCH
    if (
        project_context.approval_ref.policy_version != metadata_snapshot.approval_ref.policy_version
        or project_context.approval_ref.policy_version != approval_record.policy_version
        or project_context.approval_ref.policy_version
        != project_context.authorization_policy_version
    ):
        raise ApprovalClosureValidationErrorV3("APPROVAL_POLICY_MISMATCH")

    # 3. APPROVAL_TIME_MISMATCH
    if (
        project_context.approval_ref.approved_at != metadata_snapshot.approval_ref.approved_at
        or project_context.approval_ref.approved_at != approval_record.approved_at
    ):
        raise ApprovalClosureValidationErrorV3("APPROVAL_TIME_MISMATCH")

    # 4. APPROVAL_CONTEXT_REF_MISMATCH
    if (
        approval_record.context_ref.context_id != project_context.context_id
        or approval_record.context_ref.context_version != project_context.context_version
    ):
        raise ApprovalClosureValidationErrorV3("APPROVAL_CONTEXT_REF_MISMATCH")
    context_hash = canonical_content_sha256(project_context)
    if (
        context_hash != project_context.content_sha256
        or context_hash != approval_record.context_ref.sha256
    ):
        raise ApprovalClosureValidationErrorV3("APPROVAL_CONTEXT_REF_MISMATCH")

    # 5. APPROVAL_SNAPSHOT_REF_MISMATCH
    if (
        approval_record.snapshot_ref.snapshot_id != metadata_snapshot.snapshot_id
        or approval_record.snapshot_ref.snapshot_version != metadata_snapshot.snapshot_version
    ):
        raise ApprovalClosureValidationErrorV3("APPROVAL_SNAPSHOT_REF_MISMATCH")
    snapshot_hash = canonical_content_sha256(metadata_snapshot)
    if (
        snapshot_hash != metadata_snapshot.content_sha256
        or snapshot_hash != approval_record.snapshot_ref.sha256
    ):
        raise ApprovalClosureValidationErrorV3("APPROVAL_SNAPSHOT_REF_MISMATCH")

    # 6. APPROVAL_CONTENT_HASH_MISMATCH
    approval_hash = canonical_content_sha256(approval_record)
    if approval_hash != approval_record.content_sha256:
        raise ApprovalClosureValidationErrorV3("APPROVAL_CONTENT_HASH_MISMATCH")

    # 7. APPROVAL_CONTEXT_NOT_APPROVED
    if project_context.status != "approved":
        raise ApprovalClosureValidationErrorV3("APPROVAL_CONTEXT_NOT_APPROVED")

    # 8. APPROVAL_SNAPSHOT_NOT_APPROVED
    if metadata_snapshot.status != "approved":
        raise ApprovalClosureValidationErrorV3("APPROVAL_SNAPSHOT_NOT_APPROVED")

    # 9. APPROVAL_SNAPSHOT_BINDING_MISMATCH
    if (
        project_context.metadata_snapshot_ref.snapshot_id
        != approval_record.snapshot_ref.snapshot_id
        or project_context.metadata_snapshot_ref.snapshot_version
        != approval_record.snapshot_ref.snapshot_version
        or project_context.metadata_snapshot_ref.sha256 != approval_record.snapshot_ref.sha256
    ):
        raise ApprovalClosureValidationErrorV3("APPROVAL_SNAPSHOT_BINDING_MISMATCH")
