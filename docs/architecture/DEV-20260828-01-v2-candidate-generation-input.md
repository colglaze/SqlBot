# DEV-20260828-01：V2 SQL 候选生成输入对齐设计

- 状态：`completed`
- 创建日期：2026-08-28
- 实现需求：[REQ-20260828-01](../requirements/REQ-20260828-01-v2-candidate-generation-input.md)
- 受决策约束：[BIZ-20260828-01](../decisions/BIZ-20260828-01-v2-candidate-authority-boundary.md)
- 前置设计：[DEV-20260827-03](DEV-20260827-03-project-context-metadata-resolution.md)

## 1. 模块边界

```text
domain/sql_candidates_v2.py       V2 生成请求、模型载荷、最终候选与审计引用
application/prompts_v2.py         独立版本化 Prompt 与权威输入投影
application/candidates_v2.py      Phase 2G 重算、严格解析、交叉校验、重试与组装
application/ports/candidates.py   复用 provider 传输端口，不复用 V1 业务模型
api/app.py                        /api/v1/sql-candidates/v2/generate
tests/                             固定 provider、合成输出与完全离线回归
```

V2 领域/应用模块不导入 `domain.fact_bindings`、`application.bindings`、`application.prompts`、
`application.candidates` 或 `domain.sql_candidates`。DeepSeek HTTP 适配器仍只实现中立 provider 端口；
本次验证不实例化真实网络客户端。

## 2. 生成请求与前置重算

`GenerateSqlCandidateRequestV2` 嵌套完整 `ResolveMetadataRequestV2` 和
`BindingResolutionReportV2`。应用服务固定执行：

1. 调用 `resolve_metadata_v2(resolutionRequest)`；
2. 比较携带 report 与重算 report 的完整 canonical SHA-256；
3. 要求两者均为 `metadataResolved`、`executable=false`、无 blocking issues；
4. 重新检查原 V2 `uncertainties` 中没有 `impact=blocking`；
5. 要求 result source、全部必需 field、entity key 与 join 闭合仍完整；
6. 计算 generation input SHA-256，之后才构建 Prompt 和调用 provider。

任何失败抛出携带不可执行 resolution report 的 `CandidateInputNotReadyV2Error`，API 映射 409。provider
调用计数保持 0。

## 3. Prompt 投影

当前 Prompt 版本为 `sqlserver-fact-candidate-v2.1`；历史 `sqlserver-fact-candidate-v2` 仍可作为既有
候选的审计引用。system 消息要求 JSON-only、单条 SQL Server 只读
查询候选、`:name` 参数、单列 `fact_value`、无临时表/DDL/DML/EXEC/动态 SQL，并明确输出仍不可信。

user payload 使用 camelCase、排序稳定 JSON，结构为：

```text
contractVersion / requestId / ruleRef / projectRef / dialect
fact
  factCode / factKind / dataType / grain / nullable / nullPolicy / unit / parameters
queryRequirements
  entity / fields / filters / aggregation / timeRange / result
conditionUsages
authorizedPhysicalPlan
  resolvedBindings / resolvedEntityKeys / resultSource / authorizedJoins
exactOutputDeclarations
  parameters / result / declaredObjects / declaredUsageCoverage
outputJsonSchema
```

fields 投影删除 `sourceCandidate` 和 resolution candidate 状态，只保留逻辑 ID/role/name/type/required。
请求不携带 examples、mappingCandidate、provenance、evidence、uncertainty reason、approvalRef、未授权快照
对象或实际参数值。完整原始载荷只进入输入哈希和审计引用，不进入 Prompt。

`v2.1` 将参数、结果、解析 relation 集合和 stable `conditionId` 集合从上述同一输入确定性投影到
`exactOutputDeclarations`，要求模型逐项复制。这只消除重复声明时的格式歧义，不替代应用交叉校验，
不授予 relation/column 权限，也不构成 AST coverage 证据。变更与合成在线兼容性证据见
[BUG-20260901-01](../bugs/BUG-20260901-01-v2-live-provider-coverage-declaration.md)。

