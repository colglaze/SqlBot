from __future__ import annotations

from copy import deepcopy

from release_sql_bot.application.metadata_resolution_v2 import resolve_metadata_v2
from release_sql_bot.domain.project_bindings_v2 import ResolveMetadataRequestV2
from tests.phase2g_support import (
    refresh_gap_report,
    reseal_context,
    reseal_snapshot_and_context,
    resolve_metadata_payload,
)


def _resolve(payload):
    return resolve_metadata_v2(ResolveMetadataRequestV2.model_validate(payload))


def _codes(report):
    return {item.code for item in report.blocking_issues}


def test_approved_hash_closed_single_relation_is_metadata_resolved() -> None:
    report = _resolve(resolve_metadata_payload())

    assert report.status == "metadataResolved"
    assert report.executable is False
    assert report.blocking_issues == ()
    assert [(item.field_id, item.role.value) for item in report.resolved_bindings] == [
        ("entity.projectId", "entityKey"),
        ("factValue", "value"),
        ("filter.projectId", "filter"),
    ]
    assert [(item.parameter_name, item.field_id) for item in report.resolved_entity_keys] == [
        ("projectId", "entity.projectId")
    ]
    assert report.result_source is not None
    assert report.result_source.mode == "column"
    assert report.authorized_joins == ()
    mapping_disposition = next(
        item
        for item in report.candidate_evidence_dispositions
        if item.evidence_path == "/bindingRequest/mappingCandidate"
    )
    assert mapping_disposition.disposition == "consistent"


def test_upstream_blocking_is_preserved_and_stops_physical_resolution() -> None:
    payload = resolve_metadata_payload(blocked=True)
    original_uncertainties = deepcopy(payload["bindingRequest"]["uncertainties"])

    report = _resolve(payload)

    assert report.status == "blocked"
    assert "UPSTREAM_BINDING_BLOCKED" in _codes(report)
    assert {
        "ENTITY_KEY_UNRESOLVED",
        "VALUE_FIELD_UNRESOLVED",
        "FILTER_FIELD_UNRESOLVED",
        "FILTER_SET_INCOMPLETE",
        "AGGREGATION_UNRESOLVED",
        "TIME_RANGE_UNRESOLVED",
    } <= _codes(report)
    assert report.resolved_bindings == ()
    assert [item.model_dump(by_alias=True, mode="json") for item in report.uncertainties] == (
        original_uncertainties
    )


def test_hash_reference_status_and_scope_gates_fail_closed() -> None:
    hash_tamper = resolve_metadata_payload()
    hash_tamper["projectContext"]["authorizationPolicyVersion"] = "tampered-policy"
    assert "CONTEXT_HASH_MISMATCH" in _codes(_resolve(hash_tamper))

    scope = resolve_metadata_payload()
    scope["projectContext"]["projectRef"]["projectId"] = "project.other"
    reseal_context(scope)
    assert "CONTEXT_SCOPE_MISMATCH" in _codes(_resolve(scope))

    draft = resolve_metadata_payload()
    draft["metadataSnapshot"]["status"] = "draft"
    reseal_snapshot_and_context(draft)
    assert "SNAPSHOT_NOT_APPROVED" in _codes(_resolve(draft))

    wrong_ref = resolve_metadata_payload()
    wrong_ref["projectContext"]["metadataSnapshotRef"]["snapshotVersion"] = 2
    reseal_context(wrong_ref)
    assert "SNAPSHOT_REF_MISMATCH" in _codes(_resolve(wrong_ref))


def test_forged_gap_report_cannot_bypass_recomputation() -> None:
    payload = resolve_metadata_payload()
    payload["bindingGapReport"]["status"] = "blocked"

    report = _resolve(payload)

    assert report.status == "blocked"
    assert "GAP_REPORT_MISMATCH" in _codes(report)


def test_missing_duplicate_and_wrong_role_field_authorizations_are_blocked() -> None:
    missing = resolve_metadata_payload()
    missing["projectContext"]["fieldBindingAuthorizations"] = missing["projectContext"][
        "fieldBindingAuthorizations"
    ][1:]
    reseal_context(missing)
    assert "FIELD_AUTHORIZATION_MISSING" in _codes(_resolve(missing))

    duplicate = resolve_metadata_payload()
    extra = deepcopy(duplicate["projectContext"]["fieldBindingAuthorizations"][0])
    extra["authorizationId"] = "field.synthetic.fact-value.second"
    duplicate["projectContext"]["fieldBindingAuthorizations"].append(extra)
    reseal_context(duplicate)
    assert "FIELD_AUTHORIZATION_AMBIGUOUS" in _codes(_resolve(duplicate))

    wrong_role = resolve_metadata_payload()
    wrong_role["projectContext"]["fieldBindingAuthorizations"][0]["role"] = "groupBy"
    reseal_context(wrong_role)
    assert "FIELD_ROLE_MISMATCH" in _codes(_resolve(wrong_role))

    missing_entity = resolve_metadata_payload()
    missing_entity["projectContext"]["entityKeyAuthorizations"] = []
    reseal_context(missing_entity)
    assert "ENTITY_KEY_AUTHORIZATION_MISSING" in _codes(_resolve(missing_entity))


