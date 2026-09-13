"""V3 offline SQL AST static validation (M4 first delivery).

Pure computation. Re-runs M2, verifies references/hashes, inspects AST
through the V3 inspector port with detailed WHERE analysis.
Application layer converts port-level inspection results to domain-level
Pydantic contracts.

M4 scope: single-relation source queries only.
"""

from __future__ import annotations

from hashlib import sha256
from importlib.metadata import version

from release_sql_bot.application.canonical import (
    canonical_content_sha256,
    canonical_sha256,
)
from release_sql_bot.application.metadata_resolution_v3 import resolve_metadata_v3
from release_sql_bot.application.ports.sql_ast_v3 import (
    OfflineRelationV3,
    SqlDialectInspectorV3,
    SqlGatePolicyV3,
    SqlInspectionRequestV3,
)
from release_sql_bot.domain.sql_validation_v3 import (
    SqlCandidateValidationRefV3,
    SqlComparisonEvidenceV3,
    SqlInspectionSummaryV3,
    SqlParserRefV3,
    SqlPhysicalObjectEvidenceV3,
    SqlPlaceholderEvidenceV3,
    SqlResultColumnEvidenceV3,
    SqlStaticValidationReportV3,
    SqlValidationIssueV3,
    ValidateSqlCandidateRequestV3,
)
from release_sql_bot.infrastructure.sql.sqlglot_tsql_v3 import SqlglotTsqlInspectorV3


class SqlValidationStructureError3(Exception):
    def __init__(self):
        super().__init__("SQL_VALIDATION_INPUT_STRUCTURE_INVALID")
        self.code = "SQL_VALIDATION_INPUT_STRUCTURE_INVALID"


def _fallback():
    return SqlParserRefV3(
        name="sqlglot",
        exactVersion=version("sqlglot"),
        dialect="tsql",
        gateVersion="sqlserver-ast-safety-v1",
    )


def _cand_ref(p):
    return SqlCandidateValidationRefV3(
        candidateContentSha256=canonical_content_sha256(p.candidate),
        sqlTemplateSha256=sha256(p.candidate.sql_template.encode("utf-8")).hexdigest(),
        generationInputSha256=canonical_sha256(p.generation_request),
        resolutionReportSha256=canonical_sha256(p.generation_request.resolution_report),
        contextSha256=canonical_content_sha256(
            p.generation_request.resolution_request.project_context
        ),
        snapshotSha256=canonical_content_sha256(
            p.generation_request.resolution_request.metadata_snapshot
        ),
    )


def _reval(p):
    if not isinstance(p, ValidateSqlCandidateRequestV3):
        raise SqlValidationStructureError3()
    try:
        return ValidateSqlCandidateRequestV3.model_validate(
            p.model_dump(by_alias=True, mode="json", warnings="error")
        )
    except Exception:
        raise SqlValidationStructureError3() from None


def _expected_rels(r):
    s = {(x.schema_name, x.relation_name) for x in r.resolved_fields}
    for x in r.resolved_entity_keys:
        s.add((x.schema_name, x.relation_name))
    return s


def _entity_keys(r):
    return {
        k.parameter_name: (k.schema_name, k.relation_name, k.column_name)
        for k in r.resolved_entity_keys
    }


def _offline(p):
    snap = p.generation_request.resolution_request.metadata_snapshot

    def cols(r):
        return tuple(
            type("C", (), {"name": c.column_name, "sql_type": c.sql_type})() for c in r.columns
        )

    return tuple(
        OfflineRelationV3(schema_name=r.schema_name, relation_name=r.relation_name, columns=cols(r))
        for r in snap.relations
    )


# Gate order constants for issue ordering
_GATE_INPUT = 1
_GATE_REF = 2
_GATE_AST = 5
_GATE_WHERE = 8
_GATE_RESULT = 9


