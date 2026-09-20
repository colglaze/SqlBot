"""Optional identity/hash checks against the local 3.1.0 offline package.

Default CI without the package skips these tests. They never write private
payloads into fixtures, logs, or assertions beyond public identity hashes.
The 2026-09-17 pack is superseded and is not treated as the consumable identity.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from pathlib import Path

import pytest

from release_sql_bot.application.complete_delivery_intake_v31 import (
    CompleteDeliveryInvalidV31Error,
    select_complete_delivery_v31,
)
from release_sql_bot.domain.purpose_v31 import DeliveryPurposeV31
from release_sql_bot.infrastructure.complete_delivery_fs_v31 import (
    FilesystemCompleteDeliverySourceV31,
)

OFFLINE_ROOT = Path(
    r"D:\Python\pyWorkspace\RuleAgent\generated-rules\optimization-plan-v31-20260920-align"
)
REPORT_VERSION = "REPORT_RELEASE_ALL_001@20260920T131600000000Z-c049af189fc3-6d94836af30f"
DATA_VERSION = "RAW_DATA_RELEASE_ALL_001@20260920T131600000000Z-c049af189fc3-2845743f259a"
SOURCE_FILE_SHA256 = "c049af189fc3689bac8e96408d9e7239a8c70b66bbcd15829e509c6d524b648f"
PARSE_INPUT_SHA256 = "aebdbf4cf89d469ccd2bfc61d9f70aa24d4361e3ed5fb947c1221f8f8676b662"
BUNDLE_COMMIT = "2240e5bd18e36d17650896a10cc61e1c18e3daa0"
CATALOG_DIGEST = "6d94836af30ff47f969fe779cf19430111977e3d306a0b6ab76a42cd3b5b211b"
HISTORICAL_VERSION = "REPORT_RELEASE_ALL_001@20260905T172407000000Z-f285643e5b2b-82dbd05a800a"
SUPERSEDED_20260917_VERSION = (
    "REPORT_RELEASE_ALL_001@20260917T140000000000Z-c049af189fc3-74a69e146b34"
)
STAGE_ORDER = (
    "stateGuards",
    "prerequisites",
    "eligibility",
    "postGates",
    "exclusions",
)

pytestmark = pytest.mark.skipif(
    not OFFLINE_ROOT.is_dir(),
    reason="offline 2026-09-20 complete-delivery package is not present",
)


def _load_json(path: Path) -> object:
    import json

    return json.loads(path.read_text(encoding="utf-8"))


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_offline_summary_and_manifest_identity_only() -> None:
    summary = _load_json(OFFLINE_ROOT / "summary.json")
    report_manifest = _load_json(OFFLINE_ROOT / "report" / "manifest.json")
    data_manifest = _load_json(OFFLINE_ROOT / "data" / "manifest.json")
    assert isinstance(summary, dict)
    assert isinstance(report_manifest, dict)
    assert isinstance(data_manifest, dict)
    assert summary["sourceFileSha256"] == SOURCE_FILE_SHA256
    assert summary["parseInputSha256"] == PARSE_INPUT_SHA256
    assert summary["reportRuleVersion"] == REPORT_VERSION
    assert summary["dataRuleVersion"] == DATA_VERSION
    assert summary["reportRequestCount"] == 33
    assert summary["dataRequestCount"] == 18
    assert report_manifest["ruleVersion"] == REPORT_VERSION
    assert report_manifest["schemaVersion"] == "3.1.0"
    assert report_manifest["purpose"] == "optimization-plan-generation"
    assert report_manifest["executable"] is False
    assert report_manifest["status"] == "draft"
    assert report_manifest["requestCount"] == 33
    assert report_manifest["testCaseCount"] == 75
    assert report_manifest["catalogDigest"] == CATALOG_DIGEST
    assert report_manifest["bundleCommit"] == BUNDLE_COMMIT
    assert data_manifest["ruleVersion"] == DATA_VERSION
    assert data_manifest["schemaVersion"] == "3.1.0"
    assert data_manifest["purpose"] == "optimization-plan-generation"
    assert data_manifest["executable"] is False
    assert data_manifest["requestCount"] == 18
    assert data_manifest["testCaseCount"] == 29
    assert HISTORICAL_VERSION not in (summary["reportRuleVersion"], summary["dataRuleVersion"])
    assert SUPERSEDED_20260917_VERSION not in (
        summary["reportRuleVersion"],
        summary["dataRuleVersion"],
    )


@pytest.mark.parametrize("kind", ["report", "data"])
def test_offline_file_hashes_match_manifest(kind: str) -> None:
    directory = OFFLINE_ROOT / kind
    manifest = _load_json(directory / "manifest.json")
    assert isinstance(manifest, dict)
    recorded = manifest["fileSha256"]
    assert isinstance(recorded, dict)
    for name, expected in recorded.items():
        actual = _file_sha256(directory / name)
        assert actual == expected


def test_offline_report_candidate_has_five_stages_without_dumping_facts() -> None:
    candidate = _load_json(OFFLINE_ROOT / "report" / "candidate.json")
    assert isinstance(candidate, dict)
    assert candidate["contractVersion"] == "3.1.0"
    stages = candidate["stages"]
    assert isinstance(stages, list)
    assert [item["stage"] for item in stages] == list(STAGE_ORDER)
    result = _load_json(OFFLINE_ROOT / "report" / "result.json")
    assert isinstance(result, dict)
    assert result["schemaVersion"] == "3.1.0"
    assert result["ruleVersion"] == REPORT_VERSION
    assert result["executable"] is False
    requests = _load_json(OFFLINE_ROOT / "report" / "requests.json")
    assert isinstance(requests, list)
    assert len(requests) == 33


def test_offline_report_select_delivery_returns_five_stage_tree() -> None:
    source = FilesystemCompleteDeliverySourceV31(OFFLINE_ROOT)
    delivery = asyncio.run(
        select_complete_delivery_v31(
            source,
            purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
            rule_version=REPORT_VERSION,
        )
    )
    assert delivery.consumable is True
    assert delivery.missing == ()
    assert delivery.schema_version == "3.1.0"
    assert delivery.executable is False
    assert list(delivery.stage_names) == list(STAGE_ORDER)
    assert delivery.request_count == 33
    assert delivery.source_file_sha256 == SOURCE_FILE_SHA256


def test_offline_data_select_delivery_returns_five_stage_tree() -> None:
    source = FilesystemCompleteDeliverySourceV31(OFFLINE_ROOT)
    delivery = asyncio.run(
        select_complete_delivery_v31(
            source,
            purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
            rule_version=DATA_VERSION,
        )
    )
    assert delivery.consumable is True
    assert list(delivery.stage_names) == list(STAGE_ORDER)
    assert delivery.request_count == 18
    assert delivery.schema_version == "3.1.0"


def test_offline_historical_version_sql_compilation_fails_closed() -> None:
    source = FilesystemCompleteDeliverySourceV31(OFFLINE_ROOT)
    with pytest.raises(CompleteDeliveryInvalidV31Error, match="requested purpose"):
        asyncio.run(
            select_complete_delivery_v31(
                source,
                purpose=DeliveryPurposeV31.SQL_COMPILATION,
                rule_version=HISTORICAL_VERSION,
            )
        )


def test_offline_report_compile_without_mapping_is_blocked() -> None:
    from release_sql_bot.application.view_shaped_compile_v31 import (
        ViewShapedSqlCompilationBlockedV31,
        compile_view_shaped_sql_v31,
    )

    source = FilesystemCompleteDeliverySourceV31(OFFLINE_ROOT)
    delivery = asyncio.run(
        select_complete_delivery_v31(
            source,
            purpose=DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION,
            rule_version=REPORT_VERSION,
        )
    )
    with pytest.raises(ViewShapedSqlCompilationBlockedV31) as error:
        compile_view_shaped_sql_v31(delivery)
    assert "METADATA_REVIEW" in error.value.blockers
    assert "sql_template" not in dir(error.value)
