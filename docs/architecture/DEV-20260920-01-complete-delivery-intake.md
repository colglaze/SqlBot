# DEV-20260920-01：3.1.0 完整交付 intake 与视图形态组合契约

- 状态：`implemented-offline`（本切片：完整交付读取与组合契约；总 SQL 未生成）
- 日期：2026-09-20
- 实现需求：[REQ-20260920-01](../requirements/REQ-20260920-01-complete-delivery-intake.md)
- 业务决策：[BIZ-20260920-01](../decisions/BIZ-20260920-01-complete-delivery-intake.md)
- 上游门禁：RuleAgent [DEV-20260917-02](../../../RuleAgent/docs/DEV-20260917-02-sqlbot-complete-delivery-intake.md)、
  [DEV-20260917-01](../../../RuleAgent/docs/DEV-20260917-01-optimization-plan-rule-handoff.md)

## 1. 设计结论

在既有 V3 batch intake **之外**新增独立 3.1.0 完整交付通道。不修改 V2/V3 单事实生成链，不调用
`generate-v3`，不连接 SQL Server，不写入 MongoDB。本切片只证明：指定 3.1.0 版本能读到五阶段树，
旧 2026-09-05 版本不能用于 SQL 编译，缺闭包不可消费。

## 2. 模块

```text
src/release_sql_bot/contracts/*-3.1.0.schema.json
  冻结上游 Schema 副本（FBR / candidate / result）；catalog 仍为 3.0.0 模型
src/release_sql_bot/domain/purpose_v31.py
  照抄 Agent1 用途表，禁止第二套规则
src/release_sql_bot/domain/fact_bindings_v31.py
  FactBindingRequest 3.1.0 consumer
src/release_sql_bot/domain/rule_structure_v31.py
  catalog / candidate / result consumer（只读校验，不求值）
src/release_sql_bot/domain/complete_delivery_v31.py
  CompleteDelivery 选择结果
src/release_sql_bot/domain/composition_plan_v31.py
  一条视图形态 SQL 的组合契约（sqlGenerated=false）
src/release_sql_bot/application/complete_delivery_intake_v31.py
  selectDelivery 门禁
src/release_sql_bot/application/composition_plan_v31.py
  从完整交付冻结组合计划；无映射时 compile 仍阻断
src/release_sql_bot/application/view_shaped_compile_v31.py
  确定性视图形态 SQL 编译（见 DEV-20260920-02）
src/release_sql_bot/application/ports/complete_delivery_v31.py
  只读 CompleteDelivery 源
src/release_sql_bot/infrastructure/complete_delivery_fs_v31.py
  离线目录读取（合成或私有包路径）；不写 MongoDB
```

V2/V3 模块行为不变。V31 不得把 payload 转换成 `FactBindingRequestV3`。

## 3. selectDelivery 门禁（与 Agent1 对齐）

```text
selectDelivery(purpose, ruleVersion) -> CompleteDelivery
preconditions:
  purpose in {historical-audit, optimization-plan-generation, sql-compilation}
  2026-09-05 REPORT_RELEASE_ALL_001@20260905T172407000000Z-f285643e5b2b-82dbd05a800a
    只允许 historical-audit
  其他版本允许 optimization-plan-generation
  delivery.schemaVersion == "3.1.0" for optimization-plan-generation
  delivery.purpose allows the requested purpose
  catalog, candidate, result, batch all present
  hashes close (sourceFile, parseInput, catalogDigest, candidate, result, batch)
  executable == false
  mapping remains unresolved until metadataReview
reject:
  2026-09-05 版本用于 optimization-plan-generation 或 sql-compilation
  缺 catalog_payload 或 candidate_payload
  任何 V1/V2 文档或 3.0.0 公式树被当作本 V3/3.1.0 完整交付
  回退旧 batch 0df35b35…
```

哈希算法（不得改写）：

