from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
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
    generated = valid_generated_candidate_payload()
    return {
        "schemaVersion": "1.1.0",
        "templateCode": generated["templateCode"],
        "status": "candidate",
        "executable": False,
        "reviewStatus": "pending",
        "ruleRef": binding["bindingRequest"]["ruleRef"],
        "bindingRef": {
            "contractVersion": binding["bindingRequest"]["contractVersion"],
            "sha256": binding_request_sha256(binding),
        },
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
        "sqlTemplate": generated["sqlTemplate"],
        "parameters": generated["parameters"],
        "result": generated["result"],
        "allowedObjects": generated["allowedObjects"],
        "usageCoverage": generated["usageCoverage"],
        "assumptions": generated["assumptions"],
        "warnings": ["候选 SQL 未通过 AST、安全门禁、受限验证和人工审核，不得执行。"],
        "provenance": {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "responseModel": "deepseek-v4-flash-test-build",
            "promptVersion": "sqlserver-fact-candidate-v1",
            "providerRequestId": "fixture-generation-001",
            "systemFingerprint": "fixture-fingerprint-001",
            "attemptCount": 1,
            "maxTokens": 4096,
            "responseFormat": "json_object",
        },
    }


def binding_request_sha256(payload: dict[str, Any] | None = None) -> str:
    binding = (payload or valid_binding_payload())["bindingRequest"]
    canonical_json = json.dumps(
        binding,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256(canonical_json.encode("utf-8")).hexdigest()


def valid_generated_candidate_payload() -> dict[str, Any]:
    return {
        "templateCode": "TASK_SETTLEMENT_FEE_V1",
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
        "warnings": [],
    }


def valid_generated_candidate_content() -> str:
    return json.dumps(valid_generated_candidate_payload(), ensure_ascii=False)


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


def valid_stored_rule_version(
    *,
    schema_version: str = "2.0.0",
    rule_version: str = "REPORT_RELEASE_ALL_001@20260824T080726492521Z-000000000000",
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    outer_generated_at = generated_at or datetime(
        2026,
        8,
        24,
        8,
        7,
        26,
        492_000,
        tzinfo=UTC,
    )
    document_generated_at = outer_generated_at.replace(
        microsecond=outer_generated_at.microsecond + 521
    )
    source_sha256 = "a" * 64
    payload = {
        "rule_id": "REPORT_RELEASE_ALL_001",
        "rule_version": rule_version,
        "schema_version": schema_version,
        "source_sha256": source_sha256,
        "parser_version": "rule-reader-v2",
        "status": "draft",
        "executable": False,
        "generated_at": outer_generated_at,
        "stored_at": outer_generated_at + timedelta(seconds=1),
        "document": {
            "schemaVersion": schema_version,
            "ruleVersion": rule_version,
            "status": "draft",
            "executable": False,
            "generatedAt": document_generated_at.isoformat().replace("+00:00", "Z"),
            "source": {
                "sourceName": "synthetic-rule.md",
                "relativePath": "rules/synthetic-rule.md",
                "characterCount": 100,
                "sha256": source_sha256,
            },
            "parser": {
                "provider": "fixture",
                "model": "deterministic-parser",
                "promptVersion": "prompt-v1",
                "parserVersion": "rule-reader-v2",
            },
            "rule": {
                "ruleId": "REPORT_RELEASE_ALL_001",
                "title": "合成报告释放规则",
                "scope": "project_report",
                "sourceViews": ["synthetic_view"],
                "rootCondition": {
                    "id": "root",
                    "kind": "all",
                    "description": "全部合成条件通过",
                    "enabled": True,
                    "nullPolicy": "error",
                    "children": [],
                    "operator": None,
                    "left": None,
                    "right": None,
                    "factKey": None,
                    "factRefs": [],
                    "expression": None,
                    "value": None,
                },
                "requiredFacts": [
                    {
                        "key": "report.status",
                        "factCode": "report.status",
                        "name": "报告状态",
                        "factKind": "source",
                        "dataType": "integer",
                        "description": "合成状态事实",
                        "nullable": False,
                        "nullPolicy": "error",
                        "grain": "report",
                        "parameters": [],
                        "unit": None,
                        "allowedValues": [1],
                        "defaultValue": None,
                        "derivation": None,
                    }
                ],
                "fieldMappings": [
                    {
                        "factKey": "report.status",
                        "factCode": "report.status",
                        "mappingStatus": "mapped",
                        "viewName": "synthetic_view",
                        "viewField": "status",
                        "viewActive": True,
                        "sourceExpression": None,
                        "reviewStatus": "candidate",
                        "note": "合成映射",
                    }
                ],
                "testCases": [
                    {
                        "id": "synthetic-pass",
                        "category": "normal",
                        "description": "合成通过用例",
                        "given": {"report": {"status": 1}},
                        "expected": "pass",
                        "rationale": "状态满足",
                    }
                ],
                "responsibleRoles": ["rule-owner"],
                "failureReasons": [],
                "exceptionNotes": [],
                "recommendations": [],
                "warnings": [],
            },
        },
    }
    if schema_version == "2.0.0":
        payload["document"]["rule"]["entityType"] = "report"
    return payload