def _issue(code, message, gate=_GATE_REF, path="/", nid=None):
    return SqlValidationIssueV3(
        gateOrder=gate, code=code, fieldPath=path, message=message, normalizedIdentifier=nid
    )


def _convert_port_issue(port_issue, gate_order, field_path):
    """Convert a port-level SqlInspectionIssueV3 to domain SqlValidationIssueV3."""
    return SqlValidationIssueV3(
        gateOrder=gate_order,
        code=port_issue.code,
        fieldPath=field_path,
        message=port_issue.message,
        normalizedIdentifier=port_issue.normalized_identifier,
    )


def _convert_summary(port_summary):
    """Convert port-level SqlInspectionSummaryV3 to domain SqlInspectionSummaryV3."""
    return SqlInspectionSummaryV3(
        statementCount=port_summary.statement_count,
        rootKind=port_summary.root_kind,
        nodeCount=port_summary.node_count,
        maxDepth=port_summary.max_depth,
        physicalObjects=tuple(
            SqlPhysicalObjectEvidenceV3(
                schemaName=o.schema_name,
                relationName=o.relation_name,
                expressionPath=o.expression_path,
            )
            for o in port_summary.physical_objects
        ),
        resultColumns=tuple(
            SqlResultColumnEvidenceV3(
                alias=r.alias,
                expressionPath=r.expression_path,
                sourceSchema=r.source_schema,
                sourceRelation=r.source_relation,
                sourceColumn=r.source_column,
            )
            for r in port_summary.result_columns
        ),
        placeholders=tuple(
            SqlPlaceholderEvidenceV3(
                name=p.name, expressionPath=p.expression_path, enclosingClause=p.enclosing_clause
            )
            for p in port_summary.placeholders
        ),
        comparisons=tuple(
            SqlComparisonEvidenceV3(
                expressionPath=c.expression_path,
                operator=c.operator,
                leftSchema=c.left_schema,
                leftRelation=c.left_relation,
                leftColumn=c.left_column,
                rightSchema=c.right_schema,
                rightRelation=c.right_relation,
                rightColumn=c.right_column,
                leftParameter=c.left_parameter,
                rightParameter=c.right_parameter,
                leftIsLiteral=c.left_is_literal,
                rightIsLiteral=c.right_is_literal,
                leftLiteralValue=c.left_literal_value,
                rightLiteralValue=c.right_literal_value,
            )
            for c in port_summary.comparisons
        ),
        features=port_summary.features,
    )