| 字段 | 算法 |
| --- | --- |
| `sourceFileSha256` | 优化方案原始文件字节 SHA-256，不换行规范化 |
| `parseInputSha256` | 提取块 UTF-8 SHA-256，必须 ≠ 文件哈希 |
| `catalogDigest` | catalog 的 canonical JSON，**排除** `catalogDigest` 自身 |
| `catalogPayloadSha256` / `candidatePayloadSha256` / result / FBR payload | canonical JSON（UTF-8、`sort_keys=True`、紧凑 separators、`ensure_ascii=False`、禁 NaN） |
| `batchSha256` | 按 `requestId` 升序的 `{"requestId","payloadSha256"}` 序列的 canonical SHA-256 |
| `ruleVersion` 来源前缀 | 文件哈希前 12 位，digest 前缀为 catalogDigest 前 12 位 |

`ruleVersion` 时间戳与 `generatedAt` 对齐。时间字段不参与 hash。

缺闭包时返回 `consumable=false` 及 `missing`，不抛成“可用”。用途拒绝抛稳定错误，错误文本不含 payload。

## 4. FactBindingRequest 3.1.0

相对 3.0.0：

- `contractVersion` / `ruleRef.schemaVersion` = `3.1.0`
- `ruleRef` / `provenance` 同时携带 `sourceSha256` 与 `parseInputSha256`
- `provenance` 增加文件字节长度、提取字符数、`extractorVersion`、`extractedSections`
- `result.cardinality` = `scalar|set`；`set` 要求 `dataType=list`
- 运行参数不出现在 FBR 的待查询字段里；它们属于 candidate `runtimeParameters`

## 5. 组合计划（只定义）

从完整交付派生 `ViewShapedSqlCompositionPlanV31`：

1. **结果形态**：一行对应一个应处理实体（grain 实体键）+ 放行相关列（outcome、reasonCode、matchedRuleCodes）。不是 18/33 个独立 `fact_value`。
2. **组合方式**：按 candidate 五阶段顺序求值。`stateGuards` / `prerequisites` 命中即停；`eligibility` 首个命中后继续 `postGates` / `exclusions`。事实查询由各 FBR `queryRequirements` 提供；条件树 `all`/`any`/`not`/`compare`/`allMembers` 与表达式 `fact`/`literal`/`parameter`/`arithmetic`/`dateAdd` 组合这些查询结果。
3. **禁止**：把 `description` 编译成 SQL；为 `allMembers` 生成组成员 eligible 布尔列；把运行参数当成表列。
4. **intake 切片**不编译 SQL。无映射包时 `compile_view_shaped_sql_v31` 仍阻断 `METADATA_REVIEW`。
5. **确定性编译**是独立第二路径，见 [DEV-20260920-02](DEV-20260920-02-view-shaped-sql-compile.md)。不得删除单事实 M3/M4 对 JOIN / OR / 聚合 / 算术的拒绝。

[REQ-20260917-01](../requirements/REQ-20260917-01-view-equivalent-sql.md) 的 SQL Server 差分与发布审核仍未完成。

## 6. 读取源

- 测试默认：内存合成 `CompleteDeliverySourceV31`
- 可选：文件系统目录（`catalog.json` / `candidate.json` / `result.json` / `requests.json` / `manifest.json`），用于本机离线包身份核验
- 本轮不实现 MongoDB Schema v6 reader（落库尚未授权）

文件系统读取只输出身份字段（ruleVersion、schemaVersion、purpose、consumable、stage 名、请求数、哈希）。不得把私有事实码、物理字段或 SQL 写入日志。

## 7. 测试

- 合成 3.1.0 报告版本：五阶段齐全、`consumable=true`
- 2026-09-05 + `sql-compilation`：用途失败
- 缺 catalog/candidate payload：`consumable=false`，不回退旧 batch
- purpose 不匹配、`executable=true`、哈希不一致、3.0.0 payload：失败
- 组合计划含运行参数与 set 成员键约定，`sqlGenerated=false`；compile 阻断
- 可选：若离线包路径存在，只核对公开身份与文件/canonical 哈希，不把原文写入夹具

## 8. 非本切片

MongoDB 写入或 Schema v6 适配器、metadataReview、在线模型、SQL Server、真实总 SQL、修改 RuleAgent。
