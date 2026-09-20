# DEV-20260920-03：Schema v6 只读适配、独立求值与三值 SQL

- 状态：`completed`
- 日期：2026-09-20
- 实现需求：[REQ-20260920-03](../requirements/REQ-20260920-03-v31-mongo-evaluate-indeterminate.md)
- 业务决策：[BIZ-20260920-02](../decisions/BIZ-20260920-02-authorized-v31-followthrough.md)
- 不修改：`sqlglot_tsql_v3.py`、M3 `generate_sql_candidate_v3`、RuleAgent 生产代码

## 1. Mongo 只读

```text
infrastructure/complete_delivery_mongo_v31.py
  读 rule_versions_v3(_id=ruleVersion) 与 fact_binding_handoff_batches_v3
  schema_version 必须 3.1.0；3.0.0 batch 返回缺失
  映射为 StoredCompleteDeliveryV31，错误不含 payload
```

集合名可配置，默认与 Agent1 相同。`find_one` 按 `_id`。SqlBot 不 insert/update。

## 2. 求值器

`application/rule_evaluate_v31.py` 独立实现 Agent1 `evaluate_rule_structure_v31` 语义：

- stateGuards / prerequisites / postGates / exclusions：命中即停
- eligibility：首个 PASS 后继续后续阶段
- 阶段内无 PASS 但有 INDETERMINATE → `INDETERMINATE` / `FACT_VALUE_MISSING_OR_INVALID`
- `allMembers`：空集 + `emptyCollectionPolicy=pass` 为真；成员缺失按 `missingMemberPolicy`

公开测试只用合成 fixture。

## 3. SQL 三值命中位

```text
CASE WHEN pred THEN 1 WHEN NOT (pred) THEN 0 ELSE NULL END
```

阶段 CASE：先各规则 `= 1`，再 `IS NULL`（eligibility 仅当没有任何 `= 1`），再进入下一阶段。
`INDETERMINATE` 的 reasonCode 为 `FACT_VALUE_MISSING_OR_INVALID`。

## 4. COUNT / EXISTS

顶层 OUTER APPLY：

- count：`SELECT COUNT(*) AS fact_value`
- exists：`SELECT CASE WHEN EXISTS (SELECT 1 ...) THEN 1 ELSE 0 END`

SUM / GROUP BY / DISTINCT COUNT 仍 `AGGREGATION`。独立 AST 仅允许作为 `Count` 子节点的 `*`。

## 5. 映射权威

`ViewShapedMappingBundleV31.authority`：`syntheticCompileGrant` | `metadataReviewApproved`。
二者都是调用方显式包。FBR `mappingCandidate.unresolved` 仍不可用。
