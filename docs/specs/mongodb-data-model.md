# MongoDB 数据模型

- 状态：`proposed`
- 关联：[REQ-20260818-01](../requirements/REQ-20260818-01-release-rule-sql-generator.md)

MongoDB 保存版本和审计事实，不保存生产数据库凭证明文。集合名为建议值，Phase 1 应通过配置加统一前缀。

## 1. `project_contexts`

保存一个项目/目标在某一版本下生成 SQL 所需的当前上下文。

关键字段：

```javascript
{
  _id: ObjectId,
  project_id: "project-001",
  target: "project_report",
  version: 2,
  status: "active",
  dialect: { name: "postgres", version: "16" },
  schema_snapshot: {
    hash: "sha256:...",
    captured_at: ISODate,
    tables: [/* allowlisted tables, columns and types */]
  },
  logical_fields: {
    report_status: { table: "report", column: "status", type: "string" }
  },
  entity_key: ["project_id", "report_id"],
  unreleased_expression: { field: "release_status", operator: "ne", value: "RELEASED" },
  timezone: "Asia/Shanghai",
  capabilities: { explain: true, temp_table: true },
  budgets: { timeout_ms: 30000, max_result_rows: 10000, max_plan_cost: null },
  created_by: "operator@example.com",
  created_at: ISODate,
  context_hash: "sha256:..."
}
```

连接信息仅保存安全配置引用，例如 `connection_ref`，不保存密码。

索引：

```javascript
{ project_id: 1, target: 1, version: 1 } unique
{ project_id: 1, target: 1, status: 1 }
```

每个 `project_id + target` 最多一个 `active`，通过发布事务/条件更新保证。

## 2. `release_rules`

保存符合 [规则契约](rule-contract.md) 的不可变业务规则。

附加存储字段：

```javascript
{
  _id: ObjectId,
  /* rule contract fields */
  canonical_rule: { /* normalized tree */ },
  rule_hash: "sha256:...",
  parent_rule_id: ObjectId | null,
  inserted_at: ISODate
}
```

索引：

```javascript
{ project_id: 1, target: 1, version: 1 } unique
{ project_id: 1, target: 1, status: 1, effective_from: -1 }
{ rule_hash: 1 }
```

## 3. `sql_templates`

候选 revision 与已发布模板的业务载荷不可变；审核/发布状态只按受审计状态机转换，任何 SQL 或参数修订都创建新文档。

```javascript
{
  _id: ObjectId,
  template_id: "tpl_...",
  project_id: "project-001",
  target: "project_report",
  version: 4,
  revision: 1,
  status: "pending_review",
  parent_template_id: "tpl_previous" | null,
  rule_ref: { id: ObjectId, version: 3, hash: "sha256:..." },
  baseline_rule_ref: { id: ObjectId, version: 2, hash: "sha256:..." } | null,
  baseline_template_ref: { id: ObjectId, version: 3, hash: "sha256:..." } | null,
  context_ref: { id: ObjectId, version: 2, hash: "sha256:..." },
  plan: {
    kind: "single_query" | "staged_script",
    dialect: "postgres",
    stages: [/* typed statements and cleanup */],
    parameters: [/* name, type, source condition */],
    output_contract: [/* columns */]
  },
  condition_coverage: [/* condition_id -> stage/AST path */],
  validation_report: { /* checks, explain, sandbox */ },
  generation: {
    run_id: "run_...",
    provider: "...",
    model: "...",
    prompt_template_version: "..."
  },
  review: {
    decision: "approved" | "rejected" | null,
    reviewer_id: "..." | null,
    comment: "..." | null,
    decided_at: ISODate | null,
    evidence_hash: "sha256:..." | null
  },
  sql_hash: "sha256:...",
  artifact_hash: "sha256:...",
  created_at: ISODate,
  published_at: ISODate | null
}
```

索引：

```javascript
{ template_id: 1 } unique
{ project_id: 1, target: 1, version: 1, revision: 1 } unique
{ project_id: 1, target: 1, status: 1, published_at: -1 }
{ "rule_ref.hash": 1, "context_ref.hash": 1 }
```

## 4. `generation_runs`

保存一次编排的状态历史、重试和错误分类，避免把频繁事件不断追加到模板主文档造成无限增长。

```javascript
{
  _id: ObjectId,
  run_id: "run_...",
  idempotency_key: "...",
  project_id: "project-001",
  target: "project_report",
  input_refs: { rule_id: ObjectId, context_id: ObjectId, baseline_template_id: ObjectId | null },
  input_hash: "sha256:...",
  status: "pending_review",
  revisions: [
    { revision: 1, template_id: "tpl_...", outcome: "passed", created_at: ISODate }
  ],
  events: [
    { type: "validation.completed", at: ISODate, actor_type: "system", summary: {/* no secrets */} }
  ],
  started_at: ISODate,
  finished_at: ISODate | null,
  expires_at: ISODate | null
}
```

索引：

```javascript
{ run_id: 1 } unique
{ idempotency_key: 1 } unique
{ project_id: 1, target: 1, started_at: -1 }
{ expires_at: 1 } expireAfterSeconds: 0 // 仅在确认审计保留期后启用
```

## 5. 发布原子性

推荐 MongoDB replica set 事务：

1. 重新读取当前活动模板并比对 `parent_template_id`；
2. 验证候选仍为 `approved`，且 rule/context/artifact hash 未变化；
3. 将旧活动版本标为 `superseded`；
4. 将候选标为 `published`；
5. 写入发布事件；
6. 提交事务。

如果部署环境不能使用事务，应改用单独的 `active_template_pointers` 文档和条件更新（compare-and-swap），而不是依次无条件更新两个模板。

## 6. 数据保留

- 已批准/已发布规则、模板和审核记录长期保留；
- 失败运行的保留期由合规要求决定，不能默认启用 TTL；
- 大型原始模型响应和执行计划如需外置，MongoDB 只保存安全对象存储引用与哈希；
- 查询结果正文不进入这些集合。
