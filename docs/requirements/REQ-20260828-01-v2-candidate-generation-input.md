# REQ-20260828-01：V2 SQL 候选生成输入对齐

- 状态：`completed`
- 创建日期：2026-08-28
- 来源：用户要求 Phase 2G 完成后独立对齐 V2 候选生成输入，再复核实施 Phase 4
- 前置需求：[REQ-20260827-03](REQ-20260827-03-project-context-metadata-resolution.md)
- 受业务决策约束：[BIZ-20260828-01](../decisions/BIZ-20260828-01-v2-candidate-authority-boundary.md)
- 技术方案：[DEV-20260828-01](../architecture/DEV-20260828-01-v2-candidate-generation-input.md)

## 1. 背景

Phase 2G 已能从 `FactBindingRequestV2`、`BindingGapReport`、批准项目上下文和批准快照确定性产出
`BindingResolutionReportV2`。现有 `/sql-candidates/generate`、V1 Prompt 和
`SqlTemplateCandidate 1.1.0` 只理解历史 V1 `SqlServerBindingContext`，不能保留 V2 request、解析
报告、grant、context/snapshot 哈希和 blocking uncertainty，禁止包装或降级复用。

Phase 4 需要一个稳定、不可执行且可追溯到完整 V2 授权闭包的候选契约。开始 AST 实施前，必须先
完成本需求。

## 2. 目标

- 建立独立 `GenerateSqlCandidateRequestV2 1.0.0`，同时携带完整 Phase 2G resolution request 与
  resolution report；
- 在任何 provider 调用前重算 Phase 2F/2G 报告并要求精确一致，只允许
  `status=metadataResolved` 且不存在 blocking uncertainty；
- 建立只包含权威逻辑语义和已授权物理绑定的版本化 V2 Prompt 输入，不把 source/mapping candidate、
  parser/model provenance 或本地工作簿内容提升为授权；
- 严格解析模型允许输出的最小 JSON，并由应用侧组装不可执行、待审核、带内容哈希的
  `SqlTemplateCandidateV2 2.0.0`；
- 为 Phase 4 提供稳定候选、请求、解析报告、上下文与快照精确引用，但本需求不判断 SQL AST 安全；
- 使用固定 provider 替身完成完全离线回归；交付期间不调用任何在线模型。

## 3. 权威输入和信任边界

1. `resolutionRequest.bindingRequest` 继续是事实、参数、筛选、聚合、时间、结果和 condition usage 的
   唯一逻辑权威。
2. 只有从同一 `resolutionRequest` 确定性重算、且与携带报告逐字节 canonical 等价的
   `BindingResolutionReportV2(status=metadataResolved)` 才能提供物理绑定。
3. 完整 `ProjectBindingContextV2` 是授权来源，完整 `GovernedMetadataSnapshot` 是物理事实来源；生成
   不能扩大 `resolvedBindings`、`resolvedEntityKeys` 或 `authorizedJoins`。
4. 模型输出、`declaredObjects`、`declaredUsageCoverage`、source/mapping candidate、Prompt、provider
   元数据和假设都是不可信候选声明，不是授权、安全证明或审核结论。
5. 最终候选的状态、执行标志、审核状态、哈希、rule/request/project/context/snapshot/resolution 引用
   和 provenance 只由应用侧创建，模型不能提供或覆盖。

## 4. 范围内

- 严格 camelCase、`extra=forbid` 的 V2 生成请求、模型输出和最终候选契约；
- 完整 Phase 2G 重算与引用闭包门禁；
- 独立 `sqlserver-fact-candidate-v2` Prompt 与排序稳定的结构化 user payload；
- 与现有 provider 端口兼容、但不导入 V1 readiness/Prompt/candidate models 的 V2 生成服务；
- JSON object 严格解析、参数/结果/对象声明/condition claim 的确定性交叉校验与有界重试；
- `POST /api/v1/sql-candidates/v2/generate` 及稳定错误映射；
- 固定 provider 替身、合成脱敏 candidate fixture 和离线契约/单元/集成回归；
- README、索引、ROADMAP 和 PROG 同步。

## 5. 范围外

- 调用真实 DeepSeek 或任何在线模型作为本次交付验证；
- SQL AST、SQLGlot、字符串 SQL 安全扫描、SQL 改写或把 parser 结论写回候选；
- 连接 SQL Server、读取 catalog、prepare/compile、执行计划、试跑或正式执行；
- 读取本地参考工作簿或把候选证据转换成 context/snapshot/grant；
- MongoDB 写入、候选持久化、revision、批准、驳回、发布或审核状态迁移；
- 清除、降级、补写或隐藏 blocking uncertainty；
- 将 V2 转为 V1，或复用 V1 Prompt、readiness、生成请求和候选契约。

## 6. V2 生成输入

`GenerateSqlCandidateRequestV2 1.0.0` 必须包含：

```text
schemaVersion = 1.0.0
resolutionRequest     ResolveMetadataRequestV2 1.0.0 完整载荷
resolutionReport      BindingResolutionReportV2 1.0.0 完整载荷
```

