from __future__ import annotations

import pytest
from pydantic import ValidationError

from release_sql_bot.domain.sql_candidates import SqlTemplateCandidate
from tests.support import valid_sql_candidate


def test_candidate_contract_is_camel_case_and_non_executable() -> None:
    candidate = SqlTemplateCandidate.model_validate(valid_sql_candidate())

    payload = candidate.model_dump(by_alias=True, mode="json")
    assert payload["status"] == "candidate"
    assert payload["executable"] is False
    assert payload["reviewStatus"] == "pending"
    assert payload["dialect"] == "sqlserver"
    assert payload["result"]["columnName"] == "fact_value"


def test_candidate_cannot_claim_to_be_executable() -> None:
    payload = valid_sql_candidate()
    payload["executable"] = True

    with pytest.raises(ValidationError):
        SqlTemplateCandidate.model_validate(payload)
