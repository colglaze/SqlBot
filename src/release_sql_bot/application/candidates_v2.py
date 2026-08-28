"""Independent V2 SQL candidate generation over a recomputed Phase 2G closure."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.metadata_resolution_v2 import resolve_metadata_v2
from release_sql_bot.application.ports.candidates import (
    CandidateModelProvider,
    CandidateModelRequest,
    CandidateModelResponse,
    CandidateProviderRejectedError,
    CandidateProviderTransientError,
)
from release_sql_bot.application.prompts_v2 import (
    SQLSERVER_CANDIDATE_MAX_TOKENS_V2,
    build_sqlserver_candidate_prompt_v2,
)
from release_sql_bot.domain.fact_bindings_v2 import UncertaintyImpactV2
from release_sql_bot.domain.project_bindings_v2 import BindingResolutionReportV2
from release_sql_bot.domain.sql_candidates_v2 import (
    CandidateContextRefV2,
    CandidateDeclaredRelationV2,
    CandidateFactRefV2,
    CandidateParameterV2,
    CandidateProjectRefV2,
    CandidateProvenanceV2,
    CandidateRequestRefV2,
    CandidateResolutionRefV2,
    CandidateResultV2,
    CandidateRuleRefV2,
    CandidateSnapshotRefV2,
    GeneratedCandidatePayloadV2,
    GenerateSqlCandidateRequestV2,
    SqlTemplateCandidateV2,
)

MANDATORY_CANDIDATE_WARNING_V2 = "候选 SQL 未通过 AST、安全门禁、受限验证和人工审核，不得执行。"
_MAX_RETRY_DELAY_SECONDS = 5.0

RetrySleeper = Callable[[float], Awaitable[None]]


class CandidateInputNotReadyV2Error(RuntimeError):
    def __init__(self, resolution: BindingResolutionReportV2) -> None:
        super().__init__("V2 candidate input is not an exact metadataResolved closure")
        self.resolution = resolution


class CandidateGenerationOutputInvalidV2Error(RuntimeError):
    pass


class CandidateGenerationProviderUnavailableV2Error(RuntimeError):
    pass


class CandidateGenerationProviderRejectedV2Error(RuntimeError):
    pass


class _CandidateCrossReferenceV2Error(ValueError):
    pass


def _validated_resolution(
    payload: GenerateSqlCandidateRequestV2,
) -> BindingResolutionReportV2:
    recomputed = resolve_metadata_v2(payload.resolution_request)
    report_matches = canonical_sha256(recomputed) == canonical_sha256(payload.resolution_report)
    has_blocking_uncertainty = any(
        item.impact is UncertaintyImpactV2.BLOCKING
        for item in payload.resolution_request.binding_request.uncertainties
    )
    if (
        not report_matches
        or recomputed.status != "metadataResolved"
        or recomputed.executable is not False
        or recomputed.blocking_issues
        or recomputed.result_source is None
        or has_blocking_uncertainty
    ):
        raise CandidateInputNotReadyV2Error(recomputed)
    return recomputed


def _parse_generated_payload_v2(content: str) -> GeneratedCandidatePayloadV2:
    if not content.strip():
        raise _CandidateCrossReferenceV2Error("empty model content")
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        raise _CandidateCrossReferenceV2Error("model content is not JSON") from exc
    if not isinstance(decoded, dict):
        raise _CandidateCrossReferenceV2Error("model content must be one JSON object")
    try:
        return GeneratedCandidatePayloadV2.model_validate_json(
            content,
            strict=True,
            by_alias=True,
            by_name=False,
        )
    except ValidationError as exc:
        raise _CandidateCrossReferenceV2Error("model candidate contract is invalid") from exc


def _expected_relations(
    report: BindingResolutionReportV2,
) -> tuple[tuple[str, str], ...]:
    relations = {
        (item.physical_column.schema_name, item.physical_column.relation_name)
        for item in report.resolved_bindings
    }
    for item in report.authorized_joins:
        relations.add((item.left_column.schema_name, item.left_column.relation_name))
        relations.add((item.right_column.schema_name, item.right_column.relation_name))
    return tuple(sorted(relations))


def _validate_generated_cross_references(
    generated: GeneratedCandidatePayloadV2,
    payload: GenerateSqlCandidateRequestV2,
    report: BindingResolutionReportV2,
) -> None:
    binding = payload.resolution_request.binding_request
    expected_parameters = {
        item.name: (item.data_type, item.required, f"fact.parameters.{item.name}")
        for item in binding.fact.parameters
    }
    actual_parameters = {
        item.name: (item.data_type, item.required, item.source) for item in generated.parameters
    }
    if actual_parameters != expected_parameters:
        raise _CandidateCrossReferenceV2Error(
            "candidate parameter declarations do not match the V2 fact"
        )

    expected_result = binding.query_requirements.result
    result = generated.result
    if (
        result.column_name != expected_result.column_name
        or result.data_type is not expected_result.data_type
        or result.cardinality != expected_result.cardinality
        or result.nullable is not expected_result.nullable
        or result.null_policy is not expected_result.null_policy
        or result.unit != expected_result.unit
    ):
        raise _CandidateCrossReferenceV2Error(
            "candidate result declaration does not match the V2 result contract"
        )

    actual_relations = {
        (item.schema_name, item.relation_name) for item in generated.declared_objects
    }
    if actual_relations != set(_expected_relations(report)):
        raise _CandidateCrossReferenceV2Error(
            "candidate declaredObjects do not match the resolved relation closure"
        )

    expected_condition_ids = {item.condition_id for item in binding.usages}
    if set(generated.declared_usage_coverage) != expected_condition_ids:
        raise _CandidateCrossReferenceV2Error(
            "candidate declaredUsageCoverage does not match V2 condition usages"
        )


def _assemble_candidate_v2(
    generated: GeneratedCandidatePayloadV2,
    payload: GenerateSqlCandidateRequestV2,
    report: BindingResolutionReportV2,
    request: CandidateModelRequest,
    response: CandidateModelResponse,
    attempt_count: int,
) -> SqlTemplateCandidateV2:
    _validate_generated_cross_references(generated, payload, report)
    binding = payload.resolution_request.binding_request
    warnings = list(generated.warnings)
    if MANDATORY_CANDIDATE_WARNING_V2 not in warnings:
        warnings.append(MANDATORY_CANDIDATE_WARNING_V2)

    candidate = SqlTemplateCandidateV2(
        template_code=generated.template_code,
        status="candidate",
        executable=False,
        review_status="pending",
        rule_ref=CandidateRuleRefV2(
            rule_id=binding.rule_ref.rule_id,
            rule_version=binding.rule_ref.rule_version,
            schema_version=binding.rule_ref.schema_version,
            source_sha256=binding.rule_ref.source_sha256,
        ),
        request_ref=CandidateRequestRefV2(
            request_id=binding.request_id,
            payload_sha256=report.hashes.payload_sha256,
        ),
        project_ref=CandidateProjectRefV2(
            project_id=report.project_ref.project_id,
            project_version=report.project_ref.project_version,
        ),
        resolution_ref=CandidateResolutionRefV2(
            report_sha256=canonical_sha256(report),
            context_ref=CandidateContextRefV2(
                context_id=report.context_ref.context_id,
                context_version=report.context_ref.context_version,
                sha256=report.context_ref.sha256,
            ),
            metadata_snapshot_ref=CandidateSnapshotRefV2(
                snapshot_id=report.metadata_snapshot_ref.snapshot_id,
                snapshot_version=report.metadata_snapshot_ref.snapshot_version,
                sha256=report.metadata_snapshot_ref.sha256,
            ),
            authorization_policy_version=report.authorization_policy_version,
        ),
        generation_input_sha256=canonical_sha256(payload),
        fact_ref=CandidateFactRefV2(
            fact_code=binding.fact.fact_code,
            fact_kind=binding.fact.fact_kind,
            data_type=binding.fact.data_type,
            grain=binding.fact.grain,
        ),
        dialect="sqlserver",
        sql_template=generated.sql_template,
        parameters=tuple(
            CandidateParameterV2(
                name=item.name,
                data_type=item.data_type,
                required=item.required,
                source=item.source,
            )
            for item in sorted(generated.parameters, key=lambda value: value.name)
        ),
        result=CandidateResultV2(
            data_type=generated.result.data_type,
            nullable=generated.result.nullable,
            null_policy=generated.result.null_policy,
            unit=generated.result.unit,
        ),
        declared_objects=tuple(
            CandidateDeclaredRelationV2(schema_name=schema, relation_name=relation)
            for schema, relation in _expected_relations(report)
        ),
        declared_usage_coverage=tuple(sorted(generated.declared_usage_coverage)),
        assumptions=tuple(generated.assumptions),
        warnings=tuple(warnings),
        provenance=CandidateProvenanceV2(
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


async def _wait_before_retry_v2(
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


async def generate_sql_candidate_v2(
    provider: CandidateModelProvider,
    payload: GenerateSqlCandidateRequestV2,
    *,
    model: str,
    max_retries: int,
    retry_base_delay_seconds: float = 0.25,
    sleeper: RetrySleeper = asyncio.sleep,
) -> SqlTemplateCandidateV2:
    if not 0 <= max_retries <= 5:
        raise ValueError("max_retries must be between 0 and 5")
    if retry_base_delay_seconds < 0:
        raise ValueError("retry_base_delay_seconds cannot be negative")

    report = _validated_resolution(payload)
    prompt = build_sqlserver_candidate_prompt_v2(payload)
    request = CandidateModelRequest(
        model=model,
        prompt_version=prompt.version,
        system_prompt=prompt.system,
        user_prompt=prompt.user,
        response_format="json_object",
        max_tokens=SQLSERVER_CANDIDATE_MAX_TOKENS_V2,
    )
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        try:
            response = await provider.generate(request)
        except CandidateProviderRejectedError:
            raise CandidateGenerationProviderRejectedV2Error(
                "V2 candidate provider rejected the request"
            ) from None
        except CandidateProviderTransientError:
            if attempt == total_attempts:
                raise CandidateGenerationProviderUnavailableV2Error(
                    "V2 candidate provider retries were exhausted"
                ) from None
            await _wait_before_retry_v2(
                failed_attempt=attempt,
                retry_base_delay_seconds=retry_base_delay_seconds,
                sleeper=sleeper,
            )
            continue

        try:
            generated = _parse_generated_payload_v2(response.content)
            return _assemble_candidate_v2(
                generated,
                payload,
                report,
                request,
                response,
                attempt,
            )
        except (_CandidateCrossReferenceV2Error, ValidationError):
            if attempt == total_attempts:
                raise CandidateGenerationOutputInvalidV2Error(
                    "V2 candidate output retries were exhausted"
                ) from None
            await _wait_before_retry_v2(
                failed_attempt=attempt,
                retry_base_delay_seconds=retry_base_delay_seconds,
                sleeper=sleeper,
            )

    raise AssertionError("bounded V2 candidate generation loop exited unexpectedly")
