# REQ-20260906-02：真实上游交接与端到端候选证据闭环

- 状态：`proposed`
- 创建日期：2026-09-06
- 来源：用户要求编写下一阶段详细计划书，解决真实 Agent 1 规则交接缺失、受治理元数据输入缺失，
  以及真实 V2 SQL 候选无法形成端到端审计闭环的问题
- 前置需求：[REQ-20260828-02](REQ-20260828-02-rulereader-handoff-read-intake.md)、
  [REQ-20260827-03](REQ-20260827-03-project-context-metadata-resolution.md)、
  [REQ-20260828-01](REQ-20260828-01-v2-candidate-generation-input.md)、
  [REQ-20260827-01](REQ-20260827-01-sql-ast-safety-gate.md)、
  [REQ-20260906-01](REQ-20260906-01-v2-candidate-persistence.md)
- 受业务决策约束：[BIZ-20260906-01](../decisions/BIZ-20260906-01-agent1-metadata-review-sqlbot-boundary.md)
- 技术方案：[DEV-20260906-02](../architecture/DEV-20260906-02-real-handoff-evidence-loop-orchestration.md)
- 关联进度：[PROG-20260906](../progress/PROG-20260906.md)

## 1. 背景与问题

### 1.1 观察基线（2026-09-06）

以下为最近一次观察结果，均标注来源与观察时间；它们不是永久事实，实施前必须在 Milestone 0 重新
核实，不得假定数据库状态永远不变。

- MongoDB 只读检查（用户提供的结果摘要，观察时间 2026-09-06；本计划编写过程未重新连接
  MongoDB，也不连接验证）：
  - 连接正常；
  - `rule_versions` 集合存在，记录数为 0；
  - `fact_binding_handoffs` 集合存在，记录数为 0；
  - `rule_structure_candidates_v3` 有 1 条记录：`contractVersion=3.0.0`、状态
    `validatedBlockedCandidate`、`executable=false`、仍有 16 个 blocking issue。
- SqlBot 仓库（本地工作区，2026-09-06 观察）：
  - 当前只消费 RuleReader `FactBindingRequest 2.0.0`（[REQ-20260828-02](REQ-20260828-02-rulereader-handoff-read-intake.md)
    冻结的 Schema 副本 SHA-256 `38fec6b2…`）；
  - 当前没有真实 `ProjectBindingContextV2`，也没有可供真实请求使用的 `GovernedMetadataSnapshot`；
  - Phase 5A 受限 SQL Server 描述验证已完成离线实现，尚未进行真实隔离 SQL Server 冒烟；
  - Phase 5B、5C 与 Phase 6 尚未实施；
  - V2 候选 MongoDB 持久化实现已提交（255196f），
    [REQ-20260906-01](REQ-20260906-01-v2-candidate-persistence.md) 与 DEV-20260906-01 已完成登记；
    离线前置验收第 1–8 项通过，真实 Mongo integration 与实际账号权限隔离证明未完成（状态收敛
    见 [PROG-20260906](../progress/PROG-20260906.md)）。
- Agent 1 仓库只读参考（`D:\Python\pyWorkspace\RuleAgent`，2026-09-06 读取其 AGENTS.md 与最新
  进度，未修改该仓库）：
  - `docs/BIZ-20260905-02-v3-agent2-handoff-contract.md`（`APPROVED_PATH_B`）：Agent 1 已批准路径 B，
    冻结 `FactBindingRequest 3.0.0`；`FactBindingRequest 2.0.0` 的 `ruleRef` 固定
    `schemaVersion=2.0.0`，结构上不能精确引用 V3 规则；禁止将旧 V2 恢复为当前规则，禁止静默降级；
    未来 MongoDB 使用独立 `rule_versions_v3` 与单文档原子 `fact_binding_handoff_batches_v3`；
  - `docs/PROG-20260906.md`：16 项 blocking 已完成业务确认（裁决记录见其 BIZ-20260906-01），
    `RuleParseResultV3` 与 18 条 `FactBindingRequest 3.0.0` 已离线生成、readiness 16/16 通过
    （blocking=0、ready=true），但全部产物仍为离线待审核状态，未写入 MongoDB（落库需另行授权）。

