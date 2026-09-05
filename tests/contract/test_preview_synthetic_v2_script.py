from __future__ import annotations

import asyncio
import json

from release_sql_bot.application.candidates_v2 import CandidateGenerationOutputInvalidV2Error
from scripts import preview_synthetic_v2
from scripts.preview_synthetic_v2 import (
    build_preview_generation_request,
    main,
    offline_provider,
    run_preview,
)
from tests.fakes import FixedCandidateModelProvider


def test_preview_request_matches_offline_regression_fixture() -> None:
    request = build_preview_generation_request()

    assert request.schema_version == "1.0.0"
    assert request.resolution_request.binding_request.fact.fact_code == "report.total_amount"


def test_offline_preview_produces_non_executable_candidate_and_passed_report() -> None:
    candidate, report = asyncio.run(
        run_preview(offline_provider(), model="fixed-offline-v2", max_retries=0)
    )

    assert candidate.status == "candidate"
    assert candidate.executable is False
    assert candidate.review_status == "pending"
    assert candidate.provenance.provider == "fixed-offline"
    assert report.status == "passed"
    assert report.executable is False
    assert report.issues == ()
    assert report.parser_ref.exact_version == "30.17.0"
    assert [item.condition_id for item in report.usage_coverage] == ["amount-positive"]


def test_main_default_mode_is_offline_and_writes_both_outputs(tmp_path) -> None:
    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    candidate_payload = json.loads(
        (tmp_path / "v2-candidate-preview.json").read_text(encoding="utf-8")
    )
    report_payload = json.loads((tmp_path / "v2-static-report.json").read_text(encoding="utf-8"))
    assert candidate_payload["executable"] is False
    assert candidate_payload["reviewStatus"] == "pending"
    assert candidate_payload["provenance"]["provider"] == "fixed-offline"
    assert report_payload["status"] == "passed"
    assert report_payload["executable"] is False


def test_main_reports_clean_error_when_model_output_is_invalid(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(
        preview_synthetic_v2,
        "offline_provider",
        lambda: FixedCandidateModelProvider([CandidateGenerationOutputInvalidV2Error()]),
    )

    exit_code = main(["--output-dir", str(tmp_path)])

    assert exit_code == 4
    assert "未通过严格输出门禁" in capsys.readouterr().err
    assert not (tmp_path / "v2-candidate-preview.json").exists()
