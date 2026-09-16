# REQ-20260916-01：受治理编码映射、空值与行集结果语义

- 状态：`implemented`（离线能力已交付；测试环境已签发 1.2.0 并完成真实 generate-v3；新 SQL 待人工审核）。
- 来源：首份 source 事实与参考视图的编码/空值/基数差异，以及用户要求随 MongoDB 规则生成模板。
- 基线：[REQ-20260915-01](REQ-20260915-01-v3-mongodb-generation-entry.md)、[REQ-20260906-04](REQ-20260906-04-v3-downstream-pipeline-alignment.md)。
- 决策：[BIZ-20260916-03](../decisions/BIZ-20260916-03-value-encoding-result-semantics.md)。
- 设计：[DEV-20260916-01](../architecture/DEV-20260916-01-value-encoding-result-semantics.md)。
- 缺陷：[BUG-20260916-01](../bugs/BUG-20260916-01-first-template-view-semantic-gap.md)。

## 目标

为当前选定的单事实模板补齐可复用能力：规则请求中的逻辑值域经批准的物理编码绑定投影到 SQL Server 候选，并独立校验空值、未知码和行集返回。禁止再交付“只读原列、契约仍写 scalar”却声称语义已对齐的候选。

## 当前事实对照（离线内容，非当次仓储重读）

| 类别 | 当前选定事实 | 处理 |
| --- | --- | --- |
| 规则身份 | 精确 ruleVersion / requestId / payload 与批次哈希与 2026-09-15 本地导出一致 | 沿用；真实生成仍须重读 |
| 事实语义 | source、string、task grain、nullable、`nullPolicy=indeterminate`、非空 `allowedValues` | 保留结构化字段 |
| 查询要求 | 单实体键等值过滤，aggregation/timeRange 均为 none | 已有 M3/M4 可复用 |
| 规则使用 | 一条 eligibility usage 六元组 | 原样保留 |
| 物理绑定 | 单表两列已批准；逻辑值域与视图物理比较码不同 | 需要 context 1.2.0 编码绑定 |
| 授权 | 本地测试批准覆盖这两列，不是生产目录 | 新语义须新 context/批准版本 |
| 生成能力 | M2 可解析编码绑定；M3/M4 在绑定存在时生成并校验 CASE/rowset；无绑定则 M2 阻断 | 离线已扩展；测试环境真实生成已完成 |
| 处理类别 | 编码/空值/行集：代码扩展 + 下游契约版本；JOIN/聚合：本事实不需要 | 见下 |

事实取数只返回该标志的逻辑值；R9 是否放行仍由规则引擎比较。不得把主视图的共同前置条件、金额或其它 OR 分支写入本 SQL。

## 范围

1. `ProjectBindingContextV3` 增加 `schemaVersion=1.2.0` 的值编码绑定与结果语义绑定；1.1.0 上下文保持可读且哈希稳定。
2. `allowedValues` 非空时必须有当前 request 的 `factValue` 编码绑定，否则 M2 `blocked`。
3. `mappedCase` 生成受限 CASE 投影；`identity` 仍允许直接列投影。
4. 候选在存在行集绑定时报 `schemaVersion=3.1.0` 且 `cardinality=rowset`；无绑定的既有切片保持 3.0.0/`scalar`。
5. AST 门禁与语义校验同步：允许的 CASE 必须与绑定逐臂一致；拒绝 ISNULL 默认否、ELSE 默认否、TOP/DISTINCT、未授权列。
6. Prompt `sqlserver-fact-candidate-v3.2` 仅在存在编码或行集绑定时使用；否则保持 v3.1。
7. 合成正例/负例与规则版本 A/B 回归；公开测试不含私有对象名或真实码。
8. 不修改冻结的上游 FactBindingRequest 3.0.0 Schema。上游若要原生表达 rowset，另立版本化变更包。

## 非目标

- 不调用在线模型、不连接 SQL Server、不写批准记录、不改 RuleReader 交接。
- 不批量处理其余真实事实。
- 不把 V3 降级为 V2，不删除 `_check_m3_scope`，不放宽表列授权。

## 验收

- 有编码绑定的合成事实：CASE 臂、NULL 输入、未知码、类型字面量不匹配均可被独立校验识别。
- 无编码绑定但 `allowedValues` 非空：M2 阻断，provider 调用次数为 0。
- 行集绑定：候选 `cardinality=rowset`；含 TOP/DISTINCT 的 SQL 被拒绝；零行不被改写成单行 NULL。
- 规则版本/usages/context/snapshot 变化后不能复用旧哈希或旧覆盖结论；仅引擎侧变化时允许 SQL 文本相同。
- 旧 3.0.0 候选与 1.1.0 上下文仍可校验；新候选不继承历史人工审核。
- `uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`、`git diff --check` 通过。

## 剩余真实运行阻塞

测试环境已签发 context `1.2.0` 与新批准，并完成真实 `generate-v3`（新候选
`d804a60da09c2d393c57e3abe2c6f5b1b7029eaa744e05c8de521aaed62bb186`，静态 `passed`）。
旧候选 `27b12dcdd5e12069011a6cf0ac38c9e8a66a3fb8f972fabd3b41aea9416afae1` 未改写。
下一步是人工审核新 CASE SQL；通过后仍保持 `pending / executable=false`。
未连接 SQL Server，未发布，未写生产。
