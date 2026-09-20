"""Select and validate a Rule Schema 3.1.0 complete delivery.

Hash and purpose rules are copied from Agent1 selectDelivery. SqlBot does not
invent a second table. This service never writes MongoDB, never calls a
provider, and never compiles SQL.
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
from release_sql_bot.application.ports.complete_delivery_v31 import CompleteDeliverySourceV31
from release_sql_bot.domain.complete_delivery_v31 import (
    CompleteDeliveryV31,
    StoredCompleteDeliveryV31,
    StoredFactBindingHandoffBatchV31,
)
from release_sql_bot.domain.purpose_v31 import (
    FORMULA_TREE_V3_CONTRACT_VERSION,
    DeliveryPurposeDeniedErrorV31,
    DeliveryPurposeV31,
    assert_delivery_purpose_allowed,
)
from release_sql_bot.domain.rule_structure_v31 import (
    STAGE_ORDER_V31,
    BusinessConfirmedFactCatalogV31,
    RuleParseResultV31,
    RuleStructureCandidateV31,
)

_SCHEMA_PACKAGE = "release_sql_bot.contracts"
FACT_BINDING_SCHEMA_ID_V31 = "urn:rulereader:fact-binding-request:3.1.0"
CANDIDATE_SCHEMA_ID_V31 = "urn:rulereader:rule-structure-candidate:3.1.0"
RESULT_SCHEMA_ID_V31 = "urn:rulereader:rule-parse-result:3.1.0"
CATALOG_SCHEMA_ID_V3 = "urn:rulereader:business-confirmed-fact-catalog:3.0.0"

FACT_BINDING_SCHEMA_SHA256_V31 = "9102fa3fe2e67e7e266dcd41fc40c7f00a629158fb013f9e61ef739d11a270fc"
CANDIDATE_SCHEMA_SHA256_V31 = "cdd416b23c49e254ca12606f27f4db32ab17c9afe00de26a8d2beb9a83f531c2"
RESULT_SCHEMA_SHA256_V31 = "4e5774c559e770182b6de964324016309941f9f8292395cee2512f5e2048948f"
CATALOG_SCHEMA_SHA256_V3 = "bb2446f2112f073967358c7b0c36a9bab4565ca30d35dbce24142191b50f450c"


class CompleteDeliveryIntakeV31Error(RuntimeError):
    """Stable application error without payload or infrastructure details."""


class CompleteDeliveryNotFoundV31Error(CompleteDeliveryIntakeV31Error):
    """No complete delivery exists for the exact rule version."""


class CompleteDeliveryInvalidV31Error(CompleteDeliveryIntakeV31Error):
    """The stored envelope failed schema, identity, hash, or purpose closure."""


class CompleteDeliveryNotConsumableV31Error(CompleteDeliveryIntakeV31Error):
    """Parts are missing; the delivery must not be consumed or fall back."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        super().__init__("Complete delivery is not consumable")
        self.missing = missing


def crlf_normalized_sha256(raw: bytes) -> str:
    source_bytes = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return sha256(source_bytes).hexdigest()


def catalog_digest_sha256(catalog: BusinessConfirmedFactCatalogV31 | dict[str, Any]) -> str:
    """Agent1 catalogDigest: canonical JSON excluding the digest field itself."""

    if isinstance(catalog, BusinessConfirmedFactCatalogV31):
        payload = catalog.model_dump(by_alias=True, mode="json")
    else:
        payload = dict(catalog)
    payload.pop("catalogDigest", None)
    payload.pop("catalog_digest", None)
    return canonical_sha256(payload)


def _load_schema(name: str, expected_id: str, expected_sha256: str) -> dict[str, Any]:
    try:
        raw = files(_SCHEMA_PACKAGE).joinpath(name).read_bytes()
    except (OSError, ModuleNotFoundError) as error:
        raise CompleteDeliveryInvalidV31Error(
            "Frozen delivery Schema could not be loaded"
        ) from error
    if crlf_normalized_sha256(raw) != expected_sha256:
        raise CompleteDeliveryInvalidV31Error("Frozen delivery Schema source hash is invalid")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CompleteDeliveryInvalidV31Error(
            "Frozen delivery Schema is not valid UTF-8 JSON"
        ) from error
    if not isinstance(parsed, dict):
        raise CompleteDeliveryInvalidV31Error("Frozen delivery Schema must be an object")
    schema = cast(dict[str, Any], parsed)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise CompleteDeliveryInvalidV31Error("Frozen delivery Schema is invalid") from error
    if schema.get("$id") != expected_id:
        raise CompleteDeliveryInvalidV31Error("Frozen delivery Schema identity is invalid")
    return schema


