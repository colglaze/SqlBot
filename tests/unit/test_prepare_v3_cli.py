"""MongoDB 准备入口的离线集成验证；全部使用合成规则和只读替身。"""

import json
from pathlib import Path

import pytest

from release_sql_bot import __main__ as cli
from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.project_bindings_v3 import ResolveMetadataRequestV3
from tests.unit.test_candidates_v3 import (
    _build_synthetic_approval_port,
    _build_synthetic_handoff_repository,
)
from tests.unit.test_generate_v3_cli import (
    _authorized_args,
    _patch_assembly,
    _run_cli,
    _valid_input_wire,
)


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    wire = _valid_input_wire()
    package = {key: wire[key] for key in ("projectContext", "metadataSnapshot", "approvalRecord")}
    package.update(
        schemaVersion="1.0.0",
        ruleVersion=wire["handoffClosure"]["ruleVersion"],
        requestId=wire["bindingRequest"]["requestId"],
    )
    source, output = tmp_path / "package.json", tmp_path / "resolve.json"
    source.write_text(json.dumps(package), encoding="utf-8")
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(
            database_enabled=True,
            approval_store_v3_enabled=True,
            mongodb_uri="mongodb://127.0.0.1/synthetic",
            _env_file=None,
        ),
    )
    events = []
    repository = _build_synthetic_handoff_repository()
    approval = _build_synthetic_approval_port()

    class ReadOnlyRepository:
        async def initialize(self):
            events.append("repository.initialize")

        async def close(self):
            events.append("repository.close")

        async def get_batch_by_rule_version(self, version):
            events.append("repository.read")
            return await repository.get_batch_by_rule_version(version)

    class ReadOnlyApproval:
        async def initialize(self):
            events.append("approval.initialize")

        async def close(self):
            events.append("approval.close")

        async def get_by_approval_id(self, identity):
            events.append("approval.read")
            return await approval.get_by_approval_id(identity)

    monkeypatch.setattr(
        "release_sql_bot.infrastructure.database.mongodb.MongoRuleStore",
        lambda settings: ReadOnlyRepository(),
    )
    monkeypatch.setattr(
        "release_sql_bot.infrastructure.database.mongodb_approvals_v3.MongoApprovalRecordStoreV3",
        lambda settings: ReadOnlyApproval(),
    )
    return source, output, events


def test_prepare_then_generate_exports_one_candidate(tmp_path, monkeypatch):
    source, output, events = _setup(tmp_path, monkeypatch)
    provider, _ = _patch_assembly(monkeypatch)
    assert _run_cli(["prepare-v3", "--input", str(source), "--output", str(output)]) == 0
    assert len(provider.calls) == 0
    request = ResolveMetadataRequestV3.model_validate_json(output.read_text(encoding="utf-8"))
    assert request.handoff_closure.payload == request.binding_request
    assert events == [
        "repository.initialize",
        "approval.initialize",
        "repository.read",
        "approval.read",
        "approval.close",
        "repository.close",
    ]
    evidence, candidate = tmp_path / "evidence.json", tmp_path / "candidate.json"
    assert (
        _run_cli([*_authorized_args(output, evidence), "--candidate-output", str(candidate)]) == 0
    )
    assert len(provider.calls) == 1
    assert candidate.is_file()


def test_prepare_output_conflict_has_zero_database_calls(tmp_path, monkeypatch):
    source, output, events = _setup(tmp_path, monkeypatch)
    output.write_text("keep", encoding="utf-8")
    assert _run_cli(["prepare-v3", "--input", str(source), "--output", str(output)]) == 4
    assert events == []
    assert output.read_text(encoding="utf-8") == "keep"


def test_prepare_failure_closes_both_read_ports(tmp_path, monkeypatch, capsys):
    source, output, events = _setup(tmp_path, monkeypatch)

    class FailingApproval:
        async def initialize(self):
            raise RuntimeError("private-connection-marker")

        async def close(self):
            events.append("approval.close")

    monkeypatch.setattr(
        "release_sql_bot.infrastructure.database.mongodb_approvals_v3.MongoApprovalRecordStoreV3",
        lambda settings: FailingApproval(),
    )
    assert _run_cli(["prepare-v3", "--input", str(source), "--output", str(output)]) == 3
    assert events[-2:] == ["approval.close", "repository.close"]
    assert not output.exists()
    assert "private-connection-marker" not in capsys.readouterr().err


def test_prepare_does_not_construct_generation_dependencies(tmp_path, monkeypatch):
    source, output, _ = _setup(tmp_path, monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("generation assembly called during preparation")

    monkeypatch.setattr(cli, "build_database_resources", forbidden)
    monkeypatch.setattr(cli, "build_candidate_provider", forbidden)
    assert _run_cli(["prepare-v3", "--input", str(source), "--output", str(output)]) == 0
