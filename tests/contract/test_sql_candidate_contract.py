from __future__ import annotations

import pytest
from pydantic import ValidationError

from release_sql_bot.domain.sql_candidates import GeneratedCandidatePayload, SqlTemplateCandidate
from tests.support import valid_generated_candidate_payload, valid_sql_candidate


def test_candidate_contract_is_camel_case_and_non_executable() -> None:
    candidate = SqlTemplateCandidate.model_validate(valid_sql_candidate())

    payload = candidate.model_dump(by_alias=True, mode="json")
    assert payload["schemaVersion"] == "1.1.0"
    assert payload["status"] == "candidate"
    assert payload["executable"] is False
    assert payload["reviewStatus"] == "pending"
    assert payload["dialect"] == "sqlserver"
    assert len(payload["bindingRef"]["sha256"]) == 64
    assert payload["result"]["columnName"] == "fact_value"
    assert payload["provenance"]["promptVersion"] == "sqlserver-fact-candidate-v1"
    assert payload["provenance"]["attemptCount"] == 1
    assert payload["provenance"]["maxTokens"] == 4096
    assert payload["provenance"]["responseFormat"] == "json_object"


def test_candidate_cannot_claim_to_be_executable() -> None:
    payload = valid_sql_candidate()
    payload["executable"] = True

    with pytest.raises(ValidationError):
        SqlTemplateCandidate.model_validate(payload)


def test_generated_payload_rejects_lifecycle_and_extra_fields() -> None:
    payload = valid_generated_candidate_payload()
    payload["executable"] = False

    with pytest.raises(ValidationError, match="extra_forbidden"):
        GeneratedCandidatePayload.model_validate(payload)
