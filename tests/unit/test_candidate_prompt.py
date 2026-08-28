from __future__ import annotations

import json

from release_sql_bot.application.prompts import (
    SQLSERVER_CANDIDATE_PROMPT_VERSION,
    build_sqlserver_candidate_prompt,
)
from release_sql_bot.domain.fact_bindings import ValidateFactBindingRequest
from tests.support import valid_binding_payload


def test_candidate_prompt_is_versioned_stable_json_with_an_output_schema() -> None:
    payload = ValidateFactBindingRequest.model_validate(valid_binding_payload())

    first = build_sqlserver_candidate_prompt(payload)
    second = build_sqlserver_candidate_prompt(payload)
    prompt_input = json.loads(first.user)

    assert first == second
    assert first.version == SQLSERVER_CANDIDATE_PROMPT_VERSION
    assert "JSON" in first.system
    assert "不得执行" not in first.user
    assert prompt_input["bindingRequest"]["fact"]["factCode"] == "task.settlement_fee"
    assert prompt_input["context"]["metadataSnapshot"]["snapshotId"] == "metadata-001"
    assert prompt_input["outputJsonSchema"]["additionalProperties"] is False
