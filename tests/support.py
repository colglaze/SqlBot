from __future__ import annotations

from copy import deepcopy
from typing import Any


def valid_binding_payload() -> dict[str, Any]:
    payload = {
        "bindingRequest": {
            "contractVersion": "1.0.0",
            "status": "candidate",
            "ruleRef": {
                "ruleId": "TEST_RELEASE_002",
                "ruleVersion": "TEST_RELEASE_002@20260819T000000000000Z-000000000000",
                "schemaVersion": "2.0.0",
                "sourceSha256": "0" * 64,
            },
            "fact": {
                "factCode": "task.settlement_fee",
                "name": "任务结算费用",
                "factKind": "source",
                "dataType": "money",
                "description": "正式实验任务最终采用费用",
                "nullable": False,
                "nullPolicy": "error",
                "grain": "formal_test_task",
                "parameters": [
                    {
                        "name": "taskId",
                        "dataType": "integer",
                        "description": "正式实验任务 ID",
                        "required": True,
                    }
                ],
                "unit": "CNY",
                "allowedValues": [],
                "defaultValue": None,
                "derivation": None,
            },
            "usages": [
                {
                    "conditionId": "settlement-non-negative",
                    "conditionPath": "root/settlement-non-negative",
                    "operator": "gte",
                    "expressionSide": "left",
                }
            ],
            "mappingCandidate": {
                "factCode": "task.settlement_fee",
                "mappingStatus": "mapped",
                "viewName": "v_OrderFormaltestsettlement",
                "viewField": "zssyjsfy",
                "viewActive": True,
                "reviewStatus": "candidate",
                "note": "使用正式实验任务最终采用费用。",
            },
            "examples": [
                {
                    "testCaseId": "pass-case",
                    "value": 100,
                    "expectedRuleResult": "pass",
                }
            ],
            "targetDialect": "sqlserver",
            "requiresMetadataSnapshot": True,
            "tempTableAllowed": False,
        },
        "context": {
            "contextId": "ctx-sqlserver-001",
            "contextVersion": 1,
            "dialect": {"name": "sqlserver", "version": "2022"},
            "metadataSnapshot": {
                "snapshotId": "metadata-001",
                "version": 1,
                "sha256": "1" * 64,
            },
            "entityKeys": [
                {
                    "parameterName": "taskId",
                    "schemaName": "dbo",
                    "relationName": "v_OrderFormaltestsettlement",
                    "columnName": "sqlc",
                }
            ],
            "allowedRelations": [
                {
                    "schemaName": "dbo",
                    "relationName": "v_OrderFormaltestsettlement",
                }
            ],
            "capabilities": {"explain": True, "sessionTempTable": False},
            "tempTableAllowed": False,
        },
    }
    return deepcopy(payload)


def valid_sql_candidate() -> dict[str, Any]:
    binding = valid_binding_payload()
    return {
        "schemaVersion": "1.0.0",
        "templateCode": "TASK_SETTLEMENT_FEE_V1",
        "status": "candidate",
        "executable": False,
        "reviewStatus": "pending",
        "ruleRef": binding["bindingRequest"]["ruleRef"],
        "factRef": {
            "factCode": "task.settlement_fee",
            "factKind": "source",
            "dataType": "money",
            "grain": "formal_test_task",
        },
        "contextRef": {
            "contextId": "ctx-sqlserver-001",
            "contextVersion": 1,
            "metadataSnapshotId": "metadata-001",
            "metadataSnapshotVersion": 1,
            "metadataSnapshotSha256": "1" * 64,
        },
        "dialect": "sqlserver",
        "sqlTemplate": (
            "SELECT zssyjsfy AS fact_value "
            "FROM dbo.v_OrderFormaltestsettlement WHERE sqlc = :taskId"
        ),
        "parameters": [
            {
                "name": "taskId",
                "dataType": "integer",
                "required": True,
                "source": "fact.parameters.taskId",
            }
        ],
        "result": {
            "columnName": "fact_value",
            "dataType": "money",
            "cardinality": "scalar",
            "nullable": False,
        },
        "allowedObjects": ["dbo.v_OrderFormaltestsettlement"],
        "usageCoverage": ["settlement-non-negative"],
        "assumptions": [],
        "warnings": ["候选 SQL 尚未通过 AST、安全或人工审核。"],
        "provenance": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "promptVersion": "sql-template-v1",
        },
    }


def valid_release_rule() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "rule_id": "report-release-rule",
        "project_id": "project-001",
        "version": 3,
        "target": "project_report",
        "status": "active",
        "effective_from": "2026-08-18T00:00:00+08:00",
        "condition": {
            "combinator": "all",
            "conditions": [
                {
                    "condition_id": "report.finalized",
                    "field": "report_status",
                    "operator": "eq",
                    "value": "FINAL",
                    "value_type": "string",
                    "null_policy": "fail",
                },
                {
                    "condition_id": "report.qc_passed",
                    "field": "qc_status",
                    "operator": "in",
                    "value": ["PASS", "WAIVED"],
                    "value_type": "string",
                    "null_policy": "fail",
                },
            ],
        },
        "metadata": {
            "created_by": "rule-owner@example.com",
            "change_reason": "Allow approved QC waivers",
        },
    }
