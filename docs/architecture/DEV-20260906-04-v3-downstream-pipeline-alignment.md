# DEV-20260906-04：FactBindingRequest 3.0.0 下游管线对齐设计与实施计划

- 状态：`proposed`（2026-09-06 人工审查意见修订版：handoff 内容闭包与仓储真实性分层、usage
  六元组、契约
  版本表、runtime/implementation 双顺序、context 生命周期与 M0 状态口径已按审查结论修订）
- 创建日期：2026-09-06
- 实现需求：[REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
- 业务决策：[BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md)
- 关联缺陷：[BUG-20260906-01](../bugs/BUG-20260906-01-v3-phase4r-downstream-contract-gap.md)
- 前置设计：[DEV-20260906-03](DEV-20260906-03-fact-binding-v3-intake.md)、
  [DEV-20260827-03](DEV-20260827-03-project-context-metadata-resolution.md)、
  [DEV-20260828-01](DEV-20260828-01-v2-candidate-generation-input.md)、
  [DEV-20260827-01](DEV-20260827-01-sql-ast-safety-gate.md)、
  [DEV-20260906-01](DEV-20260906-01-v2-candidate-persistence.md)

> 本文档只做规划，本轮任务不实现任何 V3 下游代码。文中全部契约、模块与测试均为后续实施
> 对象；M0 未收口且本组文档未批准前，M1 起所有里程碑不得开始实施。上游 commit 与来源登记已
> 于 2026-09-09 完成，历史观察见第 13 节。

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
tests/contract/test_sql_candidates_v3_contract.py
tests/unit/test_metadata_resolution_v3.py
tests/unit/test_candidates_v3.py
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

### 3.3 批准记录载体（proposed，待用户批准）

metadataReview 是 context/snapshot 批准 owner。SqlBot 是 V3 契约代码、确定性校验和解析的
实现 owner。候选审核（`reviewStatus=pending`）与元数据批准是两件独立事项，不得混为一体。

proposed 不可变批准记录结构（`schema_version="1.0.0"`）：

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
- **本轮仅设计，不实现存储或事务**；未经用户批准前保持 proposed，不写入代码或测试。

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
  不排除 `approvalRef`、`approvedAt` 或其他业务字段；不复用 handoff 排除时间字段的特殊算法；
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

### 3.6 真实 M3/M6 受信批准来源门禁（proposed，冻结门禁要求，实现属于后续阶段）

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
  payload_sha256:          payload 排除时间字段后的 canonical SHA-256（上游规则）
  batch_sha256:            所引用 batch 的 canonical SHA-256
  contract_schema_id:      urn:rulereader:fact-binding-request:3.0.0
  contract_schema_sha256:  2c5e4603…（冻结 Schema 副本哈希）
  intake_status:           Literal["readyForMetadataResolution"]（intake 输出状态快照）
  payload:                 精确 FactBindingRequestV3
```

内容闭包校验规则（纯计算，M2 实现；对 closure 自身）：

1. **payload 闭合**：`canonical_sha256(closure.payload)`（排除时间字段，上游规则）==
   `payload_sha256`；`request_id == payload.requestId == <rule_version>#<fact_code>`；
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
4. filters：FilterSet 树的每个字段引用按 fields 同规则解析；`completeness=complete` 已由
   Schema const 保证，不重复引入 V2 unresolved 语义。
5. aggregation：`groupByFieldIds` 与聚合引用字段按 fields 规则解析；`mode=none` 时无需字段。
6. timeRange：`asOf`/`between` 的时间锚字段按 fields 规则解析；边界值不参与授权。
7. join：多 relation 场景实际 join 端点必须命中显式 join grant 且 snapshot relationship edge
   存在。
8. 使用位置：报告保留每个 resolved 项的 `evidenceIds`（原样来自 payload）与 usage 引用闭包
   摘要（见 5.4）。

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
- 计算步骤：对每条 usage 投影出恰好六个 camelCase 字段（`stage`/`ruleCode`/`priority`/
  `conditionId`/`conditionPath`/`outcome`）→ 按完整稳定排序键排序——`stage` 使用明确业务
  顺序 `stateGuards → prerequisites → eligibility → postGates → exclusions`，其后依次
  `priority` 数值升序、`ruleCode` 字符串升序、`conditionId` 字符串升序、`conditionPath`
  字符串升序、`outcome` 字符串升序（**排序不得只使用 stage + priority + conditionId**：
  同四元组前缀下剩余字段必须参与，保证全序唯一确定）→ 对排序后的六字段对象数组按既有
  `canonical_sha256` 规则（UTF-8、`sort_keys=True`、紧凑 separators、`ensure_ascii=False`、
  禁 NaN）计算；
- `evidenceIds` 不进入本摘要（仍通过 `payloadSha256` 与 handoff closure 追溯）；时间字段
  不进入；相同 `conditionId` 的多个不同 usage 全部保留、全部参与摘要；
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

### M0：上游 commit 锚点与跨仓库基线（`in_progress`，来源锚点已解除）

**当前状态（2026-09-09）：上游 commit 与三类哈希来源登记已由 T0 完成；M0 仍因本组
REQ/BIZ/DEV 尚未批准并提交而保持 `in_progress`，M1 不得开始。以下 2026-09-06 快照保留为历史
审计记录，不再代表当前上游状态。**

- 2026-09-09 已完成：上游 V3 契约提交、完整 commit 锚点、提交树原始字节哈希与运行时规范化
  哈希登记；真实生成仍受 10.1 节其余门禁约束。
- 2026-09-09 仍未完成：本组 REQ/BIZ/DEV 批准并提交。上游业务表达 vNext 是受影响事实进入真实
  M3/M6 的门禁，不阻止 M1/M2 的离线契约实现。

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

### M1：V3 授权上下文、快照与 handoff 闭包契约

- 前置条件：M0 收口（上游 V3 契约已提交、SHA-256 复核一致）；本 REQ/BIZ/DEV 已批准。
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
- 内容：`application/metadata_resolution_v3.py`；`ResolveMetadataRequestV3`/
  `BindingResolutionReportV3`；`HandoffClosureV3` 内容闭包校验实现（5.1 节闭包规则）。
- DoD：第 5.3 节八步确定性解析全部有 happy/blocked 测试；5.1 节内容闭包规则全部有正反
  测试；`usage_traceability_sha256` 按 5.4 节规范可独立重算（含排序不变性与四元组重复
  拒绝）；纯计算证明（解析函数无仓储/provider 注入点）且报告明确 `metadataResolved` 不
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

**M0 当前唯一剩余门禁：REQ/BIZ/DEV-20260906-04 完成人工批准并纳入 Git 提交。**

## 14. 开放问题

| # | 问题 | Owner | 阻断 |
| --- | --- | --- | --- |
| 1 | ApprovalRecordV3 结构（§3.3 proposed）与 M1 创建/版本语义已形成 proposed 方案；当前只等待用户随整个 M0 审批包批准 | metadataReview + sqlBot | M1（随 M0 审批包） |
| 2 | context 生命周期事件记录/active pointer 的存储设计与审计载体（3.2 节方案的实施确认；M1 只做契约与纯计算校验，存储设计在 M6 编排前冻结） | sqlBot + metadataReview | M6 |
| 3 | V3 Prompt 版本命名与 `exactOutputDeclarations` 等价结构设计 | sqlBot | M3 |
| 4 | parser-neutral 检查是提取共享模块还是 V3 内参数化副本（两者都合规，实施时二选一并登记） | sqlBot | M4 |
| 5 | V3 存储集合授权、账号隔离与配置键命名 | 运维 | M5 |
| 6 | M6 编排与证据包的入口形态（CLI/HTTP）与授权记录方式 | 用户 | M6 |

> 已决事项登记：`usage_traceability_sha256` 的参与字段、排序键与重复身份规则已于 5.4 节
> 冻结（原开放问题”M2 前冻结摘要规范”关闭）；上游提交与来源哈希复核已由 2026-09-09 T0
> 完成（原开放问题 7 关闭）；ApprovalRecordV3 结构与 M1 创建/版本语义已于 §3.3 形成
> proposed 方案，撤销/active pointer/supersession/MongoDB 事务仍由 M6 开放问题 2 承载。

## 15. 文档影响

实施各里程碑时同步更新：本 REQ/BIZ/DEV 状态、[BUG-20260906-01](../bugs/BUG-20260906-01-v3-phase4r-downstream-contract-gap.md)
关闭登记、README、docs/README 索引、ROADMAP Phase 4R 状态、当日 PROG（脱敏证据）。任何与
本文档的偏离必须先更新文档再实现。
