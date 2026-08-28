from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from release_sql_bot.domain.sql_validation import ValidateSqlCandidateRequestV2
from tests.phase4_support import validation_payload


def test_v2_validation_request_accepts_only_complete_camel_case_wire_payload() -> None:
    payload = validation_payload()

    parsed = ValidateSqlCandidateRequestV2.model_validate(payload)

    assert parsed.schema_version == "1.0.0"
    assert parsed.candidate.executable is False
    assert parsed.candidate.review_status == "pending"
    assert parsed.model_dump(by_alias=True, mode="json") == payload


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schemaVersion",), "2.0.0"),
        (("unexpected",), True),
        (("generation_request",), {}),
    ],
)
def test_v2_validation_request_rejects_wrong_version_extra_and_snake_case(
    path: tuple[str, ...],
    value: object,
) -> None:
    payload = validation_payload()
    target = payload
    for component in path[:-1]:
        target = target[component]
    target[path[-1]] = value

    with pytest.raises(ValidationError):
        ValidateSqlCandidateRequestV2.model_validate(payload)


def test_v2_validation_request_rejects_nested_snake_case_fallback() -> None:
    payload = validation_payload()
    changed = deepcopy(payload["candidate"]["result"])
    changed["column_name"] = changed.pop("columnName")
    payload["candidate"]["result"] = changed

    with pytest.raises(ValidationError, match="snake_case key"):
        ValidateSqlCandidateRequestV2.model_validate(payload)


def test_v2_validation_request_rejects_nested_scalar_coercion() -> None:
    payload = validation_payload()
    payload["candidate"]["projectRef"]["projectVersion"] = "1"

    with pytest.raises(ValidationError, match="require no coercion"):
        ValidateSqlCandidateRequestV2.model_validate(payload)
