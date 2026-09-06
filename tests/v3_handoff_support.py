from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
V3_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fact-binding-request-3.0.0.synthetic-ready.json"
V3_BATCH_FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "fact-binding-handoff-batch-3.0.0.synthetic.json"
)
CREATED_AT = datetime(2026, 9, 6, tzinfo=UTC)


def valid_v3_binding_payload() -> dict[str, Any]:
    return json.loads(V3_FIXTURE_PATH.read_text(encoding="utf-8"))


def canonical_payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def canonical_batch_sha256(requests: list[dict[str, Any]]) -> str:
    identities = sorted(
        (
            {"requestId": item["request_id"], "payloadSha256": item["payload_sha256"]}
            for item in requests
        ),
        key=lambda identity: identity["requestId"],
    )
    return canonical_payload_sha256(identities)


def with_fact_code(payload: dict[str, Any], fact_code: str) -> dict[str, Any]:
    """Derive a second valid request from the fixture by swapping the fact code."""

    mutated = deepcopy(payload)
    rule_version = mutated["ruleRef"]["ruleVersion"]
    mutated["fact"]["factCode"] = fact_code
    mutated["requestId"] = f"{rule_version}#{fact_code}"
    mutated["mappingCandidate"]["factCode"] = fact_code
    for field in mutated["queryRequirements"]["fields"]:
        if field["fieldId"] == "factValue":
            field["logicalName"] = fact_code
    return mutated


def _request_wrapper(payload: dict[str, Any], created_at: datetime) -> dict[str, Any]:
    return {
        "request_id": payload["requestId"],
        "rule_version": payload["ruleRef"]["ruleVersion"],
        "fact_code": payload["fact"]["factCode"],
        "contract_version": payload["contractVersion"],
        "payload_sha256": canonical_payload_sha256(payload),
        "created_at": created_at,
        "payload": payload,
    }


def valid_batch_document(
    payloads: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a consistent batch wrapper document from one or more payloads."""

    if payloads is None:
        payload_list = [valid_v3_binding_payload()]
    elif isinstance(payloads, dict):
        payload_list = [payloads]
    else:
        payload_list = [deepcopy(payload) for payload in payloads]
    requests = [_request_wrapper(payload, CREATED_AT) for payload in payload_list]
    requests.sort(key=lambda item: item["request_id"])
    return {
        "_id": requests[0]["rule_version"],
        "rule_version": requests[0]["rule_version"],
        "contract_version": "3.0.0",
        "request_count": len(requests),
        "request_ids": [item["request_id"] for item in requests],
        "batch_sha256": canonical_batch_sha256(requests),
        "created_at": CREATED_AT,
        "requests": requests,
    }
