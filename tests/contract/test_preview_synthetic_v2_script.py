from __future__ import annotations

import asyncio
import json

import pytest

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


# ---------------------------------------------------------------------------
# CLI error-branch mapping: bounded retries are exhausted when these errors
# surface, so the CLI must only emit safe classifications and exit with the
# documented code, without writing any output files.
# ---------------------------------------------------------------------------

_RAW_EXCEPTION_MARKER = "synthetic-raw-provider-detail"
_SYNTHETIC_SQL_MARKERS = ("SELECT", "total_amount", "synthetic_report_amounts", ":projectId")


def _live_cli_env(monkeypatch) -> None:
    """Force the --live branch hermetically: no real provider, no network."""

    from release_sql_bot.config.settings import Settings

    monkeypatch.setattr(
        preview_synthetic_v2,
        "get_settings",
        lambda: Settings(_env_file=None),
    )
    monkeypatch.setattr(
        preview_synthetic_v2,
        "build_candidate_provider",
        lambda settings: offline_provider(),
    )


def _assert_no_output_files(tmp_path) -> None:
    assert not (tmp_path / "v2-candidate-preview.json").exists()
    assert not (tmp_path / "v2-static-report.json").exists()


def _assert_stderr_has_no_sensitive_content(err: str) -> None:
    for marker in _SYNTHETIC_SQL_MARKERS:
        assert marker not in err
    assert _RAW_EXCEPTION_MARKER not in err


@pytest.mark.parametrize(
    ("error_name", "expected_code", "expected_keyword"),
    [
        ("input_not_ready", 3, "未形成精确 metadataResolved 闭包"),
        ("output_invalid", 4, "未通过严格输出门禁"),
        ("provider_rejected", 5, "拒绝了请求"),
        ("provider_unavailable", 6, "暂不可用"),
    ],
)
def test_main_maps_generation_errors_to_safe_exit_codes_offline(
    monkeypatch, tmp_path, capsys, error_name, expected_code, expected_keyword
) -> None:
    if error_name == "input_not_ready":
        from release_sql_bot.application.candidates_v2 import CandidateInputNotReadyV2Error
        from release_sql_bot.application.metadata_resolution_v2 import resolve_metadata_v2

        resolution = resolve_metadata_v2(build_preview_generation_request().resolution_request)
        error: Exception = CandidateInputNotReadyV2Error(resolution)
        del resolution
    else:
        from release_sql_bot.application.candidates_v2 import (
            CandidateGenerationOutputInvalidV2Error,
            CandidateGenerationProviderRejectedV2Error,
            CandidateGenerationProviderUnavailableV2Error,
        )

        error = {
            "output_invalid": CandidateGenerationOutputInvalidV2Error(_RAW_EXCEPTION_MARKER),
            "provider_rejected": CandidateGenerationProviderRejectedV2Error(_RAW_EXCEPTION_MARKER),
            "provider_unavailable": CandidateGenerationProviderUnavailableV2Error(
                _RAW_EXCEPTION_MARKER
            ),
        }[error_name]

    async def raising_run_preview(provider, *, model, max_retries):
        raise error

    monkeypatch.setattr(preview_synthetic_v2, "run_preview", raising_run_preview)

    exit_code = main(["--output-dir", str(tmp_path)])

    err = capsys.readouterr().err
    assert exit_code == expected_code
    assert expected_keyword in err
    _assert_no_output_files(tmp_path)
    _assert_stderr_has_no_sensitive_content(err)
    if error_name == "output_invalid":
        # 离线模式不得误称“在线模型”，也不得暗示可以直接重跑。
        assert "在线模型" not in err
        assert "重跑" not in err


def test_main_output_invalid_live_hints_reauthorization_not_rerun(
    monkeypatch, tmp_path, capsys
) -> None:
    from release_sql_bot.application.candidates_v2 import (
        CandidateGenerationOutputInvalidV2Error,
    )

    _live_cli_env(monkeypatch)

    async def raising_run_preview(provider, *, model, max_retries):
        raise CandidateGenerationOutputInvalidV2Error(_RAW_EXCEPTION_MARKER)

    monkeypatch.setattr(preview_synthetic_v2, "run_preview", raising_run_preview)

    exit_code = main(["--live", "--output-dir", str(tmp_path)])

    err = capsys.readouterr().err
    assert exit_code == 4
    assert "本次有界尝试内" in err
    assert "重新取得当次任务授权" in err
    assert "重跑" not in err
    _assert_no_output_files(tmp_path)
    _assert_stderr_has_no_sensitive_content(err)


def test_main_live_without_provider_exits_before_any_call(monkeypatch, tmp_path, capsys) -> None:
    _live_cli_env(monkeypatch)
    # provider 工厂在未配置 DeepSeek 时返回 None；同时确保 run_preview 绝不被触达。
    monkeypatch.setattr(
        preview_synthetic_v2,
        "build_candidate_provider",
        lambda settings: None,
    )

    async def fail_if_called(provider, *, model, max_retries):
        raise AssertionError("provider call must not happen without configuration")

    monkeypatch.setattr(preview_synthetic_v2, "run_preview", fail_if_called)

    exit_code = main(["--live", "--output-dir", str(tmp_path)])

    err = capsys.readouterr().err
    assert exit_code == 2
    assert "未配置" in err
    _assert_no_output_files(tmp_path)
    assert "RSB_DEEPSEEK_API_KEY=" not in err
    assert "://" not in err
    assert _RAW_EXCEPTION_MARKER not in err
