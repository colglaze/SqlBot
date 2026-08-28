from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
V2_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fact-binding-request-2.0.0.synthetic-blocked.json"


def valid_v2_binding_payload() -> dict[str, Any]:
    return json.loads(V2_FIXTURE_PATH.read_text(encoding="utf-8"))


def canonical_payload_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def valid_handoff_document(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_payload = deepcopy(payload or valid_v2_binding_payload())
    return {
        "_id": resolved_payload["requestId"],
        "request_id": resolved_payload["requestId"],
        "rule_version": resolved_payload["ruleRef"]["ruleVersion"],
        "fact_code": resolved_payload["fact"]["factCode"],
        "contract_version": resolved_payload["contractVersion"],
        "payload_sha256": canonical_payload_sha256(resolved_payload),
        "created_at": datetime(2026, 8, 28, tzinfo=UTC),
        "payload": resolved_payload,
    }