服务必须重算 `analyze_binding_gaps_v2` 与 `resolve_metadata_v2`，要求当前 report 与携带 report 完全
一致，并验证 report 中 request/payload/rule/source/gap/context/snapshot 哈希、全部引用与当前完整载荷
闭合。`blocked`、伪造 `metadataResolved`、报告篡改、上下文/快照篡改、任一 blocking uncertainty 或
空结果来源都必须在 provider 前阻断。

## 7. Prompt 输入投影

Prompt 只投影以下类型化数据：

- fact code/kind/type/grain/null/unit 与参数定义；
- fields、filters、aggregation、timeRange、result 和 stable condition usages 的逻辑语义；
- report 中已解析 field/entity-key/result/join 物理引用；
- 固定 SQL Server、单查询、命名参数、`fact_value` 投影和无临时表约束；
- 模型输出 JSON Schema。

Prompt 不传 source/mapping candidate、原始 evidence 文本、parser/model provenance、uncertainty reason、
工作簿内容、连接信息、审批信息或任何业务参数实际值。Prompt 的授权范围只能缩小，不能超过 report。

## 8. 候选契约

模型只允许输出：

```text
templateCode / sqlTemplate / parameters / result
declaredObjects / declaredUsageCoverage / assumptions / warnings
```

应用侧组装 `SqlTemplateCandidateV2 2.0.0`：

- 固定 `status=candidate`、`executable=false`、`reviewStatus=pending`、`dialect=sqlserver`；
- 保存精确 rule/request/project/context/snapshot/resolution refs 与 generation input SHA-256；
- 参数声明必须与事实参数逐项一致，来源固定为 `fact.parameters.<name>`；
- 结果契约必须与 V2 result 精确一致；
- `declaredObjects` 必须精确等于解析报告实际涉及的 relation 集合；
- `declaredUsageCoverage` 必须无重复并精确等于 V2 stable `conditionId` 集合；
- 附加“尚未通过 AST、安全门禁、受限验证和人工审核”的强制 warning；
- 保存 provider/model/Prompt/请求 ID/尝试次数等 provenance；
- `contentSha256` 对排除自身字段后的完整 camelCase canonical 候选计算。

参数、对象和 condition 的交叉校验只证明声明与输入一致；SQL 是否实际使用这些参数、表列或 coverage
必须由 Phase 4 AST 重算，候选声明不能代替 AST 证据。

## 9. 验收标准

1. 独立 V2 模型不导入 V1 fact binding、context、Prompt 或 candidate contract；V2 payload 无降级转换。
2. 只有精确重算为 `metadataResolved` 的输入才调用固定 provider；所有 blocked、哈希/引用/report
   篡改路径 provider 调用次数为 0。
3. Prompt payload 排除候选证据、parser provenance、uncertainty reason、审批信息和参数实际值，只含
   权威逻辑投影与批准物理绑定。
4. 合法固定 JSON 可形成 `SqlTemplateCandidateV2 2.0.0`，状态固定不可执行且引用/hash 闭合。
5. 空/非 JSON/Markdown/额外字段、错误参数、错误结果、对象声明扩大或缺失、condition claim 缺失或
   重复均不能形成候选；重试总数不超过 `maxRetries + 1`。
6. API 在未配置 provider 时返回 503，输入阻断返回 409，provider 输出或状态错误使用稳定安全码且不
   泄露 Prompt、模型原文或底层异常。
7. 测试只使用固定 provider 和合成脱敏数据；不访问网络、MongoDB、SQL Server 或本地参考工作簿。
8. 候选无执行、持久化、批准或状态迁移路径；`contentSha256` 重复计算稳定且 SQL 文本变化会改变哈希。
9. README、索引、ROADMAP、PROG、Ruff、format、pytest 与 `git diff --check` 全部通过。

## 10. 完成边界

本需求完成后才能复核 Phase 4 的历史 V1 输入假设。完成 V2 候选生成不表示 SQL 语法、安全、语义、
受限验证或人工审核已通过；Phase 4 报告仍必须固定不可执行。

## 11. 完成证据

- 新增独立 V2 生成请求、Prompt、模型载荷、候选和应用服务；V2 模块不导入 V1 binding/readiness/
  Prompt/candidate contract；
- 输入在 provider 前重算完整 Phase 2G 报告；upstream blocking、伪造报告和 context 篡改路径固定
  provider 调用次数为 0；
- Prompt 回归证明不包含 mapping/source candidate、provenance、uncertainties、approval、examples、
  evidence IDs 或运行时参数值；
- 固定 provider 覆盖正常、无效 JSON/Markdown、额外生命周期字段、参数/result/object/coverage 错误、
  transient/rejected/重试耗尽和内容哈希变化；
- 新增 `POST /api/v1/sql-candidates/v2/generate`，成功结果固定不可执行，409/422/502/503 映射安全；
- 完成时全量回归 `141 passed`，Ruff、format 与 `git diff --check` 通过；详见
  [PROG-20260828](../progress/PROG-20260828.md)。
