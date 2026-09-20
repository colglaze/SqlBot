"""Synthetic 3.1.0 complete-delivery fixtures. No private business text."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from release_sql_bot.application.canonical import canonical_sha256
from release_sql_bot.application.complete_delivery_intake_v31 import catalog_digest_sha256
from release_sql_bot.domain.complete_delivery_v31 import (
    StoredCompleteDeliveryV31,
    StoredFactBindingHandoffBatchV31,
    StoredFactBindingHandoffRequestV31,
)
from release_sql_bot.domain.fact_bindings_v31 import FactBindingRequestV31
from release_sql_bot.domain.rule_structure_v31 import (
    BusinessConfirmedFactCatalogV31,
    RuleParseResultV31,
    RuleStructureCandidateV31,
)
from release_sql_bot.domain.view_shaped_sql_v31 import (
    FactMappingGrantV31,
    KeyBindingV31,
    SubjectRelationV31,
    ViewShapedMappingBundleV31,
)

CREATED_AT = datetime(2026, 9, 17, 14, 0, tzinfo=UTC)
SOURCE_FILE_SHA256 = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PARSE_INPUT_SHA256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
RULE_SET_ID = "SYNTHETIC_REPORT_RELEASE"
CATALOG_ID = "SYNTHETIC_RELEASE_FACTS"


def _compare(condition_id: str, fact_code: str, operator: str, value: int) -> dict[str, Any]:
    return {
        "id": condition_id,
        "kind": "compare",
        "description": f"Synthetic condition {condition_id}.",
        "enabled": True,
        "children": [],
        "left": {"kind": "fact", "factCode": fact_code, "children": []},
        "operator": operator,
        "right": {"kind": "literal", "value": value, "children": []},
        "nullPolicy": "indeterminate",
    }


def _active_rule(
    code: str,
    condition: dict[str, Any],
    outcome: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "ruleCode": code,
        "priority": 10,
        "title": f"Synthetic {code}",
        "status": "active",
        "when": condition,
        "outcome": outcome,
        "reasonCode": reason,
        "failureReason": "Synthetic rule did not match.",
        "recommendations": ["Use synthetic inputs."],
        "blockingIssueIds": [],
    }


def _stage_semantics() -> dict[str, Any]:
    terminate = {"onHit": "terminate", "unknown": "indeterminate"}
    return {
        "stateGuards": terminate,
        "prerequisites": terminate,
        "eligibility": {"onHit": "firstMatchThenContinue", "unknown": "indeterminate"},
        "postGates": terminate,
        "exclusions": terminate,
    }


def _catalog_payload(digest: str) -> dict[str, Any]:
    return {
        "contractVersion": "3.0.0",
        "catalogId": CATALOG_ID,
        "catalogVersion": "2026-09-17.1",
        "catalogDigest": digest,
        "facts": [
            {
                "factCode": "task.status_code",
                "name": "Synthetic task status",
                "description": "Synthetic offline status fact.",
                "dataType": "integer",
                "nullable": True,
                "nullPolicy": "indeterminate",
                "grain": "task",
                "parameters": [
                    {
                        "name": "taskId",
                        "role": "entityKey",
                        "dataType": "string",
                        "required": True,
                        "description": "Synthetic task key.",
                    }
                ],
                "allowedValues": [],
                "unit": None,
                "evidenceRefs": ["confirmation.status"],
                "bindingProfileRef": None,
                "bindingIssues": ["Synthetic fixture has no physical binding."],
            },
            {
                "factCode": "group.member_keys",
                "name": "Synthetic member keys",
                "description": "Synthetic offline member-key set.",
                "dataType": "list",
                "nullable": False,
                "nullPolicy": "fail",
                "grain": "group",
                "parameters": [
                    {
                        "name": "groupId",
                        "role": "entityKey",
                        "dataType": "string",
                        "required": True,
                        "description": "Synthetic group key.",
                    }
                ],
                "allowedValues": [],
                "unit": None,
                "evidenceRefs": ["confirmation.members"],
                "bindingProfileRef": None,
                "bindingIssues": ["Synthetic fixture has no physical binding."],
            },
        ],
        "evidence": [
            {
                "evidenceId": "confirmation.status",
                "sourceKind": "businessConfirmation",
                "sourceId": "synthetic-confirmation",
                "sourceSha256": SOURCE_FILE_SHA256,
                "locator": "row:1",
                "note": "Synthetic catalog evidence.",
            },
            {
                "evidenceId": "confirmation.members",
                "sourceKind": "businessConfirmation",
                "sourceId": "synthetic-confirmation",
                "sourceSha256": SOURCE_FILE_SHA256,
                "locator": "row:2",
                "note": "Synthetic catalog evidence.",
            },
        ],
    }


def _candidate_payload(digest: str) -> dict[str, Any]:
    return {
        "contractVersion": "3.1.0",
        "ruleSetId": RULE_SET_ID,
        "title": "Synthetic report release",
        "scope": "Offline complete-delivery intake tests only.",
        "catalogId": CATALOG_ID,
        "catalogVersion": "2026-09-17.1",
        "catalogDigest": digest,
        "sourceViews": ["synthetic_release_view"],
        "sourceIdentity": {
            "sourceFileSha256": SOURCE_FILE_SHA256,
            "parseInputSha256": PARSE_INPUT_SHA256,
            "extractorVersion": "synthetic-v1",
            "extractedSections": ["1.3", "5.1", "5.2"],
            "sourceFileByteLength": 32,
            "parseInputCharacterCount": 16,
        },
        "runtimeParameters": [
            {
                "name": "evaluationDate",
                "dataType": "date",
                "role": "evaluationClock",
                "required": True,
                "boundValue": None,
                "inclusive": True,
                "description": "Synthetic evaluation date parameter, not a database column.",
            },
            {
                "name": "rawDataReleasedCutoffDate",
                "dataType": "date",
                "role": "cutoff",
                "required": True,
                "boundValue": "2024-11-21",
                "inclusive": True,
                "description": "Synthetic cutoff date parameter, not a database column.",
            },
        ],
        "evaluationTimezone": "Asia/Shanghai",
        "requiredFactCodes": ["task.status_code", "group.member_keys"],
        "stages": [
            {
                "stage": "stateGuards",
                "rules": [
                    _active_rule(
                        "STATE_INVALID",
                        _compare("state-invalid", "task.status_code", "ne", 19),
                        "NO_RELEASE_REQUIRED",
                        "STATE_INVALID_RESULT",
                    )
                ],
            },
            {
                "stage": "prerequisites",
                "rules": [
                    _active_rule(
                        "STATUS_WAITING",
                        _compare("status-waiting", "task.status_code", "eq", 10),
                        "WAITING_COMPLETION",
                        "STATUS_WAITING_RESULT",
                    )
                ],
            },
            {
                "stage": "eligibility",
                "rules": [
                    _active_rule(
                        "STATUS_READY",
                        {
                            "id": "status-ready",
                            "kind": "compare",
                            "description": "Synthetic ready compare.",
                            "enabled": True,
                            "children": [],
                            "left": {
                                "kind": "parameter",
                                "parameterName": "evaluationDate",
                                "children": [],
                            },
                            "operator": "gte",
                            "right": {
                                "kind": "parameter",
                                "parameterName": "rawDataReleasedCutoffDate",
                                "children": [],
                            },
                            "nullPolicy": "indeterminate",
                        },
                        "READY",
                        "STATUS_READY_RESULT",
                    )
                ],
            },
            {
                "stage": "postGates",
                "rules": [
                    _active_rule(
                        "MEMBERS_INCOMPLETE",
                        {
                            "id": "members-incomplete",
                            "kind": "allMembers",
                            "description": "Synthetic allMembers stays on the rule side.",
                            "enabled": True,
                            "children": [],
                            "collectionFactCode": "group.member_keys",
                            "emptyCollectionPolicy": "pass",
                            "duplicateMemberPolicy": "uniquePreserveOrder",
                            "missingMemberPolicy": "indeterminate",
                            "memberPredicate": _compare(
                                "member-status-ready",
                                "task.status_code",
                                "eq",
                                19,
                            ),
                        },
                        "WAITING_CONDITIONS",
                        "MEMBERS_INCOMPLETE_RESULT",
                    )
                ],
            },
            {
                "stage": "exclusions",
                "rules": [
                    _active_rule(
                        "CLOSED_TASK",
                        _compare("closed-task", "task.status_code", "eq", 20),
                        "NO_RELEASE_REQUIRED",
                        "CLOSED_TASK_RESULT",
                    )
                ],
            },
        ],
        "stageSemantics": _stage_semantics(),
        "defaultOutcome": "WAITING_CONDITIONS",
        "defaultReasonCode": "NO_RELEASE_PATH_MATCHED",
        "blockingIssues": [],
        "proposedFacts": [],
    }


def _query(
    fact_code: str, data_type: str, cardinality: str, grain: str, key: str
) -> dict[str, Any]:
    return {
        "entity": {
            "entityType": grain,
            "grain": grain,
            "keyParameters": [key],
            "evidenceIds": [f"query.{fact_code}"],
        },
        "fields": [
            {
                "fieldId": "factValue",
                "role": "value",
                "logicalName": fact_code,
                "dataType": data_type,
                "required": True,
                "evidenceIds": [f"query.{fact_code}"],
            }
        ],
        "filters": {
            "items": [],
            "completeness": "complete",
            "evidenceIds": [f"query.{fact_code}"],
        },
        "aggregation": {
            "mode": "none",
            "function": None,
            "inputFieldIds": [],
            "groupByFieldIds": [],
            "distinct": None,
            "evidenceIds": [f"query.{fact_code}"],
        },
        "timeRange": {
            "mode": "none",
            "timeFieldId": None,
            "start": None,
            "end": None,
            "timezone": None,
            "evidenceIds": [f"query.{fact_code}"],
        },
        "result": {
            "columnName": "fact_value",
            "dataType": data_type,
            "cardinality": cardinality,
            "nullable": data_type != "list",
            "nullPolicy": "indeterminate" if data_type != "list" else "fail",
            "unit": None,
        },
    }


def _bindable_fact(
    fact_code: str,
    name: str,
    data_type: str,
    grain: str,
    key: str,
    *,
    nullable: bool,
    null_policy: str,
) -> dict[str, Any]:
    return {
        "factCode": fact_code,
        "name": name,
        "factKind": "source",
        "dataType": data_type,
        "description": f"Synthetic {name}.",
        "nullable": nullable,
        "nullPolicy": null_policy,
        "grain": grain,
        "parameters": [
            {
                "name": key,
                "role": "entityKey",
                "dataType": "string",
                "required": True,
                "description": "Synthetic entity key.",
            }
        ],
        "allowedValues": [],
        "unit": None,
    }


def _request_payload(
    *,
    rule_version: str,
    digest: str,
    candidate_sha: str,
    fact: dict[str, Any],
    query: dict[str, Any],
    usage: dict[str, Any],
    example_value: Any,
) -> dict[str, Any]:
    fact_code = fact["factCode"]
    return {
        "contractVersion": "3.1.0",
        "status": "candidate",
        "executable": False,
        "requestId": f"{rule_version}#{fact_code}",
        "ruleRef": {
            "ruleSetId": RULE_SET_ID,
            "ruleVersion": rule_version,
            "schemaVersion": "3.1.0",
            "sourceSha256": SOURCE_FILE_SHA256,
            "parseInputSha256": PARSE_INPUT_SHA256,
            "catalogDigest": digest,
            "candidatePayloadSha256": candidate_sha,
        },
        "fact": fact,
        "queryRequirements": query,
        "usages": [usage],
        "examples": [
            {
                "exampleId": "example-synthetic-001",
                "value": example_value,
                "expectedOutcome": usage["outcome"],
                "evidenceIds": [f"case.{fact_code}"],
            }
        ],
        "mappingCandidate": {
            "factCode": fact_code,
            "mappingStatus": "unresolved",
            "viewName": None,
            "viewField": None,
            "viewActive": None,
            "reviewStatus": "candidate",
            "note": "Physical source is resolved by metadataReview.",
        },
        "provenance": {
            "sourceName": "synthetic-optimization-plan.md",
            "relativePath": "synthetic.md",
            "sourceSha256": SOURCE_FILE_SHA256,
            "parseInputSha256": PARSE_INPUT_SHA256,
            "sourceFileByteLength": 32,
            "parseInputCharacterCount": 16,
            "extractorVersion": "synthetic-v1",
            "extractedSections": ["1.3", "5.1", "5.2"],
            "parserVersion": "0.13.0",
            "promptVersion": "optimization-plan-v31",
            "provider": "reviewed_import",
            "model": "optimization-plan-translator-v1",
        },
        "evidence": [
            {
                "evidenceId": f"query.{fact_code}",
                "kind": "queryRequirement",
                "sourceDocument": "ruleResult",
                "sourcePath": "/factDeclarations/0/query",
            },
            {
                "evidenceId": usage["evidenceIds"][0],
                "kind": "conditionUsage",
                "sourceDocument": "candidate",
                "sourcePath": usage["conditionPath"],
            },
            {
                "evidenceId": f"case.{fact_code}",
                "kind": "example",
                "sourceDocument": "ruleResult",
                "sourcePath": "/testCases/0",
            },
        ],
        "uncertainties": [],
        "targetDialect": "sqlserver",
        "requiresMetadataSnapshot": True,
        "tempTableAllowed": False,
    }


def build_synthetic_complete_delivery() -> tuple[StoredCompleteDeliveryV31, dict[str, Any]]:
    catalog_seed = _catalog_payload("0" * 64)
    digest = catalog_digest_sha256(catalog_seed)
    catalog_payload = _catalog_payload(digest)
    BusinessConfirmedFactCatalogV31.model_validate(catalog_payload)
    candidate_payload = _candidate_payload(digest)
    candidate = RuleStructureCandidateV31.model_validate(candidate_payload)
    candidate_sha = canonical_sha256(candidate)
    catalog = BusinessConfirmedFactCatalogV31.model_validate(catalog_payload)
    catalog_sha = canonical_sha256(catalog)
    timestamp = CREATED_AT.strftime("%Y%m%dT%H%M%S%fZ")
    rule_version = f"{RULE_SET_ID}@{timestamp}-{SOURCE_FILE_SHA256[:12]}-{digest[:12]}"
    status_fact = _bindable_fact(
        "task.status_code",
        "Synthetic task status",
        "integer",
        "task",
        "taskId",
        nullable=True,
        null_policy="indeterminate",
    )
    member_fact = _bindable_fact(
        "group.member_keys",
        "Synthetic member keys",
        "list",
        "group",
        "groupId",
        nullable=False,
        null_policy="fail",
    )
    result_payload = {
        "schemaVersion": "3.1.0",
        "ruleVersion": rule_version,
        "ruleSetId": RULE_SET_ID,
        "generatedAt": CREATED_AT.isoformat().replace("+00:00", "+00:00"),
        "status": "draft",
        "executable": False,
        "source": {
            "sourceName": "synthetic-optimization-plan.md",
            "relativePath": "synthetic.md",
            "sourceSha256": SOURCE_FILE_SHA256,
            "parseInputSha256": PARSE_INPUT_SHA256,
            "sourceFileByteLength": 32,
            "parseInputCharacterCount": 16,
            "extractorVersion": "synthetic-v1",
            "extractedSections": ["1.3", "5.1", "5.2"],
            "parserVersion": "0.13.0",
            "promptVersion": "optimization-plan-v31",
            "provider": "reviewed_import",
            "model": "optimization-plan-translator-v1",
        },
        "parser": {
            "parserVersion": "0.13.0",
            "promptVersion": "optimization-plan-v31",
            "provider": "reviewed_import",
            "model": "optimization-plan-translator-v1",
        },
        "catalogRef": {
            "catalogId": CATALOG_ID,
            "catalogVersion": "2026-09-17.1",
            "catalogDigest": digest,
            "payloadSha256": catalog_sha,
        },
        "candidateRef": {
            "payloadSha256": candidate_sha,
            "parseInputSha256": PARSE_INPUT_SHA256,
        },
        "deliveryRef": {
            "catalogPayloadSha256": catalog_sha,
            "candidatePayloadSha256": candidate_sha,
            "resultPayloadSha256": None,
            "purpose": "optimization-plan-generation",
        },
        "factDeclarations": [
            {
                "fact": status_fact,
                "query": _query("task.status_code", "integer", "scalar", "task", "taskId"),
                "uncertainties": [],
            },
            {
                "fact": member_fact,
                "query": _query("group.member_keys", "list", "set", "group", "groupId"),
                "uncertainties": [],
            },
        ],
        "testCases": [
            {
                "caseId": "not-completed",
                "description": "Synthetic incomplete task waits.",
                "given": {"task.status_code": 10, "group.member_keys": ["m1"]},
                "runtime": {"evaluationDate": "2026-09-17"},
                "members": [],
                "expectedOutcome": "NO_RELEASE_REQUIRED",
                "expectedReasonCode": "STATE_INVALID_RESULT",
                "expectedMatchedRuleCodes": ["STATE_INVALID"],
            }
        ],
        "agent2ReadinessReady": True,
    }
    RuleParseResultV31.model_validate(result_payload)
    status_request = _request_payload(
        rule_version=rule_version,
        digest=digest,
        candidate_sha=candidate_sha,
        fact=status_fact,
        query=_query("task.status_code", "integer", "scalar", "task", "taskId"),
        usage={
            "stage": "stateGuards",
            "ruleCode": "STATE_INVALID",
            "priority": 10,
            "conditionId": "state-invalid",
            "conditionPath": "/stages/0/rules/0/when",
            "outcome": "NO_RELEASE_REQUIRED",
            "evidenceIds": ["condition.state-invalid"],
        },
        example_value=10,
    )
    member_request = _request_payload(
        rule_version=rule_version,
        digest=digest,
        candidate_sha=candidate_sha,
        fact=member_fact,
        query=_query("group.member_keys", "list", "set", "group", "groupId"),
        usage={
            "stage": "postGates",
            "ruleCode": "MEMBERS_INCOMPLETE",
            "priority": 10,
            "conditionId": "members-incomplete",
            "conditionPath": "/stages/3/rules/0/when",
            "outcome": "WAITING_CONDITIONS",
            "evidenceIds": ["condition.members-incomplete"],
        },
        example_value=["m1"],
    )
    payloads = [
        FactBindingRequestV31.model_validate(status_request),
        FactBindingRequestV31.model_validate(member_request),
    ]
    wrappers = [
        StoredFactBindingHandoffRequestV31(
            request_id=payload.request_id,
            rule_version=payload.rule_ref.rule_version,
            fact_code=payload.fact.fact_code,
            contract_version="3.1.0",
            payload_sha256=canonical_sha256(payload),
            created_at=CREATED_AT,
            payload=payload,
        )
        for payload in payloads
    ]
    wrappers.sort(key=lambda item: item.request_id)
    batch = StoredFactBindingHandoffBatchV31(
        mongo_id=rule_version,
        rule_version=rule_version,
        contract_version="3.1.0",
        request_count=len(wrappers),
        request_ids=[item.request_id for item in wrappers],
        batch_sha256=canonical_sha256(
            [
                {"requestId": item.request_id, "payloadSha256": item.payload_sha256}
                for item in wrappers
            ]
        ),
        created_at=CREATED_AT,
        requests=wrappers,
    )
    stored = StoredCompleteDeliveryV31(
        rule_version=rule_version,
        schema_version="3.1.0",
        purpose="optimization-plan-generation",
        status="draft",
        executable=False,
        source_file_sha256=SOURCE_FILE_SHA256,
        parse_input_sha256=PARSE_INPUT_SHA256,
        catalog_digest=digest,
        catalog_payload=catalog_payload,
        candidate_payload=candidate.model_dump(by_alias=True, mode="json"),
        result_payload=result_payload,
        batch=batch,
    )
    return stored, {
        "ruleVersion": rule_version,
        "catalogDigest": digest,
        "candidatePayloadSha256": candidate_sha,
        "statusRequest": status_request,
        "memberRequest": member_request,
    }


def build_synthetic_mapping_bundle() -> ViewShapedMappingBundleV31:
    task_key = KeyBindingV31(parameter_name="taskId", column_name="taskId")
    group_key = KeyBindingV31(parameter_name="groupId", column_name="groupId")
    return ViewShapedMappingBundleV31(
        subject=SubjectRelationV31(
            schema_name="synth",
            relation_name="synth_task",
            key_bindings=(task_key,),
        ),
        grants=(
            FactMappingGrantV31(
                fact_code="task.status_code",
                schema_name="synth",
                relation_name="synth_task_status",
                column_name="status_code",
                cardinality="scalar",
                key_bindings=(task_key,),
            ),
            FactMappingGrantV31(
                fact_code="group.member_keys",
                schema_name="synth",
                relation_name="synth_group_members",
                column_name="member_key",
                cardinality="set",
                key_bindings=(group_key,),
            ),
        ),
    )


def copy_stored(stored: StoredCompleteDeliveryV31) -> dict[str, Any]:
    return deepcopy(stored.model_dump(by_alias=True, mode="python"))
