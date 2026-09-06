"""Validated, read-only intake for RuleReader immutable V3 handoff batches.

The upstream Schema v5 collection stores one atomic single-document batch per
exact rule version. Intake verifies the frozen JSON Schema, wrapper identity,
canonical payload hashes, the batch hash, and request uniqueness. A valid V3
batch is always ready (upstream only persists ready batches and warning-only
uncertainties); any integrity failure fails the whole batch closed. This
service never touches a candidate provider and never writes to MongoDB.
"""

from __future__ import annotations

import json
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffBatchDocumentInvalidV3Error,
    FactBindingHandoffBatchRepositoryV3,
)
from release_sql_bot.domain.fact_binding_handoffs_v3 import (
    FactBindingHandoffIntakeBatchV3,
    FactBindingHandoffIntakeRequestV3,
    StoredFactBindingHandoffBatchV3,
    StoredFactBindingHandoffRequestV3,
)

FACT_BINDING_SCHEMA_ID_V3 = "urn:rulereader:fact-binding-request:3.0.0"
FACT_BINDING_SCHEMA_SHA256_V3 = "2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566"
_SCHEMA_PACKAGE = "release_sql_bot.contracts"
_SCHEMA_NAME = "fact-binding-request-3.0.0.schema.json"


class FactBindingHandoffBatchIntakeV3Error(RuntimeError):
    """Stable application error without payload or infrastructure details."""


class FactBindingHandoffBatchNotFoundErrorV3(FactBindingHandoffBatchIntakeV3Error):
    """No immutable handoff batch exists for the exact rule version."""


class FactBindingHandoffBatchInvalidErrorV3(FactBindingHandoffBatchIntakeV3Error):
    """The batch or one of its requests fails the frozen wrapper/payload contract."""


@lru_cache(maxsize=1)
def load_fact_binding_schema_v3() -> dict[str, Any]:
    """Load the packaged Schema after checking its normalized upstream source hash."""

    try:
        raw = files(_SCHEMA_PACKAGE).joinpath(_SCHEMA_NAME).read_bytes()
    except (OSError, ModuleNotFoundError) as error:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "FactBindingRequest V3 Schema could not be loaded"
        ) from error
    source_bytes = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if sha256(source_bytes).hexdigest() != FACT_BINDING_SCHEMA_SHA256_V3:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "FactBindingRequest V3 Schema source hash is invalid"
        )
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "FactBindingRequest V3 Schema is not valid UTF-8 JSON"
        ) from error
    if not isinstance(parsed, dict):
        raise FactBindingHandoffBatchInvalidErrorV3(
            "FactBindingRequest V3 Schema must be an object"
        )
    schema = cast(dict[str, Any], parsed)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "FactBindingRequest V3 Schema is invalid"
        ) from error
    if schema.get("$id") != FACT_BINDING_SCHEMA_ID_V3:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "FactBindingRequest V3 Schema identity is invalid"
        )
    return schema


def canonical_batch_sha256_v3(
    identities: list[dict[str, str]],
) -> str:
    """Recompute the upstream batch hash over requestId/payloadSha256 identities."""

    return canonical_sha256(sorted(identities, key=lambda identity: identity["requestId"]))


def _validate_batch_shape(
    batch: StoredFactBindingHandoffBatchV3,
    rule_version: str,
) -> None:
    if batch.mongo_id != rule_version or batch.rule_version != rule_version:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "Fact binding handoff batch does not match the requested rule version"
        )
    request_ids = [item.request_id for item in batch.requests]
    if batch.request_count != len(batch.requests) or len(batch.request_ids) != len(batch.requests):
        raise FactBindingHandoffBatchInvalidErrorV3(
            "Fact binding handoff batch request count is inconsistent"
        )
    if batch.request_ids != sorted(set(batch.request_ids)):
        raise FactBindingHandoffBatchInvalidErrorV3(
            "Fact binding handoff batch requestIds must be unique and sorted"
        )
    if batch.request_ids != request_ids:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "Fact binding handoff batch requestIds do not match the stored requests"
        )
    fact_codes = [item.fact_code for item in batch.requests]
    if len(fact_codes) != len(set(fact_codes)):
        raise FactBindingHandoffBatchInvalidErrorV3(
            "Fact binding handoff batch contains duplicate fact identities"
        )
    for request in batch.requests:
        if request.rule_version != rule_version:
            raise FactBindingHandoffBatchInvalidErrorV3(
                "Fact binding handoff batch contains a foreign rule version"
            )


def _validate_request(
    request: StoredFactBindingHandoffRequestV3,
    validator: Draft202012Validator,
) -> FactBindingHandoffIntakeRequestV3:
    payload = request.payload.model_dump(by_alias=True, mode="json")
    try:
        validator.validate(payload)
    except JsonSchemaValidationError as error:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "Fact binding payload failed the frozen V3 JSON Schema"
        ) from error

    expected_request_id = f"{request.rule_version}#{request.fact_code}"
    if (
        request.payload.request_id != request.request_id
        or request.request_id != expected_request_id
        or request.payload.rule_ref.rule_version != request.rule_version
        or request.payload.fact.fact_code != request.fact_code
        or request.payload.contract_version != request.contract_version
    ):
        raise FactBindingHandoffBatchInvalidErrorV3(
            "Fact binding handoff wrapper and payload identity are inconsistent"
        )
    if canonical_sha256(request.payload) != request.payload_sha256:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "Fact binding handoff canonical payload hash is inconsistent"
        )

    return FactBindingHandoffIntakeRequestV3(
        request_id=request.request_id,
        fact_code=request.fact_code,
        payload_sha256=request.payload_sha256,
        created_at=request.created_at,
        payload=request.payload,
    )


async def intake_fact_binding_handoffs_v3(
    repository: FactBindingHandoffBatchRepositoryV3,
    rule_version: str,
) -> FactBindingHandoffIntakeBatchV3:
    """Read and validate the exact-version V3 handoff batch without side effects."""

    try:
        batch = await repository.get_batch_by_rule_version(rule_version)
    except FactBindingHandoffBatchDocumentInvalidV3Error as error:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "Fact binding handoff batch wrapper is invalid"
        ) from error
    if batch is None:
        raise FactBindingHandoffBatchNotFoundErrorV3(
            "No fact binding handoff batch exists for the exact rule version"
        )

    _validate_batch_shape(batch, rule_version)
    validator = Draft202012Validator(
        load_fact_binding_schema_v3(),
        format_checker=FormatChecker(),
    )
    ordered = sorted(batch.requests, key=lambda item: item.request_id)
    intake_requests = tuple(_validate_request(request, validator) for request in ordered)
    recomputed_batch_sha256 = canonical_batch_sha256_v3(
        [
            {"requestId": request.request_id, "payloadSha256": request.payload_sha256}
            for request in ordered
        ]
    )
    if recomputed_batch_sha256 != batch.batch_sha256:
        raise FactBindingHandoffBatchInvalidErrorV3(
            "Fact binding handoff canonical batch hash is inconsistent"
        )
    return FactBindingHandoffIntakeBatchV3(
        rule_version=rule_version,
        contract_schema_id=FACT_BINDING_SCHEMA_ID_V3,
        contract_schema_sha256=FACT_BINDING_SCHEMA_SHA256_V3,
        batch_sha256=batch.batch_sha256,
        request_count=len(intake_requests),
        requests=intake_requests,
    )
