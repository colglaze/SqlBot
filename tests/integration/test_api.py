from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from release_sql_bot.api.app import create_app
from release_sql_bot.application.ports.candidates import CandidateModelResponse
from release_sql_bot.application.ports.database import DatabaseStatus
from release_sql_bot.application.ports.handoffs import (
    FactBindingHandoffRepositoryUnavailableError,
)
from release_sql_bot.application.ports.rules import (
    RuleDocumentInvalidError,
    RuleRepositoryUnavailableError,
)
from release_sql_bot.application.runtime import DatabaseResources
from release_sql_bot.config.settings import Settings
from release_sql_bot.domain.fact_binding_handoffs_v2 import StoredFactBindingHandoffV2
from release_sql_bot.domain.rule_versions import StoredRuleVersion
from tests.fakes import FixedCandidateModelProvider, FixedSqlDialectInspector
from tests.handoff_support import valid_handoff_document
from tests.phase2g_support import (
    generate_candidate_request_payload,
    resolve_metadata_payload,
    valid_generated_candidate_v2_content,
)
from tests.phase4_support import validation_payload
from tests.support import (
    binding_request_sha256,
    valid_binding_payload,
    valid_generated_candidate_content,
    valid_stored_rule_version,
)

ROOT = Path(__file__).resolve().parents[2]
V2_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "fact-binding-request-2.0.0.synthetic-blocked.json"


class ReadyInitializer:
    @property
    def status(self) -> DatabaseStatus:
        return DatabaseStatus.READY

    async def initialize(self) -> DatabaseStatus:
        return self.status

    async def close(self) -> None:
        return None


class ApiRuleRepository:
    def __init__(self, responses) -> None:
        self._responses = iter(responses)
        self.calls: list[str] = []

    async def get_latest_rule(self, *, rule_id: str):
        self.calls.append(rule_id)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


class ApiHandoffRepository:
    def __init__(self, responses) -> None:
        self._responses = iter(responses)
        self.calls: list[str] = []

    async def list_by_rule_version(self, rule_version: str):
        self.calls.append(rule_version)
        response = next(self._responses)
        if isinstance(response, Exception):
            raise response
        return response


def _app_with_rule_responses(responses):
    repository = ApiRuleRepository(responses)
    app = create_app(
        Settings(_env_file=None, environment="test"),
        DatabaseResources(
            initializer=ReadyInitializer(),
            rule_repository=repository,
        ),
    )
    return app, repository


def _app_with_handoff_responses(responses, provider=None):
    repository = ApiHandoffRepository(responses)
    app = create_app(
        Settings(_env_file=None, environment="test"),
        DatabaseResources(
            initializer=ReadyInitializer(),
            rule_repository=None,
            fact_binding_repository=repository,
        ),
        candidate_provider=provider,
    )
    return app, repository


def test_health_and_readiness_endpoints() -> None:
    app = create_app(Settings(_env_file=None, environment="test"))

    with TestClient(app) as client:
        health = client.get("/health")
        readiness = client.get("/ready")

    assert health.status_code == 200
    assert health.json() == {
        "status": "ok",
        "service": "ReleaseSQLBot",
        "version": "0.3.0",
        "environment": "test",
    }
    assert readiness.status_code == 200
    assert readiness.json() == {
        "status": "ready",
        "checks": {"config": "ok", "database": "disabled"},
    }


def test_fact_binding_validation_endpoint_is_deterministic() -> None:
    app = create_app(Settings(_env_file=None, environment="test"))

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


