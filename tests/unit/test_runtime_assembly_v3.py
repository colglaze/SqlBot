"""M3 真实装配切片 2：V3 批准端口与 V3 候选存储的 runtime 装配（离线）。

These tests assert construction and lifespan wiring only. They never call
initialize on real Mongo adapters and never open a network connection.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from release_sql_bot.api.app import create_app
from release_sql_bot.application.ports.candidate_store_v3 import (
    CandidateStoreV3Outcome,
    CandidateStoreV3Status,
)
from release_sql_bot.application.ports.database import DatabaseStatus
from release_sql_bot.application.runtime import DatabaseResources
from release_sql_bot.config.settings import Settings
from release_sql_bot.infrastructure.database import build_database_resources
from release_sql_bot.infrastructure.database.mongodb_approvals_v3 import MongoApprovalRecordStoreV3
from release_sql_bot.infrastructure.database.mongodb_candidates_v3 import MongoCandidateStoreV3

_SYNTHETIC_URI = "mongodb://synthetic:synthetic@127.0.0.1:27017/db?authSource=admin"


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {"_env_file": None}
    values.update(overrides)
    return Settings(**values)  # type: ignore[call-arg]


def _settings_with_uri(**overrides: Any) -> Settings:
    return _settings(mongodb_uri=SecretStr(_SYNTHETIC_URI), **overrides)


class RecordingInitializer:
    def __init__(self) -> None:
        self.initialize_calls = 0
        self.close_calls = 0

    @property
    def status(self) -> DatabaseStatus:
        return DatabaseStatus.DISABLED

    async def initialize(self) -> DatabaseStatus:
        self.initialize_calls += 1
        return self.status

    async def close(self) -> None:
        self.close_calls += 1


class FakeApprovalPortV3:
    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.initialize_calls = 0
        self.close_calls = 0
        self._close_error = close_error

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def get_by_approval_id(self, approval_id: str) -> None:
        del approval_id
        return None

    async def close(self) -> None:
        self.close_calls += 1
        if self._close_error is not None:
            raise self._close_error


class FakeCandidateStoreV3:
    def __init__(self) -> None:
        self.initialize_calls = 0
        self.close_calls = 0
        self.save_calls = 0

    async def initialize(self) -> None:
        self.initialize_calls += 1

    async def save(self, candidate: Any) -> CandidateStoreV3Outcome:
        self.save_calls += 1
        return CandidateStoreV3Outcome(
            status=CandidateStoreV3Status.UNAVAILABLE,
            content_sha256="",
        )

    async def close(self) -> None:
        self.close_calls += 1


def test_v3_runtime_resources_disabled_by_default() -> None:
    resources = build_database_resources(_settings())
    assert resources.approval_port_v3 is None
    assert resources.candidate_store_v3 is None


def test_v3_runtime_constructs_approval_port_when_enabled() -> None:
    resources = build_database_resources(_settings_with_uri(approval_store_v3_enabled=True))
    assert isinstance(resources.approval_port_v3, MongoApprovalRecordStoreV3)
    assert resources.approval_port_v3.ready is False
    assert resources.candidate_store_v3 is None


def test_v3_runtime_constructs_candidate_store_when_enabled() -> None:
    resources = build_database_resources(_settings_with_uri(candidate_store_v3_enabled=True))
    assert isinstance(resources.candidate_store_v3, MongoCandidateStoreV3)
    assert resources.approval_port_v3 is None


def test_v3_runtime_constructs_both_when_database_enabled_is_false() -> None:
    resources = build_database_resources(
        _settings_with_uri(
            database_enabled=False,
            approval_store_v3_enabled=True,
            candidate_store_v3_enabled=True,
        )
    )
    assert isinstance(resources.approval_port_v3, MongoApprovalRecordStoreV3)
    assert isinstance(resources.candidate_store_v3, MongoCandidateStoreV3)
    assert resources.approval_port_v3.ready is False


def test_v3_runtime_lifespan_initializes_and_closes_injected_ports() -> None:
    database = RecordingInitializer()
    approval_port = FakeApprovalPortV3()
    candidate_store = FakeCandidateStoreV3()
    app = create_app(
        _settings(environment="test"),
        DatabaseResources(
            initializer=database,
            rule_repository=None,
            approval_port_v3=approval_port,
            candidate_store_v3=candidate_store,
        ),
    )
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        runtime = app.state.runtime
        assert runtime.approval_port_v3 is approval_port
        assert runtime.candidate_store_v3 is candidate_store
        assert approval_port.initialize_calls == 1
        assert candidate_store.initialize_calls == 1
        assert approval_port.close_calls == 0
        assert candidate_store.close_calls == 0
    assert approval_port.close_calls == 1
    assert candidate_store.close_calls == 1
    assert database.close_calls == 1


def test_v3_runtime_lifespan_close_continues_after_approval_port_error() -> None:
    database = RecordingInitializer()
    approval_port = FakeApprovalPortV3(close_error=RuntimeError("approval-port-close-failed"))
    candidate_store = FakeCandidateStoreV3()
    app = create_app(
        _settings(environment="test"),
        DatabaseResources(
            initializer=database,
            rule_repository=None,
            approval_port_v3=approval_port,
            candidate_store_v3=candidate_store,
        ),
    )
    with pytest.raises(RuntimeError, match="approval-port-close-failed"):
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
    assert candidate_store.close_calls == 1
    assert approval_port.close_calls == 1
    assert database.close_calls == 1


def test_v3_approval_store_rejects_same_db_and_collection_as_candidate_v2() -> None:
    with pytest.raises(ValidationError) as exc_info:
        _settings(
            approval_store_v3_database="release_sql_bot",
            approval_store_v3_collection="sql_template_candidates",
            candidate_store_database="release_sql_bot",
            candidate_store_collection="sql_template_candidates",
        )
    assert (
        "V3 approval record store and V2 candidate store must not target the same "
        "database and collection."
    ) in str(exc_info.value)
