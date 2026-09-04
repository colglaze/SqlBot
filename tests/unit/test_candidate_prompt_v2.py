from __future__ import annotations

import json

from release_sql_bot.application.prompts_v2 import (
    SQLSERVER_CANDIDATE_PROMPT_VERSION_V2,
    build_sqlserver_candidate_prompt_v2,
)
from release_sql_bot.domain.sql_candidates_v2 import GenerateSqlCandidateRequestV2
from tests.phase2g_support import generate_candidate_request_payload


def test_v2_prompt_is_stable_authority_minimized_and_independent() -> None:
    request = GenerateSqlCandidateRequestV2.model_validate(generate_candidate_request_payload())

    first = build_sqlserver_candidate_prompt_v2(request)
    second = build_sqlserver_candidate_prompt_v2(request)
    user = json.loads(first.user)

    assert first == second
    assert first.version == SQLSERVER_CANDIDATE_PROMPT_VERSION_V2
    assert first.version != "sqlserver-fact-candidate-v1"
    assert user["requestId"] == request.resolution_report.request_id
    assert user["authorizedPhysicalPlan"]["resolvedBindings"]
    assert user["queryRequirements"]["filters"]["items"][0]["value"] == {
        "kind": "parameter",
        "parameterName": "projectId",
    }
    assert user["exactOutputDeclarations"] == {
        "parameters": [
            {
                "name": "projectId",
                "dataType": "integer",
                "required": True,
                "source": "fact.parameters.projectId",
            }
        ],
        "result": {
            "columnName": "fact_value",
            "dataType": "money",
            "cardinality": "scalar",
            "nullable": False,
            "nullPolicy": "error",
            "unit": "CNY",
        },
        "declaredObjects": [
            {
                "schemaName": "reporting",
                "relationName": "synthetic_report_amounts",
            }
        ],
        "declaredUsageCoverage": ["amount-positive"],
    }
    forbidden = {
        "mappingCandidate",
        "sourceCandidate",
        "provenance",
        "uncertainties",
        "approvalRef",
        "examples",
        "resolutionHint",
        "evidenceIds",
        "bindingGapReport",
    }
    assert all(item not in first.user for item in forbidden)
    assert "synthetic-pass" not in first.user
    assert "candidate" not in user["queryRequirements"]["fields"][0]