因此 MongoDB 中那条 `validatedBlockedCandidate`（16 blocking）是业务确认前的陈旧候选恢复记录，
不能作为当前规则版本；Agent 1 侧已确认的 0 blocking 版本尚未落库。

### 1.2 问题定性

当前障碍不是 SQL 生成算法。Phase 2F intake、Phase 2F.1 交接只读接入、Phase 2G 授权解析、V2 候选
生成与 Phase 4 AST 静态门禁均已离线完成，合成链路可用。真正缺失的是完整真实输入链：

```text
Agent 1 immutable rule
  → FactBindingRequest handoff
  → ProjectBindingContext
  → GovernedMetadataSnapshot
  → metadataResolved
  → candidate generation
  → candidate persistence
  → Phase 4 static validation
  → evidence package
```

链上任一真实输入缺失，后续所有环节都没有真实对象：没有已批准规则版本与 handoff，Phase 2G 没有
可解析请求；没有批准上下文与快照，无法形成 `metadataResolved`；没有真实 `metadataResolved`，候选
生成与持久化只能停留在合成预览；没有真实候选与存储结果，Phase 4 与后续 Phase 5B 验证就没有
合法验证对象。候选持久化只是链路末端的一个 sink，不能替代上游规则与授权闭包。

## 2. 目标

本阶段实现并证明以下最终结果（全部针对一条真实规则版本、一个真实事实）：

1. 存在一个精确、不可变、可回读的 Agent 1 规则版本（含 canonical payload SHA-256 与来源闭包）。
2. 该规则拥有完整的事实级 handoff：每个 required non-derived fact 恰好一个 handoff，无缺失、无
   额外。
3. handoff 通过 SqlBot 冻结的 `FactBindingRequest 2.0.0` Schema；若上游契约决策维持 V3
   （`FactBindingRequest 3.0.0`），则必须先由 SqlBot 以独立 REQ/BIZ/DEV 完成 intake 升级并通过本
   需求同等级验收——禁止任何形式的静默降级（见第 5 节）。
4. handoff batch 状态为 `readyForMetadataResolution`，`blockingRequestCount=0`。
5. 存在匹配该规则与 request 集合的批准 `ProjectBindingContextV2`（精确版本、自身 canonical hash
   闭合）。
6. 存在匹配该上下文的批准 `GovernedMetadataSnapshot`（`dialect=sqlserver`、精确 relation/column
   集合）。
7. Phase 2G 对真实输入重算为 `metadataResolved`，且与请求携带报告 canonical 一致。
8. 生成一个不可执行、待审核的真实 V2 candidate（`candidate / executable=false / pending`）。
9. candidate 被 insert-only 持久化到 SqlBot 自有集合；存储结果独立记录。
10. Phase 4 对同一 candidate 与同一 generation request 静态报告为 `passed`。
11. 形成不包含业务结果值和秘密的运行证据包；公开 PROG 只保存脱敏哈希、数量与状态。

## 3. 非目标

本阶段明确排除：

- SQL Server 连接与 Phase 5A 真实隔离冒烟；
- SHOWPLAN（Phase 5B）与候选有界试跑（Phase 5C）；
- 任何 SQL 执行、取数、prepare/compile；
- 自动审批、人工审核实现与正式发布（仍属 Phase 6）；
- 生产调度与在线批量生成；
- 把 V3 blocked candidate（`rule_structure_candidates_v3` 中那条 16 blocking 记录）强行转换为
  V2 规则版本或 V2 handoff；
