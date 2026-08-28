"""Canonical JSON helpers shared by offline audit boundaries."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from pydantic import BaseModel


def _json_value(value: BaseModel | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, mode="json")
    return value


def canonical_json_bytes(value: BaseModel | dict[str, Any]) -> bytes:
    """Serialize a model or object with the repository's audit hash algorithm."""

    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: BaseModel | dict[str, Any]) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


def canonical_content_sha256(
    value: BaseModel | dict[str, Any],
    *,
    hash_field: str = "contentSha256",
) -> str:
    """Hash an immutable payload while excluding its root self-hash field."""

    payload = dict(_json_value(value))
    payload.pop(hash_field, None)
    return canonical_sha256(payload)
