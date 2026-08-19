from __future__ import annotations

from fastapi.testclient import TestClient

from release_sql_bot.api.app import create_app
from release_sql_bot.config.settings import Settings
from tests.support import valid_binding_payload


def test_health_and_readiness_endpoints() -> None:
    app = create_app(Settings(environment="test"))

    with TestClient(app) as client:
        health = client.get("/health")
        readiness = client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "ReleaseSQLBot",
        "version": "0.2.0",
        "environment": "test",
    }
    assert readiness.status_code == 200
    assert readiness.json() == {
        "status": "ready",
        "checks": {"config": "ok", "database": "disabled"},
    }


def test_fact_binding_validation_endpoint_is_deterministic() -> None:
    app = create_app(Settings(environment="test"))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/fact-bindings/validate",
            json=valid_binding_payload(),
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "factCode": "task.settlement_fee",
        "contextId": "ctx-sqlserver-001",
        "issues": [],
    }