- 从飞书、工作簿或视图 SQL 自动生成 grant、快照或白名单；
- 修改 RuleReader 集合（`rule_versions`、`rule_versions_v3`、`fact_binding_handoffs`、
  `fact_binding_handoff_batches_v3`、`rule_structure_candidates_v3` 均保持 Agent 1 所有）；
- 在线批量生成；本阶段若进行在线单事实生成，也仅限用户当次明确授权下的一次有界调用。

## 4. 成功与阻断语义

阶段状态机固定为：

```text
blockedUpstream
  → readyForMetadataResolution
  → metadataResolved
  → candidateGenerated
  → candidateStored
  → staticPassed
  → evidenceComplete
```

规则：

1. 任何阶段失败都不能跳到后续阶段；每一步的失败语义在
   [DEV-20260906-02](../architecture/DEV-20260906-02-real-handoff-evidence-loop-orchestration.md)
   的状态机中冻结。
2. `candidateStored` 不表示 `staticPassed`：存储是生成链末端的单向 sink，不参与任何安全结论。
3. `staticPassed` 不表示 “SQL Server validated”：Phase 4 通过只证明离线 AST 策略满足。
4. 整个阶段所有产物固定 `executable=false`；`reviewStatus` 保持 `pending`，不因任何阶段成功而变化。
5. `blockedUpstream` 覆盖：规则缺失、版本不支持、handoff 缺失/损坏/blocking、上下文或快照未批准
   或范围不符。此时 provider 调用次数必须为 0。
6. 存储失败不伪造 `candidateStored`；生成结果与存储结果分别记录，互不覆盖。
7. `evidenceComplete` 要求证据包所有必填字段齐备且可复核；缺任一项时为 `EVIDENCE_INCOMPLETE`，
   不得以部分证据宣布闭环。

## 5. Agent 1 输入要求

Agent 1 必须提供的最小真实输入：

- 精确 `ruleId` / `ruleVersion`（不选择“最新”，不接受近似匹配）；
- `schemaVersion`；
- source SHA-256（规则正文来源内容哈希）；
- parser / Prompt provenance（解析器与 Prompt 版本、生成时间）；
- 完整 required facts（含 fact code、kind、data type、grain、参数定义）；
- 稳定 `conditionId` 及其 usages；
- entity / grain / key parameters；
- 完整 fields（含 required 标记与角色引用）；
- 完整 filters；
- aggregation；
- timeRange；
- result（列名、类型、基数、nullable、null policy、unit）；
- evidence（`evidenceIds` 与 `provenance.evidence` 闭包）;
- examples；
- uncertainties（含 impact 与 owner；ready handoff 不允许 blocking uncertainty）；
- 不可变 MongoDB wrapper（身份字段与 payload 一致）；
- canonical payload SHA-256（可独立重算校验）。

### 5.1 契约版本决策（阻断性前置）

当前 SqlBot 只冻结了 `FactBindingRequest 2.0.0` Schema。如果当前 V3 不能无损输出
`FactBindingRequest 2.0.0`，必须由 Agent 1 先完成独立契约决策；SqlBot 不负责静默降级。

2026-09-06 观察到的 Agent 1 侧决策（其仓库 `BIZ-20260905-02`，`APPROVED_PATH_B`）已经选择：V3
输出独立 `FactBindingRequest 3.0.0`，且明确 SqlBot 必须通过后续独立 REQ/BIZ/DEV 升级 intake，不
静默兼容。若该决策在跨仓库确认时维持不变，则本需求的第 2.3 条目标以“SqlBot intake 升级为 3.0.0”
的实现为前置，该升级本身是独立任务，不得在本阶段顺手实现或用 2.0.0 载荷冒充。若 Agent 1 侧决策
发生变化（例如恢复可引用 V3 的 2.0.0 导出），必须以其新的已批准决策文档为准。无论哪种结果，本
阶段都不接受“用旧 V2 草稿、合成请求或降级转换冒充真实交接”。

## 6. metadataReview 输入要求

### 6.1 ProjectBindingContextV2