def _ref_issues(p, r):
    g = p.generation_request
    carried = g.resolution_report
    cand = p.candidate
    binding = g.resolution_request.binding_request
    issues = []

    blk = [x for x in r.issues if x.impact == "blocker"]
    if r.status != "metadataResolved" or r.executable is not False or blk:
        issues.append(
            _issue(
                "RESOLUTION_NOT_READY", "Phase 2G V3 重算结果不是无阻断的 metadataResolved 闭包。"
            )
        )

    if canonical_sha256(carried) != canonical_sha256(r):
        issues.append(
            _issue("REF_MISMATCH", "携带的 Phase 2G V3 报告与当前完整输入重算结果不一致。")
        )

    if cand.content_sha256 != canonical_content_sha256(cand):
        issues.append(_issue("CAND_HASH", "候选自哈希与候选内容不一致。"))

    if cand.generation_input_sha256 != canonical_sha256(g):
        issues.append(_issue("REF_MISMATCH", "生成输入哈希不匹配。"))

    if cand.resolution_ref.report_sha256 != canonical_sha256(r):
        issues.append(_issue("REF_MISMATCH", "解析报告哈希不匹配。"))

    refs_ok = (
        cand.request_ref.request_id == binding.request_id
        and cand.request_ref.payload_sha256 == r.handoff_refs.payload_sha256
        and cand.rule_ref.rule_set_id == binding.rule_ref.rule_set_id
        and cand.rule_ref.rule_version == binding.rule_ref.rule_version
        and cand.rule_ref.schema_version == "3.0.0"
        and cand.rule_ref.source_sha256 == binding.rule_ref.source_sha256
        and cand.rule_ref.catalog_digest == binding.rule_ref.catalog_digest
        and cand.rule_ref.candidate_payload_sha256 == binding.rule_ref.candidate_payload_sha256
        and cand.project_ref.project_id == r.project_ref.project_id
        and cand.project_ref.project_version == r.project_ref.project_version
        and cand.resolution_ref.context_ref.context_id == r.context_ref.context_id
        and cand.resolution_ref.context_ref.context_version == r.context_ref.context_version
        and cand.resolution_ref.context_ref.sha256 == r.context_ref.sha256
        and cand.resolution_ref.metadata_snapshot_ref.snapshot_id == r.snapshot_ref.snapshot_id
        and cand.resolution_ref.metadata_snapshot_ref.snapshot_version
        == r.snapshot_ref.snapshot_version
        and cand.resolution_ref.metadata_snapshot_ref.sha256 == r.snapshot_ref.sha256
        and cand.fact_ref.fact_code == binding.fact.fact_code
        and cand.fact_ref.fact_kind is binding.fact.fact_kind
        and cand.fact_ref.data_type is binding.fact.data_type
        and cand.fact_ref.grain == binding.fact.grain
        and cand.status == "candidate"
        and cand.executable is False
        and cand.review_status == "pending"
        and cand.dialect == "sqlserver"
    )
    if not refs_ok:
        issues.append(_issue("REF_MISMATCH", "候选引用或声明与 V3 生成输入闭包不一致。"))

    apv = g.resolution_request.project_context.authorization_policy_version
    if cand.resolution_ref.authorization_policy_version != apv:
        issues.append(_issue("REF_MISMATCH", "授权策略版本不一致。"))

    hr = cand.handoff_refs
    if hr.batch_sha256 != r.handoff_refs.batch_sha256:
        issues.append(_issue("REF_MISMATCH", "batchSha256 不一致。"))
    if hr.payload_sha256 != r.handoff_refs.payload_sha256:
        issues.append(_issue("REF_MISMATCH", "payloadSha256 不一致。"))
    if hr.contract_schema_id != r.handoff_refs.contract_schema_id:
        issues.append(_issue("REF_MISMATCH", "contractSchemaId 不一致。"))
    if hr.contract_schema_sha256 != r.handoff_refs.contract_schema_sha256:
        issues.append(_issue("REF_MISMATCH", "contractSchemaSha256 不一致。"))

    ep = {
        (x.name, str(x.data_type), x.required, f"fact.parameters.{x.name}")
        for x in binding.fact.parameters
    }
    ap = {(x.name, str(x.data_type), x.required, x.source) for x in cand.parameters}
    if ap != ep:
        issues.append(_issue("REF_MISMATCH", "候选参数声明与 V3 事实参数不一致。"))

    er = binding.query_requirements.result
    res = cand.result
    if (
        str(res.column_name) != str(er.column_name)
        or str(res.data_type) != str(er.data_type)
        or str(res.cardinality) != str(er.cardinality)
        or res.nullable is not er.nullable
        or str(res.null_policy) != str(er.null_policy)
        or res.unit != er.unit
    ):
        issues.append(_issue("REF_MISMATCH", "候选结果声明与 V3 结果契约不一致。"))

    dr = {(o.schema_name, o.relation_name) for o in cand.declared_objects}
    if dr != _expected_rels(r):
        issues.append(_issue("REF_MISMATCH", "候选对象声明与 V3 解析关系闭包不一致。"))

    eu = [
        (str(u.stage), u.rule_code, u.priority, u.condition_id, u.condition_path, str(u.outcome))
        for u in binding.usages
    ]
    au = [
        (str(u.stage), u.rule_code, u.priority, u.condition_id, u.condition_path, str(u.outcome))
        for u in cand.declared_usage_coverage
    ]
    if au != eu:
        issues.append(_issue("USAGE_COVERAGE", "候选 usage 六元组覆盖与 V3 请求不一致。"))

    if cand.usage_traceability_sha256 != r.usage_traceability_sha256:
        issues.append(_issue("USAGE_TRACE", "候选 usage 追溯摘要与 V3 请求重算结果不一致。"))

    return issues