def test_snapshot_column_and_sql_type_must_match_exactly() -> None:
    unknown = resolve_metadata_payload()
    unknown["projectContext"]["columnGrants"][0]["columnName"] = "missing_amount"
    reseal_context(unknown)
    assert "COLUMN_NOT_IN_SNAPSHOT" in _codes(_resolve(unknown))

    incompatible = resolve_metadata_payload()
    incompatible["metadataSnapshot"]["relations"][0]["columns"][0]["sqlType"] = "nvarchar(40)"
    reseal_snapshot_and_context(incompatible)
    assert "SQL_TYPE_INCOMPATIBLE" in _codes(_resolve(incompatible))


def test_identifier_case_policy_is_explicit_and_deterministic() -> None:
    insensitive = resolve_metadata_payload()
    insensitive["projectContext"]["relationGrants"][0]["relationName"] = "SYNTHETIC_REPORT_AMOUNTS"
    insensitive["projectContext"]["columnGrants"][0]["columnName"] = "TOTAL_AMOUNT"
    reseal_context(insensitive)
    assert _resolve(insensitive).status == "metadataResolved"

    sensitive = deepcopy(insensitive)
    sensitive["metadataSnapshot"]["identifierCaseSensitivity"] = "sensitive"
    reseal_snapshot_and_context(sensitive)
    assert "RELATION_NOT_IN_SNAPSHOT" in _codes(_resolve(sensitive))


def test_two_relations_require_one_snapshot_edge_and_one_explicit_join_grant() -> None:
    approved = _resolve(resolve_metadata_payload(second_relation=True))

    assert approved.status == "metadataResolved"
    assert [item.grant_id for item in approved.authorized_joins] == [
        "join.synthetic.amounts-projects"
    ]

    not_granted = _resolve(resolve_metadata_payload(second_relation=True, include_join=False))
    assert "JOIN_PATH_NOT_GRANTED" in _codes(not_granted)

    no_snapshot_edge = resolve_metadata_payload(second_relation=True)
    no_snapshot_edge["metadataSnapshot"]["relationships"] = []
    reseal_snapshot_and_context(no_snapshot_edge)
    assert "JOIN_PATH_UNRESOLVED" in _codes(_resolve(no_snapshot_edge))


def test_candidate_evidence_is_disposed_but_never_grants_access() -> None:
    conflict = resolve_metadata_payload()
    conflict["bindingRequest"]["queryRequirements"]["fields"][0]["sourceCandidate"]["fieldName"] = (
        "project_id"
    )
    refresh_gap_report(conflict)

    report = _resolve(conflict)

    assert report.status == "metadataResolved"
    assert "CANDIDATE_EVIDENCE_CONFLICT" in {item.code for item in report.warnings}
    assert "conflict" in {item.disposition for item in report.candidate_evidence_dispositions}

    candidate_only = resolve_metadata_payload()
    candidate_only["projectContext"]["fieldBindingAuthorizations"] = candidate_only[
        "projectContext"
    ]["fieldBindingAuthorizations"][1:]
    reseal_context(candidate_only)
    blocked = _resolve(candidate_only)
    assert blocked.status == "blocked"
    assert "FIELD_AUTHORIZATION_MISSING" in _codes(blocked)


def test_resolver_is_deterministic_and_does_not_mutate_inputs() -> None:
    payload = resolve_metadata_payload()
    model = ResolveMetadataRequestV2.model_validate(payload)
    before = model.model_dump(by_alias=True, mode="json")

    first = resolve_metadata_v2(model)
    second = resolve_metadata_v2(model)

    assert first == second
    assert model.model_dump(by_alias=True, mode="json") == before
    assert first.hashes.request_sha256 == first.binding_gap_report.hashes.request_sha256
    assert first.hashes.payload_sha256 == first.binding_gap_report.hashes.payload_sha256
