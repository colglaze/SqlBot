"""M6 second slice: generate-v3 CLI entry (offline).

All tests inject fake ports and a fixed provider. No real MongoDB, no
online model, no .env.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from release_sql_bot.__main__ import main
from release_sql_bot.application.evidence_loop_v3 import (
    TrustedIdentifiersV3,
    _validate_model_identity,
    _validate_prompt_version,
    _validate_provider_identity,
)
from release_sql_bot.application.ports.database import DatabaseStatus
from release_sql_bot.application.runtime import DatabaseResources
from release_sql_bot.config.settings import Settings, get_settings
from release_sql_bot.domain.evidence_pack_v3 import EvidencePackV3
from release_sql_bot.infrastructure.database.disabled import DisabledDatabaseInitializer
from tests.unit.test_candidate_persistence_v3 import FakeStoreV3
from tests.unit.test_candidates_v3 import (
    _build_synthetic_approval_port,
    _build_synthetic_closure_wire,
    _build_synthetic_handoff_repository,
    _valid_provider,
)
from tests.unit.test_evidence_loop_v3 import _abs_provider
from tests.v3_metadata_support import valid_resolve_metadata_request_v3_wire

_MARKER_URI = "mongodb://MARKER_URI_9b2e:MARKER_URI_9b2e@127.0.0.1:27017/db"
_SQL_LEAK_MARKERS = (
    "synthetic_value",
    "synthetic_table",
    "FROM dbo",
    "SELECT ABS",
    "SELECT t.synthetic_value",
    ":syntheticKey",
)


def _valid_input_wire() -> dict[str, Any]:
    """Start from the shared M2 fixture, then align handoff batch hash.

    ``valid_resolve_metadata_request_v3_wire()`` is the CLI input contract.
    The injected ``_build_synthetic_handoff_repository`` recomputes
    ``batchSha256`` the same way intake does, so the file must carry that
    corrected hash or M3 handoff verification would fail after M2.
    """
    wire = valid_resolve_metadata_request_v3_wire()
    wire["handoffClosure"]["batchSha256"] = _build_synthetic_closure_wire()["batchSha256"]
    return wire


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _run_cli(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as exc_info:
        main(argv)
    code = exc_info.value.code
    return 0 if code is None else int(code)


def _authorized_args(input_path: Path, output_path: Path) -> list[str]:
    return [
        "generate-v3",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--authorize-online-provider",
    ]


def _patch_assembly(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: Any | None = None,
    store: FakeStoreV3 | None = None,
    approval_port: Any | None = None,
    initializer: Any | None = None,
    omit_approval_port: bool = False,
) -> tuple[Any, FakeStoreV3]:
    resolved_provider = provider if provider is not None else _valid_provider()
    resolved_store = store if store is not None else FakeStoreV3()
    resolved_approval = None
    if not omit_approval_port:
        resolved_approval = (
            approval_port if approval_port is not None else _build_synthetic_approval_port()
        )
    resources = DatabaseResources(
        initializer=(initializer if initializer is not None else DisabledDatabaseInitializer()),
        rule_repository=None,
        fact_binding_batch_repository_v3=_build_synthetic_handoff_repository(),
        approval_port_v3=resolved_approval,
        candidate_store_v3=resolved_store,
    )
    monkeypatch.setattr(
        "release_sql_bot.__main__.build_database_resources",
        lambda settings: resources,
    )
    monkeypatch.setattr(
        "release_sql_bot.__main__.build_candidate_provider",
        lambda settings: resolved_provider,
    )
    return resolved_provider, resolved_store


def _trusted_from_settings(settings: Settings) -> TrustedIdentifiersV3:
    return TrustedIdentifiersV3(
        providers=frozenset(settings.evidence_trusted_providers),
        models=frozenset(settings.evidence_trusted_models),
        prompt_versions=frozenset(settings.evidence_trusted_prompt_versions),
    )


@pytest.fixture()
def cli_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    input_path = tmp_path / "resolve-request.json"
    output_path = tmp_path / "evidence-pack.json"
    _write_json(input_path, _valid_input_wire())
    return input_path, output_path


def test_missing_authorize_flag_exits_4_without_provider_or_output(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, output_path = cli_paths
    provider, _store = _patch_assembly(monkeypatch)
    path_cls = type(input_path)
    original_is_file = path_cls.is_file
    is_file_calls = {"count": 0}

    def counting_is_file(self: Path) -> bool:
        is_file_calls["count"] += 1
        return original_is_file(self)

    monkeypatch.setattr(path_cls, "is_file", counting_is_file)
    code = _run_cli(["generate-v3", "--input", str(input_path), "--output", str(output_path)])
    assert code == 4
    assert is_file_calls["count"] == 0
    assert len(provider.calls) == 0
    assert output_path.exists() is False


def test_input_isfile_permission_error_exits_4_without_leak(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    provider = _valid_provider()
    factory_calls = {"db": 0, "provider": 0}

    def count_db(_settings: Settings) -> Any:
        factory_calls["db"] += 1
        raise AssertionError("database factory must not run after input stat failure")

    def count_provider(_settings: Settings) -> Any:
        factory_calls["provider"] += 1
        return provider

    monkeypatch.setattr("release_sql_bot.__main__.build_database_resources", count_db)
    monkeypatch.setattr("release_sql_bot.__main__.build_candidate_provider", count_provider)

    path_cls = type(input_path)

    def boom_is_file(_self: Path) -> bool:
        raise PermissionError(errno.EACCES, f"合成标记 {_MARKER_STAT}")

    monkeypatch.setattr(path_cls, "is_file", boom_is_file)
    code = _run_cli(_authorized_args(input_path, output_path))
    captured = capsys.readouterr()
    text = captured.out + captured.err
    assert code == 4
    assert factory_calls == {"db": 0, "provider": 0}
    assert len(provider.calls) == 0
    assert output_path.exists() is False
    assert _MARKER_STAT not in text
    assert "合成标记" not in text
    assert "Traceback (most recent call last)" not in text
    assert "PermissionError" not in text
    assert "runId" not in captured.out
    assert "authorizedOnlineProvider" not in captured.out


def test_missing_input_file_exits_4(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _input_path, output_path = cli_paths
    provider, _store = _patch_assembly(monkeypatch)
    code = _run_cli(_authorized_args(output_path.parent / "missing.json", output_path))
    assert code == 4
    assert len(provider.calls) == 0
    assert output_path.exists() is False


def test_invalid_json_exits_4(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, output_path = cli_paths
    input_path.write_text("{", encoding="utf-8")
    provider, _store = _patch_assembly(monkeypatch)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 4
    assert len(provider.calls) == 0
    assert output_path.exists() is False


def test_invalid_contract_exits_4_without_echoing_input(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    payload = {"schemaVersion": "1.0.0"}
    _write_json(input_path, payload)
    provider, _store = _patch_assembly(monkeypatch)
    code = _run_cli(_authorized_args(input_path, output_path))
    captured = capsys.readouterr()
    assert code == 4
    assert len(provider.calls) == 0
    assert json.dumps(payload) not in captured.out
    assert json.dumps(payload) not in captured.err


def test_m2_blocked_exits_2_without_provider_or_pack(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, output_path = cli_paths
    wire = _valid_input_wire()
    wire["bindingRequest"]["queryRequirements"]["entity"]["entityType"] = "tampered"
    _write_json(input_path, wire)
    provider, _store = _patch_assembly(monkeypatch)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 2
    assert len(provider.calls) == 0
    assert output_path.exists() is False


def test_success_writes_evidence_complete_pack(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    _patch_assembly(monkeypatch)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 0
    assert output_path.is_file()
    pack_wire = json.loads(output_path.read_text(encoding="utf-8"))
    pack = EvidencePackV3.model_validate(pack_wire)
    assert pack.stage == "evidenceComplete"
    assert pack.executable is False
    assert pack.schema_version == "1.1.0"
    assert pack.run_id is not None
    assert pack.authorized_online_provider is True
    summary = json.loads(capsys.readouterr().out)
    for key in ("runId", "authorizedOnlineProvider", "startedAt", "endedAt"):
        assert summary[key] == pack_wire[key]
    assert summary["authorizedOnlineProvider"] is True


def test_existing_output_without_overwrite_is_unchanged(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, output_path = cli_paths
    original = b"original-evidence-pack-bytes\n"
    output_path.write_bytes(original)
    _patch_assembly(monkeypatch)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 4
    assert output_path.read_bytes() == original


def test_static_blocked_writes_pack_with_stored_outcome(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, output_path = cli_paths
    _patch_assembly(monkeypatch, provider=_abs_provider())
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 2
    pack = EvidencePackV3.model_validate_json(output_path.read_text(encoding="utf-8"))
    assert pack.stage == "candidateStored"
    assert pack.store_outcome == "stored"
    assert pack.static_status == "blocked"


def test_summary_and_pack_do_not_leak_sql_or_uri(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    settings = Settings(mongodb_uri=SecretStr(_MARKER_URI), _env_file=None)
    monkeypatch.setattr("release_sql_bot.__main__.get_settings", lambda: settings)
    _patch_assembly(monkeypatch)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 0
    captured = capsys.readouterr()
    pack_text = output_path.read_text(encoding="utf-8")
    for text in (captured.out, captured.err, pack_text):
        for marker in _SQL_LEAK_MARKERS:
            assert marker not in text
        assert "MARKER_URI_9b2e" not in text


def test_empty_trusted_providers_nulls_provider_identity(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, output_path = cli_paths
    settings = Settings(evidence_trusted_providers=(), _env_file=None)
    monkeypatch.setattr("release_sql_bot.__main__.get_settings", lambda: settings)
    _patch_assembly(monkeypatch)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 0
    pack = EvidencePackV3.model_validate_json(output_path.read_text(encoding="utf-8"))
    assert pack.provider is None
    assert "M6_PROVIDER_IDENTITY_UNTRUSTED" in pack.issue_codes


def test_missing_approval_port_exits_4(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, output_path = cli_paths
    provider, _store = _patch_assembly(monkeypatch, omit_approval_port=True)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 4
    assert len(provider.calls) == 0
    assert output_path.exists() is False


def test_empty_trusted_providers_tuple_is_deny_all() -> None:
    settings = Settings(evidence_trusted_providers=(), _env_file=None)
    value, code = _validate_provider_identity(
        "fixed-offline-v3",
        _trusted_from_settings(settings),
    )
    assert settings.evidence_trusted_providers == ()
    assert value is None
    assert code == "M6_PROVIDER_IDENTITY_UNTRUSTED"


def test_empty_trusted_models_tuple_is_deny_all() -> None:
    settings = Settings(evidence_trusted_models=(), _env_file=None)
    value, code = _validate_model_identity(
        "fixed-model-v3",
        _trusted_from_settings(settings),
    )
    assert settings.evidence_trusted_models == ()
    assert value is None
    assert code == "M6_MODEL_IDENTITY_UNTRUSTED"


def test_empty_trusted_prompt_versions_tuple_is_deny_all() -> None:
    settings = Settings(evidence_trusted_prompt_versions=(), _env_file=None)
    value, code = _validate_prompt_version(
        "sqlserver-fact-candidate-v3.1",
        _trusted_from_settings(settings),
    )
    assert settings.evidence_trusted_prompt_versions == ()
    assert value is None
    assert code == "M6_PROMPT_VERSION_UNTRUSTED"


def test_safe_summary_emits_allowlist_counts_not_values() -> None:
    settings = Settings(_env_file=None)
    summary = settings.safe_summary()
    assert summary["evidence_trusted_providers_count"] == 1
    assert summary["evidence_trusted_models_count"] == 1
    assert summary["evidence_trusted_prompt_versions_count"] == 2
    dumped = json.dumps(summary)
    assert "fixed-offline-v3" not in dumped
    assert "fixed-model-v3" not in dumped
    assert "sqlserver-fact-candidate-v3.0" not in dumped
    assert "sqlserver-fact-candidate-v3.1" not in dumped


_MARKER_DUP = "MARKER_DUP_ALLOWLIST_7f3a"
_MARKER_ASSEMBLY = "MARKER_ASSEMBLY_RUNTIME_9b2e"
_MARKER_WRITE = "MARKER_WRITE_OSERROR_4c8d"
_MARKER_INIT = "MARKER_INIT_FAIL_2e1a"
_MARKER_CLOSE = "MARKER_CLOSE_FAIL_8d3c"
_MARKER_STAT = "MARKER_STAT_EACCES_6a1f"


def _assert_no_leak(captured: pytest.CaptureFixture[str], *markers: str) -> None:
    result = captured.readouterr()
    text = result.out + result.err
    assert "Traceback (most recent call last)" not in text
    for marker in markers:
        assert marker not in text


class _TrackingInitializer:
    def __init__(
        self, *, fail_init: bool = False, fail_close: bool = False, marker: str = ""
    ) -> None:
        self.status = DatabaseStatus.DISABLED
        self.initialize_calls = 0
        self.close_calls = 0
        self._fail_init = fail_init
        self._fail_close = fail_close
        self._marker = marker

    async def initialize(self) -> DatabaseStatus:
        self.initialize_calls += 1
        if self._fail_init:
            raise RuntimeError(self._marker)
        return self.status

    async def close(self) -> None:
        self.close_calls += 1
        if self._fail_close:
            raise RuntimeError(self._marker)


class _TrackingStore(FakeStoreV3):
    def __init__(
        self,
        *,
        fail_init: bool = False,
        fail_close: bool = False,
        marker: str = "",
    ) -> None:
        super().__init__()
        self.initialize_calls = 0
        self.close_calls = 0
        self._fail_init = fail_init
        self._fail_close = fail_close
        self._marker = marker

    async def initialize(self) -> None:
        self.initialize_calls += 1
        if self._fail_init:
            raise RuntimeError(self._marker)
        return await super().initialize()

    async def close(self) -> None:
        self.close_calls += 1
        if self._fail_close:
            raise RuntimeError(self._marker)


class _TrackingApproval:
    def __init__(
        self,
        inner: Any,
        *,
        fail_init: bool = False,
        fail_close: bool = False,
        marker: str = "",
    ) -> None:
        self._inner = inner
        self.initialize_calls = 0
        self.close_calls = 0
        self._fail_init = fail_init
        self._fail_close = fail_close
        self._marker = marker

    async def get_by_approval_id(self, approval_id: str) -> Any:
        return await self._inner.get_by_approval_id(approval_id)

    async def initialize(self) -> None:
        self.initialize_calls += 1
        if self._fail_init:
            raise RuntimeError(self._marker)

    async def close(self) -> None:
        self.close_calls += 1
        if self._fail_close:
            raise RuntimeError(self._marker)


def test_duplicate_allowlist_settings_error_exits_4_without_leak(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    factory_calls = {"db": 0, "provider": 0}

    def exploding_settings() -> Settings:
        return Settings(
            evidence_trusted_providers=(_MARKER_DUP, _MARKER_DUP),
            _env_file=None,
        )

    monkeypatch.setattr("release_sql_bot.__main__.get_settings", exploding_settings)
    monkeypatch.setattr(
        "release_sql_bot.__main__.build_database_resources",
        lambda settings: factory_calls.__setitem__("db", factory_calls["db"] + 1),
    )
    monkeypatch.setattr(
        "release_sql_bot.__main__.build_candidate_provider",
        lambda settings: factory_calls.__setitem__("provider", factory_calls["provider"] + 1),
    )
    with pytest.raises(ValidationError) as settings_exc:
        exploding_settings()
    assert any(_MARKER_DUP in str(err.get("input", "")) for err in settings_exc.value.errors())

    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 4
    assert factory_calls == {"db": 0, "provider": 0}
    assert output_path.exists() is False
    _assert_no_leak(capsys, _MARKER_DUP)


def test_database_resource_factory_error_exits_3_without_leak(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    provider, _store = _patch_assembly(monkeypatch)

    def boom_db(_settings: Settings) -> Any:
        raise RuntimeError(f"database factory {_MARKER_ASSEMBLY}")

    monkeypatch.setattr("release_sql_bot.__main__.build_database_resources", boom_db)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 3
    assert len(provider.calls) == 0
    assert output_path.exists() is False
    _assert_no_leak(capsys, _MARKER_ASSEMBLY)


def test_provider_factory_error_exits_3_without_leak(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    provider, _store = _patch_assembly(monkeypatch)

    def boom_provider(_settings: Settings) -> Any:
        raise RuntimeError(f"provider factory {_MARKER_ASSEMBLY}")

    monkeypatch.setattr("release_sql_bot.__main__.build_candidate_provider", boom_provider)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 3
    assert len(provider.calls) == 0
    assert output_path.exists() is False
    _assert_no_leak(capsys, _MARKER_ASSEMBLY)


def test_write_oserror_exits_4_without_success_summary_or_leak(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    provider, _store = _patch_assembly(monkeypatch)

    def boom_fdopen(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError(f"permission denied {_MARKER_WRITE}")

    monkeypatch.setattr("release_sql_bot.__main__.os.fdopen", boom_fdopen)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 4
    assert len(provider.calls) == 1
    assert output_path.exists() is False
    captured = capsys.readouterr()
    assert "runId" not in captured.out
    assert "authorizedOnlineProvider" not in captured.out
    assert "Traceback (most recent call last)" not in captured.out + captured.err
    assert _MARKER_WRITE not in captured.out + captured.err


def test_mid_write_failure_leaves_no_partial_pack(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_path, output_path = cli_paths
    _patch_assembly(monkeypatch)
    real_fdopen = os.fdopen

    def wrapping_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        handle = real_fdopen(fd, *args, **kwargs)
        original_write = handle.write

        def write(data: str) -> int:
            original_write(data[: max(1, len(data) // 8)])
            handle.flush()
            raise OSError(f"disk full {_MARKER_WRITE}")

        handle.write = write  # type: ignore[method-assign]
        return handle

    monkeypatch.setattr("release_sql_bot.__main__.os.fdopen", wrapping_fdopen)
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 4
    assert output_path.exists() is False
    leftovers = list(output_path.parent.glob(".rsb-v3-evidence-*"))
    assert leftovers == []


def test_overwrite_replace_failure_keeps_original_bytes(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    original = b"original-evidence-pack-bytes-keep\n"
    output_path.write_bytes(original)
    _patch_assembly(monkeypatch)

    def boom_replace(_src: str | os.PathLike[str], _dst: str | os.PathLike[str]) -> None:
        raise OSError(f"replace failed {_MARKER_WRITE}")

    monkeypatch.setattr("release_sql_bot.__main__.os.replace", boom_replace)
    args = _authorized_args(input_path, output_path) + ["--overwrite"]
    code = _run_cli(args)
    assert code == 4
    assert output_path.read_bytes() == original
    captured = capsys.readouterr()
    assert "runId" not in captured.out
    assert _MARKER_WRITE not in captured.out + captured.err
    leftovers = list(output_path.parent.glob(".rsb-v3-evidence-*"))
    assert leftovers == []


def test_initialize_failure_still_closes_initialized_resources(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    initializer = _TrackingInitializer(marker=_MARKER_INIT)
    approval = _TrackingApproval(_build_synthetic_approval_port(), marker=_MARKER_INIT)
    store = _TrackingStore(fail_init=True, marker=_MARKER_INIT)
    provider, _store = _patch_assembly(
        monkeypatch,
        store=store,
        approval_port=approval,
        initializer=initializer,
    )
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 3
    assert len(provider.calls) == 0
    assert output_path.exists() is False
    assert initializer.initialize_calls == 1
    assert approval.initialize_calls == 1
    assert store.initialize_calls == 1
    assert initializer.close_calls == 1
    assert approval.close_calls == 1
    assert store.close_calls == 1
    _assert_no_leak(capsys, _MARKER_INIT)


def test_close_failure_still_closes_remaining_resources(
    cli_paths: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_path, output_path = cli_paths
    initializer = _TrackingInitializer(fail_close=True, marker=_MARKER_CLOSE)
    approval = _TrackingApproval(_build_synthetic_approval_port(), marker=_MARKER_CLOSE)
    store = _TrackingStore(fail_close=True, marker=_MARKER_CLOSE)
    provider, _store = _patch_assembly(
        monkeypatch,
        store=store,
        approval_port=approval,
        initializer=initializer,
    )
    code = _run_cli(_authorized_args(input_path, output_path))
    assert code == 3
    assert len(provider.calls) == 1
    assert output_path.exists() is False
    assert store.close_calls == 1
    assert approval.close_calls == 1
    assert initializer.close_calls == 1
    _assert_no_leak(capsys, _MARKER_CLOSE)
