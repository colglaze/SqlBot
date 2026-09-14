# DEV-20260906-04：FactBindingRequest 3.0.0 下游管线对齐设计与实施计划

- 状态：`approved`（2026-09-09 用户明确批准五项设计；2026-09-06 人工审查意见修订版：handoff
  内容闭包与仓储真实性分层、usage 六元组、契约版本表、runtime/implementation 双顺序、context
  生命周期与 M0 状态口径已按审查结论修订。本次批准只表示设计已批准，不表示实现已完成）
- 创建日期：2026-09-06
- 实现需求：[REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
- 业务决策：[BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md)
- 关联缺陷：[BUG-20260906-01](../bugs/BUG-20260906-01-v3-phase4r-downstream-contract-gap.md)
- 前置设计：[DEV-20260906-03](DEV-20260906-03-fact-binding-v3-intake.md)、
  [DEV-20260827-03](DEV-20260827-03-project-context-metadata-resolution.md)、
  [DEV-20260828-01](DEV-20260828-01-v2-candidate-generation-input.md)、
  [DEV-20260827-01](DEV-20260827-01-sql-ast-safety-gate.md)、
  [DEV-20260906-01](DEV-20260906-01-v2-candidate-persistence.md)

> 本文档记录已批准设计及实施状态（设计本身不因实施进度而变更）。
> M0 已收口（`completed`）：上游 commit 与来源登记已于 2026-09-09 完成（提交 `e3b00b2`），
> M0 规划批次已进入提交 `61a377f`，五项设计已由用户批准并落档。
> M1 已完成（`completed`）。
> M2 为 `in_progress`（共 12 个子任务已完成）：已完成内容闭包校验、usage 追溯摘要、
> ResolveMetadataRequestV3、BindingResolutionReportV3、报告契约审核修复、
> 输入门禁内部辅助函数（`_validate_resolution_input_v3`：结构重验 + 六步内容/范围门禁）、
> 单 column grant 物理引用解析（`_resolve_column_grant_v3`）、
> 字段绑定与实体键授权闭包（`_resolve_fields_and_entity_keys_v3`）、
> filters 物理字段解析（`_resolve_filters_v3`）、
> aggregation 授权引用解析（`_resolve_aggregation_v3`）、
> timeRange 时间字段授权解析（`_resolve_time_range_v3`）、
> 指定 join grant 物理授权解析（`_resolve_join_grant_v3`）、
> 多关系授权连接闭包选择（`_select_join_closure_v3`）；
> 剩余 完整 ResolvedJoinV3 构造（evidence_ids、方向计划）、entityType/grain 授权映射、
> 公开 `resolve_metadata_v3` 编排及报告组装。
> 当前 HEAD `77c0651`，全量 1016 passed。
> 字段绑定/实体键辅助函数完成不等于完整解析服务完成，M2 不标记 `completed`。
> 剩余设计缺口分析见 [DEV-20260911-01](DEV-20260911-01-v3-m2-remaining-design-gaps.md)（proposed）。
> 历史观察见第 13 节；当前状态见第 12 节及最新 PROG。

## 1. 设计结论

为 `FactBindingRequest 3.0.0` 建立与 V2 并列、互不导入的下游契约链。**实施顺序**
（implementation milestone order，代码交付次序）为：

```text
intake_fact_binding_handoffs_v3（已完成，b3e3d35）
  → HandoffClosureV3 / RepositoryVerifiedHandoffV3（内容闭包与仓储背书，双层契约）
                                      【M2，契约形状 M1 冻结】
  → ProjectBindingContextV3 / GovernedMetadataSnapshotV3（M1 冻结契约）
  → resolve_metadata_v3（Phase 2G V3，纯计算）        【M2】
  → generate_sql_candidate_v3（独立 Prompt，provider 前仓储背书+重算）【M3】
  → validate_sql_candidate_v3（Phase 4 V3，纯计算）   【M4，可先于 M5 实现】
  → CandidateTemplateStoreV3（insert-only，独立集合）  【M5】
  → Phase 4R 编排与证据包（V3 通道）                   【M6】
```

**运行状态顺序（runtime state order）与实施顺序不同，且以既有 Phase 4R 状态机为准**：

```text
blockedUpstream → readyForMetadataResolution → metadataResolved
  → candidateGenerated → candidateStored → staticPassed → evidenceComplete
```

即运行时生成成功后**先**对同一 candidate 执行 insert-only 保存并记录存储 outcome，
**再**对该 candidate 运行 Phase 4 静态门禁；静态 `blocked` 的候选因此仍保有审计记录。
存储成功不代表静态通过；静态 blocked 不允许删除、覆盖或修改已存候选。保持该运行顺序是
[BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md) 的冻结
决策；实施顺序（M4 代码先于 M5 交付）不影响运行顺序。

- V2 链（`analyze_binding_gaps_v2` → `resolve_metadata_v2` → `generate_sql_candidate_v2` →
  `validate_sql_candidate_v2` → `CandidateTemplateStore`）零改动，继续服务 2.0.0；
- V3 链不调用 `analyze_binding_gaps_v2`：V3 ready 请求没有 resolutionStatus 族字段、
  `impact` 固定 `warning`、filters 固定 `complete`，V2 gap 语义不适用（延续
  [DEV-20260906-03](DEV-20260906-03-fact-binding-v3-intake.md) 第 1 节结论）；V3 的
  Phase 2F 等价证明由 handoff 内容闭包 + 仓储背书承担，不得用 V2 `BindingGapReport` 替代；
- 复用边界：只复用经证明与规则版本无关的底层算法（`canonical_sha256`、标识符规范化、
  SQLGlot parser-neutral 检查逻辑、有界重试模式）与物理元数据 DTO 形状；V2 请求/报告/候选
  契约对象一律不复用。

## 2. 模块边界与文件清单（规划，实施时按此冻结）

新增（命名暂定，实施前可微调但形状不变）：

```text
src/release_sql_bot/domain/project_bindings_v3.py
  ProjectBindingContextV3 / GovernedMetadataSnapshotV3 及嵌套 grant 模型
src/release_sql_bot/domain/handoff_closure_v3.py
  HandoffClosureV3（纯计算可序列化内容闭包）与
  RepositoryVerifiedHandoffV3（受信应用服务在同一次调用中仓储背书后的内部结果；
  独立文件，不触碰既有 intake 模块）
src/release_sql_bot/domain/sql_candidates_v3.py
  GenerateSqlCandidateRequestV3 / SqlTemplateCandidateV3 及嵌套引用模型
src/release_sql_bot/domain/sql_validation_v3.py
  ValidateSqlCandidateRequestV3 / SqlStaticValidationReportV3
src/release_sql_bot/application/validate_handoff_closure_v3.py
  validate_handoff_closure_v3（内容闭包纯计算校验，M2 第一子任务）
src/release_sql_bot/application/metadata_resolution_v3.py
  resolve_metadata_v3（Phase 2G V3 确定性解析）
src/release_sql_bot/application/candidates_v3.py
  generate_sql_candidate_v3（含 provider 前重算与有界重试）
src/release_sql_bot/application/prompts_v3.py
  独立 V3 Prompt（版本命名空间 sqlserver-fact-candidate-v3.x）
src/release_sql_bot/application/ports/candidate_store_v3.py
  CandidateTemplateStoreV3 端口（独立协议）
src/release_sql_bot/infrastructure/database/mongodb_candidates_v3.py
  V3 insert-only 适配器（独立集合）
tests/contract/test_project_bindings_v3_contract.py
tests/contract/test_handoff_closure_v3_contract.py
tests/contract/test_metadata_resolution_v3_contract.py
tests/contract/test_metadata_resolution_v3_report_contract.py
tests/contract/test_sql_candidates_v3_contract.py
tests/unit/test_handoff_closure_validation_v3.py
tests/unit/test_usage_traceability_v3.py
tests/unit/test_metadata_resolution_v3.py
tests/unit/test_candidates_v3.py
tests/v3_metadata_support.py
tests/unit/test_sql_validation_v3.py
tests/unit/test_candidate_store_v3.py
tests/fixtures/*.v3.synthetic.json（合成脱敏）
```

- `project_bindings_v3.py` 包含 `ProjectBindingContextV3`、`GovernedMetadataSnapshotV3`、
  `ApprovalRecordV3` 及 context/snapshot/approval 嵌套引用与 grant 模型；
- `handoff_closure_v3.py` 包含 `HandoffClosureV3` 与 `RepositoryVerifiedHandoffV3`；
- M1 定向测试要求 `test_project_bindings_v3_contract.py` 与
  `test_handoff_closure_v3_contract.py` 全部通过。

修改（仅装配点，无行为改动）：

```text
src/release_sql_bot/api/app.py            V3 显式路由（随 M6 任务实现）
config/settings.py / .env.example         V3 存储集合等配置键（随 M5 任务实现）
tests/integration/test_api.py             V3 路由用例（随 M6 任务实现）
```

禁止改动：`domain/fact_bindings_v2.py`、`domain/project_bindings_v2.py`、
`domain/sql_candidates_v2.py`、`domain/sql_validation.py`、`domain/sqlserver_validation.py`、
`application/metadata_resolution_v2.py`、`application/candidates_v2.py`、
`application/sql_validation.py`、`application/ports/candidate_store.py`、
`infrastructure/database/mongodb_candidates.py` 及其测试期望。V3 模块不得 import 上述 V2
契约模型；共享算法（canonical 哈希等）提取到中立模块或直接引用
`application/canonical.py` 这类无契约语义的底层函数。

### 2.1 已确认事实到机器输入的转换计划（2026-09-09 用户指令）

固定 `RuleDataReferences` bundle 中实际存在的 Excel 与其他资料内容已经用户确认。实现不得因
“待确认”“pending”等滞后状态字段再次向用户索取同一事实。规则结构和执行顺序以用户指定的
主来源为优先依据；工作簿实际内容用于字段、对象、枚举和关系证据；其他固定来源用于交叉核对。
所有引用必须固定私有 commit、bundle digest 与来源文件 SHA-256，公开仓库只保留脱敏索引和
摘要。

转换流程：

1. 读取并校验固定 bundle，形成私有事实覆盖矩阵；忽略状态标签，只按实际内容判断是否存在。
2. `businessRuleReview` 对规则结构、事实、筛选、聚合、时间和组合语义做确定性对齐；已登记的
   上游表达缺口以新 catalog digest、规则版本和 handoff 修订，旧载荷保持不可变。
3. `metadataReview` 将已确认物理事实转成 `ProjectBindingContextV3`、
   `GovernedMetadataSnapshotV3` 与精确 grants，并产生版本化批准记录。原始工作簿不能直接充当
   snapshot 或 grant。
4. SqlBot 在 M1/M2 只消费上述机器载荷并重算全部引用闭包；载荷缺字段时 fail closed，不从
   Prompt、历史候选或数据库运行结果补全。
5. 只有完整遍历固定资料后仍不存在的信息才登记为缺失。资料内部存在不同表述时，
   `businessRuleReview` 按主来源优先级形成版本化证据裁决；模型不得猜测，也不要求用户重复提供
   已有内容。局部未裁决只阻断受影响事实，不阻断无依赖的离线契约开发。

因此后续通常不再需要用户提供业务事实；剩余工作是工程转换、批准载体设计和运行授权。

## 3. V3 项目授权上下文（M1 冻结）

### 3.1 结论：新增 `ProjectBindingContextV3`

`ProjectBindingContextV2` 不能承载 V3：其 `rule_ref` 绑定 V2 `ruleId` 语义与自由
`schemaVersion`；`requestIds` 上限 384 而 V3 `requestId` 上限 420；无法表达
`catalogDigest`/`candidatePayloadSha256` 引用闭包。新增独立 `ProjectBindingContextV3`。

### 3.2 冻结字段

```text
ProjectBindingContextV3（camelCase、extra=forbid、strict）
  schema_version = "1.0.0"
  context_id / context_version          稳定 ID + 整数版本
  status: draft | approved | superseded
  project_ref:                          project_id / project_version（形状同 V2）
  rule_ref:                             ruleSetId（^[A-Z][A-Z0-9_]*$，≤120）/
                                        ruleVersion（≤260）/ schemaVersion="3.0.0" /
                                        sourceSha256 / catalogDigest / candidatePayloadSha256
  request_ids:                          1..n，每条 ≤420，必须与匹配 batch 的 requestId 集合
                                        精确一致（<ruleVersion>#<factCode>）
  metadata_snapshot_ref:                snapshotId / snapshotVersion / sha256
  authorization_policy_version
  relation_grants / column_grants / field_binding_authorizations /
  entity_key_authorizations / join_grants   授权形状与 V2 决策一致
                                        （requestId+fieldId+role、requestId+parameterName+
                                        fieldId 唯一性不变）
  approval_ref:                         approval_id / policy_version / approved_at；
                                        与 SQL 候选审批无关的独立批准记录
  content_sha256                        排除自身字段后的 canonical hash，可独立重算
```

不可变版本语义（2026-09-06 审查修订，消除“置 superseded”与“禁止 update”的矛盾）：

- context 业务载荷 **insert-only、不可变**：任何内容修订创建 `context_version+1` 的新文档，
  旧版本的业务载荷（含其 `status` 字段原值）永不原地修改；禁止 update/replace；
- `status` 字段是载荷的固有声明（`draft | approved | superseded`），由批准方在**创建该版本
  时**写入；版本发布后应用只校验、不推进、不改写；
- 如需 active/superseded 生命周期视图，使用**独立生命周期事件记录**或 **active pointer +
  compare-and-swap** 指向当前有效版本，生命周期载体本身有独立审计（事件时间、操作者、
  指向的 contextVersion）；不允许通过修改旧 context 文档表达生命周期变化；
- `approvalRef` 与生命周期事件均为审计记录，独立于 SQL 候选审批；
- **M1 范围收缩**：只实现上述契约与纯计算校验（哈希闭包、状态/引用校验、版本比较）；不实现
  MongoDB context 仓储与生命周期事件存储——该存储设计在 M6 编排前按本节方案另立设计确认。

### 3.3 批准记录载体（设计已批准，实现属于 M1）

metadataReview 是 context/snapshot 批准 owner。SqlBot 是 V3 契约代码、确定性校验和解析的
实现 owner。候选审核（`reviewStatus=pending`）与元数据批准是两件独立事项，不得混为一体。

已批准不可变批准记录结构（`schema_version="1.0.0"`，实施时绑定常量）：

```text
ApprovalRecordV3（camelCase、extra=forbid、strict、insert-only）
  schema_version:              Literal["1.0.0"]
  approval_id:                不透明稳定 ID（可预先分配；不从载荷内容派生；
                              ^[A-Za-z0-9][A-Za-z0-9._:-]*$；max_length=200；
                              不表达内容完整性、批准范围或授权结果）
  context_ref:                精确绑定已批准的上下文载荷
    context_id:               stable ID（^[A-Za-z0-9][A-Za-z0-9._:-]*$；max_length=200）
    context_version:          整数版本（>=1）
    sha256:                   必须 == ProjectBindingContextV3.contentSha256
  snapshot_ref:               精确绑定已批准的快照载荷
    snapshot_id:              stable ID（^[A-Za-z0-9][A-Za-z0-9._:-]*$；max_length=200）
    snapshot_version:          整数版本（>=1）
    sha256:                   必须 == GovernedMetadataSnapshotV3.contentSha256
  policy_version:             稳定 ID（^[A-Za-z0-9][A-Za-z0-9._:-]*$；max_length=160）
  actor_ref (wire actorRef):  批准主体（stable ID；max_length=200）
  approved_at:                批准时间（带时区 ISO-8601）
  content_sha256:             记录自身的 canonical SHA-256（排除自身哈希字段后计算；
                              64 位小写十六进制）
```

- `approval_id` 是不透明稳定 ID，**不是 SHA-256**；它仅满足稳定 ID 契约，不从载荷内容派生，
  不表达内容完整性、批准范围或授权结果；内容完整性只由 `contextRef.sha256`、
  `snapshotRef.sha256` 和 `ApprovalRecordV3.contentSha256` 表达；
- `context_ref.sha256` 必须等于 `ProjectBindingContextV3.contentSha256`；`snapshot_ref.sha256`
  必须等于 `GovernedMetadataSnapshotV3.contentSha256`；任一哈希不一致即 fail closed；
- 所有 SHA-256 字段固定为 64 位小写十六进制；`approved_at` 必须带时区；
- `context_ref` 与 `snapshot_ref` 必须分别精确绑定 ID、版本和内容哈希（三要素缺一不可）；
- 批准记录独立于 SQL 候选审批（`reviewStatus` 由应用固定，不构成批准）；
- ApprovalRecordV3 是 insert-only 的正向批准记录；修订必须产生新 context/snapshot 版本和
  新批准记录，不能覆盖旧记录；
- `approval_id` 可预先分配；context/snapshot 的 `approvalRef` 引用该 ID，完成载荷哈希后再
  构造 ApprovalRecordV3；三者形成完整批准包，缺一即无效；
- 任一引用或哈希不一致时批准记录无效并 fail closed；
- 撤销、active pointer、supersession 事件和 MongoDB 事务**不属于 M1**，保持 M6 前的独立存储
  设计事项；真实 M6 使用前必须完成设计和实现；
- **本轮仅设计，不实现存储或事务**；设计已随 M0 审批包批准，真实存储与事务实现仍属 M6 前
  后续里程碑。

#### `validate_approval_closure_v3` 纯函数（M1 实现，V3 专属）

M1 不实现 `ResolveMetadataRequestV3`，但 M1 契约测试需要验证 context/snapshot/approval 三者关系。
冻结一个 V3 专属、无 wire `schemaVersion`、无外部副作用的纯函数：

```text
validate_approval_closure_v3(
    project_context: ProjectBindingContextV3,
    metadata_snapshot: GovernedMetadataSnapshotV3,
    approval_record: ApprovalRecordV3,
) -> None
```

- 直接接受 `ProjectBindingContextV3`、`GovernedMetadataSnapshotV3` 和 `ApprovalRecordV3`，
  因此**不是跨版本共享函数**，V2 模块不得调用或导入该函数；
- 可复用的只有 canonical SHA-256 等底层算法，不是本函数本身；
- 成功返回 `None`；任一九组检查失败时抛出 `ApprovalClosureValidationErrorV3`；
- 异常只携带稳定中性 issue code，不泄漏私有标识符或载荷内容；
- 不访问 MongoDB、provider、SQL Server 或环境变量；不修改任何输入对象；
- 验证逻辑即 DEV §5.2 所列九个有序检查组；
- **实际载荷哈希算法**：使用 `application/canonical.py` 的 `canonical_content_sha256` 约定——
  使用 camelCase、JSON 模式的完整模型内容；仅排除根级 `contentSha256`；不删除嵌套 sha256；
  不排除 `approvalRef`、`approvedAt` 或其他业务字段；
  approval/context/snapshot 使用 `canonical_content_sha256`，仅排除根级
  `contentSha256` 自哈希字段；handoff payload 使用 `canonical_sha256`
  对完整 `FactBindingRequestV3` payload 计算；handoff payload 本身不包含
  `wrapper.createdAt` 等存储时间字段；两条路径都不根据字段名称递归删除时间、
  `timeRange`、`approvedAt` 或其他业务字段；
  不改变输入对象，不静默排序或修复输入；
- **信任边界**：`validate_approval_closure_v3` 成功只证明格式、实际载荷哈希、引用及声明状态
  内部一致；它**不能证明** `actorRef` 是真实批准者、人工批准确实发生或该批准当前仍有效；
  真实 M3/M6 必须通过独立受信批准来源核验（见 §3.6）；
- M2 调用该函数，并把领域异常转换为 `BindingResolutionReportV3(status=blocked, ...)`；
- 该函数**不是新的 wire 契约对象**，不加入 §4.3 版本表，无 `schemaVersion` 字段。

## 3.4 复杂事实责任分工（冻结）

- **Agent 1 / businessRuleReview**：业务规则、事实、筛选、聚合、时间语义、派生逻辑 owner；
  负责把固定私有资料中的已确认事实转为新版本 catalog digest、规则版本和 handoff；
  复杂布尔事实（R5/R6/R7/R8/合并组）的业务表达与派生语义由 Agent 1 负责；
- **Agent 2 / SqlBot**：只为 `source`/`aggregate`/`exists` 基础事实生成受约束的单事实只读 SQL
  候选；不假设真实数据库存在 `*_eligible` 物理列；集合型或复合条件必须在上游 vNext 中拆分为
  可追溯基础事实，或定义明确的派生表达式；AST 只能证明参数/对象/列/join/`fact_value` 来源，
  不能证明规则业务语义；
- **metadataReview**：物理绑定、实体键、join 授权及 context/snapshot 的产生与批准 owner；
  原始工作簿不能直接充当 snapshot 或 grant；
- 不在 SqlBot 文档复制私有规则正文、公式和物理字段。

#### 3.5 冻结领域异常（M1 实现）

`ApprovalClosureValidationErrorV3` 定义在 `src/release_sql_bot/domain/project_bindings_v3.py`，
与契约模型同文件，不单独建立异常模块。

异常**只携带稳定 code**，不携带：

- ID 实际值；
- 哈希实际值；
- 私有对象或字段；
- `context`/`snapshot`/`approval` 原始载荷。

冻结以下 9 个 issue code，与 §5.2 九个有序检查组对应（按顺序 fail-fast）：

| # | issue code | 对应检查组（§5.2） |
| --- | --- | --- |
| 1 | `APPROVAL_ID_MISMATCH` | context/snapshot 的 `approvalRef.approvalId` 与 `approval_record.approvalId` 不一致 |
| 2 | `APPROVAL_POLICY_MISMATCH` | 四处 `policyVersion` 不一致（含 `context.authorizationPolicyVersion`） |
| 3 | `APPROVAL_TIME_MISMATCH` | 两份 `approvalRef` 与 `approval_record` 的 `approvedAt` 不一致（wire 字符串精确比较） |
| 4 | `APPROVAL_CONTEXT_REF_MISMATCH` | `contextRef` 的 ID/版本/哈希与 context 不匹配（独立重算哈希） |
| 5 | `APPROVAL_SNAPSHOT_REF_MISMATCH` | `snapshotRef` 的 ID/版本/哈希与 snapshot 不匹配（独立重算哈希） |
| 6 | `APPROVAL_CONTENT_HASH_MISMATCH` | `approval_record` 自哈希不可重算 |
| 7 | `APPROVAL_CONTEXT_NOT_APPROVED` | context 非 `approved` |
| 8 | `APPROVAL_SNAPSHOT_NOT_APPROVED` | snapshot 非 `approved` |
| 9 | `APPROVAL_SNAPSHOT_BINDING_MISMATCH` | `context.metadataSnapshotRef` 与 `approval_record.snapshotRef` 不一致 |

映射规则：

- 九个有序检查组中的每一项必须映射到上述唯一 code，不得多对一或一对多；
- 同一输入同时存在多个错误时，按上表顺序返回第一个错误（fail-fast）；
- M1 函数 `validate_approval_closure_v3` 抛出 `ApprovalClosureValidationErrorV3`；
- M2 捕获该异常并转换成 `blocked` 报告；
- M2 报告只记录 code，不记录异常内部输入。

### 3.6 真实 M3/M6 受信批准来源门禁（门禁要求已批准，实现属于后续阶段）

`validate_approval_closure_v3` 成功只证明内容闭包内部一致，不证明批准真实性。
真实 M3/M6 调用前，受信应用服务**必须**通过受控的只读批准记录端口，按精确 `approvalId`
取得 metadataReview 登记的记录，并执行以下独立核验步骤：

1. 通过受控只读批准记录端口按精确 `approvalId` 取得 metadataReview 登记的记录；
2. 比较取得的记录与携带记录的完整内容及哈希（逐字段一致）；
3. context/snapshot 必须匹配该受信记录（ID、版本、内容哈希三要素）；
4. 正式使用前按已实现生命周期规则检查有效性（active/superseded 状态）；
5. 来源不可用、记录不存在、不匹配或失效时，provider/store 不发生后续副作用。

约束：

- handoff 背书和批准记录核验是**两个独立步骤**，不得合并；
- 不接受调用方自报 `actorRef`/`status` 作为批准真实性证明；
- 端口实现、存储和生命周期仍属于 M6 前的后续阶段，**不得塞入 M1**；
- 不增加 12 项 wire 版本表对象数。

**测试计划明确区分：**

- **M1**：自洽包可以通过内容校验，但不得标记为真实批准；M1 保持纯函数和现有五文件范围，
  不加入数据库访问或新 wire 对象；
- **真实调用服务（后续测试要求，本轮不实现）**：完全自洽、却未登记在受信批准来源的包，
  必须在 provider/store 之前被拒绝。

## 4. 元数据快照与契约版本决策（M1 冻结）

### 4.1 逐字段判定（`GovernedMetadataSnapshotV2` 与 FBR 版本无关性）

| 字段 | 内容 | 与 FBR 版本关系 |
| --- | --- | --- |
| `snapshot_id` / `snapshot_version` / `status` | 身份与审批状态 | 无关 |
| `dialect` | 固定 `sqlserver` | 无关 |
| `identifier_case_sensitivity` | 标识符大小写策略 | 无关 |
| `captured_at` / `source_ref` | 采集时间与来源引用 | 无关 |
| `relations` / `relationships` | 物理关系、列、类型、边 | 无关（纯物理事实） |
| `approval_ref` / `content_sha256` | 批准与自哈希 | 无关 |

字段级结论：快照不含任何 `FactBindingRequest` 语义，是纯物理元数据 DTO。

### 4.2 复用判定

按 [BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md)
第 1、4 条，字段级无关不等于可以 import：V3 模块禁止导入 V2 consumer 模型（REQ-20260906-03
第 6 节已确立）。因此：

- **默认（冻结方向）**：新增 `GovernedMetadataSnapshotV3`，字段形状与上表等价、
  `schema_version="1.0.0"`；DEV 实施时逐字段登记与 V2 的等价性映射作为测试夹具断言；
- **可选替代（被否决为默认，保留为评审选项）**：把物理 DTO 提取为版本中立共享模块供 V2/V3
  同时引用。该重构在 M1 前不实施；若未来采用，必须以独立评审证明模块不携带任何 V2 权威
  语义、且 V2 行为零变化。
- 快照的物理采集、批准流程与 owner 不变（metadataReview；采集需独立授权）。

### 4.3 契约版本表（下游对象独立演进，2026-09-06 审查新增）

消费 `FactBindingRequest 3.0.0` 不等于下游对象继承 `3.0.0`：每个下游对象的 `schemaVersion`
描述**其自身契约**的版本，独立演进、独立提升。V2 现值作为先例依据列出：

| 对象 | 版本字段与首版 | 依据与说明 |
| --- | --- | --- |
| `FactBindingRequest`（上游交接载荷） | `contractVersion = "3.0.0"`（上游冻结，不可变） | 上游权威；消费方不得改写 |
| `HandoffClosureV3`（内容闭包） | `schemaVersion = "1.0.0"` | 新契约首版；其引用闭包携带 FBR `contractVersion=3.0.0` 与 Schema 身份/哈希 |
| `ProjectBindingContextV3` | `schemaVersion = "1.0.0"` | 延续 `ProjectBindingContextV2`（1.0.0）先例；其 `rule_ref.schemaVersion = "3.0.0"` 表示**所引用的 RuleReader 规则契约版本** |
| `GovernedMetadataSnapshotV3` | `schemaVersion = "1.0.0"` | 纯物理元数据 DTO，与 FBR 版本无关（4.1/4.2 节），延续 V2 快照先例 |
| `ApprovalRecordV3`（批准记录） | `schemaVersion = "1.0.0"` | 新契约首版；insert-only 正向批准记录，独立于 SQL 候选审批 |
| `ResolveMetadataRequestV3` | `schemaVersion = "1.0.0"` | 解析请求包装版本，延续 V2 先例；必须携带 `approvalRecord`（§5.2），M2 解析前验证其引用闭包 |
| `BindingResolutionReportV3` | `schemaVersion = "1.0.0"` | 报告契约独立演进，延续 V2 resolution report 先例 |
| `GenerateSqlCandidateRequestV3` | `schemaVersion = "1.0.0"` | 生成请求包装版本，延续 V2 先例 |
| `SqlTemplateCandidateV3` | `schemaVersion = "3.0.0"` | **候选契约自身版本**：延续 `SqlTemplateCandidateV2.schemaVersion="2.0.0"` 的既有先例（候选契约版本与生成管线世代对齐），是显式设计决定，不是对 FBR 3.0.0 的机械继承；其 `rule_ref.schemaVersion="3.0.0"` 才是所引用规则契约版本 |
| `ValidateSqlCandidateRequestV3` | `schemaVersion = "1.0.0"` | 静态门禁请求包装版本，延续 V2 先例 |
| `SqlStaticValidationReportV3` | `schemaVersion = "1.0.0"` | 延续 `SqlStaticValidationReportV2`（1.0.0）先例 |
| V3 候选存储文档包装 | `schemaVersion = "1.0.0"` | 延续 V2 存储文档包装（`mongodb_candidates.py` 的 `schemaVersion="1.0.0"`）先例；内嵌候选本体另带其自身 `3.0.0` |

共 **12 个独立顶层契约对象**。`RepositoryVerifiedHandoffV3` 是受信应用服务在同一次调用中
仓储背书后的内部结果，**没有 wire `schemaVersion`**，不列入本表。

规则：

1. 首版采用 `1.0.0` 的理由统一登记为"延续对应 V2 对象的既有版本先例，自身契约独立演进"；
   `SqlTemplateCandidateV3` 采用 `3.0.0` 的理由如上表单独登记。
2. `ruleRef.schemaVersion` 是唯一表达"所引用 RuleReader 规则契约版本"的字段；其余对象的
   `schemaVersion` 一律指自身契约。
3. V2/V3 隔离依赖**类型系统与引用闭包**（独立模型、独立模块、闭包/报告/候选的哈希
   引用链），不依赖任何“相同数字的 schemaVersion”；测试矩阵第 2 项的双向拒绝按类型断言，
   不按版本号断言。
4. 任一下游对象需要破坏性演进时，提升其自身 `schemaVersion` 并按文档治理规则记录迁移策略；
   不得以“对齐 FBR 版本”为由联动修改其他对象版本。

## 5. Phase 2G V3：元数据解析（M2）

### 5.1 handoff 内容闭包与仓储真实性（`HandoffClosureV3` / `RepositoryVerifiedHandoffV3`，M1 冻结形状、M2 实现）

Phase 2G V3 **不得只接受一个裸 `FactBindingRequestV3`**。2026-09-06 二轮审查冻结**两层概念**，
禁止混用：

1. **`HandoffClosureV3`（内容闭包）**：纯计算、可序列化契约。它只能证明其携带的
   payload/request/batch/Schema 引用**内部一致**（哈希闭合）；**不能证明**：batch 真实存在
   于 MongoDB、batch 由 RuleReader 写入、`intakeStatus` 来自真实 intake、载荷属于当前批准的
   真实运行。从 HTTP/CLI JSON 反序列化得到的 `HandoffClosureV3` 只是内容闭包，**永远不得
   称为 repository-verified attestation**。
2. **`RepositoryVerifiedHandoffV3`（仓储背书）**：受信应用服务在**当前应用调用中**，通过
   `FactBindingHandoffBatchRepositoryV3` 按精确 `ruleVersion` 重新读取 batch、重新执行
   `intake_fact_binding_handoffs_v3`、从 intake 结果选择唯一 request、并与携带
   closure/context/snapshot 逐字段逐哈希比较后构造的**内部结果**。它不是 wire 契约，不可
   从外部 JSON 反序列化。

`HandoffClosureV3` 形状（camelCase、extra=forbid、strict、schemaVersion="1.0.0"；即原
attestation 形状更名）：

```text
HandoffClosureV3
  rule_version:            ≤260；必须 == payload.ruleRef.ruleVersion
  request_id:              ≤420；必须 == payload.requestId == <ruleVersion>#<factCode>
  fact_code:               与 batch wrapper 的 fact_code 逐字节一致
  payload_sha256:          使用既有 canonical_sha256 对完整 payload 计算；
                          当前 payload 不含 handoff wrapper 的 createdAt 等存储时间字段
  batch_sha256:            所引用 batch 的 canonical SHA-256
  contract_schema_id:      urn:rulereader:fact-binding-request:3.0.0
  contract_schema_sha256:  2c5e4603…（冻结 Schema 副本哈希）
  intake_status:           Literal["readyForMetadataResolution"]（intake 输出状态快照）
  payload:                 精确 FactBindingRequestV3
```

内容闭包校验规则（纯计算，M2 实现；对 closure 自身）：

实现于 `application/validate_handoff_closure_v3.py` 的
`validate_handoff_closure_v3(closure: HandoffClosureV3) -> None`。
成功返回 None，失败抛出 `HandoffClosureValidationErrorV3`（仅携带稳定 code）。

按以下顺序 fail-fast，第一个失败即返回：

| # | code | 检查内容 |
|---|------|----------|
| 1 | `HANDOFF_STRUCTURE_INVALID` | 重验 closure 结构和完整嵌套 V3 consumer 约束（防止 model_copy 绕过） |
| 2 | `HANDOFF_SCHEMA_SOURCE_INVALID` | 冻结 Schema 加载器无法加载或来源校验失败 |
| 3 | `HANDOFF_SCHEMA_REF_MISMATCH` | contractSchemaId/contractSchemaSha256 与加载器常量不一致 |
| 4 | `HANDOFF_PAYLOAD_SCHEMA_INVALID` | payload 不符合随包冻结的 V3 JSON Schema |
| 5 | `HANDOFF_IDENTITY_MISMATCH` | closure 与 payload 身份不一致 |
| 6 | `HANDOFF_PAYLOAD_HASH_MISMATCH` | payload 内容哈希不一致 |

校验细节：

1. **payload 闭合**：使用既有 `canonical_sha256(closure.payload)` 对完整 payload 计算
   （当前 payload 不含 handoff wrapper 的 createdAt 等存储时间字段），
   重算值必须 == `payload_sha256`；
   `request_id == payload.requestId == <rule_version>#<fact_code>`；
2. `contract_schema_id`/`contract_schema_sha256` 与冻结 Schema 加载器常量一致；
3. 客户端**自报的** `intake_status` 只是快照，本身不构成任何证明。

**仓储背书规则（一切会产生外部副作用的路径的前置，在同一次应用调用中执行）**——覆盖：
provider 调用、candidate store 写入、Phase 4R 真实证据包、未来任何真实 V3 生成入口：

1. 接受精确 `ruleVersion` + `requestId`；
2. 通过仓储重新读取 batch（不信任调用方提供的 batch 内容）；
3. 重新执行 V3 intake（Schema/身份/哈希/引用闭包门禁全部重跑）；
4. 从 intake 返回结果中选择唯一 request；
5. 与携带 closure/context/snapshot 逐字段逐哈希比较；
6. 全部一致后才构造 `RepositoryVerifiedHandoffV3` 并允许调用 provider 或 store。

**仅“重新计算调用方提供的 hash”不构成仓储背书**；batch/request/payload/Schema 任一哈希或
闭包不一致、或仓储无 batch → 该请求失败，provider 调用次数与 store `save` 调用次数均为 0。

其他边界：

- `ResolveMetadataRequestV3` 保持纯计算并携带 `HandoffClosureV3`；其 `metadataResolved` 只
  表示内容与授权解析闭合，**不得宣称真实 batch 已验证**；
- 纯离线单元测试可携带按闭包规则构造的合成 `HandoffClosureV3`（任何篡改用例必须失败），
  但此类测试结论**明确不构成真实仓储证明**；
- 该证明**不得**用 V2 `BindingGapReport` 或任何 V2 报告替代；V3 没有 gap 语义，内容闭包 +
  仓储背书合起来才是 Phase 2F 的 V3 等价物；
- candidate（第 6 节）、V3 静态报告（第 7 节）与 Phase 4R 证据包中的 `batch_sha256`/
  `payload_sha256` 是**追溯引用，不是真实性证明**；真实运行的证据包还必须记录 repository
  verification 阶段及其结果，形成 intake → 仓储背书 → 解析 → 生成 → 门禁 → 存储的全链路
  可追溯闭环。

### 5.2 请求契约

```text
ResolveMetadataRequestV3（camelCase、extra=forbid、strict）
  schema_version = "1.0.0"            解析请求自身的包装版本，独立演进
  project_ref:                        ProjectRefV3 形状
  handoff_closure:                    HandoffClosureV3（5.1 节内容闭包；非仓储背书）
  binding_request:                    FactBindingRequestV3（与 closure.payload 逐字节一致）
  project_context:                    ProjectBindingContextV3（status=approved）
  metadata_snapshot:                  GovernedMetadataSnapshotV3（status=approved）
  approval_record:                    ApprovalRecordV3（§3.3 批准记录；M2 解析前必须验证）
```

无 `binding_gap_report` 字段：V2 的 `BindingGapReport` 是 Phase 2F 输出；V3 的等价门禁由
内容闭包 + 仓储背书（5.1 节）与 intake batch 校验（Schema/身份/哈希/evidence 闭包）承担。

**M2 批准记录验证（九个有序检查组，解析前必须全部通过，任一失败 → `blocked`）：**

按以下顺序执行九个检查组，输入有多个失败时返回第一个失败组的 code（fail-fast）。
同组可包含多个字段比较；不是"每个字段一个错误码"。

1. **`APPROVAL_ID_MISMATCH`**：`project_context.approvalRef.approvalId` 必须等于
   `approval_record.approvalId`（精确字符串一致性；approvalId 是不透明稳定 ID，不做哈希校验）；
   `metadata_snapshot.approvalRef.approvalId` 同样必须等于 `approval_record.approvalId`。
   组内顺序：context 先，snapshot 后。
2. **`APPROVAL_POLICY_MISMATCH`**：以下四处 `policyVersion` 必须一致——
   `project_context.approvalRef.policyVersion`、`metadata_snapshot.approvalRef.policyVersion`、
   `approval_record.policyVersion`、`project_context.authorizationPolicyVersion`。
   不能遗漏上下文本体的授权策略版本。
3. **`APPROVAL_TIME_MISMATCH`**：`project_context.approvalRef.approvedAt`、
   `metadata_snapshot.approvalRef.approvedAt`、`approval_record.approvedAt` 三者一致。
   采用已序列化 wire 字符串精确比较，输入均须带时区；不在校验时静默改写时间。
4. **`APPROVAL_CONTEXT_REF_MISMATCH`**：`approval_record.contextRef` 的 `contextId` 和
   `contextVersion` 精确匹配 `project_context`；独立重算 `project_context` 内容哈希，
   重算值必须同时等于 `project_context.contentSha256` 和 `approval_record.contextRef.sha256`。
5. **`APPROVAL_SNAPSHOT_REF_MISMATCH`**：`approval_record.snapshotRef` 的 `snapshotId` 和
   `snapshotVersion` 精确匹配 `metadata_snapshot`；独立重算 `metadata_snapshot` 内容哈希，
   重算值必须同时等于 `metadata_snapshot.contentSha256` 和 `approval_record.snapshotRef.sha256`。
6. **`APPROVAL_CONTENT_HASH_MISMATCH`**：独立重算 `approval_record` 内容哈希，并与
   `approval_record.contentSha256` 比较。
7. **`APPROVAL_CONTEXT_NOT_APPROVED`**：`project_context.status` 必须为 `approved`。
8. **`APPROVAL_SNAPSHOT_NOT_APPROVED`**：`metadata_snapshot.status` 必须为 `approved`。
9. **`APPROVAL_SNAPSHOT_BINDING_MISMATCH`**：`project_context.metadataSnapshotRef` 与
   `approval_record.snapshotRef` 的 ID、版本和哈希全部一致。

任一失败：`BindingResolutionReportV3.status=blocked`；不产生部分授权计划；不调用
provider/store；issue code 不包含私有标识符或载荷内容。M3 在 provider 前重算 M2 时必须重复
这些检查，不能仅信任携带的 `metadataResolved` 报告。approvalId 只做精确字符串一致性检查，
不使用 approvalId 替代任何哈希校验。
禁止为对齐形状而伪造空 gap report。

### 5.3 确定性解析规则

全部为纯计算，不访问 SQL Server、仓储或 provider（`HandoffClosureV3` 的内容闭包校验是纯
计算；仓储重读与重验只属于有外部副作用的路径，见 5.1 节仓储背书规则与 6.1 节 M3 方案）：

#### 5.3.0 输入门禁子任务（内部辅助函数，2026-09-10 新增，已完成）

实现未来解析服务使用的内部辅助函数 `_validate_resolution_input_v3`，
成功仅表示输入内容闭包和范围一致，不代表物理授权解析完成、批准真实有效或仓储背书完成。

新增文件：`application/metadata_resolution_v3.py`

```text
_validate_resolution_input_v3(
    request: ResolveMetadataRequestV3,
) -> ResolveMetadataRequestV3
```

成功返回重新构造并通过本轮门禁的独立请求副本。不得修改原输入，也不得返回原输入对象。

内部异常：`MetadataResolutionInputErrorV3`（只携带稳定 code，不携带输入内容）。

按以下顺序 fail-fast（第一个失败即返回）：

| # | code | 检查内容 |
|---|------|----------|
| 0 | `METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID` | 请求结构重验：根对象必须是 `ResolveMetadataRequestV3`；dict、V2 模型和其他对象不得自动接入；用 `model_dump(by_alias=True, mode="json", warnings="error")` 序列化后重建，防止 model_copy 或构造后修改绕过原始契约 |
| 1 | `HANDOFF_STRUCTURE_INVALID` 等 6 code | 调用现有 `validate_handoff_closure_v3`，完整保留其六组检查及错误码 |
| 2 | `HANDOFF_BINDING_REQUEST_MISMATCH` | `bindingRequest` 与 `closure.payload` 字节一致：使用 `canonical_json_bytes` 分别序列化完整模型并比较字节，不得只比较 requestId、ruleRef 或摘要字段 |
| 3 | `APPROVAL_ID_MISMATCH` 等 9 code | 调用现有 `validate_approval_closure_v3`，保留九组 fail-fast 检查及原 code |
| 4 | `PROJECT_REF_MISMATCH` | `request.projectRef` 与 `projectContext.projectRef` 完整相等（projectId、projectVersion） |
| 5 | `RULE_REF_MISMATCH` | `projectContext.ruleRef` 与 `bindingRequest.ruleRef` 完整相等（ruleSetId、ruleVersion、schemaVersion、sourceSha256、catalogDigest、candidatePayloadSha256） |
| 6 | `REQUEST_NOT_IN_CONTEXT` | `bindingRequest.requestId` 必须在 `projectContext.requestIds` 中 |

本轮不定义公开 `resolve_metadata_v3` 函数；不生成 `BindingResolutionReportV3`；
不添加"尚未实现"的假 blocked 报告或成功占位结果。

#### 5.3.2 字段绑定与实体键授权闭包（内部辅助函数，2026-09-10 新增）

实现未来解析服务使用的内部辅助函数 `_resolve_fields_and_entity_keys_v3`，
成功只表示字段与实体键的显式授权引用闭包完整，不表示 SQL 可执行。

```text
_resolve_fields_and_entity_keys_v3(
    request: ResolveMetadataRequestV3,
) -> tuple[tuple[ResolvedFieldV3, ...], tuple[ResolvedEntityKeyV3, ...]]
```

输出第一项按 `queryRequirements.fields` 原顺序返回；
第二项按 `entity.keyParameters` 原顺序返回。
使用已有 V3 DTO，不新增 wire 契约。
任一失败抛出中性异常，不返回部分结果。

内部异常：`MetadataBindingResolutionErrorV3`（只携带稳定 code）。

处理顺序（fail-fast）：

| # | code | 检查内容 |
|---|------|----------|
| 0 | 传播 `MetadataResolutionInputErrorV3` | 调用 `_validate_resolution_input_v3(request)` |
| 1 | `FIELD_AUTHORIZATION_MISSING` | 按完整键 `(requestId, fieldId, role)` 匹配 field authorization；不匹配则拒绝 |
| 2 | `ENTITY_KEY_AUTHORIZATION_MISSING` | 按 `(requestId, parameterName)` 匹配 entity-key authorization；零条则拒绝 |
| 3 | `ENTITY_KEY_AUTHORIZATION_AMBIGUOUS` | 同一参数对应多条不同 fieldId 的授权；拒绝歧义 |
| 4 | `ENTITY_KEY_FIELD_NOT_FOUND` | entity-key 指定的 fieldId 不存在于本请求 fields |
| 5 | `ENTITY_KEY_FIELD_ROLE_MISMATCH` | 指定 field 存在但 role 不是 entityKey |
| 6 | `ENTITY_KEY_COLUMN_GRANT_MISMATCH` | entity-key 与对应 field authorization 的 columnGrantId 不同 |

（历史快照：字段/实体键子任务完成时尚未实现 filters/aggregation/timeRange；
filters 与 aggregation 辅助函数已分别在 §5.3.3、§5.3.4 完成。）

本轮不实现 SQL 类型兼容、join 解析或报告组装。

#### 5.3.1 单 column grant 物理引用解析（内部辅助函数，2026-09-10 新增）

实现未来解析服务使用的内部辅助函数 `_resolve_column_grant_v3`，
成功只表示给定 column grant 的物理引用链与批准包中的快照一致，
不表示字段绑定授权、实体键、join 已解析，或 SQL 可执行。

```text
_resolve_column_grant_v3(
    request: ResolveMetadataRequestV3,
    column_grant_id: str,
) -> PhysicalColumnRefV3
```

返回新构造的 `PhysicalColumnRefV3`，包含快照中准确拼写的 schemaName/relationName/columnName。

内部异常：`MetadataColumnResolutionErrorV3`（只携带稳定 code）。

处理顺序（fail-fast）：

| # | code | 检查内容 |
|---|------|----------|
| 0 | 传播 `MetadataResolutionInputErrorV3` | 调用 `_validate_resolution_input_v3(request)`，使用返回的独立副本 |
| 1 | `COLUMN_GRANT_ID_INVALID` | 参数必须是字符串，符合 `^[A-Za-z0-9][A-Za-z0-9._:-]*$`，最长 200，不 strip/casefold |
| 2 | `SNAPSHOT_RELATION_AMBIGUOUS` | 建立快照物理标识符唯一性索引；sensitive 逐码点精确比较，insensitive 用 `str.casefold()`；关系键 `(schema, relation)` 重复即拒绝 |
| 3 | `SNAPSHOT_COLUMN_AMBIGUOUS` | 列键 `(schema, relation, column)` 重复即拒绝；同名列位于不同关系中合法 |
| 4 | `COLUMN_GRANT_NOT_FOUND` | 在 `context.columnGrants` 中按 grantId 精确匹配；grant ID 不适用物理标识符大小写策略 |
| 5 | `RELATION_GRANT_NOT_FOUND` | 读取 column grant 的 `relationGrantId`，在 `context.relationGrants` 中精确匹配 |
| 6 | `RELATION_NOT_IN_SNAPSHOT` | 用 relation grant 的 `schemaName/relationName` 按大小写策略匹配唯一 snapshot relation |
| 7 | `COLUMN_NOT_IN_SNAPSHOT` | 在上述关系中，用 `column grant.columnName` 按同一策略匹配唯一 snapshot column |

大小写规则：只使用 `snapshot.identifierCaseSensitivity`，不读取数据库 collation 或环境变量。
索引规范化只用于比较，不修改快照或输出拼写。

1. 输入门禁：先按 5.1 节内容闭包规则校验 `handoff_closure`（payload/Schema 哈希与身份，
   任一失败 → `blocked`）；再校验
   context/snapshot `status=approved`、自身 canonical hash 重算一致、
   `metadata_snapshot_ref` 精确匹配、`request_ids` 包含当前 `requestId`、
   `rule_ref` 与 payload `ruleRef` 逐字段一致（含 `catalogDigest`/
   `candidatePayloadSha256`）；任一失败 → `blocked`（issue 归 metadataReview）。
2. entity：`entityType`/`grain` 解析为授权 relation；`keyParameters`（V3 参数带
   `role=entityKey`）逐个按 `requestId+parameterName+fieldId` 命中 entity-key authorization，
   且 field binding → column grant → snapshot 列三重命中。
3. fields：逐个按 `requestId+fieldId+role` 命中 field binding authorization；物理列必须同时
   命中 column grant 与 snapshot 列；`mappingCandidate` 仅作不可信参考，不参与判定。
4. filters：`FilterSetV3.items`（平铺列表，非递归树）的每个字段引用按 fields 同规则解析；
   每项 `evidenceIds` 必须命中请求顶层 evidence，`completeness=complete` 已由
   Schema const 保证，不重复引入 V2 unresolved 语义。
5. aggregation：`groupByFieldIds` 与聚合引用字段按 fields 规则解析；`mode=none` 时无需字段。
6. timeRange：`asOf`/`between` 的时间锚字段按 fields 规则解析；边界值不参与授权。
7. join：多 relation 场景实际 join 端点必须命中显式 join grant 且 snapshot relationship edge
   存在。
8. 使用位置：报告保留每个 resolved 项的 `evidenceIds`（原样来自 payload）与 usage 引用闭包
   摘要（见 5.4）。

#### 5.3.3 filters 物理字段解析（内部辅助函数，2026-09-11 新增）

实现未来解析服务使用的内部辅助函数 `_resolve_filters_v3`，
成功只表示每个 filter item 所引用字段的授权物理映射完整，
不表示过滤语义正确、SQL 可执行或完整 M2 已完成。

```text
_resolve_filters_v3(
    request: ResolveMetadataRequestV3,
) -> tuple[ResolvedFilterV3, ...]
```

按 `queryRequirements.filters.items` 原顺序返回。使用已有 V3 DTO，不新增 wire 契约。
即使 `items` 为空，也必须先完成输入门禁与字段/实体键校验，成功后返回空 tuple。
任一失败抛出中性异常，不返回部分结果。

内部异常：`MetadataFilterResolutionErrorV3`（只携带稳定 code）。

处理顺序（fail-fast）：

| # | code | 检查内容 |
|---|------|----------|
| 0 | 传播 `MetadataResolutionInputErrorV3` | 调用 `_validate_resolution_input_v3(request)`，使用返回的独立副本 |
| 1 | 传播 `MetadataBindingResolutionErrorV3` 等 | 调用 `_resolve_fields_and_entity_keys_v3(verified)`，不接受调用方提供的 resolvedFields 或"已验证"标志 |
| 2 | `FILTER_EVIDENCE_REFERENCE_INVALID` | 每项 filter 的 `evidenceIds` 必须命中请求顶层 evidence |
| 3 | `FIELD_AUTHORIZATION_MISSING`（传播） | 按 `field_id` 查找已授权 `ResolvedFieldV3`；找不到则传播字段授权错误 |

逐项映射规则：
- `filterId`、`fieldId`：原样来自当前 filter item；
- `schemaName`/`relationName`/`columnName`：来自该 `fieldId` 对应的已授权 `ResolvedFieldV3` 的准确拼写；
- `evidenceIds`：原样复制当前 filter item 自身的 `evidenceIds`，不使用字段或 FilterSet 的 `evidenceIds` 替代；
- 字段授权按字段实际声明的 `role` 校验，不因被 filter 引用就强行要求 `role="filter"`；
- 不解释 operator、不计算 literal/parameter 值、不改变 nullPolicy、required 或任何输入内容；
- 不添加 filterId 唯一性限制，不排序、去重或合并 filter items。

#### 5.3.4 aggregation 授权引用解析（内部辅助函数，2026-09-11 新增）

实现未来解析服务使用的内部辅助函数 `_resolve_aggregation_v3`，
成功只表示聚合声明中每个引用字段都有对应授权结果、六个声明字段被原样保留，
不表示聚合计算正确、SQL 可执行或完整 M2 已完成。

```text
_resolve_aggregation_v3(
    request: ResolveMetadataRequestV3,
) -> ResolvedAggregationV3
```

原样复制六个字段：`mode`、`function`、`inputFieldIds`、`groupByFieldIds`、
`distinct`、`evidenceIds`。枚举转对应 wire 字符串；三个列表分别复制，
不与输入共享可变列表。即使 `inputFieldIds`/`groupByFieldIds` 为空（`none`/
`precomputed`/`exists`），也必须先完成输入门禁与字段/实体键校验。
任一失败抛出中性异常，不返回部分结果。

处理顺序（fail-fast）：

| # | code | 检查内容 |
|---|------|----------|
| 0 | 传播 `MetadataResolutionInputErrorV3` | 调用 `_validate_resolution_input_v3(request)`，使用返回的独立副本 |
| 1 | 传播 `MetadataBindingResolutionErrorV3` 等 | 调用 `_resolve_fields_and_entity_keys_v3(verified)`，不接受调用方提供的 resolvedFields 或"已验证"标志 |
| 2 | `FIELD_AUTHORIZATION_MISSING`（传播） | `inputFieldIds` + `groupByFieldIds` 中每个引用均有对应已授权字段结果；按字段实际 role 校验，不强制改成 `value`/`groupBy` |
| 3 | 构造结果 | 原样复制六个声明字段，列表分别复制 |

语义边界：支持既有四种 mode（`none`/`precomputed`/`compute`/`exists`）；
`compute` 的 `function` 支持现有六种枚举（`sum`/`count`/`countDistinct`/
`avg`/`min`/`max`）。不排序、去重、补默认值、改写 `distinct`；
`countDistinct` 不自动改写成 `count`。不调用 filters helper。
（历史快照：aggregation 子任务完成时尚未实现 timeRange/join；
timeRange 辅助函数已在 §5.3.5 完成。）

不实现 join、公开 `resolve_metadata_v3` 或报告组装。

#### 5.3.5 timeRange 时间字段授权解析（内部辅助函数，2026-09-11 新增）

实现未来解析服务使用的内部辅助函数 `_resolve_time_range_v3`，
成功只表示时间字段的授权物理映射，不表示时间条件业务正确、
SQL 可执行或完整 M2 已完成。

```text
_resolve_time_range_v3(
    request: ResolveMetadataRequestV3,
) -> ResolvedTimeRangeV3
```

处理顺序（fail-fast）：

| # | code | 检查内容 |
|---|------|----------|
| 0 | 传播 `MetadataResolutionInputErrorV3` | 调用 `_validate_resolution_input_v3(request)`，使用返回的独立副本 |
| 1 | 传播 `MetadataBindingResolutionErrorV3` 等 | 调用 `_resolve_fields_and_entity_keys_v3(verified)`，不接受调用方提供的 resolvedFields 或"已验证"标志 |
| 2 | `FIELD_AUTHORIZATION_MISSING`（传播） | `asOf`/`between` 模式下 `timeFieldId` 有对应已授权字段结果；按字段实际 role 校验，不强制改成 `time` |
| 3 | 构造结果 | `none` 返回空物理标识；`asOf`/`between` 使用已授权字段的准确拼写 |

语义边界：`none` 模式也必须先完成输入门禁与字段/实体键校验。
不新增日期有效性、起止大小、时区换算或夏令时规则；
`start`/`end`/`inclusive`/`timezone` 保持输入原样。不调用
filters/aggregation helper。不实现 join、公开 `resolve_metadata_v3` 或报告组装。

#### 5.3.6 指定 join grant 的物理授权解析（内部辅助函数，2026-09-11 新增）

实现未来解析服务使用的内部辅助函数 `_resolve_join_grant_v3`，
成功只表示指定 join grant 与快照物理边闭合，不表示完整 join 计划、
M2 完成或 SQL 可执行。

```text
_resolve_join_grant_v3(
    request: ResolveMetadataRequestV3,
    join_grant_id: str,
) -> tuple[PhysicalColumnRefV3, PhysicalColumnRefV3, JoinTypeV3]
```

返回顺序固定为：左端物理列、右端物理列、join type。

处理顺序（fail-fast）：

| # | code | 检查内容 |
|---|------|----------|
| 0 | 传播 `MetadataResolutionInputErrorV3` | 调用 `_validate_resolution_input_v3(request)` |
| 1 | `JOIN_GRANT_ID_INVALID` | 字符串、长度 1–200、匹配稳定 ID 格式，不 strip/大小写转换 |
| 2 | `JOIN_GRANT_NOT_FOUND` | 在 `projectContext.joinGrants` 按 grantId 精确匹配 |
| 3 | 传播列解析异常 | 依次调用 `_resolve_column_grant_v3`（先左后右） |
| 4 | `JOIN_ENDPOINTS_IDENTICAL` | 两端规范化后完全相同 |
| 5 | `JOIN_RELATIONSHIP_NOT_FOUND` / `JOIN_RELATIONSHIP_AMBIGUOUS` | 无向端点匹配快照 relationship 边 |

内部异常：`MetadataJoinResolutionErrorV3`（只携带稳定 code）。
只验证指定 grant，不遍历所有可用 join，不选择路径或方向。
返回值始终保留 join grant 的左右方向（LEFT JOIN 不因无向比较而交换）。
暂不构造 ResolvedJoinV3，不伪造 evidenceIds。

#### 5.3.6.1 多关系授权连接闭包选择（内部辅助函数，2026-09-11 新增）

实现未来解析服务使用的内部辅助函数 `_select_join_closure_v3`，
沿用既有 V2 的确定性连接闭包原则，返回被选中的 join grant ID
（按 ID 升序排列），不表示 SQL 执行顺序。

```text
_select_join_closure_v3(
    request: ResolveMetadataRequestV3,
) -> tuple[str, ...]
```

处理顺序（fail-fast）：

| # | code | 检查内容 |
|---|------|----------|
| 0 | 传播 `MetadataResolutionInputErrorV3` | 调用 `_validate_resolution_input_v3(request)` |
| 1 | 传播字段/实体键异常 | 调用 `_resolve_fields_and_entity_keys_v3(verified)` |
| 2 | 传播单个 grant 异常 | 按 grantId 升序遍历，逐项调用 `_resolve_join_grant_v3`，任一失败即传播。此步骤在所需关系收集之后、单关系提前返回之前执行，确保所有 grant 都被校验 |
| 3 | 收集所需关系 | 从所有已解析字段的物理引用收集 `(schema, relation)`，按大小写策略规范化 |
| 4 | 单关系提前返回 | ≤1 个所需关系返回空 tuple（位于 grant 验证之后） |
| 5 | 筛选候选 | 仅保留两端属于两个不同所需关系的已验证 grant |
| 6 | 连通性检查 | 无向图遍历；无法连接全部所需关系 → `JOIN_CLOSURE_DISCONNECTED` |
| 7 | 歧义检查 | 连通但候选数 ≠ 关系数-1 → `JOIN_CLOSURE_AMBIGUOUS` |
| 8 | 返回 | 候选 grant ID 升序 tuple |

内部异常：`MetadataJoinClosureErrorV3`（只携带稳定 code）。
不引入未使用的桥接关系，不自动选择生成树或最短路径。
不构造 ResolvedJoinV3，不伪造 evidenceIds，不实现公开编排。

### 5.4 报告契约与 usage 六元组追溯保留

```text
BindingResolutionReportV3
  schema_version = "1.0.0"             报告契约自身版本（见 4.3 节版本表）
  status: blocked | metadataResolved；executable=false 固定
  request_ref / project_ref / context_ref / snapshot_ref（含哈希）
  handoff_refs:                        batch_sha256 / payload_sha256 /
                                       contract_schema_id / contract_schema_sha256
                                       （来自 5.1 内容闭包，向下游传递；属追溯引用，
                                       不是真实性证明）
  resolution_hashes:                   payload/context/snapshot 等 canonical SHA-256
  resolved_fields / resolved_entity_keys / resolved_filters /
  resolved_aggregation / resolved_time_range / resolved_joins
  usage_traceability_sha256:           完整 usage 六元组数组摘要（规范见下；
                                       wire 字段 usageTraceabilitySha256）
  issues:                              稳定排序，携带 V2 同型的 owner 三类映射
```

**usage 唯一性事实（2026-09-06 二轮审查按现行代码更正，`domain/fact_bindings_v3.py`）**：

- V3 consumer 的既有重复门禁是**四元组** `(stage, ruleCode, conditionId, conditionPath)`
  组合唯一；**不保证 `conditionId` 单独唯一**；
- 因此**同一个 `conditionId` 可以出现在不同 stage、ruleCode 或 conditionPath 中**：这些是
  合法的不同 usage，必须全部保留、不得合并；文档中“conditionId 全请求唯一 /
  consumer 强制 conditionId 唯一 / 用 conditionId 消除排序歧义 / 防御性拒绝重复
  conditionId”的表述全部作废；
- 下游权威追溯身份仍采用**完整六元组** `(stage, ruleCode, priority, conditionId,
  conditionPath, outcome)`；不得为下游实现方便收紧上游契约，除非另立跨仓库契约变更需求。

**`usage_traceability_sha256` 规范（本节即冻结；Python/domain 字段
`usage_traceability_sha256`，camelCase wire 字段 `usageTraceabilitySha256`，全仓库只允许
这一组名称）**：

- **traceability linkage（追溯链路）**：应用从权威 `FactBindingRequestV3.usages` 确定性复制
  完整六元组并计算 canonical 摘要；报告、候选、存储记录都必须能追溯到完整 usages；
- **实现**：`application/usage_traceability_v3.py` 的
  `compute_usage_traceability_sha256_v3(usages: Sequence[FactUsageV3]) -> str`；
- 计算步骤：对每条 usage 投影出恰好六个 camelCase 字段（`stage`/`ruleCode`/`priority`/
  `conditionId`/`conditionPath`/`outcome`）→ 按完整稳定排序键排序——`stage` 使用明确业务
  顺序 `stateGuards → prerequisites → eligibility → postGates → exclusions`，其后依次
  `priority` 数值升序、`ruleCode` 字符串升序、`conditionId` 字符串升序、`conditionPath`
  字符串升序、`outcome` 字符串升序（**排序不得只使用 stage + priority + conditionId**：
  同四元组前缀下剩余字段必须参与，保证全序唯一确定）→ 对排序后的六字段对象数组按既有
  `canonical_sha256` 规则（UTF-8、`sort_keys=True`、紧凑 separators、`ensure_ascii=False`、
  禁 NaN）计算；
- 先检查上游四元组唯一性 `(stage, ruleCode, conditionId, conditionPath)`，完全相同则拒绝；
  `evidenceIds` 不进入本摘要（仍通过 `payloadSha256` 与 handoff closure 追溯）；
  相同 `conditionId` 的多个不同 usage 全部保留、全部参与摘要；
- **上游顺序语义核查（2026-09-06）**：冻结的上游 Schema 对 `usages` 数组只声明
  `minItems: 1`，无 `uniqueItems`、无任何顺序语义；首个命中语义由 `priority` 字段承载。
  若上游未来明确 usages 数组原顺序具有额外业务语义，**必须停止排序并登记“保留原顺序还是
  规范排序”为阻断性开放问题**，不得自行选择；
- 摘要只证明 usages 六元组投影一致，**不证明** AST 或业务语义正确；**不能替代**逐项六元组
  比较：Phase 3/4 必须重算该摘要并与请求 `usages` 比对，候选 coverage 还须按第 7 节对完整
  六元组逐项精确校验；
- **AST evidence 边界**：SQLGlot 解析只能从 SQL 中证明参数、对象、列、join 端点、
  `fact_value` 来源以及与事实查询有关的位置；**不能**证明该事实在 RuleAgent 规则树中的
  `stage`、`ruleCode`、`priority` 或 `outcome` 业务语义——这些语义只能以追溯方式从权威
  请求复制并校验一致性，永不从 SQL 推导；
- **禁止**把 usage 折叠成 `conditionId` 集合，或折叠成 `conditionId + stage + outcome`
  的子集（`ruleCode`/`priority`/`conditionPath` 同为六元组身份字段）。

**报告输入格式约束（2026-09-10 补全，BUG-20260910-01 修复）**：

`BindingResolutionReportV3` 在共享 `V3ReportModel`（camelCase、extra=forbid、frozen、
`str_strip_whitespace=True`、非 strict）基础上，仅在本类覆盖以下配置：

- `strict=True`：禁止 Pydantic 类型强转。`issues`、`resolvedFields`、
  `resolvedEntityKeys`、`resolvedFilters`、`resolvedJoins` 等列表字段传入
  `tuple` 时必须拒绝。注：`requestRef.requestId` 传入整数时，由 `RequestRefV3`
  自身的 `str` 类型约束拒绝（两个版本中均已被拒绝，错误位置
  `("requestRef", "requestId")`，类型 `string_type`），属于既有约束，
  不是本轮修复内容；
- `str_strip_whitespace=False`：关闭字符串两端空白的自动删除。
  SHA-256 字段由既有格式约束（`pattern=_SHA256_PATTERN`）拒绝前后空白。
  普通说明字段是否允许空白，由其自身契约决定；
- 保持既有 camelCase、extra=forbid、snake_case 拒绝、frozen 和其他约束；
- 不修改共享 `V3ReportModel`，避免影响既有 intake 报告（V3 intake report 等）；
- 不新增哈希重算、批准校验或授权解析到 Pydantic validator。

**blocked 报告六项输出完整性（2026-09-10 补全）**：

`status=blocked` 时，`_validate_report_consistency` 必须检查全部六个结果字段：

1. `resolvedFields` 必须为空列表；
2. `resolvedEntityKeys` 必须为空列表；
3. `resolvedFilters` 必须为空列表；
4. `resolvedJoins` 必须为空列表；
5. `resolvedAggregation` 必须为 `None`；
6. `resolvedTimeRange` 必须为 `None`；
7. 必须至少包含一个 `impact=blocker` 的 issue；
8. `executable` 始终为 `false`（由 `Literal[False]` 保证）。

即使 `resolvedAggregation.mode="none"` 或 `resolvedTimeRange.mode="none"`，它仍是
非 `None` 的解析结果对象，blocked 也必须显式拒绝，不能静默删除、清空或修复输入。
`metadataResolved` 的合法行为保持不变，包括允许上述可选字段为 `None`。

## 6. V3 候选生成（M3）

1. `GenerateSqlCandidateRequestV3 = schema_version("1.0.0") + resolution_request(5.2) +
   resolution_report(5.4)`；provider 调用前必须在**同一次应用调用**中完成：① 按 5.1 节
   仓储背书规则重新读取 batch、重新执行 intake、选择唯一 request 并与携带
   closure/context/snapshot 逐字段逐哈希比较（仅重算调用方提供的 hash 不算背书）；② 完整
   重算 Phase 2G V3 并与携带报告 canonical 一致；任一背书/闭包/报告不一致即阻断，
   **provider 调用次数与 store `save` 调用次数均为 0**。
2. **M3 应用服务安全方案（二选一，2026-09-06 二轮审查冻结，推荐方案 a）**：
   - **方案 a（推荐，采用）**：M3 生成服务直接依赖只读 handoff repository
     （`FactBindingHandoffBatchRepositoryV3`），在 provider 调用前于服务自身内部完成仓储
     重读与背书——安全边界能在 provider 调用服务自身被验证；
   - 方案 b（备选，仅当方案 a 的依赖注入在装配上不可行时）：M3 只能由 M6 编排服务调用，
     不提供任何独立 HTTP/CLI/wire 入口，输入使用编排服务内部构造的
     `RepositoryVerifiedHandoffV3`；采用方案 b 时必须在实施登记中说明放弃方案 a 的具体
     装配原因。
   两种方案下，从 HTTP/CLI JSON 反序列化的 `HandoffClosureV3` 都只是内容闭包，永远不得
   被当作仓储背书。
3. 独立 Prompt：新命名空间 `sqlserver-fact-candidate-v3.x`，不复用 V2 Prompt 文本或其
   `exactOutputDeclarations` 结构；V3 Prompt 投影必须包含完整 usage 六元组的结构化投影
   （`stage`/`ruleCode`/`priority`/`conditionId`/`conditionPath`/`outcome`）与实体键/筛选/
   聚合/时间契约，参数只投影名称、类型、required 与来源声明，不投影参数值。
4. `SqlTemplateCandidateV3`（应用组装，模型输出只是不可信 payload）：
   - `schema_version="3.0.0"`——**候选契约自身版本**（见 §4.3 中 SqlTemplateCandidateV3 条目），
     是显式设计决定，不是机械继承）；`status=candidate`、`executable=false`、`review_status=pending`
     （全部固定，模型不能提供或覆盖）；
   - `rule_ref`：`ruleSetId`/`ruleVersion`/`schemaVersion="3.0.0"`（此字段表示所引用的
     RuleReader 规则契约版本）/`sourceSha256`/`catalogDigest`/`candidatePayloadSha256`
     ——V3 provenance 闭包；
   - `request_ref`、`project_ref`、`context_ref`、`snapshot_ref`、`resolution_ref`
     （report sha256）、`generation_input_sha256`、`fact_ref` 与 V2 审计形状等价但绑定
     V3 引用；新增 `handoff_refs`（`batch_sha256`/`payload_sha256`/
     `contract_schema_id`/`contract_schema_sha256`，取自内容闭包）——**追溯引用，不是
     真实性证明**（真实性由仓储背书阶段及其证据记录承担）；
   - `declared_usage_coverage`：**完整六元组结构化条目列表**（`stage`/`ruleCode`/
     `priority`/`conditionId`/`conditionPath`/`outcome`），禁止退化为字符串集合或
     `conditionId + stage + outcome` 子集；同一 `conditionId` 的多个 usage 全部保留；
   - `usage_traceability_sha256`：应用从权威请求 usages 按 5.4 节规范重算并写入候选
     （wire 字段 `usageTraceabilitySha256`）；候选本体因此同时携带完整六元组（逐项校验
     依据）与摘要（快速绑定）以及可解析到完整不可变上游载荷的强引用（`handoff_refs`），
     满足“从 intake 到存储无损保留”；
   - `content_sha256`：排除自身字段后的完整 canonical 哈希。
5. 重试语义复用 V2 已证明的有界重试模式（总尝试次数 ≤ `maxRetries+1`），超时/限流/5xx/空
   响应/非法输出分类不变。
6. 输出门禁：模型 payload 的 declared 对象/参数/coverage 与权威输入交叉校验（复用算法模式，
   契约为 V3）；`declared_usage_coverage` 六元组必须与请求 `usages` 逐条精确一致；任一
   不一致拒绝且不落库（store 零调用）。

## 7. V3 Phase 4：静态门禁（M4）

1. `ValidateSqlCandidateRequestV3 = schema_version("1.0.0") + generation_request(第 6 节
   请求契约) + candidate(SqlTemplateCandidateV3)`；parser 前按 5.1 节**内容闭包**规则校验
   closure 一致性、重算 Phase 2G V3、候选自哈希与全部审计引用，快照存在但未获授权的
   表列仍阻断；closure 任一哈希/闭包不一致 → `blocked` 且无任何下游调用。Phase 4 是纯
   计算、无外部副作用，其 `passed` 表示内容与授权闭包满足，**不宣称仓储真实性**——真实性
   由副作用路径（provider/store/证据包）前的仓储背书承担（运行顺序下候选入库前已完成
   背书）。
2. `SqlStaticValidationReportV3`：`schema_version="1.0.0"`（4.3 节版本表）；纯计算
   `passed | blocked`，与候选始终 `executable=false`；报告携带 `handoff_refs`
   （`batch_sha256`/`payload_sha256`，**追溯引用而非真实性证明**）与
   `usage_traceability_sha256`（wire 字段 `usageTraceabilitySha256`）；issue 排序与脱敏
   规则沿用 V2 报告的工程模式。
3. **AST-derived evidence（AST 可证明范围）**：可复用的 parser-neutral 检查（版本无关算法，
   实现提取为中立函数或按 V3 请求参数化）仅覆盖——完整语句列表、根节点只读结构、命名参数
   抽取、scope-aware 对象/列 qualification、join 端点抽取、临时表/外部源/集合运算/EXEC 检测、
   唯一 `fact_value` 投影检查，以及与事实查询有关的位置证据；SQLGlot 版本与 `tsql` adapter
   固定值不变。**AST 不能证明**该事实在 RuleAgent 规则树中的 `stage`、`ruleCode`、
   `priority` 或 `outcome` 业务语义，报告中不得出现任何此类推导结论。
4. **usage traceability / reference integrity 门禁（V3 特有）**：该检查只做“引用完整性”
   校验，名称不得表述为 rule semantic proof——
   a. 重算请求 `usages` 的 `usage_traceability_sha256`（5.4 节规范）并与候选携带值比对；
   b. 候选 `declared_usage_coverage` 的**完整六元组**条目与请求 `usages` 逐条精确一致
   （`stage`/`ruleCode`/`priority`/`conditionId`/`conditionPath`/`outcome` 全部六字段）；
   c. **禁止**只按 `conditionId` 集合相等（或 `conditionId + stage + outcome` 子集）宣称
   V3 语义覆盖——例：同一 conditionId 若出现在 eligibility 与 exclusions 两个语义角色中，
   丢失 stage 即丢失“命中方向相反”的关键语义；六元组语义只经 traceability linkage 从权威
   请求复制并校验，永不从 SQL 推导。
5. `passed` 不构成执行许可，不改变 `reviewStatus`，不清除 warning；静态 `blocked` 的候选
   仍保留其在存储中的审计记录（运行顺序见第 1 节与第 12 节），不得删除、覆盖或修改。

## 8. V3 候选持久化（M5）

1. 现状：`CandidateTemplateStore.save(candidate: SqlTemplateCandidateV2)` 类型绑定 V2；V2
   适配器（`mongodb_candidates.py`）与集合 `sql_template_candidates` 不改动。
2. 默认方案：新增 `CandidateTemplateStoreV3` 端口 + `MongoCandidateStoreV3` 适配器 + 独立
   集合（配置键沿用 `RSB_CANDIDATE_STORE_*` 模式扩展，默认
   `sql_template_candidates_v3`）。
3. 被否决的默认替代：“受版本约束的安全泛化”（同一端口/集合按 `schemaVersion` 判别）仅在
   独立评审同时证明以下三点后才可重新考虑：文档可区分字段存在于每个历史文档；同 hash 跨
   版本冲突不可能发生；V2 适配器零改动。
4. 不变量（与 V2 存储验收一致）：insert-only（无 update/replace/delete/bulk/drop）；
   `contentSha256` 唯一索引启动时建立；同 hash 幂等（duplicate 不报错不重复落库，保留首次
   时间）；ping/建索引失败 → unavailable、insert 异常 → failed，均不抛出、不影响生成结果；
   生成失败时 store `save` 调用次数为 0；内容闭包或仓储背书任一校验失败时 save 调用次数
   同样为 0（第 1 节运行顺序下，存储发生在生成成功之后、静态门禁之前，且 store 写入属于
   外部副作用路径，必须已在同一次应用调用中完成仓储背书）。
5. **六元组保留**：候选本体自带完整 usage 六元组（`declared_usage_coverage`）+
   `usage_traceability_sha256` + `handoff_refs`（`batch_sha256`/`payload_sha256`），
   store 只保存候选，因此存储记录天然可追溯到完整不可变上游载荷；仅保存 usageDigest 不
   合格（见 5.4 节 traceability linkage）；存储文档中的 `batch_sha256`/`payload_sha256`
   是追溯引用，真实性证明由背书阶段证据记录承担。
6. 文档可区分性：V3 存储文档包装自身 `schemaVersion="1.0.0"`（4.3 节版本表），内嵌候选
   本体 `schemaVersion="3.0.0"`（候选契约自身版本）；测试断言 V2 集合与 V3 集合内容互斥，
   禁止同 hash/Schema 语义混淆；V2/V3 隔离按类型与引用闭包断言，不按版本号数字断言。
7. 静态 `blocked` 的已存候选是审计记录：不删除、不覆盖、不修改（与第 1 节运行顺序一致）。

## 9. Phase 5 对齐边界

- V3 Phase 5A（`ValidateSqlServerRequestV3`、V3 携带报告比对、binder 复用评估）不在本次
  首个实施切片；待 V3 Phase 4 验收后另立需求；
- 在 V3 Phase 4 完成前，禁止把任何真实 V3 candidate 送入现有 V2 Phase 5A 入口；V2 契约的
  `extra=forbid` 拒绝是预期行为，不得增设兼容层或转换；
- `sqlserver-token-binder-v1` 与 ODBC adapter 属于版本无关能力，未来 V3 Phase 5A 评估复用
  时按第 1 节复用边界另行评审。

## 10. API / CLI 隔离

- V2/V3 路由按显式版本段隔离（V3 入口形态随 M6 任务规划）；请求内不携带版本嗅探字段，
  服务端不做自动版本识别、降级或“最新”选择；
- V3 错误码命名空间独立于 V2（延续 DEV-20260906-03 第 13 节开放问题 2 的结论方向）；
- 当前规划任务不新增任何入口；入口实现属于 M6 及以后任务。

### 10.1 调用 Agent 2 生成 V3 SQL candidate 的完整条件

用户可调用的真实生成路径必须同时满足：

1. 调用方显式给出精确 `ruleVersion + requestId`；不接受“最新”或仅 `ruleId`。
2. M3 服务在同一次调用中从 MongoDB 重读对应 V3 batch、重跑 intake 并构造
   `RepositoryVerifiedHandoffV3`；仓储无记录或任一哈希不一致即停止。
3. 该 handoff 已解决 BUG-20260908-02 所登记的业务表达缺口，或当前请求可由批准设计证明不受
   该缺口影响；真实整批链路不得静默跳过坏请求。
4. 存在与该规则、请求集合精确匹配且已批准的 V3 context、snapshot 和 grants；Excel 中的已确认
   内容必须先转换为这些机器载荷，不能直接传给 provider。
5. M1–M5 实现与离线测试完成，Phase 2G 重算结果为 `metadataResolved`，V3 candidate store 可用；
   M6 提供显式 CLI/API 编排入口。
6. MongoDB 使用最小权限：RuleReader V3 集合只读，SqlBot 候选集合仅具备所需 insert/index 权限；
   配置完整且启动检查通过。
7. 在线 provider 配置完整，并取得用户对当次调用及其有界重试的明确授权；离线 fake provider
   回归不需要在线授权。
8. 返回对象始终为 `candidate / reviewStatus=pending / executable=false`；后续静态通过、数据库
   描述验证或持久化均不构成人工批准或执行许可。

实现顺序允许 M1/M2 与上游新版本 handoff 的工程转换并行；但真实 M3/M6 调用必须等待第 1–7 项
全部满足。

## 11. 测试矩阵（最低范围）

1. **V3 happy path**：合成脱敏 V3 请求（含按闭包规则构造的完整 `HandoffClosureV3`）resolution →
   candidate → static passed 全链路（fake provider + fake store，离线）；此类合成夹具的
   结论明确不构成真实仓储证明。
2. **V3↔V2 双向拒绝**：`ResolveMetadataRequestV2`/`GenerateSqlCandidateRequestV2`/
   `ValidateSqlCandidateRequestV2`/`ValidateSqlServerRequestV2`/V2 store 拒绝 V3 载荷；
   V3 契约拒绝 V2 载荷；按**类型与引用闭包**断言（不按版本号数字断言）；代码审查断言无任何
   V3↔V2 转换函数。
3. **usage 六元组丢失与合并检测**：候选 coverage 缺 `stage`/`ruleCode`/`priority`/
   `conditionId`/`conditionPath`/`outcome` 任一字段、或与请求 usages 不一致 → 拒绝；仅
   `conditionId` 集合相等或 `conditionId + stage + outcome` 子集相等 → 不通过（专设反例：
   同 conditionId 跨 stage/outcome 变体）；`usage_traceability_sha256` 按 5.4 节规范可独立
   重算。
4. **同 conditionId 多 usage 合法性（按现行 consumer 四元组门禁）**：同 `conditionId`、
   不同 stage/outcome 的两个 usage **合法且不得合并**；同 `conditionId`、不同
   ruleCode/conditionPath 的两个 usage **合法且不得合并**；完整六元组任一字段变化都改变
   `usage_traceability_sha256`；输入数组重排不改变摘要（canonical 排序不变性）；完全相同
   的上游四元组 `(stage, ruleCode, conditionId, conditionPath)` 按现行 consumer 规则拒绝。
5. **内容闭包与仓储真实性分离**：完全自洽（哈希全部闭合）但**从未存在于仓储**的伪造
   closure，在真实 provider 路径被仓储背书拒绝（provider/store 均 0 调用）；仓储 batch
   hash 与调用方 closure 不一致 → provider/store 均 0 调用；仓储无 batch → provider/store
   均 0 调用；从 JSON 反序列化的 closure 不得被服务标记为 repository verified（类型系统
   断言 `RepositoryVerifiedHandoffV3` 仅能由应用服务构造）。
6. **篡改阻断**：closure 的 payload/Schema 哈希、身份闭包或 `intake_status` 快照不一致；
   context/snapshot 未批准、canonical hash 不一致、`metadataSnapshotRef` 失配、grant 引用
   悬空、`catalogDigest`/`candidatePayloadSha256` 闭包破坏；报告/候选哈希篡改 → 全部阻断
   且 provider 与 store 副作用均为 0。
7. **provider 前零调用**：closure 校验失败、仓储背书失败、Phase 2G V3 失败、授权缺失、
   携带报告不一致时 provider 调用次数为 0（测试断言）。
8. **store 前零调用与运行顺序**：生成失败或输出门禁拒绝时 store `save` 调用次数为 0；store
   失败不伪造 stored；运行顺序测试——生成成功后先落库并记录存储 outcome，再对同一 candidate
   运行 Phase 4，静态 `blocked` 的已存候选仍可回读且未被修改。
9. **V2 全量回归**：现有离线基线（当前 424 passed）零行为变化；V2 契约测试期望不修改。
10. **泄漏检查**：日志、报告与公开 PROG 不含 SQL、参数值、对象清单、连接信息、provider 原始
    响应与秘密字段（沿用 Phase 5A 日志卫生测试模式）。
11. **快照等价性**：`GovernedMetadataSnapshotV3` 与 V2 快照的字段等价映射有夹具级断言。
12. **存储隔离**：V2/V3 集合内容互斥、文档可区分（包装 `schemaVersion="1.0.0"` + 候选本体
    `"3.0.0"`）、同 hash 幂等只作用于本集合。
13. **版本独立性**：4.3 节版本表中各对象 `schemaVersion` 与其版本字段的绑定有契约测试；
    修改 FBR `contractVersion` 常量不改变任何下游对象断言（防机械继承回归）。

## 12. 里程碑

> 里程碑顺序修订说明：Phase 4R 原里程碑（DEV-20260906-02 第 11 节 M0–M8）以 V2 链为锚。
> 本节定义的 V3 下游链 M0–M6 插入在其 M0（基线审计）与原 M1（Agent 1 就绪）之间：原 M1–M8
> 的真实闭环推进现在要求先完成本链 M1–M6。原里程碑文本不改写，修订登记在
> [DEV-20260906-02](DEV-20260906-02-real-handoff-evidence-loop-orchestration.md) 后续审计
> 修订章节。

### M0：上游 commit 锚点与跨仓库基线（`completed`，2026-09-09 用户批准并落档）

**2026-09-09 历史快照（不代表当前实施状态）：** 当时 M0 已收口，上游 commit
与三类哈希来源登记已由 T0 完成；本组 REQ/BIZ/DEV 已由用户明确批准并落档；
M0 规划批次已进入提交 `61a377f`；M1 已启动（`in_progress`），首个契约子任务
及审核修复已完成；九组批准校验和 handoff 契约尚未实施。

**当前状态**：M1 已完成（`completed`）；M2 为 `in_progress`。
M2 实施进度与剩余范围以本节 §12 M2 的"§5.3 八步规则实施对应关系"表为准；
当前代码基线与验证结果详见最新 PROG。

- 2026-09-09 已完成：上游 V3 契约提交、完整 commit 锚点、提交树原始字节哈希与运行时规范化
  哈希登记；真实生成仍受 10.1 节其余门禁约束。
- 2026-09-09 已完成：本组 REQ/BIZ/DEV 批准并落档（M0 状态 `completed`）；Git 提交需用户另行
  明确授权。上游业务表达 vNext 是受影响事实进入真实 M3/M6 的门禁，不阻止 M1/M2 的离线契约实现。
- 2026-09-09 五项设计已由本次用户指令明确批准：
  1. DEV §4.3 的 12 项独立契约版本表；
  2. `ApprovalRecordV3`、九组有序 fail-fast 内容闭包校验，以及真实 M3/M6 的独立受信批准来源门禁；
  3. V3 下游采用独立契约链，禁止 V3↔V2 转换、包装或降级，V2 行为保持不变；
  4. `businessRuleReview`、`metadataReview`、`SqlBot` 的职责分工；
  5. DEV §12 的 M1–M6 实施顺序，以及离线开发与真实调用门禁。

2026-09-06 历史快照（保留用于审计）：

- 当时已完成子任务：
  - SqlBot 与 RuleAgent 只读基线审计（SqlBot 424 passed 离线基线、HEAD `b3e3d35`；RuleAgent
    HEAD `65a9684`、V3 Schema 未跟踪、SHA-256 `2c5e4603…` 复核一致；最新刷新见第 13 节）；
  - 设计缺口复现与登记（[BUG-20260906-01](../bugs/BUG-20260906-01-v3-phase4r-downstream-contract-gap.md)）；
  - 本链规划文档草稿（REQ/BIZ/DEV）及按人工审查意见的修订。
- 当时未完成：
  1. 本轮规划文档通过人工批准并纳入 Git 提交；
  2. RuleAgent V3 契约与相关代码完成独立审查并提交，取得真实 commit SHA；
  3. 提交后重新计算 V3 Schema SHA-256 并与 `2c5e4603…` 比对一致（不一致 → 停止并另立契约
     差异评审）。
- 失败语义：任何基线与登记值不符（尤其 Schema SHA-256 变化）→ 停止，另立评审。
- 测试范围：只读检查命令，无新增测试。

### M1：V3 授权上下文、快照与 handoff 闭包契约（`completed`，2026-09-10）

- 前置条件：M0 收口（上游 V3 契约已提交、SHA-256 复核一致）；本 REQ/BIZ/DEV 已批准。
- **2026-09-10 M1 验收历史快照**：M1 当时已完成（`completed`），五个实现文件全部完成，全量 610 passed：
  - **已完成**：`project_bindings_v3.py` 契约模型及 `test_project_bindings_v3_contract.py`（110 项定向契约测试，含 6 项补充的批准校验精确反例、生命周期隔离证据）；
  - **已完成**：`validate_approval_closure_v3.py` 九组纯计算校验及对应测试；
  - **已完成**：`handoff_closure_v3.py`（`HandoffClosureV3` + `RepositoryVerifiedHandoffV3`）及
    `test_handoff_closure_v3_contract.py`（76 项契约测试，含 verified 构造边界、V2 完整 fixture 拒绝、字段边界参数化）；
  - **审核修复**：已修复 contextRef 类型错误、授权身份唯一性遗漏、requestIds 元素缺少下界、异常 code 未受约束四项缺陷；
  - **审核修复（2026-09-10）**：`RepositoryVerifiedHandoffV3` 构造器已改为无条件 `TypeError`；
    删除 `HandoffClosureV3` 中 M2 跨字段身份比对；移除领域层重复常量；补齐边界测试和生命周期隔离证据；
  - M1 DoD 逐项核对见 [PROG-20260910](../progress/PROG-20260910.md)。M1 当时已标记 completed，交付记录注明尚未提交、待代码审核。相关实现随后已进入提交 `29716b0`、`189daca`；当前全量验证基线见 [PROG-20260911](../progress/PROG-20260911.md) 顶部摘要。
- **M1 允许的五个实现文件固定为：**
  1. `src/release_sql_bot/domain/project_bindings_v3.py`（context + snapshot + grants + approval +
     `ApprovalClosureValidationErrorV3` 异常，定义在同文件）
  2. `src/release_sql_bot/domain/handoff_closure_v3.py`（`HandoffClosureV3` 内容闭包 +
     `RepositoryVerifiedHandoffV3` 内部结果契约，形状按 5.1 节冻结）
  3. `src/release_sql_bot/application/validate_approval_closure_v3.py`（V3 专属纯函数；
     V2 模块不得导入；无 wire schemaVersion）
  4. `tests/contract/test_project_bindings_v3_contract.py`
  5. `tests/contract/test_handoff_closure_v3_contract.py`
  不得使用仓库根目录下的 `application/` 路径。
- 内容：4.3 节契约版本表落为各模型 `schema_version` 常量；快照等价性夹具断言；
  **`validate_approval_closure_v3` 纯函数**（§3.3）及其契约测试；
  **`ApprovalClosureValidationErrorV3` 异常**（§3.5）及其 9 个 issue code。
- DoD：契约测试覆盖 camelCase/extra=forbid/strict/枚举/长度/唯一性；V2 载荷被拒；context
  不可变载荷与生命周期方案（3.2 节）有测试（版本递增、旧载荷不变、生命周期载体独立）；
  类型断言证明 `RepositoryVerifiedHandoffV3` 不可从 JSON 反序列化；版本独立性测试（矩阵 13）；
  `validate_approval_closure_v3` 九个有序检查组全部有正反测试（含 ID/版本/哈希不一致、非 approved、
  自哈希篡改、输入对象逐字节不变）。
- 失败语义：契约校验失败即构造失败，无部分对象。
- 测试范围：矩阵 2（V2→V3 方向）、5（类型断言部分）、11、13；新增契约测试。

#### `test_project_bindings_v3_contract.py` 最低覆盖（除现有 12 项外，再明确）

- 九个有序检查组（§5.2）各有一个精确反例；
- 多重错误输入验证 fail-fast 顺序（同一输入存在多个错误时，按 §3.5 顺序返回第一个）；
- 异常文本不包含测试输入中的 ID、哈希或载荷值；
- V2 模块不导入 V3 纯函数（静态断言）；
- 纯函数调用前后输入对象序列化结果完全一致；
- **哈希算法专项**：修改 context 业务内容但保留旧自哈希和批准引用，必须失败；
  修改 snapshot 业务内容但保留旧自哈希和批准引用，必须失败；
  只修改 `context.authorizationPolicyVersion`，必须失败。

#### `test_handoff_closure_v3_contract.py`

- 仍只测试 handoff 闭包，不混入 approvalRecord 测试。

### M2：V3 元数据解析（Phase 2G V3）

- 前置条件：M1 完成。
- 状态：`in_progress`（共 12 个子任务已完成；内容闭包校验、usage 摘要、请求/报告契约、
  报告契约修复、输入门禁、column grant 解析、字段/实体键授权闭包、
  filters 物理字段解析、aggregation 授权引用解析、
  timeRange 时间字段授权解析、指定 join grant 物理授权解析、
  多关系授权连接闭包选择已完成；
  `application/metadata_resolution_v3.py` 已存在；
  公开 resolve_metadata_v3 编排、报告组装、完整 ResolvedJoinV3 构造及
  §5.3 步骤 2（entityType/grain 映射待澄清）未实现；
  当前 HEAD `77c0651`，全量 1016 passed）。
  实施细节与剩余范围以本节"§5.3 八步规则实施对应关系"表为准。
  剩余设计缺口分析见 [DEV-20260911-01](DEV-20260911-01-v3-m2-remaining-design-gaps.md)（proposed）。
- 已完成子任务：
  - `application/validate_handoff_closure_v3.py`（内容闭包纯计算校验，六组有序 fail-fast 检查）；
    `domain/handoff_closure_v3.py` 增加 `HandoffClosureValidationErrorV3`；
    `tests/unit/test_handoff_closure_validation_v3.py`（36 项单元测试通过）。
  - `application/usage_traceability_v3.py`（`compute_usage_traceability_sha256_v3`，
    六元组投影 + 完整稳定排序键 + 四元组重复拒绝）；
    `tests/unit/test_usage_traceability_v3.py`（20 项单元测试通过，含排序不变性、
    同 conditionId 多 usage 合法性、输入不可变）。
  - `domain/project_bindings_v3.py` 增加 `ResolveMetadataRequestV3`
    （七顶层字段：schemaVersion、projectRef、handoffClosure、bindingRequest、
    projectContext、metadataSnapshot、approvalRecord）；
    `tests/v3_metadata_support.py`（可复用自洽 happy-path 合成夹具，
    从实际 V3 fixture 派生 ruleRef/requestId/实体键授权，
    两次闭包校验器均通过）；
    `tests/contract/test_metadata_resolution_v3_contract.py`（34 项契约测试通过，
    含 camelCase/extra=forbid/strict、缺字段、snake_case、类型强制、
    V2 拒绝、bindingGapReport/repositoryVerified 拒绝、结构合法但语义不一致允许构造、
    夹具自洽性证据、完整 ruleRef/projectRef 相等、实体键授权引用闭合、
    重复 relation/column 诊断）。
  - `domain/project_bindings_v3.py` 增加 `BindingResolutionReportV3`
    （schemaVersion、status、executable=false、requestRef、projectRef、
    contextRef、snapshotRef、handoffRefs、resolutionHashes、
    resolvedFields/EntityKeys/Filters/Aggregation/TimeRange/Joins、
    usageTraceabilitySha256、issues；blocked/metadataResolved 内部一致性校验）；
    `tests/v3_metadata_support.py` 新增报告合成夹具；
    `tests/contract/test_metadata_resolution_v3_report_contract.py`（34 项契约测试通过，
    含两种状态往返、版本/状态/executable 约束、缺字段、snake_case、类型强制、
    SHA-256 格式、blocked 必须含 blocker、blocked 不得携带部分输出、
    metadataResolved 不得含阻断 issue、空 filters/aggregation/timeRange/join 合法、
    evidence 引用无损、无执行/审核/SQL 字段）。
  - **审核修复（2026-09-10）**：`BindingResolutionReportV3` 修复 blocked 报告
    六项输出完整性（新增 aggregation/timeRange None 检查）和顶层静默规范化
    （strict=True、str_strip_whitespace=False）；新增 15 项针对性测试；
    报告契约测试总计 49 项。详见
    [BUG-20260910-01](../bugs/BUG-20260910-01-v3-binding-resolution-report-contract-gaps.md)。
  - **第五子任务已完成（2026-09-10）**：`application/metadata_resolution_v3.py` 已存在，
    输入门禁内部辅助函数 `_validate_resolution_input_v3` 已实现
    （结构重验 + 六步内容/范围门禁）；47 项单元测试通过。
  - **第六子任务已完成（2026-09-10）**：`_resolve_column_grant_v3`
    （单 column grant 物理引用解析：column grant → relation grant →
    snapshot relation → snapshot column）；52 项单元测试通过。
  - **测试修复已完成（2026-09-10）**：补强返回副本隔离（含捕获浅拷贝的辅助函数）、
    失败输入不变性（实际传入对象前后对比）、evidenceIds 顺序敏感性、
    结构异常脱敏（真实 V3 根参数化）、双向顺序证据；生产代码未修改。
  - **第七子任务已完成（2026-09-10）**：`_resolve_fields_and_entity_keys_v3`
    （字段绑定与实体键授权闭包，26 项单元测试通过，全量 874 passed）。
  - **第八子任务已完成（2026-09-11）**：`_resolve_filters_v3`
    （filters 物理字段解析：按 `queryRequirements.filters.items` 原顺序，
    逐项映射到已授权 `ResolvedFieldV3` 的物理引用；校验每项 `evidenceIds`
    命中请求顶层 evidence）；原交付 24 项 / 全量 898 passed，
    补强验收证据后当前 30 项 / 全量 904 passed。
  - **第九子任务已完成（2026-09-11）**：`_resolve_aggregation_v3`
    （aggregation 授权引用解析：确认 `inputFieldIds` + `groupByFieldIds`
    中每个引用均有对应授权字段结果，原样复制六个声明字段）；
    原交付 25 项 / 全量 929 passed，补强验收证据后当前 32 项 / 全量 936 passed。
  - **第十子任务已完成（2026-09-11）**：`_resolve_time_range_v3`
    （timeRange 时间字段授权解析：`none` 返回空物理标识，
    `asOf`/`between` 映射到已授权字段的准确拼写）；23 项单元测试通过，全量 959 passed。
  - **第十一子任务已完成（2026-09-11）**：`_resolve_join_grant_v3`
    （指定 join grant 物理授权解析：验证 grant ID、两端 column grants 及
    无向匹配快照 relationship 边）；原交付 22 项 / 981 passed，
    补强验收证据后当前 30 项 / 989 passed。
  - **第十二子任务已完成（2026-09-11）**：`_select_join_closure_v3`
    （多关系授权连接闭包选择：收集所需关系、逐项验证 grant、
    无向连通性检查）；原交付 18 项 / 1007 passed，
    修复门禁绕过后当前 27 项 / 1016 passed。
  - 剩余工作：完整 ResolvedJoinV3 构造（含 evidence_ids 与方向计划）、
    公开 `resolve_metadata_v3` 编排及报告组装、
    entityType/grain 授权映射未实现；
    字段绑定与实体键授权闭包辅助函数已完成，但完整解析服务与报告组装仍未实现，
    M2 不标记 `completed`；
    真实仓储背书由后续有副作用的应用服务在生成/存储前完成。
- **§5.3 八步规则实施对应关系**（严格对应原规则每步，标明状态与函数/测试依据）：

  | §5.3 步骤 | 状态 | 实现依据 |
  | --- | --- | --- |
  | 1. 输入门禁 | ✅ 已实现 | `_validate_resolution_input_v3`（`application/metadata_resolution_v3.py`）；47 项单元测试（`test_metadata_resolution_v3_input_gate.py`） |
  | 2. entity | ⚠️ 部分实现 | entity-key 授权闭包（`_resolve_fields_and_entity_keys_v3`，`test_metadata_resolution_v3_field_entity_bindings.py`）已实现；`entityType`/`grain` 到授权 relation 的映射未实现，登记为待澄清设计项（见 §14 开放问题第 7 项） |
  | 3. fields | ✅ 已实现 | `_resolve_fields_and_entity_keys_v3`；26 项单元测试（同上） |
  | 4. filters | ✅ 辅助函数完成，尚未集成完整解析服务 | `_resolve_filters_v3`（`application/metadata_resolution_v3.py`）；30 项单元测试（`test_metadata_resolution_v3_filters.py`） |
  | 5. aggregation | ✅ 辅助函数完成，尚未集成完整解析服务 | `_resolve_aggregation_v3`（`application/metadata_resolution_v3.py`）；32 项单元测试（`test_metadata_resolution_v3_aggregation.py`） |
  | 6. timeRange | ✅ 辅助函数完成，尚未集成完整解析服务 | `_resolve_time_range_v3`（`application/metadata_resolution_v3.py`）；23 项单元测试（`test_metadata_resolution_v3_time_range.py`） |
  | 7. join | ⚠️ 部分实现：指定 grant 校验与授权连接闭包选择完成；完整 ResolvedJoinV3 构造（evidence_ids、方向计划）、执行方向语义及报告集成未完成 | `_resolve_join_grant_v3` + `_select_join_closure_v3`（`application/metadata_resolution_v3.py`）；30 项 grant 测试（`test_metadata_resolution_v3_join_grant.py`）+ 27 项闭包测试（`test_metadata_resolution_v3_join_closure.py`） |
  | 8. 使用位置 | ⚠️ 部分实现 | evidence 复制（`ResolvedFieldV3.evidenceIds` 等，由 `_resolve_fields_and_entity_keys_v3` 覆盖）、usage 摘要算法（`compute_usage_traceability_sha256_v3`，`usage_traceability_v3.py`，20 项测试）已完成；摘要接入完整报告组装未实现 |

  上表之外的支撑与集成工作：
  - **已完成的支撑函数**：`_resolve_column_grant_v3`（单 column grant 物理引用解析：column grant → relation grant → snapshot relation → snapshot column，52 项单元测试 `test_metadata_resolution_v3_column_grant.py`）。该函数服务于多步的物理列解析，不是额外的规则步骤。
  - **未完成的集成工作**：公开 `resolve_metadata_v3` 编排及 `BindingResolutionReportV3` 报告组装，需整合上述各步输出并补齐未完成步骤。

- DoD：第 5.3 节八步确定性解析全部有 happy/blocked 测试；5.1 节内容闭包规则全部有正反
  测试（已完成）；usage 追溯摘要排序、重复拒绝和输入重排测试已完成；
  `ResolveMetadataRequestV3` 和 `BindingResolutionReportV3` 契约测试已完成；
  纯计算证明（解析函数无仓储/provider 注入点）且报告明确 `metadataResolved` 不
  构成真实仓储证明。
- 失败语义：closure 或任一门禁失败 → `blocked` 报告，携带既有 owner 分类 issue；纯解析
  路径本无 provider/store 注入点。
- 测试范围：矩阵 1（resolution 段）、3、4、6。

### M3：V3 候选生成

- 前置条件：M2 完成；provider 授权机制确认（在线调用另行授权）。
- 内容：`application/candidates_v3.py` + `prompts_v3.py`；`GenerateSqlCandidateRequestV3`/
  `SqlTemplateCandidateV3`（含完整六元组 coverage、`usage_traceability_sha256` 与
  `handoff_refs`）；采用 6.2 节**方案 a**——生成服务直接依赖只读 handoff repository，在
  provider 调用前于服务自身完成仓储重读、intake 重执行与背书比较。
- DoD：provider 前仓储背书（含伪造 closure 拒绝、仓储 hash 不一致、仓储无 batch 三类
  零调用测试）、Phase 2G 重算比对、有界重试、输出门禁、provenance 闭包全部有测试；离线
  固定 provider 覆盖失败路径。
- 失败语义：背书/重算失败或未授权 → 停止（provider 与 store 调用 0 次）；重试耗尽/
  输出拒绝 → 不落库。
- 测试范围：矩阵 1（生成段）、2、3、4、5、6、7、8。

真实调用附加门禁：受影响事实必须使用完成业务表达修订的新版本 handoff，并具备已批准的
context/snapshot/grants；离线 fake provider 实现和测试不以真实 provider 授权为前置。

### M4：V3 静态门禁（Phase 4 V3）

- 前置条件：M3 完成（M4 代码交付可先于 M5——实施顺序不影响运行顺序，见第 1 节）。
- 内容：`domain/sql_validation_v3.py` + `application/sql_validation_v3.py`；parser-neutral
  检查复用实现；usage traceability / reference integrity 门禁。
- DoD：第 7 节全部检查有正反测试；六元组 traceability 门禁含跨 stage/outcome 反例与
  “AST 不能证明规则语义”的否定断言；SQLGlot 版本固定不变。
- 失败语义：`blocked` 报告，候选与报告 `executable=false` 不变，已存候选不被修改。
- 测试范围：矩阵 1（静态段）、3、4、6。

### M5：V3 候选存储

- 前置条件：M3 完成（候选契约可用）；存储账号与 V3 集合授权确认（运维）。实现顺序上可与
  M4 并行或先于 M4；M6 编排前 M4、M5 必须都完成。
- 内容：V3 store 端口 + Mongo 适配器 + 配置键。
- DoD：第 8 节不变量全部有测试（insert-only 扫描、唯一索引、幂等、失败语义、生命周期、
  集合互斥、日志卫生）；候选本体六元组与 `handoff_refs` 落库可回读；store 写入前仓储背书
  已完成（同一次应用调用）。
- 失败语义：unavailable/failed 不抛出、不伪造成功；生成失败或背书/闭包校验失败时 save
  零调用。
- 测试范围：矩阵 5、6、8、12；V2 存储回归。

> **M5 第一切片实施范围（2026-09-13，已完成）**：
> 本轮完成独立的 V3 存储契约、端口、MongoDB 适配器及配置，通过 fake MongoDB
> 验证候选可以不可变保存、重复保存幂等、失败正确降级。
>
> **新增文件**：
> - `src/release_sql_bot/application/ports/candidate_store_v3.py`：
>   `CandidateTemplateStoreV3` 端口 + `CandidateStoreV3Status` + `CandidateStoreV3Outcome`
> - `src/release_sql_bot/domain/stored_candidate_v3.py`：
>   `StoredCandidateV3` 包装契约（`schemaVersion="1.0.0"`，独立 `contentSha256` 校验）
> - `src/release_sql_bot/infrastructure/database/mongodb_candidates_v3.py`：
>   `MongoCandidateStoreV3` 适配器（注入 `client_factory`，insert-only）
> - `src/release_sql_bot/config/settings.py`：
>   新增 `candidate_store_v3_enabled` / `candidate_store_v3_database` / `candidate_store_v3_collection`
> - `tests/unit/test_candidate_store_v3.py`：21 项离线测试（fake client）
>
> **不变量**：关闭不创建 client；initialize 成功前不 insert；ping + 唯一索引；失败降级 unavailable；
> 只允许 `insert_one`；重复 `contentSha256` 返回 `duplicate`；写异常返回 `failed`；
> 未就绪/关闭返回 `unavailable`；不改变候选状态；save 前独立重验 V3 结构与哈希；
> 日志不含 SQL/对象名/参数值/URI/凭据。
>
> **本轮不装配真实服务，不调用数据库，不接入 M3 生成服务。**
> M5 下一切片仍须完成：同一次应用调用中的 handoff 仓储背书、批准核验和生成后存储装配；
> 生成或背书失败时 `store.save` 零调用；按 `candidateGenerated → candidateStored → staticPassed`
> 顺序测试；记录存储 outcome，保留静态 blocked 候选。

> **M5 第二切片实施范围（2026-09-13，已完成）**：
> 本轮完成 V3 生成后存储内部应用服务的离线装配。
>
> **新增文件**：
> - `src/release_sql_bot/application/candidate_persistence_v3.py`：
>   `GenerateAndStoreResultV3` 结果类型 + `generate_and_store_sql_candidate_v3` 服务函数
> - `tests/unit/test_candidate_persistence_v3.py`：16 项离线测试
>
> **服务行为**：
> - 调用现有 `generate_sql_candidate_v3` 完成全部门禁和生成
> - 生成成功后对同一候选调用一次 `store.save`
> - 返回 `GenerateAndStoreResultV3(candidate, store_outcome)`
> - 生成失败时 store 零调用，存储失败时仍返回候选
>
> **测试覆盖**：正常生成保存（provider→save 各 1 次）、四种存储 outcome 参数化、
> 前置门禁失败（store 零调用）、模型/生成失败（store 零调用，重试上限）、
> 独立重新背书、输入不变性。
>
> 静态 blocked 的保留由 `test_run_order_then_static_blocked` 证明；
> Mongo fake 组合由 `test_generate_and_store_mongo_fake_inserts_once` 证明。

### M6：Phase 4R 编排与证据包（V3 通道）

- 前置条件：M1–M5 全部完成（实现顺序不限，见第 1 节双顺序说明）；上游真实 batch 已落库
  （Agent 1 侧独立授权）；用户对编排实现与在线生成的当次授权；context 生命周期存储设计按
  3.2 节方案确认。
- 内容：按修订后的 Phase 4R 编排设计实现 V3 编排服务、CLI 入口、证据包组装与泄漏检查；
  编排按第 1 节**运行状态顺序**执行（生成成功 → 先 insert-only 存储并记录 outcome → 再
  对同一 candidate 运行 Phase 4）；API 路由若纳入本阶段一并显式版本化。
- DoD：前置背书或解析失败时 provider/store 副作用为 0；生成或存储之后的失败必须保留已发生
  outcome 与不可变审计记录并停止后续阶段。编排对每个有外部副作用的阶段
  在同一次调用中完成仓储背书并记录 repository verification 阶段与结果进证据包；证据包
  字段齐备、可复核，且包含 `batch_sha256`/`payload_sha256`（追溯引用）与背书结果；公开
  PROG 只有脱敏内容；静态 blocked 候选的审计记录完整保留。
- 失败语义：沿用 Phase 4R 状态机，停在当前阶段并记录 issue codes。
- 测试范围：矩阵 1–13 全量 + 端到端编排失败路径。

#### M6 第一刀实施范围（2026-09-14，已完成）

本轮完成 M6 第一刀：证据包契约 + 离线编排函数的全部离线实现，
不包括真实仓储适配器装配、在线 provider 调用、CLI/HTTP 路由、context 生命周期存储。

**新增文件（3 个）：**

1. `src/release_sql_bot/domain/evidence_pack_v3.py`：
   `EvidencePackV3(V3ReportModel, schemaVersion="1.0.0")` camelCase/extra=forbid/frozen。
   必填字段：schemaVersion, stage, ruleVersion, requestId, batchSha256, payloadSha256,
   repositoryVerificationStatus, contextSha256, snapshotSha256, resolutionReportSha256,
   candidateContentSha256(nullable), storeOutcome(nullable), staticStatus(nullable),
   staticReportSha256(nullable), provider/model/promptVersion(nullable),
   attemptCount(default 0), issueCodes(tuple), startedAt, endedAt, executable(always false)。
   禁止包含：SQL 文本、参数值、对象/字段清单、连接串、URI、API Key、provider 原始响应。
   不从 V2 证据/编排模块 import 契约类型。

2. `src/release_sql_bot/application/evidence_loop_v3.py`：
   `run_offline_v3_evidence_loop` 公开编排函数。行为：
   （1）生成失败 → 不 store.save，不静态；stage=blockedUpstream；
   candidateContentSha256/storeOutcome/staticStatus 均为 null。
   （2）生成成功后先 save。stored/duplicate → 再静态校验。
   （3）store unavailable/failed → 记录真实 storeOutcome，跳过静态，stage=candidateGenerated，
   candidateContentSha256 仍填，staticStatus=null。
   （4）静态 blocked → stage=candidateStored，staticStatus=blocked，
   issueCodes 含 STATIC_VALIDATION_BLOCKED；候选生命周期不变；不得再 save。
   （5）静态 passed → stage=evidenceComplete，staticStatus=passed。
   （6）全程 executable=false。initialize/close 由调用方负责。

3. `tests/unit/test_evidence_loop_v3.py`（12 项）：
   A. 合法 SQL → evidenceComplete；B. 仓储无批次 → provider 0, save 0；
   C. provider 拒绝 → provider 1, save 0；D. FakeStore failed → save 1, static 跳过；
   E. ABS() SQL → save 1 STORED, static blocked；F. 泄漏检查；G. 输入 wire 不变。

**验证**：`uv run pytest` → 1265 passed, 1 warning（M6 专项 12 项全含）。

**禁止**：改 V2 模块、新增 CLI/HTTP 路由、context 生命周期存储；
把证据包提交进 Git；调用 SQL Server 验证；从 V2 证据/编排模块 import 契约类型；
把 BUG-20260906-01 标为关闭。

#### M6 第一刀审核修复设计（2026-09-14）

本轮修复 M6 第一刀审核发现的三类问题，仅完成离线证据包收尾，不进入下一里程碑。

**1. 运行证据失真修复**

- 在 `evidence_loop_v3.py` 内引入 `CountingProviderProxy`，实现
  `CandidateModelProvider` 协议，每次调用底层 `generate` 前计数，
  原样转发请求、响应及异常。`attemptCount` 必须等于代理实际调用次数，
  禁止用异常类型推测（如"拒绝固定 1 次"或 `max_retries+1`）。
- `repository_verification_status` 判定规则：
  - 代理 `call_count > 0` → provider 已被调用 → M3 前置门禁通过 → `"verified"`；
    后续 provider 拒绝、重试耗尽、输出非法均不得写 `"failed"`。
  - `call_count == 0` → 未调用 provider → 按 gate/scope error code 映射：
    仓储/批准端口不可用 code → `"unavailable"`，其它 → `"failed"`。
- provider 异常无 `.code` 属性时使用 DEV 登记的固定中性 code：
  - `M6_PROVIDER_REJECTED`（`CandidateGenerationProviderRejectedV3Error`）；
  - `M6_PROVIDER_UNAVAILABLE`（`CandidateGenerationProviderUnavailableV3Error`）；
  - `M6_GENERATION_OUTPUT_INVALID`（`CandidateGenerationOutputInvalidV3Error`）。
- store `failed`/`unavailable` 记录固定中性 issue code：
  - `M6_STORE_SAVE_FAILED`；
  - `M6_STORE_UNAVAILABLE`。
- gate/scope 错误保留既有稳定 code（`exc.code`），不得复制 `str(exc)`。

**2. 敏感字符串传播修复**

- 禁止直接把 `response.provider` 等不可信字符串复制进证据包。
- 为当前离线切片明确可信标识来源和精确允许列表：
  - `provenance.provider` 来自 `response.provider`（模型响应，不可信）；
  - `request.model` 来自调用方参数（不可信，需允许列表校验）；
  - `request.prompt_version` 来自应用层 prompt 构建（不可信，需允许列表校验）。
- 离线切片可信标识精确允许列表（不使用正则）：
  - provider：`{"fixed-offline-v3"}`（测试替身）；
  - model：`{"fixed-model-v3"}`；
  - promptVersion：`{"sqlserver-fact-candidate-v3.0"}`。
- 不可信标识输出 null，并记录固定中性 code：
  - provider → `M6_PROVIDER_IDENTITY_UNTRUSTED`；
  - model → `M6_MODEL_IDENTITY_UNTRUSTED`；
  - promptVersion → `M6_PROMPT_VERSION_UNTRUSTED`。
- 校验只影响证据包输出；不得替换发送给 provider 的 model，
  不得改写候选、候选哈希或存储内容。
- 不记录原始异常、响应、SQL、参数值、URI 或密钥。
- 实际候选及存储内容不变（候选 dump、store outcome 如实记录）。

**2.1. provider 失败时保留请求证据（r2 新增）**

- `CountingProviderProxy` 在每次调用底层 `generate` 前，
  记录实际请求中经过上述安全校验的 `model`/`promptVersion`。
- 不保存完整请求、system_prompt、user_prompt、原始响应或异常消息。
- provider 调用次数 > 0 后发生拒绝/重试耗尽/输出非法：
  `repositoryVerificationStatus=verified`；
  `model`/`promptVersion` 使用代理已记录的安全值。
- provider 调用次数为 0 时，`provider`/`model`/`promptVersion` 均为 null。

**3. 契约修复**

- `candidateContentSha256`/`storeOutcome`/`staticStatus`/`staticReportSha256`/
  `provider`/`model`/`promptVersion` 必须出现（`...`），但允许 null；去掉 `default=None`。
- `storeOutcome` 限定为 `stored`/`duplicate`/`unavailable`/`failed`/`null`（`Literal` 约束）。
- 补充阶段一致性校验（`model_validator`）：
  - `blockedUpstream`：候选、存储、静态字段均 null；
    `attemptCount=0` 时 `model`/`promptVersion`/`provider` 均 null；
    `attemptCount>0` 时允许安全 `model`/`promptVersion`（不可信降级为 null）。
  - `candidateGenerated`：候选 hash 非空，store 为 `failed`/`unavailable`，静态字段均 null；
  - `candidateStored`：候选 hash 非空，store 为 `stored`/`duplicate`，
    `staticStatus=blocked`，静态 hash 非空；
  - `evidenceComplete`：候选 hash 非空，store 为 `stored`/`duplicate`，
    `staticStatus=passed`，静态 hash 非空。
- 静态 blocked 时使用精确 `STATIC_VALIDATION_BLOCKED`（恢复与静态门禁一致的原稳定码）。
- 保持 camelCase、`extra=forbid`、`frozen`、`executable=false`；`attemptCount` 保留默认 0。

**固定中性 code 登记总表**

| Code | 触发条件 |
|---|---|
| `STATIC_VALIDATION_BLOCKED` | 静态门禁阻断（原稳定码） |
| `M6_PROVIDER_REJECTED` | provider 永久拒绝 |
| `M6_PROVIDER_UNAVAILABLE` | provider 重试耗尽 |
| `M6_GENERATION_OUTPUT_INVALID` | 输出非法重试耗尽 |
| `M6_STORE_SAVE_FAILED` | store 返回 failed |
| `M6_STORE_UNAVAILABLE` | store 返回 unavailable |
| `M6_PROVIDER_IDENTITY_UNTRUSTED` | provider 标识不在允许列表 |
| `M6_MODEL_IDENTITY_UNTRUSTED` | model 标识不在允许列表 |
| `M6_PROMPT_VERSION_UNTRUSTED` | promptVersion 不在允许列表 |

#### M6 离线证据包演示脚本（2026-09-14，已完成）

本轮新增 M6 离线证据包演示脚本，执行一条命令即可运行真实 V3 编排并
生成可查看的 `EvidencePackV3` JSON。不进入下一切片，不修改既有业务模块。

**新增文件（2 个）：**

1. **`scripts/preview_evidence_v3.py`**：
   - 复用 `scripts/preview_synthetic_v3` 的现成构造函数
     （`_build_generation_request`、`_build_synthetic_handoff_repository`、
     `_build_synthetic_approval_port`、`_synthetic_provider_content`），不复制大段 fixture。
   - provider 标识固定 `fixed-offline-v3`；请求 model 使用 `fixed-model-v3`
     （与 M6 当前允许列表一致）。
   - 脚本内实现最小内存 candidate store，`save` 返回当前候选真实 hash；
     不实例化真实 MongoDB adapter。
   - 调用 `run_offline_v3_evidence_loop`，`max_retries=0`。
   - `store.initialize/close` 由脚本负责，`close` 放 `finally`。
   - 默认输出 `.codex_tmp/v3-evidence-pack.json`；支持 `--output-dir`。
   - 退出码：`0` = evidenceComplete；`2` = 其他阶段（仍保存证据包）；
     `3` = 文件创建/写入失败。
   - **文件已存在时不跑编排，保留原文件并返回 `3`**；先检查后编排，无覆盖开关。
   - stdout 仅显示 stage/storeOutcome/staticStatus/attemptCount/issueCodes/
     executable/输出路径；不输出 SQL、参数、原始响应或连接信息。

2. **`tests/contract/test_preview_evidence_v3_script.py`**（10 项）：
   - 默认成功路径：`EvidencePackV3` 校验通过，`evidenceComplete`/`stored`/
     `static passed`/`attemptCount=1`/`executable=false`，安全元信息齐全。
   - `--output-dir` 经 `main()` 生效。
   - 文件已存在时退出 `3`，原字节不变，provider=0，save=0，stderr 含「已存在」。
   - 空批次仓储 → `blockedUpstream`，退出 `2`，provider=0，save=0，失败证据包可解析。
   - store failed → `candidateGenerated`，退出 `2`，静态未调用，错误码与候选 hash 保留。
   - 正常及失败路径均关闭 store。
   - `main()` + `capsys` 泄漏检查：证据包 JSON、stdout、stderr、repr(pack) 均不含
     `synthetic_value`/`synthetic_table`/`FROM dbo`/`SELECT`。

**验证**：`uv run pytest tests/contract/test_preview_evidence_v3_script.py` →
10 passed；`uv run python -m scripts.preview_evidence_v3 --output-dir <新空目录>` →
退出 `0`，输出 `v3-evidence-pack.json`，文件被 `.gitignore` 忽略。

**禁止**：不读取 .env、不连接真实数据库/在线模型、不扩大 SQL/事实范围、
不改允许列表和证据包契约、不实现审批发布/context 生命周期/Phase 5。

#### M3/M4 切片：严格实体键等值 filters（2026-09-14，已完成）

本轮扩展 M3/M4，支持 source 请求中与已授权实体键一致的参数等值 filters。

**语义与边界：**

支持条件（必须全部满足）：
1. factKind=source、aggregation.mode=none、timeRange.mode=none、单一关系、无 JOIN；
2. 每个 filter：operator=eq、value.kind=parameter、required=true、
   nullPolicy=error；
3. 参数存在于 fact.parameters，role=entityKey、required=true；
4. filter.fieldId 与该参数对应的实体键授权 fieldId 完全一致；
5. M2 resolvedFilter 与 resolvedEntityKey 解析为同一 schema/relation/column；
6. 该列在已批准快照中明确 nullable=false。

继续阻断：literal、gte 等非 eq 算子、可选 filter、其他 nullPolicy、
错字段/参数、可空实体键列、聚合、exists、JOIN、时间范围。

**新增文件（1 个）：**

1. `src/release_sql_bot/application/filter_constraints_v3.py`：
   纯计算辅助模块 `qualified_filter_param_names`，逐个 filter 建立
   可确定性复核的对应关系。M3/M4 分别独立调用，互不信任。

**修改文件（4 个）：**

2. `src/release_sql_bot/application/candidates_v3.py`：
   将 `filters.items` 非空一律拒绝改为严格资格检查。

3. `src/release_sql_bot/application/prompts_v3.py`：
   Prompt 版本升至 `sqlserver-fact-candidate-v3.1`，新增
   `filterConstraints` 字段提供确定性解析后的 filter 约束。

4. `src/release_sql_bot/application/sql_validation_v3.py`：
   新增 `_check_filters`，独立从完整输入重算过滤条件合法性并与 AST 对应。

5. `src/release_sql_bot/domain/sql_candidates_v3.py`：
   `CandidateProvenanceV3.prompt_version` 增加 `v3.1`；
   `SqlTemplateCandidateV3` 范围说明更新。

**M6 同步（1 个）：**

6. `src/release_sql_bot/application/evidence_loop_v3.py`：
   仅 prompt 版本允许列表增加 `v3.1`；未改允许列表。

**新增测试（2 个文件 / 22 项）：**

- `tests/unit/test_candidates_v3_filters.py`（19 项）：
  旧路径兼容、单/多合法 filter、混合阻断、非法形状参数化、
  非 eq 算子、字面值阻断、M6 端到端 evidenceComplete、输入不变。
- `tests/unit/test_sql_validation_v3_filters.py`（3 项）：
  无 filter 返回空、单合法 filter 合格、不合格 filter 不达标。

**验证**：`uv run pytest` → **1332 passed**（M6 单元 47 + 演示合约 10 +
M3/M4 filter 22 + 其它各项全含）。

**禁止**：未连接真实 MongoDB/在线模型、未扩大允许列表、
未实现审批发布/context 生命周期/Phase 5、未关闭 BUG-20260906-01。

**残留边界**：真实仓储适配器装配、在线 provider、生产 CLI/HTTP、
context 生命周期、Phase 6 审核发布仍需用户授权。

## 13. 上游 RuleAgent 外部前置（M0 外部依赖）

**观察口径（2026-09-06 二轮审查冻结）**：RuleAgent 工作区处于活跃并行变化中；下列每组数字
都是**带时间戳的观察快照**，不是长期当前事实。工作区活跃变化期间，不得把任何一次快照的
测试数量当作固定跨仓库门禁反复引用。**M0 的稳定门禁只有本节末尾的外部前置清单**（工作树
经独立审查、V3 契约与实现已提交、取得真实 commit SHA、Schema SHA-256 在该提交树上重新
核对、必要检查在该提交树上通过）。本任务未修改 RuleAgent，未运行其任何持久化脚本。

### 2026-09-06 历史观察（保留用于审计，不再代表当前上游状态）

- 上游 HEAD `65a96846b3ae991b42a8159dbbee43a9bbe15846`；`contracts/`
  `fact-binding-request-3.0.0.schema.json` 为**未跟踪工作区文件**，V3 契约与大量 V3 代码、
  文档均未提交；不得把未提交的 HEAD 写成 Schema 来源 commit；
- Schema 原始字节 SHA-256 复核（2026-09-06 观察）为
  `2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566`，与 SqlBot 冻结值
  一致（本日双工具复核）；
- **快照 1**（上游自身 PROG-20260906 记录，2026-09-06）：173 passed, 5 deselected；
- **快照 2**（2026-09-06 日间观察）：174 passed, 6 deselected；`ruff check` 报 2 个错误
  （当时存在的未跟踪临时脚本 `.scratch_find.py` 的 RUF002）、`ruff format --check` 报
  1 个文件需重排版（未跟踪的 `scripts/persist_report_release_v3_delivery.py`）；mypy、
  pip check 通过；
- **快照 3**（2026-09-06 19:55–19:56，命令实际执行）：174 passed, 6 deselected；ruff check
  All checks passed（`.scratch_find.py` 已不存在）；format 176 files already formatted；
  mypy Success（61 files）；pip check 通过；git 条目 94；
- **快照 4**（2026-09-06 人工复核观察，时间点以审查记录为准）：189 passed, 6 deselected；
  ruff check 通过；format 179 files already formatted；mypy 通过；pip check 通过；git
  条目 99；V3 Schema 与大量 V3 工作仍未提交；
- **快照 5**（2026-09-06 21:02:21–21:02:30，本轮修订开始/结束期间实际执行，只读）：
  `git -c safe.directory=... status --short --branch` → `## main...origin/main`、99 条目；
  `pytest -q` → **201 passed, 6 deselected**；`ruff check .` → All checks passed；
  `format --check .` → 179 files already formatted；`mypy` → Success（61 source files）；
  `pip check` → No broken requirements found。
- 快照间差异（如实登记，不猜测原因、不追改上游）：pytest 通过数 173→174→189→201 持续
  演进（快照 5 比快照 4 再 +12），ruff 错误在快照 3 后消失，format 文件数 176→179，
  git 条目 94→99。

### 2026-09-09 当前已验证状态（T0 只读核验）

- RuleReader HEAD `01ddae0`，提交 `bad6fd349a6ecbff190b9bd0ac1bc34a48588325` 已跟踪
  `contracts/fact-binding-request-3.0.0.schema.json`，无后续修改；
- 提交树原始字节（LF, 29870 字节）SHA-256 = `0e39c7ac…`；运行时 CRLF 规范化哈希 =
  `2c5e4603…`；两端 JSON 结构相等；
- 来源清单已登记 `verifiedCommitTree` 与 `runtime`（见 `docs/specs/fact-binding-request-3.0.0-source.json`）。

### 外部前置清单（2026-09-06 历史版本，当前状态见上方"2026-09-09 当前已验证状态"）

以下为 2026-09-06 观察时的前置清单，部分已由 T0 解除：

① ~~RuleAgent V3 契约与相关代码完成独立代码审查并提交，取得真实 commit SHA~~（**已由 T0
完成**：commit `bad6fd3…`，RuleReader HEAD `01ddae0`）；
② ~~在该提交树上重新计算 V3 Schema SHA-256~~（**已由 T0 完成**：提交树 `0e39c7ac…`，
运行时 `2c5e4603…`，来源清单已更新）；
③ 必要检查（pytest/ruff/mypy/pip check）在该提交树上通过并以该次运行结果登记（**历史观察
时的要求**）；
④ 上游 Schema v5 migration 与真实 batch 写入仍需单独授权；
⑤ 本任务与 M1 前的全部里程碑均不运行真实持久化脚本。

**2026-09-09 历史快照（不代表当前实施状态）：** M0 已收口，
REQ/BIZ/DEV-20260906-04 已由用户明确批准并落档，M0 状态为 `completed`；
Git 提交需用户另行明确授权；M1 已启动（`in_progress`），首个项目授权/
快照/批准记录契约子任务及其审核修复已完成，后续仍受 M1 剩余范围约束。

**当前状态**：M1 已完成（`completed`）；M2 为 `in_progress`。
详见本节 M1/M2 及最新 PROG。

## 14. 开放问题

| # | 问题 | Owner | 阻断 |
| --- | --- | --- | --- |
| 1 | ApprovalRecordV3 结构（§3.3）与 M1 创建/版本语义已随五项设计获批；**M1 契约及九组纯计算批准闭包校验已完成，该项不再阻断 M1**；真实存储、生命周期和受信批准来源核验仍属后续门禁，按 §3.6、M3/M6 和开放问题第 2 项执行 | metadataReview + sqlBot | **已关闭（M1 范围）** |
| 2 | context 生命周期事件记录/active pointer 的存储设计与审计载体（3.2 节方案的实施确认；M1 只做契约与纯计算校验，存储设计在 M6 编排前冻结） | sqlBot + metadataReview | M6 |
| 3 | V3 Prompt 版本命名与 `exactOutputDeclarations` 等价结构设计 | sqlBot | M3 |
| 4 | parser-neutral 检查是提取共享模块还是 V3 内参数化副本（两者都合规，实施时二选一并登记） | sqlBot | M4 |
| 5 | V3 存储集合授权、账号隔离与配置键命名 | 运维 | M5 |
| 6 | M6 编排与证据包的入口形态（CLI/HTTP）与授权记录方式 | 用户 | M6 |
| 7 | `entityType`/`grain` 到授权 relation 的映射（§5.3 第 2 步）：实体键辅助解析（`_resolve_fields_and_entity_keys_v3`）已实现，但不证明 `entityType`/`grain` → 授权 relation 的映射要求已实现；该映射尚未实现，需澄清设计，且不新增映射契约、不推断业务关系 | sqlBot（实现）；metadataReview（授权语义复核） | 该映射的实现与完整 M2 验收；不阻断已明确契约的独立离线子任务 |

> 已决事项登记：`usage_traceability_sha256` 的参与字段、排序键与重复身份规则已于 5.4 节
> 冻结（原开放问题"M2 前冻结摘要规范"关闭）；上游提交与来源哈希复核已由 2026-09-09 T0
> 完成（历史开放问题 7 — 上游提交与来源哈希复核 — 已关闭。当前表第 7 项是另行新增的 entityType/grain 映射问题，与历史问题不是同一事项）；
> ApprovalRecordV3 结构与 M1 创建/版本语义已于 §3.3 随五项设计获批，撤销/active
> pointer/supersession/MongoDB 事务仍由 M6 开放问题 2 承载。

## 15. 文档影响

实施各里程碑时同步更新：本 REQ/BIZ/DEV 状态、[BUG-20260906-01](../bugs/BUG-20260906-01-v3-phase4r-downstream-contract-gap.md)
关闭登记、README、docs/README 索引、ROADMAP Phase 4R 状态、当日 PROG（脱敏证据）。任何与
本文档的偏离必须先更新文档再实现。
