"""Contract tests for the offline V3 evidence-pack demo script.

Verifies that :func:`scripts.preview_evidence_v3.main` runs the real M6
evidence loop end-to-end, produces a contract-valid ``EvidencePackV3``
JSON, handles the failure and file-exists paths, and never leaks SQL or
synthetic markers.

All tests use ``tmp_path`` and fixed fakes. No network, no .env, no
MongoDB/SQL Server/online model.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from release_sql_bot.application.ports.candidate_store_v3 import CandidateStoreV3Status
from release_sql_bot.domain.evidence_pack_v3 import EvidencePackV3
from release_sql_bot.domain.sql_candidates_v3 import SqlTemplateCandidateV3
from scripts import preview_evidence_v3
from scripts.preview_evidence_v3 import (
    OUTPUT_NAME,
    InMemoryCandidateStoreV3,
    run_evidence_demo,
)

# Synthetic markers that must never appear in output.
_MARKER_SQL = "synthetic_value"
_MARKER_TABLE = "synthetic_table"
_MARKER_FROM = "FROM dbo"
_MARKER_SELECT = "SELECT"


class _CountingStore(InMemoryCandidateStoreV3):
    """Store that counts save calls and records close."""

    def __init__(self, status: CandidateStoreV3Status = CandidateStoreV3Status.STORED) -> None:
        self._status = status
        self.save_count = 0
        self.closed = False

    async def save(self, candidate: SqlTemplateCandidateV3):  # type: ignore[override]
        from release_sql_bot.application.ports.candidate_store_v3 import (
            CandidateStoreV3Outcome,
        )

        self.save_count += 1
        return CandidateStoreV3Outcome(
            status=self._status,
            content_sha256=candidate.content_sha256,
        )

    async def close(self) -> None:
        self.closed = True


class _EmptyHandoffRepository:
    """Handoff repository that returns no batch (triggers blockedUpstream)."""

    async def get_batch_by_rule_version(self, rule_version: str) -> None:
        return None


# ---------------------------------------------------------------------------
# 1. Default success path (A)
# ---------------------------------------------------------------------------


def test_default_success_produces_valid_evidence_complete_pack(tmp_path: Path) -> None:
    """Default run yields a contract-valid evidenceComplete pack."""
    exit_code, output_path, pack = run_evidence_demo(output_dir=tmp_path)

    assert exit_code == 0
    assert output_path is not None
    assert output_path.exists()
    assert pack is not None
    assert pack.stage == "evidenceComplete"
    assert pack.store_outcome == "stored"
    assert pack.static_status == "passed"
    assert pack.attempt_count == 1
    assert pack.executable is False

    # Round-trip through the contract
    wire = json.loads(output_path.read_text(encoding="utf-8"))
    rebuilt = EvidencePackV3.model_validate(wire)
    assert rebuilt.stage == "evidenceComplete"
    assert rebuilt.executable is False

    # Safe metadata present
    assert pack.provider == "fixed-offline-v3"
    assert pack.model == "fixed-model-v3"
    assert pack.prompt_version == "sqlserver-fact-candidate-v3.1"
    assert pack.candidate_content_sha256 is not None
    assert pack.static_report_sha256 is not None


# ---------------------------------------------------------------------------
# 2. --output-dir via main() (B)
# ---------------------------------------------------------------------------


def test_output_dir_flag_via_main(tmp_path: Path) -> None:
    """--output-dir places the file in the requested directory (via main)."""
    custom_dir = tmp_path / "custom-sub"
    exit_code = preview_evidence_v3.main(["--output-dir", str(custom_dir)])

    assert exit_code == 0
    output_path = custom_dir / OUTPUT_NAME
    assert output_path.exists()
    wire = json.loads(output_path.read_text(encoding="utf-8"))
    pack = EvidencePackV3.model_validate(wire)
    assert pack.stage == "evidenceComplete"


# ---------------------------------------------------------------------------
# 3. File already exists → exit 3, original bytes unchanged, no pipeline (C)
# ---------------------------------------------------------------------------


def test_existing_file_exit_3_no_pipeline(tmp_path: Path) -> None:
    """If the output file already exists, exit 3 and do NOT run the pipeline."""
    existing = tmp_path / OUTPUT_NAME
    original_bytes = b"original-content-that-must-not-change"
    existing.write_bytes(original_bytes)

    store = _CountingStore()

    with (
        patch(
            "scripts.preview_evidence_v3.InMemoryCandidateStoreV3",
            return_value=store,
        ),
        patch(
            "scripts.preview_evidence_v3._build_provider",
            side_effect=AssertionError("provider must not be built when file exists"),
        ) as mock_build_provider,
    ):
        exit_code = preview_evidence_v3.main(["--output-dir", str(tmp_path)])

    assert exit_code == 3
    # Original file untouched
    assert existing.read_bytes() == original_bytes
    # Pipeline was never run: provider factory never called, save not called, store not initialized
    mock_build_provider.assert_not_called()
    assert store.save_count == 0
    assert store.closed is False


def test_existing_file_stderr_message(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """File-exists path prints the 'already exists' message to stderr."""
    existing = tmp_path / OUTPUT_NAME
    existing.write_bytes(b"original")

    exit_code = preview_evidence_v3.main(["--output-dir", str(tmp_path)])

    assert exit_code == 3
    captured = capsys.readouterr()
    assert "已存在" in captured.err


# ---------------------------------------------------------------------------
# 4. Empty batch → blockedUpstream, exit 2, provider=0, save=0 (D)
# ---------------------------------------------------------------------------


def test_empty_batch_blocked_upstream_exit_2_no_provider_no_save(tmp_path: Path) -> None:
    """Missing batch blocks upstream; provider and save are never called."""
    store = _CountingStore()

    with (
        patch(
            "scripts.preview_evidence_v3._build_synthetic_handoff_repository",
            return_value=_EmptyHandoffRepository(),
        ),
        patch(
            "scripts.preview_evidence_v3.InMemoryCandidateStoreV3",
            return_value=store,
        ),
    ):
        exit_code, output_path, pack = run_evidence_demo(output_dir=tmp_path)

    assert exit_code == 2
    assert output_path is not None
    assert pack is not None
    assert pack.stage == "blockedUpstream"
    assert pack.attempt_count == 0
    assert pack.provider is None
    assert pack.model is None
    assert pack.prompt_version is None
    assert pack.candidate_content_sha256 is None
    # Provider and save were never called
    assert store.save_count == 0

    # Pack is still contract-valid
    wire = json.loads(output_path.read_text(encoding="utf-8"))
    EvidencePackV3.model_validate(wire)


# ---------------------------------------------------------------------------
# 5. Store failed → candidateGenerated, exit 2 (E)
# ---------------------------------------------------------------------------


def test_store_failed_candidate_generated_exit_2(tmp_path: Path) -> None:
    """Store failure skips static validation; pack retains candidate hash + code."""
    store = _CountingStore(CandidateStoreV3Status.FAILED)

    with patch(
        "release_sql_bot.application.evidence_loop_v3.validate_sql_candidate_v3"
    ) as mock_validate:
        exit_code, output_path, pack = run_evidence_demo(
            output_dir=tmp_path,
            store_override=store,
        )
        mock_validate.assert_not_called()

    assert exit_code == 2
    assert output_path is not None
    assert pack is not None
    assert pack.stage == "candidateGenerated"
    assert pack.store_outcome == "failed"
    assert pack.static_status is None
    assert pack.candidate_content_sha256 is not None
    assert "M6_STORE_SAVE_FAILED" in pack.issue_codes

    # Pack is still contract-valid
    wire = json.loads(output_path.read_text(encoding="utf-8"))
    EvidencePackV3.model_validate(wire)


# ---------------------------------------------------------------------------
# 6. Store closed on all paths (F)
# ---------------------------------------------------------------------------


def test_store_closed_on_success_path(tmp_path: Path) -> None:
    """Store.close() is called after a successful run."""
    store = _CountingStore(CandidateStoreV3Status.STORED)
    exit_code, _, _ = run_evidence_demo(output_dir=tmp_path, store_override=store)
    assert exit_code == 0
    assert store.closed is True


def test_store_closed_on_failure_path(tmp_path: Path) -> None:
    """Store.close() is called even when the pipeline fails upstream."""
    store = _CountingStore(CandidateStoreV3Status.STORED)
    exit_code, _, _ = run_evidence_demo(
        output_dir=tmp_path,
        handoff_repository_override=_EmptyHandoffRepository(),
        store_override=store,
    )
    assert exit_code == 2
    assert store.closed is True


# ---------------------------------------------------------------------------
# 7. No SQL or markers in stdout/stderr or evidence pack (G)
# ---------------------------------------------------------------------------


def test_no_sensitive_markers_via_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Evidence pack, stdout, stderr, and repr must not contain SQL or markers."""
    exit_code = preview_evidence_v3.main(["--output-dir", str(tmp_path)])

    assert exit_code == 0
    output_path = tmp_path / OUTPUT_NAME
    pack_json = output_path.read_text(encoding="utf-8")

    # All four markers must be absent from every surface.
    markers = (_MARKER_SQL, _MARKER_TABLE, _MARKER_FROM, _MARKER_SELECT)

    # Check the evidence pack JSON
    for marker in markers:
        assert marker not in pack_json, f"marker leaked into JSON: {marker}"

    # Check stdout and stderr
    captured = capsys.readouterr()
    for marker in markers:
        assert marker not in captured.out, f"marker leaked into stdout: {marker}"
        assert marker not in captured.err, f"marker leaked into stderr: {marker}"

    # Check repr of the parsed pack
    pack = EvidencePackV3.model_validate_json(pack_json)
    pack_repr = repr(pack)
    for marker in markers:
        assert marker not in pack_repr, f"marker leaked into repr: {marker}"


# ---------------------------------------------------------------------------
# 8. main() exit codes (H)
# ---------------------------------------------------------------------------


def test_main_exit_code_matches_stage(tmp_path: Path) -> None:
    """main() returns 0 for evidenceComplete, 2 for other stages."""
    # Success path
    assert preview_evidence_v3.main(["--output-dir", str(tmp_path)]) == 0

    # Failure path (empty batch, unique filename to avoid exit 3)
    sub = tmp_path / "fail"
    with patch(
        "scripts.preview_evidence_v3._build_synthetic_handoff_repository",
        return_value=_EmptyHandoffRepository(),
    ):
        assert preview_evidence_v3.main(["--output-dir", str(sub)]) == 2
