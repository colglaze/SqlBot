"""Validated, read-only intake for RuleReader immutable V2 handoff records."""

from __future__ import annotations

import json
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from release_sql_bot.application.binding_intake_v2 import analyze_binding_gaps_v2
from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffDocumentInvalidError,
    FactBindingHandoffRepository,
)
from release_sql_bot.domain.fact_binding_handoffs_v2 import (
    FactBindingHandoffIntakeBatchV2,
    FactBindingHandoffIntakeRecordV2,
    StoredFactBindingHandoffV2,
)

FACT_BINDING_SCHEMA_ID_V2 = "urn:rulereader:fact-binding-request:2.0.0"
FACT_BINDING_SCHEMA_SHA256_V2 = "38fec6b22511984983e7e7fbbdb40afd58aeffd51b2de8ab73fdfb187024026b"
_SCHEMA_PACKAGE = "release_sql_bot.contracts"
_SCHEMA_NAME = "fact-binding-request-2.0.0.schema.json"


class FactBindingHandoffIntakeError(RuntimeError):
    """Stable application error without payload or infrastructure details."""


class FactBindingHandoffNotFoundError(FactBindingHandoffIntakeError):
    """No immutable handoff exists for the exact rule version."""


class FactBindingHandoffInvalidError(FactBindingHandoffIntakeError):
    """One or more records fail the frozen wrapper or payload contract."""


@lru_cache(maxsize=1)
def load_fact_binding_schema_v2() -> dict[str, Any]:
    """Load the packaged Schema after checking its normalized upstream source hash."""

    try:
        raw = files(_SCHEMA_PACKAGE).joinpath(_SCHEMA_NAME).read_bytes()
    except (OSError, ModuleNotFoundError) as error:
        raise FactBindingHandoffInvalidError(
            "FactBindingRequest V2 Schema could not be loaded"
        ) from error
    source_bytes = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if sha256(source_bytes).hexdigest() != FACT_BINDING_SCHEMA_SHA256_V2:
        raise FactBindingHandoffInvalidError("FactBindingRequest V2 Schema source hash is invalid")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FactBindingHandoffInvalidError(
            "FactBindingRequest V2 Schema is not valid UTF-8 JSON"
        ) from error
    if not isinstance(parsed, dict):
        raise FactBindingHandoffInvalidError("FactBindingRequest V2 Schema must be an object")
    schema = cast(dict[str, Any], parsed)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise FactBindingHandoffInvalidError("FactBindingRequest V2 Schema is invalid") from error
    if schema.get("$id") != FACT_BINDING_SCHEMA_ID_V2:
        raise FactBindingHandoffInvalidError("FactBindingRequest V2 Schema identity is invalid")
    return schema


def _validate_batch_identity(
    records: tuple[StoredFactBindingHandoffV2, ...],
    rule_version: str,
) -> None:
    request_ids = [record.request_id for record in records]
    fact_codes = [record.fact_code for record in records]
    if len(request_ids) != len(set(request_ids)) or len(fact_codes) != len(set(fact_codes)):
        raise FactBindingHandoffInvalidError(
            "Fact binding handoff batch contains duplicate identities"
        )
    if any(record.rule_version != rule_version for record in records):
        raise FactBindingHandoffInvalidError(
            "Fact binding handoff does not match the requested rule version"
        )


def _validate_record(
    record: StoredFactBindingHandoffV2,
    validator: Draft202012Validator,
) -> FactBindingHandoffIntakeRecordV2:
    payload = record.payload.model_dump(by_alias=True, mode="json")
    try:
        validator.validate(payload)
    except JsonSchemaValidationError as error:
        raise FactBindingHandoffInvalidError(
            "Fact binding payload failed the frozen JSON Schema"
        ) from error

    expected_request_id = f"{record.rule_version}#{record.fact_code}"
    if (
        record.mongo_id != record.request_id
        or record.request_id != expected_request_id
        or record.payload.request_id != record.request_id
        or record.payload.rule_ref.rule_version != record.rule_version
        or record.payload.fact.fact_code != record.fact_code
        or record.payload.contract_version != record.contract_version
    ):
        raise FactBindingHandoffInvalidError(
            "Fact binding handoff wrapper and payload identity are inconsistent"
        )
    if record.payload.rule_ref.source_sha256 != record.payload.provenance.source.sha256:
        raise FactBindingHandoffInvalidError("Fact binding handoff source identity is inconsistent")
    if canonical_sha256(record.payload) != record.payload_sha256:
        raise FactBindingHandoffInvalidError(
            "Fact binding handoff canonical payload hash is inconsistent"
        )

    report = analyze_binding_gaps_v2(record.payload)
    return FactBindingHandoffIntakeRecordV2(
        request_id=record.request_id,
        fact_code=record.fact_code,
        payload_sha256=record.payload_sha256,
        created_at=record.created_at,
        payload=record.payload,
        gap_report=report,
    )


async def intake_fact_binding_handoffs_v2(
    repository: FactBindingHandoffRepository,
    rule_version: str,
) -> FactBindingHandoffIntakeBatchV2:
    """Read and validate the complete exact-version handoff set without side effects."""

    try:
        records = await repository.list_by_rule_version(rule_version)
    except FactBindingHandoffDocumentInvalidError as error:
        raise FactBindingHandoffInvalidError("Fact binding handoff wrapper is invalid") from error
    if not records:
        raise FactBindingHandoffNotFoundError(
            "No fact binding handoff exists for the exact rule version"
        )

    records = tuple(sorted(records, key=lambda record: (record.fact_code, record.request_id)))
    _validate_batch_identity(records, rule_version)
    validator = Draft202012Validator(
        load_fact_binding_schema_v2(),
        format_checker=FormatChecker(),
    )
    intake_records = tuple(_validate_record(record, validator) for record in records)
    blocking_count = sum(record.gap_report.status == "blocked" for record in intake_records)
    return FactBindingHandoffIntakeBatchV2(
        rule_version=rule_version,
        contract_schema_id=FACT_BINDING_SCHEMA_ID_V2,
        contract_schema_sha256=FACT_BINDING_SCHEMA_SHA256_V2,
        status="blocked" if blocking_count else "readyForMetadataResolution",
        record_count=len(intake_records),
        blocking_request_count=blocking_count,
        records=intake_records,
    )