批准的 `ProjectBindingContextV2` 必须包含（与 [REQ-20260827-03](REQ-20260827-03-project-context-metadata-resolution.md)
一致）：

- 精确 project / rule / request 范围（`projectRef`、`ruleRef`、允许的 `requestIds`）；
- `status=approved`；
- 自身 canonical hash（`contentSha256`，可重算）；
- `metadataSnapshotRef`（精确 snapshotId + version）；
- `authorizationPolicyVersion`；
- relation grants；
- column grants；
- field binding authorizations（`requestId + fieldId + role` 唯一）；
- entity-key authorizations（`requestId + parameterName + fieldId`）；
- join grants（多关系场景显式批准）；
- 稳定 `approvalRef`（与 SQL 候选审核无关的独立批准记录）。

### 6.2 GovernedMetadataSnapshot

批准的 `GovernedMetadataSnapshot` 必须包含：

- `status=approved`；
- 自身 canonical hash；
- `dialect=sqlserver`；
- 标识符大小写策略（`sensitive | insensitive`）；
- 完整 relation / column 集合（精确 `schema.relation`、`relationKind`、每个 column 的名称、
  `sqlType`、`nullable`）；
- relationship edges（如允许跨关系绑定）；
- 版本化来源引用与 `approvalRef`。

### 6.3 不可替代性

实时 catalog、工作簿、飞书文档、私有 bundle 和模型输出都不能替代这些批准输入。快照的物理数据
采集本身需要独立授权（DBA 导出或受限只读 catalog 抓取）与 metadataReview 人工批准，属于独立任务；
本阶段只消费已批准载荷。缺少任一批准输入时，本阶段在 `blockedUpstream` 处停止，不得用合成上下文
或快照推进。

## 7. 候选生成与持久化

1. 数据库前完整重算 Phase 2F/2G：`analyze_binding_gaps_v2` 与 `resolve_metadata_v2` 必须对真实
   输入重算，且与携带报告 canonical 一致；不一致即阻断，provider 调用次数为 0。
2. provider 前阻断所有引用和授权问题：任何 blocking uncertainty、哈希不一致、批准状态缺失、
   范围不匹配都在调用模型前停止。
3. 调用次数有界：单事实生成总尝试次数不超过 `maxRetries + 1`（复用现有有界重试语义），不自动
   重新生成。
4. 模型只生成候选声明（SQL 文本、参数/结果/对象/coverage 声明、假设与告警）；候选状态由应用固定，
   模型不能提供或覆盖 `status`、`executable`、`reviewStatus`、引用与哈希。
5. `contentSha256` 完整：对排除自身字段后的完整 canonical camelCase 候选计算，可独立重算。
6. 候选存储使用 SqlBot 自有数据库和集合（`release_sql_bot.sql_template_candidates` 或后续批准的
   等价配置），不写 RuleReader 任何集合。
7. insert-only；同一 `contentSha256` 幂等（duplicate 不报错、不重复落库）；禁止 update / replace /
   delete。
8. 存储失败不能伪装成功：连接、权限、超时失败折叠为独立 outcome，候选照常返回但证据包记录真实
   存储结果。
9. 存储结果与生成结果分别记录；`candidateStored` 只由真实写入结果产生。
10. 不得写 `rule_versions` 或 `fact_binding_handoffs`（含 V3 的对应集合）；SqlBot 对上游集合保持
    只读。

## 8. Phase 4 静态门禁

必须使用真实生成的同一 candidate 和同一 generation request（不得替换、不得重新生成）：