def validate_sql_candidate_v3(
    payload: ValidateSqlCandidateRequestV3,
    inspector: SqlDialectInspectorV3 | None = None,
) -> SqlStaticValidationReportV3:
    try:
        v = _reval(payload)
    except SqlValidationStructureError3:
        raise

    if inspector is None:
        inspector = SqlglotTsqlInspectorV3()

    r = resolve_metadata_v3(v.generation_request.resolution_request)
    issues = _ref_issues(v, r)

    if issues:
        return SqlStaticValidationReportV3(
            status="blocked",
            candidateRef=_cand_ref(v),
            parserRef=_fallback(),
            issues=tuple(sorted(set(issues), key=lambda i: (i.gate_order, i.code))),
            inspection=None,
            usageTraceabilitySha256=r.usage_traceability_sha256,
            handoffRefs={
                "batchSha256": r.handoff_refs.batch_sha256,
                "payloadSha256": r.handoff_refs.payload_sha256,
            },
        )

    sens = v.generation_request.resolution_request.metadata_snapshot.identifier_case_sensitivity
    result = inspector.inspect(
        SqlInspectionRequestV3(
            sql=v.candidate.sql_template,
            dialect="tsql",
            identifier_case_sensitivity=sens,
            offline_schema=_offline(v),
            gate_policy=SqlGatePolicyV3(),
        )
    )

    # Convert port-level issues to domain-level with proper gateOrder/fieldPath
    for pi in result.issues:
        converted = _convert_port_issue(pi, gate_order=_GATE_AST, field_path="/ast")
        issues.append(converted)

    # Convert summary
    summary = _convert_summary(result.summary)

    # Semantic checks
    issues.extend(_semantic(v, r, summary))

    si = tuple(sorted(set(issues), key=lambda i: (i.gate_order, i.code)))
    return SqlStaticValidationReportV3(
        status="blocked" if si else "passed",
        candidateRef=_cand_ref(v),
        parserRef=SqlParserRefV3(
            name=inspector.parser_name,
            exactVersion=inspector.parser_version,
            dialect=inspector.dialect,
            gateVersion=inspector.gate_version,
        ),
        issues=si,
        inspection=summary,
        usageTraceabilitySha256=r.usage_traceability_sha256,
        handoffRefs={
            "batchSha256": r.handoff_refs.batch_sha256,
            "payloadSha256": r.handoff_refs.payload_sha256,
        },
    )


