from __future__ import annotations

from copy import deepcopy
from datetime import timedelta

import pytest
from bson import ObjectId
from pydantic import ValidationError

from release_sql_bot.domain.rule_versions import StoredRuleVersion
from tests.support import valid_stored_rule_version


@pytest.mark.parametrize("schema_version", ["1.0.0", "2.0.0"])
def test_real_rulereader_envelope_versions_are_supported(schema_version: str) -> None:
    model = StoredRuleVersion.model_validate(
        valid_stored_rule_version(schema_version=schema_version)
    )

    payload = model.model_dump(mode="json", by_alias=True)

    assert payload["schemaVersion"] == schema_version
    assert payload["document"]["schemaVersion"] == schema_version
    assert payload["ruleId"] == "REPORT_RELEASE_ALL_001"
    assert "_id" not in payload


@pytest.mark.parametrize(
    ("outer_path", "replacement"),
    [
        ("rule_id", "OTHER_RULE"),
        ("rule_version", "OTHER_RULE@V1"),
        ("schema_version", "1.0.0"),
        ("source_sha256", "b" * 64),
        ("parser_version", "different-parser"),
        ("status", "active"),
    ],
)
def test_envelope_references_must_match_inner_document(
    outer_path: str,
    replacement: str,
) -> None:
    payload = valid_stored_rule_version()
    payload[outer_path] = replacement

    with pytest.raises(ValidationError, match="规则版本包装引用不一致"):
        StoredRuleVersion.model_validate(payload)


def test_generated_time_allows_bson_millisecond_truncation_but_not_real_drift() -> None:
    valid = valid_stored_rule_version()
    invalid = deepcopy(valid)
    invalid["generated_at"] = invalid["generated_at"] + timedelta(milliseconds=2)

    StoredRuleVersion.model_validate(valid)
    with pytest.raises(ValidationError, match="generated_at"):
        StoredRuleVersion.model_validate(invalid)


def test_unknown_schema_and_bson_specific_rule_values_are_rejected() -> None:
    unknown_schema = valid_stored_rule_version()
    unknown_schema["schema_version"] = "3.0.0"
    unknown_schema["document"]["schemaVersion"] = "3.0.0"
    bson_value = valid_stored_rule_version()
    bson_value["document"]["rule"]["testCases"][0]["given"]["opaque"] = ObjectId()

    with pytest.raises(ValidationError):
        StoredRuleVersion.model_validate(unknown_schema)
    with pytest.raises(ValidationError):
        StoredRuleVersion.model_validate(bson_value)


def test_entity_type_follows_the_real_schema_version_shape() -> None:
    schema_one = valid_stored_rule_version(schema_version="1.0.0")
    schema_one["document"]["rule"]["entityType"] = "report"
    schema_two = valid_stored_rule_version(schema_version="2.0.0")
    schema_two["document"]["rule"].pop("entityType")

    with pytest.raises(ValidationError, match="Schema 1.0.0"):
        StoredRuleVersion.model_validate(schema_one)
    with pytest.raises(ValidationError, match="Schema 2.0.0"):
        StoredRuleVersion.model_validate(schema_two)
