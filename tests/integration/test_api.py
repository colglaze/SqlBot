from __future__ import annotations

from fastapi.testclient import TestClient

from release_sql_bot.api.app import create_app
from release_sql_bot.config.settings import Settings


def test_health_and_readiness_endpoints() -> None:
    app = create_app(Settings(environment="test"))

    with TestClient(app) as client:
        health = client.get("/health")
        readiness = client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "ReleaseSQLBot",
        "version": "0.1.0",
        "environment": "test",
    }
    assert readiness.status_code == 200
    assert readiness.json() == {
        "status": "ready",
        "checks": {"config": "ok", "database": "disabled"},
    }
