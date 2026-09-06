"""Synthetic V2 SQL candidate preview over the current 0.3.0 contracts.

Re-implementation of the preview script lost in the 2026-09-01 history rewrite
(see docs/bugs/BUG-20260901-01-v2-live-provider-coverage-declaration.md); this
is a replacement built against the current contracts, not a restored original.

The script never starts the service and never executes SQL. Default mode is a
fully offline self-test: it builds the same synthetic, desensitized
``GenerateSqlCandidateRequestV2`` used by the offline regression fixtures, runs
one generation attempt against a fixed offline provider, and evaluates the real
Phase 4 SQLGlot AST gate locally. ``--live`` instead performs one logical
generation run against the configured DeepSeek provider with the configured
bounded retry policy; running ``--live`` requires the user's explicit
authorization for that task and is still only a preview.

Outputs are written as ``v2-candidate-preview.json`` and
``v2-static-report.json`` into a git-ignored directory (default ``.codex_tmp``).
Candidates and reports remain ``executable=false`` and are not approved SQL.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from release_sql_bot.application.candidates_v2 import (
    CandidateGenerationOutputInvalidV2Error,
    CandidateGenerationProviderRejectedV2Error,
    CandidateGenerationProviderUnavailableV2Error,
    CandidateInputNotReadyV2Error,
    generate_sql_candidate_v2,
)
from release_sql_bot.application.ports.candidates import (
    CandidateModelProvider,
    CandidateModelResponse,
)
from release_sql_bot.application.sql_validation import validate_sql_candidate_v2
from release_sql_bot.config.settings import get_settings
from release_sql_bot.domain.sql_candidates_v2 import (
    GenerateSqlCandidateRequestV2,
    SqlTemplateCandidateV2,
)
from release_sql_bot.domain.sql_validation import (
    SqlStaticValidationReportV2,
    ValidateSqlCandidateRequestV2,
)
from release_sql_bot.infrastructure.llm import build_candidate_provider
from release_sql_bot.infrastructure.sql import build_sql_dialect_inspector
from tests.fakes import FixedCandidateModelProvider
from tests.phase2g_support import (
    generate_candidate_request_payload,
    valid_generated_candidate_v2_content,
)

DEFAULT_OUTPUT_DIR = Path(".codex_tmp")
CANDIDATE_OUTPUT_NAME = "v2-candidate-preview.json"
REPORT_OUTPUT_NAME = "v2-static-report.json"

OFFLINE_PROVIDER_NAME = "fixed-offline"
OFFLINE_MODEL_NAME = "fixed-offline-v2"
OFFLINE_REQUEST_ID = "preview-synthetic-offline-001"


def build_preview_generation_request() -> GenerateSqlCandidateRequestV2:
    """Build the synthetic desensitized generation request shared with tests."""
    return GenerateSqlCandidateRequestV2.model_validate(generate_candidate_request_payload())


def offline_provider() -> FixedCandidateModelProvider:
    """Return a fixed offline provider that emits the canonical valid payload."""
    return FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider=OFFLINE_PROVIDER_NAME,
                request_id=OFFLINE_REQUEST_ID,
                model=OFFLINE_MODEL_NAME,
                content=valid_generated_candidate_v2_content(),
            )
        ]
    )


async def run_preview(
    provider: CandidateModelProvider,
    *,
    model: str,
    max_retries: int,
) -> tuple[SqlTemplateCandidateV2, SqlStaticValidationReportV2]:
    """Run one generation attempt and the real Phase 4 gate for the same input."""
    generation = build_preview_generation_request()
    candidate = await generate_sql_candidate_v2(
        provider,
        generation,
        model=model,
        max_retries=max_retries,
    )
    validation_request = ValidateSqlCandidateRequestV2.model_validate(
        {
            "schemaVersion": "1.0.0",
            "generationRequest": generation.model_dump(by_alias=True, mode="json"),
            "candidate": candidate.model_dump(by_alias=True, mode="json"),
        }
    )
    report = validate_sql_candidate_v2(build_sql_dialect_inspector(), validation_request)
    return candidate, report


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_outputs(
    candidate: SqlTemplateCandidateV2,
    report: SqlStaticValidationReportV2,
    output_dir: Path,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = output_dir / CANDIDATE_OUTPUT_NAME
    report_path = output_dir / REPORT_OUTPUT_NAME
    _write_json(candidate_path, candidate.model_dump(by_alias=True, mode="json"))
    _write_json(report_path, report.model_dump(by_alias=True, mode="json"))
    return candidate_path, report_path


def _print_summary(
    candidate: SqlTemplateCandidateV2,
    report: SqlStaticValidationReportV2,
    candidate_path: Path,
    report_path: Path,
) -> None:
    print("V2 合成预览完成（未启动服务，未执行任何 SQL）。")
    print(f"  provider/model: {candidate.provenance.provider}/{candidate.provenance.model}")
    print(f"  prompt version: {candidate.provenance.prompt_version}")
    print(f"  candidate content sha256: {candidate.content_sha256}")
    print(f"  static gate status: {report.status}（issues={len(report.issues)}）")
    print(f"  candidate output: {candidate_path}")
    print(f"  static report output: {report_path}")
    print("候选与报告固定 executable=false / reviewStatus=pending，不得复制到数据库客户端执行。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the synthetic V2 candidate preview (offline by default).",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help=(
            "Call the configured DeepSeek provider once instead of the offline "
            "fixed provider. Requires explicit per-task user authorization."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the two JSON outputs (default: .codex_tmp).",
    )
    args = parser.parse_args(argv)

    if args.live:
        settings = get_settings()
        provider = build_candidate_provider(settings)
        if provider is None:
            print(
                "DeepSeek provider 未配置（需要 RSB_DEEPSEEK_API_KEY/BASE_URL/MODEL）；"
                "离线自测请去掉 --live。",
                file=sys.stderr,
            )
            return 2
        model = settings.deepseek_model
        max_retries = settings.deepseek_max_retries
    else:
        provider = offline_provider()
        model = OFFLINE_MODEL_NAME
        max_retries = 0

    try:
        candidate, report = asyncio.run(run_preview(provider, model=model, max_retries=max_retries))
    except CandidateInputNotReadyV2Error:
        print("合成输入未形成精确 metadataResolved 闭包，预览中止。", file=sys.stderr)
        return 3
    except CandidateGenerationOutputInvalidV2Error:
        # 到达这里时内部有界重试（maxRetries + 1）已经耗尽，不能暗示自动重跑。
        if args.live:
            print(
                "模型响应在本次有界尝试内未通过严格输出门禁，未形成有效候选；"
                "如需再次调用在线模型，须重新取得当次任务授权。",
                file=sys.stderr,
            )
        else:
            print(
                "候选生成响应未通过严格输出门禁，未形成有效候选。",
                file=sys.stderr,
            )
        return 4
    except CandidateGenerationProviderRejectedV2Error:
        print(
            "provider 拒绝了请求（鉴权、余额或参数问题）；请检查 DeepSeek 配置。",
            file=sys.stderr,
        )
        return 5
    except CandidateGenerationProviderUnavailableV2Error:
        print("provider 暂不可用（超时、限流或网络问题，且有界重试已耗尽）。", file=sys.stderr)
        return 6
    candidate_path, report_path = write_outputs(candidate, report, args.output_dir)
    _print_summary(candidate, report, candidate_path, report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