## 4. 模型载荷与候选组装

`GeneratedCandidatePayloadV2` 严格字段：

```text
templateCode
sqlTemplate
parameters[]             name / dataType / required / source
result                   fact_value / dataType / scalar / nullable / nullPolicy / unit
declaredObjects[]        schemaName / relationName
declaredUsageCoverage[]  conditionId
assumptions[] / warnings[]
```

应用交叉校验：

- 参数映射精确等于 `fact.parameters`，无重复，source 固定；
- result 精确等于 `queryRequirements.result`；
- declared relation 集合精确等于 report 中 resolved bindings 与 authorized join 端点涉及的 relation；
- coverage claim 精确等于 `usages.conditionId`；
- 模型载荷不允许状态、ref、hash、provenance 或审批字段。

应用组装 `SqlTemplateCandidateV2 2.0.0`，加入 rule/request/project/context/snapshot/resolution/input refs、
固定状态、强制 warning 和 provider provenance。先构造排除 `contentSha256` 的完整候选，再用共享
canonical 算法计算并填入内容哈希；最终模型 frozen、camelCase 输出。

本层不分析 SQL 文本中的 placeholder、对象、列、语句类型或结果投影；这些声明即使结构交叉校验通过
仍可能与真实 SQL 不一致，Phase 4 必须从 AST 独立重算。

## 5. 有界重试与错误

继续遵守现有 provider 端口的传输错误分类，但 V2 使用独立异常类型。总尝试次数固定
`maxRetries + 1`：transient provider 错误、空内容、非法 JSON 和无效 V2 模型载荷可在边界内重试；
provider rejected 不重试。错误不保存或回传模型原文、完整 Prompt 或底层响应正文。

API 稳定映射：

| 条件 | HTTP / code |
| --- | --- |
| provider 未配置 | 503 / `V2_CANDIDATE_PROVIDER_UNAVAILABLE` |
| 输入未达到精确 metadataResolved | 409 / `V2_CANDIDATE_INPUT_NOT_READY` |
| provider 拒绝 | 502 / `V2_CANDIDATE_PROVIDER_REJECTED` |
| provider 暂时不可用/重试耗尽 | 503 / `V2_CANDIDATE_PROVIDER_UNAVAILABLE` |
| 输出无效/重试耗尽 | 502 / `V2_CANDIDATE_OUTPUT_INVALID` |
| wire schema 错误 | 422 |

## 6. Phase 4 交接

Phase 4 输入必须同时携带完整 V2 generation request 与最终 candidate，并重算：

- generation request → Phase 2G report；
- candidate `contentSha256` 与全部审计 refs；
- AST 真实参数、对象、列、结果投影与 condition usage coverage。

候选的 `declaredObjects` 与 `declaredUsageCoverage` 仅作差异输入。Phase 4 不能用其替代 AST，也不能
反向修补 SQL、RuleReader 语义或 Phase 2G 授权。

## 7. 测试与完成边界

- 契约：camelCase、extra、snake_case、版本、重复 ID/hash；
- 前置：完整重算、report/请求/上下文/快照篡改、upstream blocking，全部 provider 零调用；
- Prompt：稳定、最小化、不含候选证据/provenance/uncertainty reason/approval/实际参数值；
- 输出：正常、空、非 JSON、Markdown、额外字段、错误参数/result/object/coverage；
- 重试：成功、transient、rejected、invalid 与耗尽上限；
- API：成功不可执行、409、422、502、503，错误不泄露；
- 全程固定 provider、合成脱敏数据、无网络/数据库/工作簿/SQL parser/SQL 执行。

本需求完成并同步文档后，才能读取和复核现有 Phase 4 REQ/DEV 的历史 V1 输入假设并开始实现。

本设计已于 2026-08-28 按固定 provider 的最小离线纵向切片实现；未调用在线模型、SQL Server 或
本地参考工作簿。验证证据见 [PROG-20260828](../progress/PROG-20260828.md)。
