"""Legacy V1 path for generating one non-executable SQL candidate."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from hashlib import sha256

from pydantic import ValidationError

from release_sql_bot.application.bindings import validate_binding_readiness
from release_sql_bot.application.ports.candidates import (
    CandidateModelProvider,
    CandidateModelRequest,
    CandidateModelResponse,
    CandidateProviderRejectedError,
    CandidateProviderTransientError,
)
from release_sql_bot.application.prompts import (
    SQLSERVER_CANDIDATE_MAX_TOKENS,
    build_sqlserver_candidate_prompt,
)
from release_sql_bot.domain.fact_bindings import BindingReadiness, ValidateFactBindingRequest
from release_sql_bot.domain.sql_candidates import (
    CandidateBindingRef,
    CandidateContextRef,
    CandidateFactRef,
    CandidateProvenance,
    GeneratedCandidatePayload,
    SqlTemplateCandidate,
)

MANDATORY_CANDIDATE_WARNING = "候选 SQL 未通过 AST、安全门禁、受限验证和人工审核，不得执行。"
_PARAMETER_PATTERN = re.compile(r"(?<!:):([A-Za-z][A-Za-z0-9_]*)\b")
_MAX_RETRY_DELAY_SECONDS = 5.0

RetrySleeper = Callable[[float], Awaitable[None]]


class CandidateInputNotReadyError(RuntimeError):
    def __init__(self, readiness: BindingReadiness) -> None:
        super().__init__("Fact binding is not ready for candidate generation")
        self.readiness = readiness


class CandidateGenerationOutputInvalidError(RuntimeError):
    pass


class CandidateGenerationProviderUnavailableError(RuntimeError):
    pass


class CandidateGenerationProviderRejectedError(RuntimeError):
    pass


class _CandidateCrossReferenceError(ValueError):
    pass


def _binding_ref(payload: ValidateFactBindingRequest) -> CandidateBindingRef:
    try:
        canonical_json = json.dumps(
            payload.binding_request.model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise _CandidateCrossReferenceError(
            "binding request cannot be canonicalized for audit"
        ) from exc
    return CandidateBindingRef(
        contract_version=payload.binding_request.contract_version,
        sha256=sha256(canonical_json.encode("utf-8")).hexdigest(),
    )


def _parse_generated_payload(content: str) -> GeneratedCandidatePayload:
    if not content.strip():
        raise _CandidateCrossReferenceError("empty model content")
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError as exc:
        raise _CandidateCrossReferenceError("model content is not JSON") from exc
    if not isinstance(decoded, dict):
        raise _CandidateCrossReferenceError("model content must be one JSON object")
    try:
        return GeneratedCandidatePayload.model_validate_json(
            content,
            strict=True,
            by_alias=True,
            by_name=False,
        )
    except ValidationError as exc:
        raise _CandidateCrossReferenceError("model candidate contract is invalid") from exc


def _validate_cross_references(
    generated: GeneratedCandidatePayload,
    payload: ValidateFactBindingRequest,
) -> None:
    fact = payload.binding_request.fact
    expected_parameters = {
        parameter.name: (parameter.data_type, parameter.required) for parameter in fact.parameters
    }
    actual_parameters = {
        parameter.name: (parameter.data_type, parameter.required)
        for parameter in generated.parameters
    }
    if actual_parameters != expected_parameters:
        raise _CandidateCrossReferenceError("candidate parameters do not match the fact")
    for parameter in generated.parameters:
        if parameter.source != f"fact.parameters.{parameter.name}":
            raise _CandidateCrossReferenceError("candidate parameter source is invalid")

    placeholders = set(_PARAMETER_PATTERN.findall(generated.sql_template))
    if placeholders != set(expected_parameters):
        raise _CandidateCrossReferenceError("SQL placeholders do not match the fact parameters")

    if generated.result.data_type is not fact.data_type:
        raise _CandidateCrossReferenceError("candidate result type does not match the fact")
    if generated.result.nullable is not fact.nullable:
        raise _CandidateCrossReferenceError("candidate result nullability does not match the fact")

    allowed_relations = {relation.qualified_name for relation in payload.context.allowed_relations}
    if not set(generated.allowed_objects).issubset(allowed_relations):
        raise _CandidateCrossReferenceError("candidate declares an object outside the allowlist")

    expected_condition_ids = {usage.condition_id for usage in payload.binding_request.usages}
    if set(generated.usage_coverage) != expected_condition_ids:
        raise _CandidateCrossReferenceError("candidate usage coverage is incomplete")


def _assemble_candidate(
    generated: GeneratedCandidatePayload,
    payload: ValidateFactBindingRequest,
    request: CandidateModelRequest,
    response: CandidateModelResponse,
    attempt_count: int,
) -> SqlTemplateCandidate:
    _validate_cross_references(generated, payload)
    binding = payload.binding_request
    fact = binding.fact
    context = payload.context
    warnings = list(generated.warnings)
    if MANDATORY_CANDIDATE_WARNING not in warnings:
        warnings.append(MANDATORY_CANDIDATE_WARNING)

    return SqlTemplateCandidate(
        template_code=generated.template_code,
        rule_ref=binding.rule_ref,
        binding_ref=_binding_ref(payload),
        fact_ref=CandidateFactRef(
            fact_code=fact.fact_code,
            fact_kind=fact.fact_kind.value,
            data_type=fact.data_type,
            grain=fact.grain,
        ),
        context_ref=CandidateContextRef(
            context_id=context.context_id,
            context_version=context.context_version,
            metadata_snapshot_id=context.metadata_snapshot.snapshot_id,
            metadata_snapshot_version=context.metadata_snapshot.version,
            metadata_snapshot_sha256=context.metadata_snapshot.sha256,
        ),
        sql_template=generated.sql_template,
        parameters=generated.parameters,
        result=generated.result,
        allowed_objects=generated.allowed_objects,
        usage_coverage=generated.usage_coverage,
        assumptions=generated.assumptions,
        warnings=warnings,
        provenance=CandidateProvenance(
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
    )


async def _wait_before_retry(
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


async def generate_sql_candidate(
    provider: CandidateModelProvider,
    payload: ValidateFactBindingRequest,
    *,
    model: str,
    max_retries: int,
    retry_base_delay_seconds: float = 0.25,
    sleeper: RetrySleeper = asyncio.sleep,
) -> SqlTemplateCandidate:
    if not 0 <= max_retries <= 5:
        raise ValueError("max_retries must be between 0 and 5")
    if retry_base_delay_seconds < 0:
        raise ValueError("retry_base_delay_seconds cannot be negative")

    readiness = validate_binding_readiness(payload)
    if readiness.status != "ready":
        raise CandidateInputNotReadyError(readiness)

    prompt = build_sqlserver_candidate_prompt(payload)
    request = CandidateModelRequest(
        model=model,
        prompt_version=prompt.version,
        system_prompt=prompt.system,
        user_prompt=prompt.user,
        response_format="json_object",
        max_tokens=SQLSERVER_CANDIDATE_MAX_TOKENS,
    )
    total_attempts = max_retries + 1

    for attempt in range(1, total_attempts + 1):
        try:
            response = await provider.generate(request)
        except CandidateProviderRejectedError:
            raise CandidateGenerationProviderRejectedError(
                "Candidate provider rejected the request"
            ) from None
        except CandidateProviderTransientError:
            if attempt == total_attempts:
                raise CandidateGenerationProviderUnavailableError(
                    "Candidate provider retries were exhausted"
                ) from None
            await _wait_before_retry(
                failed_attempt=attempt,
                retry_base_delay_seconds=retry_base_delay_seconds,
                sleeper=sleeper,
            )
            continue

        try:
            generated = _parse_generated_payload(response.content)
            return _assemble_candidate(generated, payload, request, response, attempt)
        except (_CandidateCrossReferenceError, ValidationError):
            if attempt == total_attempts:
                raise CandidateGenerationOutputInvalidError(
                    "Candidate output retries were exhausted"
                ) from None
            await _wait_before_retry(
                failed_attempt=attempt,
                retry_base_delay_seconds=retry_base_delay_seconds,
                sleeper=sleeper,
            )

    raise AssertionError("bounded candidate generation loop exited unexpectedly")