@lru_cache(maxsize=1)
def load_fact_binding_schema_v31() -> dict[str, Any]:
    return _load_schema(
        "fact-binding-request-3.1.0.schema.json",
        FACT_BINDING_SCHEMA_ID_V31,
        FACT_BINDING_SCHEMA_SHA256_V31,
    )


@lru_cache(maxsize=1)
def load_candidate_schema_v31() -> dict[str, Any]:
    return _load_schema(
        "rule-structure-candidate-3.1.0.schema.json",
        CANDIDATE_SCHEMA_ID_V31,
        CANDIDATE_SCHEMA_SHA256_V31,
    )


@lru_cache(maxsize=1)
def load_result_schema_v31() -> dict[str, Any]:
    return _load_schema(
        "rule-parse-result-3.1.0.schema.json",
        RESULT_SCHEMA_ID_V31,
        RESULT_SCHEMA_SHA256_V31,
    )


@lru_cache(maxsize=1)
def load_catalog_schema_v3() -> dict[str, Any]:
    return _load_schema(
        "business-confirmed-fact-catalog-3.0.0.schema.json",
        CATALOG_SCHEMA_ID_V3,
        CATALOG_SCHEMA_SHA256_V3,
    )


def _validate_json_schema(payload: dict[str, Any], schema: dict[str, Any], label: str) -> None:
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)
    except JsonSchemaValidationError as error:
        raise CompleteDeliveryInvalidV31Error(f"{label} failed the frozen JSON Schema") from error


def _missing_parts(stored: StoredCompleteDeliveryV31) -> tuple[str, ...]:
    missing: list[str] = []
    if not stored.catalog_payload:
        missing.append("catalog")
    if not stored.candidate_payload:
        missing.append("candidate")
    if not stored.result_payload:
        missing.append("result")
    if stored.batch is None:
        missing.append("batch")
    return tuple(missing)


def _validate_batch(batch: StoredFactBindingHandoffBatchV31, rule_version: str) -> None:
    if batch.mongo_id != rule_version or batch.rule_version != rule_version:
        raise CompleteDeliveryInvalidV31Error(
            "Fact binding handoff batch does not match the requested rule version"
        )
    request_ids = [item.request_id for item in batch.requests]
    if batch.request_count != len(batch.requests) or len(batch.request_ids) != len(batch.requests):
        raise CompleteDeliveryInvalidV31Error(
            "Fact binding handoff batch request count is inconsistent"
        )
    if batch.request_ids != sorted(set(batch.request_ids)):
        raise CompleteDeliveryInvalidV31Error(
            "Fact binding handoff batch requestIds must be unique and sorted"
        )
    if batch.request_ids != request_ids:
        raise CompleteDeliveryInvalidV31Error(
            "Fact binding handoff batch requestIds do not match the stored requests"
        )
    fact_codes = [item.fact_code for item in batch.requests]
    if len(fact_codes) != len(set(fact_codes)):
        raise CompleteDeliveryInvalidV31Error(
            "Fact binding handoff batch contains duplicate fact identities"
        )
    identities = [
        {"requestId": item.request_id, "payloadSha256": item.payload_sha256}
        for item in sorted(batch.requests, key=lambda item: item.request_id)
    ]
    if canonical_sha256(identities) != batch.batch_sha256:
        raise CompleteDeliveryInvalidV31Error(
            "Fact binding handoff canonical batch hash is inconsistent"
        )
    validator = Draft202012Validator(load_fact_binding_schema_v31(), format_checker=FormatChecker())
    for request in batch.requests:
        if request.rule_version != rule_version:
            raise CompleteDeliveryInvalidV31Error(
                "Fact binding handoff batch contains a foreign rule version"
            )
        payload = request.payload.model_dump(by_alias=True, mode="json")
        try:
            validator.validate(payload)
        except JsonSchemaValidationError as error:
            raise CompleteDeliveryInvalidV31Error(
                "Fact binding payload failed the frozen 3.1.0 JSON Schema"
            ) from error
        expected_request_id = f"{request.rule_version}#{request.fact_code}"
        if (
            request.payload.request_id != request.request_id
            or request.request_id != expected_request_id
            or request.payload.rule_ref.rule_version != request.rule_version
            or request.payload.fact.fact_code != request.fact_code
            or request.payload.contract_version != request.contract_version
        ):
            raise CompleteDeliveryInvalidV31Error(
                "Fact binding handoff wrapper and payload identity are inconsistent"
            )
        if canonical_sha256(request.payload) != request.payload_sha256:
            raise CompleteDeliveryInvalidV31Error(
                "Fact binding handoff canonical payload hash is inconsistent"
            )
        if request.payload.executable is not False:
            raise CompleteDeliveryInvalidV31Error("Fact binding request must remain non-executable")
        if not request.payload.mapping_is_unresolved():
            raise CompleteDeliveryInvalidV31Error(
                "Physical mapping remains unresolved until metadataReview"
            )


