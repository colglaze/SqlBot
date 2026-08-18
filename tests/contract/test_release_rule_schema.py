from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "docs" / "specs" / "release-rule.schema.json"
RULE_DOC_PATH = ROOT / "docs" / "specs" / "rule-contract.md"


def test_release_rule_schema_is_valid_draft_2020_12() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)


def test_documented_rule_example_matches_schema() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    document = RULE_DOC_PATH.read_text(encoding="utf-8")
    match = re.search(r"```json\s+(.*?)\s+```", document, flags=re.DOTALL)
    assert match is not None, "rule-contract.md must contain a JSON example"
    example = json.loads(match.group(1))

    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(example), key=lambda error: list(error.path))

    assert errors == []