def _semantic(p, r, s):
    issues = []
    binding = p.generation_request.resolution_request.binding_request

    if s.statement_count != 1:
        issues.append(_issue("SQL_STMT_COUNT", "SQL 必须恰好一条语句。", _GATE_AST))
    if s.root_kind != "Select":
        issues.append(_issue("SQL_ROOT", "SQL 根节点必须是 SELECT。", _GATE_AST))
    if len(s.physical_objects) != 1:
        issues.append(_issue("SQL_OBJ_COUNT", "SQL 必须恰好一个物理对象。", _GATE_AST))

    bad = {
        "forbidden",
        "into",
        "cte",
        "distinct",
        "group",
        "having",
        "top",
        "join",
        "union",
        "setOp",
        "subquery",
        "star",
        "or",
        "not",
        "temp",
        "crossDb",
        "unknownObj",
        "agg",
        "case",
        "arith",
    }
    for f in s.features:
        if f in bad or f.startswith("forbidden:"):
            issues.append(_issue("SQL_BAD_FEAT", f"禁止特征：{f}", _GATE_AST))

    if len(s.result_columns) != 1:
        issues.append(_issue("SQL_RES_SHAPE", "SQL 必须恰好一个结果列。", _GATE_RESULT))
    else:
        rc = s.result_columns[0]
        if rc.alias != "fact_value":
            issues.append(_issue("SQL_RES_ALIAS", "结果列别名必须是 fact_value。", _GATE_RESULT))
        else:
            vf_list = [f for f in r.resolved_fields if f.field_id == "factValue"]
            if vf_list:
                vf = vf_list[0]
                if (rc.source_schema, rc.source_relation, rc.source_column) != (
                    vf.schema_name,
                    vf.relation_name,
                    vf.column_name,
                ):
                    issues.append(
                        _issue(
                            "SQL_RES_SRC", "fact_value 投影未证明依赖授权结果来源。", _GATE_RESULT
                        )
                    )

    ep = {x.name for x in binding.fact.parameters}
    ap = {pl.name for pl in s.placeholders if pl.name is not None}
    if ap != ep:
        issues.append(_issue("SQL_PARAM_MISMATCH", "AST 参数与事实参数不一致。", _GATE_WHERE))

    issues.extend(_check_keys(s, r))
    return issues


def _check_keys(s, r):
    """Check WHERE contains exactly entity key = :param conditions.

    All comparisons collected and validated individually.
    No dict overwrite that could mask duplicates.
    """
    issues = []
    expected = _entity_keys(r)
    if not expected:
        return issues

    # Collect all valid (param -> col) bindings
    actual_bindings: list[tuple[str, tuple[str | None, str | None, str | None]]] = []
    params_seen: dict[str, int] = {}

    for comp in s.comparisons:
        # Self-comparison check
        if comp.left_parameter and comp.right_parameter:
            issues.append(_issue("SQL_SELF_CMP", "参数不能自比较。", _GATE_WHERE))
            continue

        # Literal check
        if comp.left_is_literal or comp.right_is_literal:
            issues.append(_issue("SQL_LITERAL", "条件不能使用字面值。", _GATE_WHERE))
            continue

        # Must be column = :parameter
        col_side = None
        param_side = None
        if comp.left_column and comp.right_parameter:
            col_side = (comp.left_schema, comp.left_relation, comp.left_column)
            param_side = comp.right_parameter
        elif comp.right_column and comp.left_parameter:
            col_side = (comp.right_schema, comp.right_relation, comp.right_column)
            param_side = comp.left_parameter

        if col_side is None or param_side is None:
            issues.append(_issue("SQL_CMP_SHAPE", "条件必须是列与参数的比较。", _GATE_WHERE))
            continue

        if comp.operator != "eq":
            issues.append(
                _issue(
                    "SQL_CMP_OP",
                    f"实体键条件必须使用等值比较，不支持 {comp.operator}。",
                    _GATE_WHERE,
                )
            )
            continue

        # Track duplicate params
        params_seen[param_side] = params_seen.get(param_side, 0) + 1
        if params_seen[param_side] > 1:
            issues.append(
                _issue("SQL_KEY_DUPLICATE", f"参数 {param_side} 存在重复条件。", _GATE_WHERE)
            )

        actual_bindings.append((param_side, col_side))

    # Check coverage
    for pn, (es, er, ec) in expected.items():
        matching = [(p, c) for p, c in actual_bindings if p == pn]
        if not matching:
            issues.append(_issue("SQL_KEY_MISSING", f"缺少实体键条件：{pn}。", _GATE_WHERE))
        elif matching[0][1] != (es, er, ec):
            issues.append(_issue("SQL_KEY_WRONG", f"实体键 {pn} 绑定了错误列。", _GATE_WHERE))

    for pn, _ in actual_bindings:
        if pn not in expected:
            issues.append(_issue("SQL_KEY_EXTRA", f"多余实体键条件：{pn}。", _GATE_WHERE))

    return issues