async def select_complete_delivery_v31(
    source: CompleteDeliverySourceV31,
    *,
    purpose: DeliveryPurposeV31,
    rule_version: str,
) -> CompleteDeliveryV31:
    """Read tree + catalog + result + batch for one exact version."""

    try:
        assert_delivery_purpose_allowed(rule_version, purpose)
    except DeliveryPurposeDeniedErrorV31 as error:
        raise CompleteDeliveryInvalidV31Error(
            "Delivery is not available for the requested purpose"
        ) from error

    stored = await source.get_delivery(rule_version)
    if stored is None:
        raise CompleteDeliveryNotFoundV31Error(
            "No complete delivery exists for the exact rule version"
        )
    if stored.rule_version != rule_version:
        raise CompleteDeliveryInvalidV31Error(
            "Stored delivery does not match the requested rule version"
        )
    if stored.schema_version == FORMULA_TREE_V3_CONTRACT_VERSION:
        raise CompleteDeliveryInvalidV31Error(
            "3.0.0 formula trees are not consumable 3.1.0 deliveries"
        )
    if stored.schema_version != "3.1.0":
        raise CompleteDeliveryInvalidV31Error("Complete delivery schemaVersion must be 3.1.0")
    if stored.executable is not False:
        raise CompleteDeliveryInvalidV31Error(
            "Complete delivery must remain a non-executable draft"
        )
    if stored.purpose != DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION.value:
        raise CompleteDeliveryInvalidV31Error(
            "Delivery purpose does not allow the requested purpose"
        )
    if purpose not in {
        DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
    }:
        raise CompleteDeliveryInvalidV31Error(
            "Delivery purpose does not allow the requested purpose"
        )

    missing = _missing_parts(stored)
    if missing:
        raise CompleteDeliveryNotConsumableV31Error(missing)

    catalog_payload = cast(dict[str, Any], stored.catalog_payload)
    candidate_payload = cast(dict[str, Any], stored.candidate_payload)
    result_payload = cast(dict[str, Any], stored.result_payload)
    batch = stored.batch
    assert batch is not None

    _validate_json_schema(catalog_payload, load_catalog_schema_v3(), "catalog")
    _validate_json_schema(candidate_payload, load_candidate_schema_v31(), "candidate")
    _validate_json_schema(result_payload, load_result_schema_v31(), "result")
    catalog = BusinessConfirmedFactCatalogV31.model_validate(catalog_payload)
    candidate = RuleStructureCandidateV31.model_validate(candidate_payload)
    result = RuleParseResultV31.model_validate(result_payload)
    _validate_batch(batch, rule_version)

    catalog_payload_sha256 = canonical_sha256(catalog)
    candidate_payload_sha256 = canonical_sha256(candidate)
    result_payload_sha256 = canonical_sha256(result)
    digest = catalog_digest_sha256(catalog)
    if digest != catalog.catalog_digest:
        raise CompleteDeliveryInvalidV31Error("catalogDigest does not close")
    if stored.catalog_digest not in {None, digest}:
        raise CompleteDeliveryInvalidV31Error("catalogDigest does not close")
    if candidate.catalog_digest != digest or result.catalog_ref.catalog_digest != digest:
        raise CompleteDeliveryInvalidV31Error("catalogDigest does not close across the delivery")
    if result.catalog_ref.payload_sha256 != catalog_payload_sha256:
        raise CompleteDeliveryInvalidV31Error("catalog payload hash does not close")
    if result.delivery_ref.catalog_payload_sha256 != catalog_payload_sha256:
        raise CompleteDeliveryInvalidV31Error("catalog payload hash does not close")
    if result.candidate_ref.payload_sha256 != candidate_payload_sha256:
        raise CompleteDeliveryInvalidV31Error("candidate payload hash does not close")
    if result.delivery_ref.candidate_payload_sha256 != candidate_payload_sha256:
        raise CompleteDeliveryInvalidV31Error("candidate payload hash does not close")
    if (
        result.delivery_ref.result_payload_sha256 is not None
        and result.delivery_ref.result_payload_sha256 != result_payload_sha256
    ):
        raise CompleteDeliveryInvalidV31Error("result payload hash does not close")
    source_file = candidate.source_identity.source_file_sha256
    parse_input = candidate.source_identity.parse_input_sha256
    if (
        source_file != result.source.source_sha256
        or parse_input != result.source.parse_input_sha256
    ):
        raise CompleteDeliveryInvalidV31Error("sourceFile/parseInput hashes do not close")
    if result.candidate_ref.parse_input_sha256 != parse_input:
        raise CompleteDeliveryInvalidV31Error("sourceFile/parseInput hashes do not close")
    if stored.source_file_sha256 not in {None, source_file}:
        raise CompleteDeliveryInvalidV31Error("sourceFile hash does not close")
    if stored.parse_input_sha256 not in {None, parse_input}:
        raise CompleteDeliveryInvalidV31Error("parseInput hash does not close")
    if result.rule_version != rule_version or result.schema_version != "3.1.0":
        raise CompleteDeliveryInvalidV31Error(
            "result identity does not match the requested delivery"
        )
    if result.executable is not False or result.status != "draft":
        raise CompleteDeliveryInvalidV31Error(
            "Complete delivery must remain a non-executable draft"
        )
    if candidate.stage_names() != STAGE_ORDER_V31:
        raise CompleteDeliveryInvalidV31Error("candidate must expose the frozen five-stage tree")
    prefix = f"{result.rule_set_id}@"
    if not rule_version.startswith(prefix):
        raise CompleteDeliveryInvalidV31Error("ruleVersion must belong to ruleSetId")
    if rule_version[len(prefix) :].split("-")[-2] != source_file[:12]:
        raise CompleteDeliveryInvalidV31Error(
            "ruleVersion source prefix must close to the source file hash"
        )
    if rule_version.split("-")[-1] != digest[:12]:
        raise CompleteDeliveryInvalidV31Error(
            "ruleVersion digest prefix must close to catalogDigest"
        )
    for request in batch.requests:
        ref = request.payload.rule_ref
        if (
            ref.source_sha256 != source_file
            or ref.parse_input_sha256 != parse_input
            or ref.catalog_digest != digest
            or ref.candidate_payload_sha256 != candidate_payload_sha256
            or ref.rule_version != rule_version
        ):
            raise CompleteDeliveryInvalidV31Error(
                "fact request hashes do not close to the delivery"
            )

    return CompleteDeliveryV31(
        rule_version=rule_version,
        purpose=purpose.value,
        schema_version="3.1.0",
        consumable=True,
        missing=(),
        status="draft",
        delivery_purpose="optimization-plan-generation",
        source_file_sha256=source_file,
        parse_input_sha256=parse_input,
        catalog_digest=digest,
        catalog_payload_sha256=catalog_payload_sha256,
        candidate_payload_sha256=candidate_payload_sha256,
        result_payload_sha256=result_payload_sha256,
        batch_sha256=batch.batch_sha256,
        request_count=batch.request_count,
        stage_names=candidate.stage_names(),
        catalog=catalog,
        candidate=candidate,
        result=result,
        batch=batch,
    )
