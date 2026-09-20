"""Filesystem loader for a 3.1.0 complete-delivery directory. Read-only."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.domain.complete_delivery_v31 import (
    StoredCompleteDeliveryV31,
    StoredFactBindingHandoffBatchV31,
    StoredFactBindingHandoffRequestV31,
)
from release_sql_bot.domain.fact_bindings_v31 import FactBindingRequestV31

CREATED_AT = datetime(2026, 9, 17, 14, 0, tzinfo=UTC)


class FilesystemCompleteDeliveryV31Error(RuntimeError):
    """Stable loader error without payload, path, or schema details."""


class FilesystemCompleteDeliverySourceV31:
    """Load catalog/candidate/result/requests from a directory or bundle root."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._index = _index_deliveries(root)

    async def get_delivery(self, rule_version: str) -> StoredCompleteDeliveryV31 | None:
        directory = self._index.get(rule_version)
        if directory is None:
            return None
        return load_stored_complete_delivery_from_directory(directory)


def load_stored_complete_delivery_from_directory(directory: Path) -> StoredCompleteDeliveryV31:
    try:
        return _load_stored_complete_delivery_from_directory(directory)
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValidationError,
        TypeError,
        ValueError,
        KeyError,
        AttributeError,
    ) as error:
        raise FilesystemCompleteDeliveryV31Error(
            "Complete delivery files could not be parsed"
        ) from error


def _load_stored_complete_delivery_from_directory(directory: Path) -> StoredCompleteDeliveryV31:
    catalog = _read_json(directory / "catalog.json")
    candidate = _read_json(directory / "candidate.json")
    result = _read_json(directory / "result.json")
    requests = _read_json(directory / "requests.json")
    manifest = (
        _read_json(directory / "manifest.json") if (directory / "manifest.json").is_file() else {}
    )
    if (
        not isinstance(catalog, dict)
        or not isinstance(candidate, dict)
        or not isinstance(result, dict)
    ):
        raise ValueError("complete delivery JSON objects are required")
    if not isinstance(requests, list):
        raise ValueError("requests.json must be a list of FactBindingRequest objects")
    payloads = [FactBindingRequestV31.model_validate(item) for item in requests]
    wrappers = [
        StoredFactBindingHandoffRequestV31(
            request_id=payload.request_id,
            rule_version=payload.rule_ref.rule_version,
            fact_code=payload.fact.fact_code,
            contract_version="3.1.0",
            payload_sha256=canonical_sha256(payload),
            created_at=CREATED_AT,
            payload=payload,
        )
        for payload in payloads
    ]
    wrappers.sort(key=lambda item: item.request_id)
    rule_version = str(result.get("ruleVersion") or manifest.get("ruleVersion") or "")
    batch = StoredFactBindingHandoffBatchV31(
        mongo_id=rule_version,
        rule_version=rule_version,
        contract_version="3.1.0",
        request_count=len(wrappers),
        request_ids=[item.request_id for item in wrappers],
        batch_sha256=canonical_sha256(
            [
                {"requestId": item.request_id, "payloadSha256": item.payload_sha256}
                for item in wrappers
            ]
        ),
        created_at=CREATED_AT,
        requests=wrappers,
    )
    return StoredCompleteDeliveryV31(
        rule_version=rule_version,
        schema_version=str(result.get("schemaVersion") or manifest.get("schemaVersion") or ""),
        purpose=str(manifest.get("purpose") or result.get("deliveryRef", {}).get("purpose") or ""),
        status=str(result.get("status") or manifest.get("status") or ""),
        executable=bool(result.get("executable")),
        source_file_sha256=_optional_hash(
            manifest.get("sourceFileSha256")
            or candidate.get("sourceIdentity", {}).get("sourceFileSha256")
        ),
        parse_input_sha256=_optional_hash(
            manifest.get("parseInputSha256")
            or candidate.get("sourceIdentity", {}).get("parseInputSha256")
        ),
        catalog_digest=_optional_hash(
            manifest.get("catalogDigest") or catalog.get("catalogDigest")
        ),
        catalog_payload=catalog,
        candidate_payload=candidate,
        result_payload=result,
        batch=batch,
    )


def file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def identity_summary_v31(*, rule_version: str, **fields: object) -> dict[str, object]:
    """Public identity fields only. Callers must not add private payloads."""

    return {"ruleVersion": rule_version, **fields}


def _index_deliveries(root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    directories = [root] if (root / "catalog.json").is_file() else []
    if not directories:
        directories = [
            child
            for child in (root / "report", root / "data")
            if child.is_dir() and (child / "catalog.json").is_file()
        ]
    for directory in directories:
        rule_version = _peek_rule_version(directory)
        if rule_version:
            index[rule_version] = directory
    return index


def _peek_rule_version(directory: Path) -> str:
    for name in ("result.json", "manifest.json"):
        path = directory / name
        if not path.is_file():
            continue
        try:
            payload = _read_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            value = payload.get("ruleVersion")
            if isinstance(value, str) and value:
                return value
    return ""


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _optional_hash(value: object) -> str | None:
    if isinstance(value, str) and len(value) == 64:
        return value
    return None