def test_v2_blocking_analysis_never_calls_the_candidate_provider() -> None:
    provider = FixedCandidateModelProvider([])
    app = create_app(
        Settings(_env_file=None, environment="test"),
        candidate_provider=provider,
    )
    payload = json.loads(V2_FIXTURE_PATH.read_text(encoding="utf-8"))

    with TestClient(app) as client:
        response = client.post("/api/v1/fact-bindings/v2/analyze", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["executable"] is False
    assert provider.calls == []


def test_v2_handoff_read_intake_is_blocked_and_never_calls_provider() -> None:
    document = valid_handoff_document()
    stored = StoredFactBindingHandoffV2.model_validate(document)
    provider = FixedCandidateModelProvider([])
    app, repository = _app_with_handoff_responses([(stored,)], provider)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/fact-binding-handoffs/v2",
            params={"ruleVersion": stored.rule_version},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["executable"] is False
    assert response.json()["recordCount"] == 1
    assert response.json()["blockingRequestCount"] == 1
    assert response.json()["records"][0]["payload"] == document["payload"]
    assert repository.calls == [stored.rule_version]
    assert provider.calls == []


def test_v2_handoff_read_intake_maps_safe_not_found_invalid_and_unavailable_errors() -> None:
    document = valid_handoff_document()
    stored = StoredFactBindingHandoffV2.model_validate(document)
    invalid = stored.model_copy(update={"payload_sha256": "f" * 64})
    unavailable = FactBindingHandoffRepositoryUnavailableError("mongo-secret.example")
    app, _ = _app_with_handoff_responses([(), (invalid,), unavailable])

    with TestClient(app) as client:
        responses = [
            client.get(
                "/api/v1/fact-binding-handoffs/v2",
                params={"ruleVersion": stored.rule_version},
            )
            for _ in range(3)
        ]

    assert [response.status_code for response in responses] == [404, 502, 503]
    assert [response.json()["detail"]["code"] for response in responses] == [
        "FACT_BINDING_HANDOFF_NOT_FOUND",
        "FACT_BINDING_HANDOFF_INVALID",
        "FACT_BINDING_HANDOFF_REPOSITORY_UNAVAILABLE",
    ]
    assert "mongo-secret.example" not in " ".join(response.text for response in responses)


def test_v2_handoff_read_intake_is_unavailable_when_database_is_disabled() -> None:
    app = create_app(Settings(_env_file=None, environment="test"))
    rule_version = valid_handoff_document()["rule_version"]

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/fact-binding-handoffs/v2",
            params={"ruleVersion": rule_version},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == ("FACT_BINDING_HANDOFF_REPOSITORY_UNAVAILABLE")


def test_v2_metadata_resolution_api_is_pure_and_never_calls_provider_or_repository() -> None:
    provider = FixedCandidateModelProvider([])
    repository = ApiRuleRepository([])
    app = create_app(
        Settings(_env_file=None, environment="test"),
        DatabaseResources(
            initializer=ReadyInitializer(),
            rule_repository=repository,
        ),
        candidate_provider=provider,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/fact-bindings/v2/resolve-metadata",
            json=resolve_metadata_payload(),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "metadataResolved"
    assert response.json()["executable"] is False
    assert provider.calls == []
    assert repository.calls == []


def test_v2_metadata_resolution_api_preserves_upstream_blocking_without_io() -> None:
    provider = FixedCandidateModelProvider([])
    app = create_app(
        Settings(_env_file=None, environment="test"),
        candidate_provider=provider,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/fact-bindings/v2/resolve-metadata",
            json=resolve_metadata_payload(blocked=True),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["resolvedBindings"] == []
    assert provider.calls == []


def test_v2_metadata_resolution_api_maps_contract_errors_to_422() -> None:
    payload = resolve_metadata_payload()
    payload["metadataSnapshot"]["implicitCatalogLookup"] = True
    app = create_app(Settings(_env_file=None, environment="test"))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/fact-bindings/v2/resolve-metadata",
            json=payload,
        )

    assert response.status_code == 422


def test_v2_payload_cannot_enter_the_legacy_v1_generation_route() -> None:
    provider = FixedCandidateModelProvider([])
    app = create_app(
        Settings(_env_file=None, environment="test"),
        candidate_provider=provider,
    )
    payload = json.loads(V2_FIXTURE_PATH.read_text(encoding="utf-8"))

    with TestClient(app) as client:
        response = client.post("/api/v1/sql-candidates/generate", json=payload)

    assert response.status_code == 422
    assert provider.calls == []


def test_v2_candidate_generation_api_returns_only_hash_closed_pending_candidate() -> None:
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="deepseek",
                request_id="api-v2-generation-001",
                model="offline-v2-model-build",
                content=valid_generated_candidate_v2_content(),
            )
        ]
    )
    app = create_app(
        Settings(_env_file=None, environment="test"),
        candidate_provider=provider,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sql-candidates/v2/generate",
            json=generate_candidate_request_payload(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "2.0.0"
    assert body["status"] == "candidate"
    assert body["executable"] is False
    assert body["reviewStatus"] == "pending"
    assert len(body["contentSha256"]) == 64
    assert len(provider.calls) == 1


def test_v2_candidate_api_blocks_and_is_unavailable_without_provider() -> None:
    provider = FixedCandidateModelProvider([])
    configured = create_app(
        Settings(_env_file=None, environment="test"),
        candidate_provider=provider,
    )

    with TestClient(configured) as client:
        blocked = client.post(
            "/api/v1/sql-candidates/v2/generate",
            json=generate_candidate_request_payload(blocked=True),
        )

    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "V2_CANDIDATE_INPUT_NOT_READY"
    assert provider.calls == []

    unconfigured = create_app(Settings(_env_file=None, environment="test"))
    with TestClient(unconfigured) as client:
        unavailable = client.post(
            "/api/v1/sql-candidates/v2/generate",
            json=generate_candidate_request_payload(),
        )

    assert unavailable.status_code == 503
    assert unavailable.json()["detail"]["code"] == ("V2_CANDIDATE_PROVIDER_UNAVAILABLE")


def test_v2_candidate_generation_api_hides_invalid_model_output() -> None:
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="deepseek",
                request_id="api-v2-generation-invalid",
                model="offline-v2-model-build",
                content="sensitive invalid v2 model output",
            )
        ]
    )
    app = create_app(
        Settings(_env_file=None, environment="test", deepseek_max_retries=0),
        candidate_provider=provider,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sql-candidates/v2/generate",
            json=generate_candidate_request_payload(),
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "V2_CANDIDATE_OUTPUT_INVALID"
    assert "sensitive invalid v2 model output" not in response.text


def test_v2_candidate_generation_api_rejects_wire_extensions_before_provider() -> None:
    payload = generate_candidate_request_payload()
    payload["modelMayApprove"] = True
    provider = FixedCandidateModelProvider([])
    app = create_app(
        Settings(_env_file=None, environment="test"),
        candidate_provider=provider,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sql-candidates/v2/generate",
            json=payload,
        )

    assert response.status_code == 422
    assert provider.calls == []


def test_v2_static_validation_api_is_pure_and_always_non_executable() -> None:
    provider = FixedCandidateModelProvider([])
    app = create_app(
        Settings(_env_file=None, environment="test"),
        candidate_provider=provider,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sql-candidates/v2/validate-static",
            json=validation_payload(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "passed"
    assert body["executable"] is False
    assert body["issues"] == []
    assert body["parserRef"] == body["inspection"]["parserRef"]
    assert provider.calls == []


def test_v2_static_validation_api_returns_blocked_for_forbidden_sql() -> None:
    payload = validation_payload(
        sql=("DELETE FROM reporting.synthetic_report_amounts WHERE project_id = :projectId")
    )
    app = create_app(Settings(_env_file=None, environment="test"))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sql-candidates/v2/validate-static",
            json=payload,
        )

    assert response.status_code == 200
    assert response.json()["status"] == "blocked"
    assert response.json()["executable"] is False
    assert "SQL_ROOT_NOT_SELECT" in {item["code"] for item in response.json()["issues"]}


def test_v2_static_validation_api_tamper_and_wire_fail_before_inspector() -> None:
    tampered = validation_payload()
    tampered["candidate"]["sqlTemplate"] += " ORDER BY amounts.total_amount"
    invalid_wire = validation_payload()
    invalid_wire["allowExecution"] = True
    inspector = FixedSqlDialectInspector([])
    app = create_app(
        Settings(_env_file=None, environment="test"),
        sql_inspector=inspector,
    )

    with TestClient(app) as client:
        blocked = client.post(
            "/api/v1/sql-candidates/v2/validate-static",
            json=tampered,
        )
        rejected = client.post(
            "/api/v1/sql-candidates/v2/validate-static",
            json=invalid_wire,
        )

    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert "CANDIDATE_HASH_MISMATCH" in {item["code"] for item in blocked.json()["issues"]}
    assert rejected.status_code == 422
    assert inspector.calls == []


def test_v2_static_validation_api_hides_unknown_inspector_errors() -> None:
    inspector = FixedSqlDialectInspector(
        [RuntimeError("sensitive parser detail must not be exposed")]
    )
    app = create_app(
        Settings(_env_file=None, environment="test"),
        sql_inspector=inspector,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/api/v1/sql-candidates/v2/validate-static",
            json=validation_payload(),
        )

    assert response.status_code == 500
    assert "sensitive parser detail" not in response.text
    assert len(inspector.calls) == 1


def test_candidate_generation_endpoint_returns_only_a_non_executable_candidate() -> None:
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="deepseek",
                request_id="api-generation-001",
                model="deepseek-v4-flash-test-build",
                content=valid_generated_candidate_content(),
                system_fingerprint="api-fingerprint-001",
            )
        ]
    )
    app = create_app(
        Settings(_env_file=None, environment="test"),
        candidate_provider=provider,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sql-candidates/generate",
            json=valid_binding_payload(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "1.1.0"
    assert body["status"] == "candidate"
    assert body["executable"] is False
    assert body["reviewStatus"] == "pending"
    assert body["bindingRef"] == {
        "contractVersion": "1.0.0",
        "sha256": binding_request_sha256(),
    }
    assert body["provenance"] == {
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "responseModel": "deepseek-v4-flash-test-build",
        "promptVersion": "sqlserver-fact-candidate-v1",
        "providerRequestId": "api-generation-001",
        "systemFingerprint": "api-fingerprint-001",
        "attemptCount": 1,
        "maxTokens": 4096,
        "responseFormat": "json_object",
    }
    assert len(provider.calls) == 1


def test_candidate_generation_endpoint_never_calls_provider_for_blocked_input() -> None:
    payload = valid_binding_payload()
    payload["bindingRequest"]["fact"]["factKind"] = "derived"
    provider = FixedCandidateModelProvider([])
    app = create_app(
        Settings(_env_file=None, environment="test"),
        candidate_provider=provider,
    )

    with TestClient(app) as client:
        response = client.post("/api/v1/sql-candidates/generate", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "CANDIDATE_INPUT_NOT_READY"
    assert provider.calls == []


def test_candidate_generation_endpoint_is_unavailable_without_provider_config() -> None:
    app = create_app(Settings(_env_file=None, environment="test"))

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sql-candidates/generate",
            json=valid_binding_payload(),
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "CANDIDATE_PROVIDER_UNAVAILABLE"


def test_candidate_generation_endpoint_hides_invalid_model_output() -> None:
    provider = FixedCandidateModelProvider(
        [
            CandidateModelResponse(
                provider="deepseek",
                request_id="api-generation-invalid",
                model="deepseek-v4-flash-test-build",
                content="sensitive non-json model output",
            )
        ]
    )
    app = create_app(
        Settings(_env_file=None, environment="test", deepseek_max_retries=0),
        candidate_provider=provider,
    )

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sql-candidates/generate",
            json=valid_binding_payload(),
        )

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "CANDIDATE_OUTPUT_INVALID"
    assert "sensitive non-json model output" not in response.text


def test_latest_rule_endpoint_reads_repository_on_every_request() -> None:
    first_payload = valid_stored_rule_version(rule_version="REPORT_RELEASE_ALL_001@V3")
    second_payload = valid_stored_rule_version(rule_version="REPORT_RELEASE_ALL_001@V4")
    app, repository = _app_with_rule_responses(
        [
            StoredRuleVersion.model_validate(first_payload),
            StoredRuleVersion.model_validate(second_payload),
        ]
    )

    with TestClient(app) as client:
        first = client.get(
            "/api/v1/rules/latest",
            params={"ruleId": "REPORT_RELEASE_ALL_001"},
        )
        second = client.get(
            "/api/v1/rules/latest",
            params={"ruleId": "REPORT_RELEASE_ALL_001"},
        )

    assert first.status_code == 200
    assert first.json()["ruleVersion"].endswith("@V3")
    assert second.status_code == 200
    assert second.json()["ruleVersion"].endswith("@V4")
    assert "_id" not in second.json()
    assert repository.calls == ["REPORT_RELEASE_ALL_001", "REPORT_RELEASE_ALL_001"]


def test_latest_rule_endpoint_maps_not_found_invalid_and_unavailable_errors() -> None:
    app, _ = _app_with_rule_responses(
        [
            None,
            RuleDocumentInvalidError("must not be exposed"),
            RuleRepositoryUnavailableError("must not be exposed"),
        ]
    )

    with TestClient(app) as client:
        responses = [
            client.get(
                "/api/v1/rules/latest",
                params={"ruleId": "REPORT_RELEASE_ALL_001"},
            )
            for _ in range(3)
        ]

    assert [response.status_code for response in responses] == [404, 502, 503]
    assert [response.json()["detail"]["code"] for response in responses] == [
        "RULE_NOT_FOUND",
        "RULE_DOCUMENT_INVALID",
        "RULE_REPOSITORY_UNAVAILABLE",
    ]
    combined = " ".join(response.text for response in responses)
    assert "must not be exposed" not in combined


def test_latest_rule_endpoint_is_unavailable_when_database_is_disabled() -> None:
    app = create_app(Settings(_env_file=None, environment="test"))

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/rules/latest",
            params={"ruleId": "REPORT_RELEASE_ALL_001"},
        )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "RULE_REPOSITORY_UNAVAILABLE"