1. 重算 Phase 2G 并与携带 resolution report canonical 比对；
2. 重算 candidate hash（`contentSha256`）与 generation input hash；
3. 验证单 SELECT（完整解析列表恰好一条非空语句）；
4. 验证只读（根节点普通 `SELECT`，拒绝 DML/DDL/EXEC/hint/集合运算/临时对象/外部源）；
5. 验证参数（AST 实际参数与事实参数、candidate 参数精确一致，位置合法）；
6. 验证对象和列（物理源精确 `schema.relation`，同时命中批准快照与授权列闭包，拒绝星号与未授权列）；
7. 验证 join（实际 join 端点必须命中显式 join grant 与快照 relationship edge）；
8. 验证唯一 `fact_value` 投影及其授权来源；
9. 验证 stable condition coverage（重算 usage entries 与 V2 `usages.conditionId` 精确一致）；
10. 输出 `passed | blocked` 报告，报告与候选始终 `executable=false`；
11. `staticPassed` 不改变候选审核状态，不构成执行许可。

## 9. 运行证据包

运行证据包不进入公开 Git。每条真实闭环运行至少包含：

- `ruleVersion`（精确版本标识）；
- handoff batch hash（batch canonical SHA-256）；
- request 数量；
- context / snapshot / resolution report hash；
- candidate hash（`contentSha256`）；
- candidate store outcome（`stored | duplicate | unavailable | failed` 及时间）；
- static report hash；
- provider / model / Prompt version；
- attemptCount；
- 阶段状态（第 4 节状态机取值）；
- issue codes（稳定编码，不含 payload）；
- 开始和结束时间。

公开 PROG 只保存脱敏哈希（前缀）、数量和状态，不保存：

- 规则正文；
- SQL；
- 对象或字段清单；
- 参数值；
- 连接信息；
- provider 原始响应；
- 私有证据内容。

证据包的保存位置与保留期是开放问题（见 DEV 第 13 节），在确认前证据包仅存在于本地被 Git 忽略的
目录（与 `.codex_tmp` 同级策略），不得提交。

## 10. 验收标准

1. 状态机、issue code、输入契约与证据包字段在 DEV 中冻结，REQ/BIZ/DEV 互相引用且链接有效。
2. 端到端编排服务按 DEV 设计实现后：任一阶段失败时，后续阶段副作用（provider 调用、Mongo 写入）
   均为 0，且有测试证明。
3. 真实闭环运行一次成功后，证据包字段齐备，公开文档只有脱敏内容；泄漏检查有测试覆盖。
4. 候选持久化前置验收项（DEV 第 6 节）全部满足并登记 PROG 后，才允许进入真实存储验证。
5. 契约版本决策（第 5.1 节）在 Milestone 1 前以已批准文档形式登记；未登记时后续里程碑保持阻断。
6. 默认测试全部离线，不访问 MongoDB、DeepSeek、SQL Server、飞书或私有 bundle；真实 Mongo
   integration、在线 provider 与 SQL Server 验证分别拆成显式授权任务。
7. README、docs/README、ROADMAP 与当日 PROG 同步；新文档状态为 `proposed`，不把未完成能力标记为
   `completed`。
8. `uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`、`git diff --check` 全部
   通过。

## 11. 阻塞项

以下任一项未确认时，对应后续里程碑保持 `blockedUpstream` 或按 DEV 状态机阻断，不得绕行：

1. Agent 1 规则版本与 handoff 未落库（MongoDB Schema v5 写入需 Agent 1 侧独立授权）；
2. 交接契约版本决策未在 SqlBot 侧登记（2.0.0 直接可用，或 SqlBot intake 升级 3.0.0 已另立需求）；
3. `ProjectBindingContextV2` 未批准（metadataReview owner 未产出或未批准）；
4. `GovernedMetadataSnapshot` 未批准或快照来源未确认；
5. 候选持久化的真实 Mongo integration 测试与实际存储账号权限隔离证明未完成（离线前置验收第
   1–8 项已通过，见 [DEV-20260906-02](../architecture/DEV-20260906-02-real-handoff-evidence-loop-orchestration.md)
   第 6.1 节；进入真实存储验证前必须补齐）；
6. 在线 provider 调用未获得用户当次明确授权；
7. 私有参考资料被用于构造任何授权输入（直接阻断，不允许继续）。
