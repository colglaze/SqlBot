"""V3 SQL candidate generation service (M3 first delivery).

Within a single generation call and before any provider invocation, the
service performs the full pre-provider gate:

1. Re-validate the generation request on an independent copy.
2. Check the first-delivery scope (source, no aggregation/time/filter/join).
3. Re-run V3 intake from the real (injected) handoff repository by exact
   ruleVersion; select the unique requestId; compare batch/schema/payload
   hashes and full request content against the carried handoff closure
   and rule references. Caller-supplied ready/repositoryVerified flags are
   NOT accepted as proof.
4. Obtain the approval record through the controlled read-only approval
   port by exact approvalId; compare the full record and context/snapshot
   ID/version/hash. Source unavailable, missing record, mismatch, or
   inactive status all block.
5. Re-run resolve_metadata_v3; require metadataResolved; compare the
   canonical report against the carried report.

Any failure: provider call count is 0. No request trimming, no ignoring
of unsupported conditions, no V2 downgrade.

M3 first-delivery scope (explicit, narrow):
- factKind=source; aggregation.mode=none; timeRange.mode=none;
- filters.items empty; fields limited to factValue + entity keys;
- all required physical columns in ONE authorized relation; no JOIN;
- parameters limited to declared entity-key parameters.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_json_bytes,
    canonical_sha256,
)
from release_sql_bot.application.handoff_intake_v3 import (
    FactBindingHandoffBatchIntakeV3Error,
    FactBindingHandoffBatchNotFoundErrorV3,
    intake_fact_binding_handoffs_v3,
)
from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3
from release_sql_bot.application.ports.approval_records_v3 import (
    ApprovalRecordLookupError,
    ApprovalRecordPortV3,
)
from release_sql_bot.application.ports.candidates import (
    CandidateModelProvider,
    CandidateModelRequest,
    CandidateModelResponse,
    CandidateProviderRejectedError,
    CandidateProviderTransientError,
)
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffBatchDocumentInvalidV3Error,
    FactBindingHandoffBatchRepositoryV3,
    FactBindingHandoffBatchRepositoryV3UnavailableError,
)
from release_sql_bot.application.prompts_v3 import (
    SQLSERVER_CANDIDATE_MAX_TOKENS_V3,
    build_sqlserver_candidate_prompt_v3,
)
from release_sql_bot.domain.project_bindings_v3 import ApprovalRecordV3
from release_sql_bot.domain.sql_candidates_v3 import (
    CandidateContextRefV3,
    CandidateDeclaredRelationV3,
    CandidateFactRefV3,
    CandidateHandoffRefsV3,
    CandidateParameterV3,
    CandidateProjectRefV3,
    CandidateProvenanceV3,
    CandidateRequestRefV3,
    CandidateResolutionRefV3,
    CandidateResultV3,
    CandidateRuleRefV3,
    CandidateSnapshotRefV3,
    CandidateUsageEntryV3,
    GeneratedCandidatePayloadV3,
    GenerateSqlCandidateRequestV3,
    SqlTemplateCandidateV3,
)

MANDATORY_CANDIDATE_WARNING_V3 = "候选 SQL 未通过 AST、安全门禁、受限验证和人工审核，不得执行。"
_MAX_RETRY_DELAY_SECONDS = 5.0

RetrySleeper = Callable[[float], Awaitable[None]]


# ---------------------------------------------------------------------------
# Neutral error codes (M3 gate)
# ---------------------------------------------------------------------------

_M3_SCOPE_CODES: frozenset[str] = frozenset(
    {
        "M3_SCOPE_UNSUPPORTED_FACT_KIND",
        "M3_SCOPE_UNSUPPORTED_AGGREGATION",
        "M3_SCOPE_UNSUPPORTED_TIME_RANGE",
        "M3_SCOPE_UNSUPPORTED_FILTERS",
        "M3_SCOPE_UNSUPPORTED_FIELDS",
        "M3_SCOPE_MULTIPLE_RELATIONS",
        "M3_SCOPE_UNSUPPORTED_JOIN",
        "M3_SCOPE_UNSUPPORTED_PARAMETERS",
    }
)

_M3_GATE_CODES: frozenset[str] = frozenset(
    {
        "M3_REQUEST_STRUCTURE_INVALID",
        "M3_HANDOFF_REPOSITORY_UNAVAILABLE",
        "M3_HANDOFF_BATCH_NOT_FOUND",
        "M3_HANDOFF_BATCH_INVALID",
        "M3_HANDOFF_REQUEST_NOT_FOUND",
        "M3_HANDOFF_BATCH_HASH_MISMATCH",
        "M3_HANDOFF_PAYLOAD_HASH_MISMATCH",
        "M3_HANDOFF_SCHEMA_MISMATCH",
        "M3_HANDOFF_RULE_REF_MISMATCH",
        "M3_APPROVAL_PORT_UNAVAILABLE",
        "M3_APPROVAL_RECORD_NOT_FOUND",
        "M3_APPROVAL_RECORD_MISMATCH",
        "M3_APPROVAL_CONTEXT_MISMATCH",
        "M3_APPROVAL_SNAPSHOT_MISMATCH",
        "M3_RESOLUTION_BLOCKED",
        "M3_RESOLUTION_REPORT_MISMATCH",
    }
)


class CandidateScopeErrorV3(RuntimeError):
    """Neutral error when the request falls outside the M3 first-delivery scope."""

    def __init__(self, code: str) -> None:
        if code not in _M3_SCOPE_CODES:
            raise ValueError(f"unknown M3 scope code: {code}")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code


class CandidateGateErrorV3(RuntimeError):
    """Neutral error for M3 pre-provider gate failures."""

    def __init__(self, code: str) -> None:
        if code not in _M3_GATE_CODES:
            raise ValueError(f"unknown M3 gate code: {code}")
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code


class CandidateGenerationOutputInvalidV3Error(RuntimeError):
    """Model output failed cross-validation after bounded retries."""

    def __init__(self, attempts: int) -> None:
        super().__init__(f"V3 candidate output invalid after {attempts} attempts")
        self.attempts = attempts


class CandidateGenerationProviderUnavailableV3Error(RuntimeError):
    pass


class CandidateGenerationProviderRejectedV3Error(RuntimeError):
    pass


class _CandidateCrossReferenceV3Error(ValueError):
    pass


# ---------------------------------------------------------------------------
# Scope check (M3 first-delivery)
# ---------------------------------------------------------------------------


def _check_m3_scope(payload: GenerateSqlCandidateRequestV3) -> None:
    """Check whether the request fits the M3 first-delivery scope.

    Raises CandidateScopeErrorV3 with a neutral code when the request
    requires capabilities not yet implemented in this delivery slice.
    """
    request = payload.resolution_request.binding_request
    report = payload.resolution_report

    # factKind must be source
    if str(request.fact.fact_kind) != "source":
        raise CandidateScopeErrorV3("M3_SCOPE_UNSUPPORTED_FACT_KIND")

    # aggregation.mode must be none
    agg = request.query_requirements.aggregation
    if str(agg.mode) != "none":
        raise CandidateScopeErrorV3("M3_SCOPE_UNSUPPORTED_AGGREGATION")

    # timeRange.mode must be none
    tr = request.query_requirements.time_range
    if str(tr.mode) != "none":
        raise CandidateScopeErrorV3("M3_SCOPE_UNSUPPORTED_TIME_RANGE")

    # filters.items must be empty OR contain only strict entity-key eq filters.
    if request.query_requirements.filters.items:
        from release_sql_bot.application.filter_constraints_v3 import (
            qualified_filter_param_names,
        )

        qualified = qualified_filter_param_names(payload)
        if not qualified:
            raise CandidateScopeErrorV3("M3_SCOPE_UNSUPPORTED_FILTERS")

    # Fields: only factValue + entity keys allowed.
    # factValue is enforced by the consumer's own closure; here we only
    # check that every non-factValue field has role=entityKey AND is
    # referenced by an explicit entity-key authorization for the current
    # request.  We use the real (requestId, parameterName → fieldId)
    # authorization relationship — fieldId is NOT assumed to equal
    # parameterName, and a matching name does not exempt the role check.
    project_context = payload.resolution_request.project_context
    current_request_id = request.request_id
    authorized_entity_key_field_ids = {
        eka.field_id
        for eka in project_context.entity_key_authorizations
        if eka.request_id == current_request_id
    }
    for field in request.query_requirements.fields:
        if field.field_id == "factValue":
            continue
        if str(field.role) != "entityKey":
            raise CandidateScopeErrorV3("M3_SCOPE_UNSUPPORTED_FIELDS")
        if field.field_id not in authorized_entity_key_field_ids:
            raise CandidateScopeErrorV3("M3_SCOPE_UNSUPPORTED_FIELDS")

    # All physical columns must be in ONE authorized relation
    relations: set[tuple[str, str]] = set()
    for rf in report.resolved_fields:
        relations.add((rf.schema_name, rf.relation_name))
    for re in report.resolved_entity_keys:
        relations.add((re.schema_name, re.relation_name))
    if len(relations) > 1:
        raise CandidateScopeErrorV3("M3_SCOPE_MULTIPLE_RELATIONS")

    # No joins allowed
    if report.resolved_joins:
        raise CandidateScopeErrorV3("M3_SCOPE_UNSUPPORTED_JOIN")

    # Parameters: only declared entity-key parameters
    for param in request.fact.parameters:
        if str(param.role) != "entityKey":
            raise CandidateScopeErrorV3("M3_SCOPE_UNSUPPORTED_PARAMETERS")


# ---------------------------------------------------------------------------
# Pre-provider gate steps
# ---------------------------------------------------------------------------


def _revalidate_generation_request(
    payload: GenerateSqlCandidateRequestV3,
) -> GenerateSqlCandidateRequestV3:
    """Re-validate the generation request structure on an independent copy."""
    if not isinstance(payload, GenerateSqlCandidateRequestV3):
        raise CandidateGateErrorV3("M3_REQUEST_STRUCTURE_INVALID")
    try:
        wire = payload.model_dump(by_alias=True, mode="json", warnings="error")
        return GenerateSqlCandidateRequestV3.model_validate(wire)
    except Exception:
        raise CandidateGateErrorV3("M3_REQUEST_STRUCTURE_INVALID") from None


async def _verify_handoff_repository(
    payload: GenerateSqlCandidateRequestV3,
    repository: FactBindingHandoffBatchRepositoryV3,
) -> None:
    """Re-read batch from repository via V3 intake, compare against closure.

    Uses a single read through intake_fact_binding_handoffs_v3 (which calls
    get_batch_by_rule_version internally). Verifies batch hash, payload
    hash, schema identity, and rule reference consistency. The caller-
    supplied repositoryVerified flag is NOT accepted.
    """
    request = payload.resolution_request
    closure = request.handoff_closure
    rule_version = closure.rule_version
    request_id = closure.request_id

    # Re-run V3 intake (single repository read; validates schema, identity,
    # hashes, batch hash internally).
    # FactBindingHandoffBatchRepositoryV3UnavailableError propagates from
    # the repository through intake (not caught by intake's own handlers).
    try:
        intake_batch = await intake_fact_binding_handoffs_v3(repository, rule_version)
    except FactBindingHandoffBatchRepositoryV3UnavailableError:
        raise CandidateGateErrorV3("M3_HANDOFF_REPOSITORY_UNAVAILABLE") from None
    except FactBindingHandoffBatchNotFoundErrorV3:
        raise CandidateGateErrorV3("M3_HANDOFF_BATCH_NOT_FOUND") from None
    except FactBindingHandoffBatchIntakeV3Error:
        raise CandidateGateErrorV3("M3_HANDOFF_BATCH_INVALID") from None
    except FactBindingHandoffBatchDocumentInvalidV3Error:
        raise CandidateGateErrorV3("M3_HANDOFF_BATCH_INVALID") from None

    # Select the unique request by requestId
    matching = [r for r in intake_batch.requests if r.request_id == request_id]
    if len(matching) != 1:
        raise CandidateGateErrorV3("M3_HANDOFF_REQUEST_NOT_FOUND")
    intake_request = matching[0]

    # Compare batch hash
    if intake_batch.batch_sha256 != closure.batch_sha256:
        raise CandidateGateErrorV3("M3_HANDOFF_BATCH_HASH_MISMATCH")

    # Compare payload hash
    if intake_request.payload_sha256 != closure.payload_sha256:
        raise CandidateGateErrorV3("M3_HANDOFF_PAYLOAD_HASH_MISMATCH")

    # Compare schema identity
    if intake_batch.contract_schema_id != closure.contract_schema_id:
        raise CandidateGateErrorV3("M3_HANDOFF_SCHEMA_MISMATCH")
    if intake_batch.contract_schema_sha256 != closure.contract_schema_sha256:
        raise CandidateGateErrorV3("M3_HANDOFF_SCHEMA_MISMATCH")

    # Compare rule reference
    if intake_request.payload.rule_ref != request.binding_request.rule_ref:
        raise CandidateGateErrorV3("M3_HANDOFF_RULE_REF_MISMATCH")

    # Compare full request content (canonical JSON bytes)
    if canonical_json_bytes(intake_request.payload) != canonical_json_bytes(
        request.binding_request
    ):
        raise CandidateGateErrorV3("M3_HANDOFF_PAYLOAD_HASH_MISMATCH")


async def _verify_approval_record(
    payload: GenerateSqlCandidateRequestV3,
    approval_port: ApprovalRecordPortV3,
) -> ApprovalRecordV3:
    """Obtain and verify the approval record through the controlled port.

    Compares the full record content and context/snapshot ID/version/hash.
    Source unavailable, missing record, mismatch, or inactive status all block.
    """
    request = payload.resolution_request
    carried_record = request.approval_record
    approval_id = carried_record.approval_id

    # Obtain record from the controlled port
    try:
        live_record = await approval_port.get_by_approval_id(approval_id)
    except ApprovalRecordLookupError:
        raise CandidateGateErrorV3("M3_APPROVAL_PORT_UNAVAILABLE") from None

    if live_record is None:
        raise CandidateGateErrorV3("M3_APPROVAL_RECORD_NOT_FOUND")

    # Compare the full record content (canonical JSON)
    if canonical_json_bytes(live_record) != canonical_json_bytes(carried_record):
        raise CandidateGateErrorV3("M3_APPROVAL_RECORD_MISMATCH")

    # Verify context reference matches
    if (
        live_record.context_ref.context_id != request.project_context.context_id
        or live_record.context_ref.context_version != request.project_context.context_version
        or live_record.context_ref.sha256 != request.project_context.content_sha256
    ):
        raise CandidateGateErrorV3("M3_APPROVAL_CONTEXT_MISMATCH")

    # Verify snapshot reference matches
    if (
        live_record.snapshot_ref.snapshot_id != request.metadata_snapshot.snapshot_id
        or live_record.snapshot_ref.snapshot_version != request.metadata_snapshot.snapshot_version
        or live_record.snapshot_ref.sha256 != request.metadata_snapshot.content_sha256
    ):
        raise CandidateGateErrorV3("M3_APPROVAL_SNAPSHOT_MISMATCH")

    return live_record


def _verify_resolution(payload: GenerateSqlCandidateRequestV3) -> None:
    """Re-run resolve_metadata_v3 and compare against the carried report.

    Requires metadataResolved status and canonical report match.
    """
    recomputed = resolve_metadata_v3(payload.resolution_request)

    if recomputed.status != "metadataResolved":
        raise CandidateGateErrorV3("M3_RESOLUTION_BLOCKED")

    if canonical_sha256(recomputed) != canonical_sha256(payload.resolution_report):
        raise CandidateGateErrorV3("M3_RESOLUTION_REPORT_MISMATCH")


# ---------------------------------------------------------------------------
# Output parsing and cross-validation
# ---------------------------------------------------------------------------


def _parse_generated_payload_v3(content: str) -> GeneratedCandidatePayloadV3:
    """Parse and validate the model output against the V3 payload contract."""
    if not content or not content.strip():
        raise _CandidateCrossReferenceV3Error("empty model content")
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        raise _CandidateCrossReferenceV3Error("model content is not JSON") from exc
    if not isinstance(decoded, dict):
        raise _CandidateCrossReferenceV3Error("model content must be one JSON object")
    try:
        return GeneratedCandidatePayloadV3.model_validate_json(
            content,
            strict=True,
            by_alias=True,
            by_name=False,
        )
    except ValidationError as exc:
        raise _CandidateCrossReferenceV3Error("model candidate contract is invalid") from exc


def _validate_generated_cross_references(
    generated: GeneratedCandidatePayloadV3,
    payload: GenerateSqlCandidateRequestV3,
) -> None:
    """Cross-validate model declarations against authoritative input."""
    request = payload.resolution_request.binding_request
    report = payload.resolution_report

    # Parameters must match authoritative fact parameters
    expected_parameters = {
        item.name: (str(item.data_type), item.required, f"fact.parameters.{item.name}")
        for item in request.fact.parameters
    }
    actual_parameters = {
        item.name: (str(item.data_type), item.required, item.source)
        for item in generated.parameters
    }
    if actual_parameters != expected_parameters:
        raise _CandidateCrossReferenceV3Error(
            "candidate parameter declarations do not match the V3 fact"
        )

    # Result must match the request result contract
    expected_result = request.query_requirements.result
    result = generated.result
    if (
        str(result.column_name) != str(expected_result.column_name)
        or str(result.data_type) != str(expected_result.data_type)
        or str(result.cardinality) != str(expected_result.cardinality)
        or result.nullable is not expected_result.nullable
        or str(result.null_policy) != str(expected_result.null_policy)
        or result.unit != expected_result.unit
    ):
        raise _CandidateCrossReferenceV3Error(
            "candidate result declaration does not match the V3 result contract"
        )

    # Declared objects must match the resolved relation closure
    expected_relations = {(item.schema_name, item.relation_name) for item in report.resolved_fields}
    for item in report.resolved_entity_keys:
        expected_relations.add((item.schema_name, item.relation_name))
    actual_relations = {
        (item.schema_name, item.relation_name) for item in generated.declared_objects
    }
    if actual_relations != expected_relations:
        raise _CandidateCrossReferenceV3Error(
            "candidate declaredObjects do not match the resolved relation closure"
        )

    # Declared usage coverage: full six-tuples must match request usages
    # Exactly six fields: stage, ruleCode, priority, conditionId, conditionPath, outcome
    expected_usages = [
        (
            str(item.stage),
            item.rule_code,
            item.priority,
            item.condition_id,
            item.condition_path,
            str(item.outcome),
        )
        for item in request.usages
    ]
    actual_usages = [
        (
            str(item.stage),
            item.rule_code,
            item.priority,
            item.condition_id,
            item.condition_path,
            str(item.outcome),
        )
        for item in generated.declared_usage_coverage
    ]
    if actual_usages != expected_usages:
        raise _CandidateCrossReferenceV3Error(
            "candidate declaredUsageCoverage does not match V3 usages"
        )


# ---------------------------------------------------------------------------
# Candidate assembly
# ---------------------------------------------------------------------------


def _assemble_candidate_v3(
    generated: GeneratedCandidatePayloadV3,
    payload: GenerateSqlCandidateRequestV3,
    request: CandidateModelRequest,
    response: CandidateModelResponse,
    attempt_count: int,
) -> SqlTemplateCandidateV3:
    """Assemble the final SqlTemplateCandidateV3 from validated model output."""
    _validate_generated_cross_references(generated, payload)

    binding = payload.resolution_request.binding_request
    report = payload.resolution_report

    warnings = list(generated.warnings)
    if MANDATORY_CANDIDATE_WARNING_V3 not in warnings:
        warnings.append(MANDATORY_CANDIDATE_WARNING_V3)

    # Build authorized relations list (sorted)
    relations = {(item.schema_name, item.relation_name) for item in report.resolved_fields}
    for item in report.resolved_entity_keys:
        relations.add((item.schema_name, item.relation_name))
    declared_objects = tuple(
        CandidateDeclaredRelationV3(schema_name=schema, relation_name=relation)
        for schema, relation in sorted(relations)
    )

    # Build usage coverage tuples (original order from request)
    usage_coverage = tuple(
        CandidateUsageEntryV3(
            stage=item.stage,
            rule_code=item.rule_code,
            priority=item.priority,
            condition_id=item.condition_id,
            condition_path=item.condition_path,
            outcome=item.outcome,
        )
        for item in generated.declared_usage_coverage
    )

    # Build parameters (sorted by name)
    parameters = tuple(
        CandidateParameterV3(
            name=item.name,
            data_type=item.data_type,
            required=item.required,
            source=item.source,
        )
        for item in sorted(generated.parameters, key=lambda p: p.name)
    )

    candidate = SqlTemplateCandidateV3(
        template_code=generated.template_code,
        status="candidate",
        executable=False,
        review_status="pending",
        rule_ref=CandidateRuleRefV3(
            rule_set_id=binding.rule_ref.rule_set_id,
            rule_version=binding.rule_ref.rule_version,
            schema_version="3.0.0",
            source_sha256=binding.rule_ref.source_sha256,
            catalog_digest=binding.rule_ref.catalog_digest,
            candidate_payload_sha256=binding.rule_ref.candidate_payload_sha256,
        ),
        request_ref=CandidateRequestRefV3(
            request_id=binding.request_id,
            payload_sha256=report.handoff_refs.payload_sha256,
        ),
        project_ref=CandidateProjectRefV3(
            project_id=report.project_ref.project_id,
            project_version=report.project_ref.project_version,
        ),
        resolution_ref=CandidateResolutionRefV3(
            report_sha256=canonical_sha256(report),
            context_ref=CandidateContextRefV3(
                context_id=report.context_ref.context_id,
                context_version=report.context_ref.context_version,
                sha256=report.context_ref.sha256,
            ),
            metadata_snapshot_ref=CandidateSnapshotRefV3(
                snapshot_id=report.snapshot_ref.snapshot_id,
                snapshot_version=report.snapshot_ref.snapshot_version,
                sha256=report.snapshot_ref.sha256,
            ),
            authorization_policy_version=payload.resolution_request.project_context.authorization_policy_version,
        ),
        handoff_refs=CandidateHandoffRefsV3(
            batch_sha256=report.handoff_refs.batch_sha256,
            payload_sha256=report.handoff_refs.payload_sha256,
            contract_schema_id=report.handoff_refs.contract_schema_id,
            contract_schema_sha256=report.handoff_refs.contract_schema_sha256,
        ),
        generation_input_sha256=canonical_sha256(payload),
        fact_ref=CandidateFactRefV3(
            fact_code=binding.fact.fact_code,
            fact_kind=binding.fact.fact_kind,
            data_type=binding.fact.data_type,
            grain=binding.fact.grain,
        ),
        dialect="sqlserver",
        sql_template=generated.sql_template,
        parameters=parameters,
        result=CandidateResultV3(
            data_type=generated.result.data_type,
            nullable=generated.result.nullable,
            null_policy=generated.result.null_policy,
            unit=generated.result.unit,
        ),
        declared_objects=declared_objects,
        declared_usage_coverage=usage_coverage,
        usage_traceability_sha256=report.usage_traceability_sha256,
        assumptions=tuple(generated.assumptions),
        warnings=tuple(warnings),
        provenance=CandidateProvenanceV3(
            provider=response.provider,
            model=request.model,
            response_model=response.model,
            prompt_version=request.prompt_version,
            provider_request_id=response.request_id,
            system_fingerprint=response.system_fingerprint,
            attempt_count=attempt_count,
            max_tokens=request.max_tokens,
            response_format=request.response_format,
        ),
        content_sha256="0" * 64,
    )
    return candidate.model_copy(update={"content_sha256": canonical_content_sha256(candidate)})


# ---------------------------------------------------------------------------
# Bounded retry helper
# ---------------------------------------------------------------------------


async def _wait_before_retry_v3(
    *,
    failed_attempt: int,
    retry_base_delay_seconds: float,
    sleeper: RetrySleeper,
) -> None:
    delay = min(
        retry_base_delay_seconds * (2 ** (failed_attempt - 1)),
        _MAX_RETRY_DELAY_SECONDS,
    )
    await sleeper(delay)


# ---------------------------------------------------------------------------
# Public generation entry point
# ---------------------------------------------------------------------------


async def generate_sql_candidate_v3(
    *,
    provider: CandidateModelProvider,
    payload: GenerateSqlCandidateRequestV3,
    handoff_repository: FactBindingHandoffBatchRepositoryV3,
    approval_port: ApprovalRecordPortV3,
    model: str,
    max_retries: int,
    retry_base_delay_seconds: float = 0.25,
    sleeper: RetrySleeper = asyncio.sleep,
) -> SqlTemplateCandidateV3:
    """Generate one V3 SQL candidate with full pre-provider gate.

    All gate steps run before any provider call. Any gate failure results
    in provider call count of 0. No request trimming, no V2 downgrade.

    Args:
        provider: Bounded model provider port.
        payload: V3 generation request (resolution request + report).
        handoff_repository: Read-only V3 handoff batch repository.
        approval_port: Controlled read-only approval record port.
        model: Model identifier for the provider.
        max_retries: Maximum retry attempts (0-5).
        retry_base_delay_seconds: Base delay for exponential backoff.
        sleeper: Async sleep function for retry delays.

    Returns:
        SqlTemplateCandidateV3 with fixed status/audit fields.

    Raises:
        CandidateScopeErrorV3: request outside M3 first-delivery scope.
        CandidateGateErrorV3: any pre-provider gate failure.
        CandidateGenerationOutputInvalidV3Error: output invalid after retries.
        CandidateGenerationProviderUnavailableV3Error: provider exhausted.
        CandidateGenerationProviderRejectedV3Error: provider rejected.
    """
    if not 0 <= max_retries <= 5:
        raise ValueError("max_retries must be between 0 and 5")
    if retry_base_delay_seconds < 0:
        raise ValueError("retry_base_delay_seconds cannot be negative")

    # 1. Re-validate generation request on independent copy
    verified = _revalidate_generation_request(payload)

    # 2. Check M3 first-delivery scope
    _check_m3_scope(verified)

    # 3. Repository handoff verification (re-read, re-intake, compare)
    await _verify_handoff_repository(verified, handoff_repository)

    # 4. Approval record verification through controlled port
    await _verify_approval_record(verified, approval_port)

    # 5. Re-run M2 resolution and compare against carried report
    _verify_resolution(verified)

    # --- All gates passed; now call the provider ---
    prompt = build_sqlserver_candidate_prompt_v3(verified)
    model_request = CandidateModelRequest(
        model=model,
        prompt_version=prompt.version,
        system_prompt=prompt.system,
        user_prompt=prompt.user,
        response_format="json_object",
        max_tokens=SQLSERVER_CANDIDATE_MAX_TOKENS_V3,
    )
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        try:
            response = await provider.generate(model_request)
        except CandidateProviderRejectedError:
            raise CandidateGenerationProviderRejectedV3Error(
                "V3 candidate provider rejected the request"
            ) from None
        except CandidateProviderTransientError:
            if attempt == total_attempts:
                raise CandidateGenerationProviderUnavailableV3Error(
                    "V3 candidate provider retries were exhausted"
                ) from None
            await _wait_before_retry_v3(
                failed_attempt=attempt,
                retry_base_delay_seconds=retry_base_delay_seconds,
                sleeper=sleeper,
            )
            continue

        try:
            generated = _parse_generated_payload_v3(response.content)
            return _assemble_candidate_v3(
                generated,
                verified,
                model_request,
                response,
                attempt,
            )
        except (_CandidateCrossReferenceV3Error, ValidationError):
            if attempt == total_attempts:
                raise CandidateGenerationOutputInvalidV3Error(attempts=attempt) from None
            await _wait_before_retry_v3(
                failed_attempt=attempt,
                retry_base_delay_seconds=retry_base_delay_seconds,
                sleeper=sleeper,
            )

    raise AssertionError("bounded V3 candidate generation loop exited unexpectedly")
