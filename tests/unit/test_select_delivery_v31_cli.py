from __future__ import annotations

import json
from pathlib import Path

import pytest

from release_sql_bot.__main__ import main
from release_sql_bot.domain.purpose_v31 import (
    HISTORICAL_REPORT_RELEASE_V3_RULE_VERSION,
    DeliveryPurposeV31,
)
from tests.v31_delivery_support import (
    build_synthetic_complete_delivery,
    build_synthetic_mapping_bundle,
)


def _write_delivery(root: Path) -> str:
    stored, meta = build_synthetic_complete_delivery()
    root.mkdir(parents=True, exist_ok=True)
    (root / "catalog.json").write_text(
        json.dumps(stored.catalog_payload, ensure_ascii=False), encoding="utf-8"
    )
    (root / "candidate.json").write_text(
        json.dumps(stored.candidate_payload, ensure_ascii=False), encoding="utf-8"
    )
    (root / "result.json").write_text(
        json.dumps(stored.result_payload, ensure_ascii=False), encoding="utf-8"
    )
    requests = [
        item.payload.model_dump(by_alias=True, mode="json") for item in stored.batch.requests
    ]
    (root / "requests.json").write_text(json.dumps(requests, ensure_ascii=False), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "ruleVersion": stored.rule_version,
                "schemaVersion": stored.schema_version,
                "purpose": stored.purpose,
                "status": stored.status,
                "executable": stored.executable,
                "sourceFileSha256": stored.source_file_sha256,
                "parseInputSha256": stored.parse_input_sha256,
                "catalogDigest": stored.catalog_digest,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return meta["ruleVersion"]


def test_select_delivery_cli_prints_identity_without_sql(tmp_path, capsys) -> None:
    delivery_root = tmp_path / "synthetic-v31"
    rule_version = _write_delivery(delivery_root)

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "select-delivery-v31",
                "--purpose",
                DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION.value,
                "--rule-version",
                rule_version,
                "--delivery-root",
                str(delivery_root),
            ]
        )

    assert exit_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["consumable"] is True
    assert payload["sqlGenerated"] is False
    assert payload["stageNames"] == [
        "stateGuards",
        "prerequisites",
        "eligibility",
        "postGates",
        "exclusions",
    ]
    assert "METADATA_REVIEW" in payload["compilationBlockers"]
    dumped = json.dumps(payload)
    assert "SELECT" not in dumped
    assert "member_keys" not in dumped
    assert "status_code" not in dumped


def test_select_delivery_cli_rejects_historical_sql_compilation(tmp_path, capsys) -> None:
    delivery_root = tmp_path / "synthetic-v31"
    _write_delivery(delivery_root)

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "select-delivery-v31",
                "--purpose",
                DeliveryPurposeV31.SQL_COMPILATION.value,
                "--rule-version",
                HISTORICAL_REPORT_RELEASE_V3_RULE_VERSION,
                "--delivery-root",
                str(delivery_root),
            ]
        )

    assert exit_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["sqlGenerated"] is False
    assert "error" in payload


def test_select_delivery_cli_requires_delivery_root_or_mongodb(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "select-delivery-v31",
                "--purpose",
                DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION.value,
                "--rule-version",
                "SYNTHETIC_REPORT_RELEASE@unused",
            ]
        )

    assert exit_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"]
    assert "sqlGenerated" not in payload or payload.get("sqlGenerated") is False


def test_select_delivery_cli_from_mongodb_without_config(capsys, monkeypatch) -> None:
    from release_sql_bot.config.settings import Settings

    monkeypatch.setattr(
        "release_sql_bot.__main__.get_settings",
        lambda: Settings(_env_file=None),
    )

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "select-delivery-v31",
                "--purpose",
                DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION.value,
                "--rule-version",
                "SYNTHETIC_REPORT_RELEASE@unused",
                "--from-mongodb",
            ]
        )

    assert exit_info.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["sqlGenerated"] is False
    assert "mongodb://" not in json.dumps(payload)


def test_select_delivery_cli_with_mapping_prints_hashes_not_sql(tmp_path, capsys) -> None:
    delivery_root = tmp_path / "synthetic-v31"
    rule_version = _write_delivery(delivery_root)
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(
        json.dumps(
            build_synthetic_mapping_bundle().model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "select-delivery-v31",
                "--purpose",
                DeliveryPurposeV31.OPTIMIZATION_PLAN_GENERATION.value,
                "--rule-version",
                rule_version,
                "--delivery-root",
                str(delivery_root),
                "--mapping-grants",
                str(mapping_path),
            ]
        )

    assert exit_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["sqlGenerated"] is True
    assert payload["executable"] is False
    assert payload["staticStatus"] == "passed"
    assert payload["contentSha256"]
    assert payload["mappingSha256"]
    dumped = json.dumps(payload)
    assert "SELECT" not in dumped
    assert "sqlTemplate" not in dumped
    assert "OUTER APPLY" not in dumped
