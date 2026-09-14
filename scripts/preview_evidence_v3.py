"""Offline V3 evidence-pack demo: run the real M6 evidence loop once.

Fully offline: does NOT read .env, does NOT connect to MongoDB, SQL Server,
or any online model. Reuses the existing synthetic V3 fixtures from
:mod:`scripts.preview_synthetic_v3` (generation request, in-memory handoff
repository, in-memory approval port, fixed provider) and runs the real
:func:`release_sql_bot.application.evidence_loop_v3.run_offline_v3_evidence_loop`
with ``max_retries=0``.

The script assembles an :class:`EvidencePackV3` JSON and writes it to a
git-ignored temporary directory. Only a short status summary is printed to
stdout; no SQL, parameters, raw responses, or connection info are shown.

Exit codes::

    0  evidenceComplete (pack saved)
    2  other terminal stage reached (pack still saved)
    3  output file could not be created/written

Expected command::

    uv run python -m scripts.preview_evidence_v3
    uv run python -m scripts.preview_evidence_v3 --output-dir <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from release_sql_bot.application.ports.candidate_store_v3 import (
    CandidateStoreV3Outcome,
    CandidateStoreV3Status,
)
from release_sql_bot.domain.evidence_pack_v3 import EvidencePackV3
from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3
from scripts.preview_synthetic_v3 import (
    OFFLINE_PROVIDER_NAME,
    _build_generation_request,
    _build_synthetic_approval_port,
    _build_synthetic_handoff_repository,
)
from tests.fakes import FixedCandidateModelProvider

# Request model must match the M6 offline allowlist (exact match).
OFFLINE_REQUEST_MODEL = "fixed-model-v3"

DEFAULT_OUTPUT_DIR = Path(".codex_tmp")
OUTPUT_NAME = "v3-evidence-pack.json"


class InMemoryCandidateStoreV3:
    """Minimal in-memory candidate store for the offline demo.

    Returns the candidate's real ``content_sha256`` on save (never a fake
    fixed hash). No MongoDB adapter is instantiated.
    """

    async def initialize(self) -> None:
        return None

    async def save(self, candidate: SqlTemplateCandidateV3) -> CandidateStoreV3Outcome:
        return CandidateStoreV3Outcome(
            status=CandidateStoreV3Status.STORED,
            content_sha256=candidate.content_sha256,
        )

    async def close(self) -> None:
        return None


def _build_provider(model: str) -> FixedCandidateModelProvider:
    """Build the canonical fixed offline V3 provider.

    Reuses the shared synthetic provider content from the existing V3 preview
    script so the payload matches the M3 gate expectations.
    """
    from release_sql_bot.application.ports.candidates import CandidateModelResponse
    from scripts.preview_synthetic_v3 import _synthetic_provider_content

    return FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider=OFFLINE_PROVIDER_NAME,
                request_id="preview-evidence-v3-001",
                model=model,
                content=_synthetic_provider_content(),
            )
        ]
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Offline V3 evidence-pack demo (no network, no database)."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    return parser.parse_args(argv)


def _print_summary(pack: EvidencePackV3, output_path: Path) -> None:
    """Print a short status summary (no SQL or raw content)."""
    print("V3 离线证据包演示（未启动服务，未执行任何 SQL）。")
    print(f"  stage: {pack.stage}")
    print(f"  storeOutcome: {pack.store_outcome}")
    print(f"  staticStatus: {pack.static_status}")
    print(f"  attemptCount: {pack.attempt_count}")
    print(f"  issueCodes: {list(pack.issue_codes)}")
    print(f"  executable: {pack.executable}")
    print("  verified 仅指本次合成内存仓储核验，不是真实仓储证明。")
    print(f"  evidence pack output: {output_path}")


def _write_pack(pack: EvidencePackV3, output_path: Path) -> None:
    """Write the evidence pack as camelCase JSON."""
    wire = pack.model_dump(by_alias=True, mode="json")
    output_path.write_text(
        json.dumps(wire, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _map_exit_code(pack: EvidencePackV3) -> int:
    """Map the terminal stage to an exit code."""
    if pack.stage == "evidenceComplete":
        return 0
    return 2


def run_evidence_demo(
    *,
    output_dir: Path,
    output_name: str = OUTPUT_NAME,
    handoff_repository_override=None,
    store_override=None,
    model: str = OFFLINE_REQUEST_MODEL,
) -> tuple[int, Path | None, EvidencePackV3 | None]:
    """Run the evidence loop and write the pack. Returns (exit_code, path, pack).

    The output file is checked *before* any orchestration: if it already
    exists the pipeline is not run and ``(3, existing_path, None)`` is
    returned. A write failure after a successful run yields
    ``(3, None, pack)``.
    """
    from release_sql_bot.application.evidence_loop_v3 import run_offline_v3_evidence_loop

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / output_name

    # Do not overwrite an existing file — check before any side effects.
    if output_path.exists():
        return 3, output_path, None

    if handoff_repository_override is not None:
        handoff_repository = handoff_repository_override
    else:
        handoff_repository = _build_synthetic_handoff_repository()

    approval_port = _build_synthetic_approval_port()
    payload = _build_generation_request()

    if store_override is not None:
        store = store_override
    else:
        store = InMemoryCandidateStoreV3()

    provider = _build_provider(model)

    try:
        asyncio.run(store.initialize())
        pack = asyncio.run(
            run_offline_v3_evidence_loop(
                provider=provider,
                payload=payload,
                handoff_repository=handoff_repository,
                approval_port=approval_port,
                store=store,
                model=model,
                max_retries=0,
            )
        )
    finally:
        asyncio.run(store.close())

    try:
        _write_pack(pack, output_path)
    except OSError:
        return 3, None, pack

    return _map_exit_code(pack), output_path, pack


def main(argv: list[str] | None = None) -> int:
    """Run the offline V3 evidence-pack demo."""
    args = _parse_args(argv)

    exit_code, output_path, pack = run_evidence_demo(output_dir=args.output_dir)

    if exit_code == 3:
        if pack is None and output_path is not None:
            # File already existed; pipeline was never run.
            print("输出文件已存在，保留原文件，未写入。", file=sys.stderr)
        else:
            # Pack was generated but could not be written.
            print("输出文件创建或写入失败。", file=sys.stderr)
        return 3

    if pack is not None and output_path is not None:
        _print_summary(pack, output_path)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
