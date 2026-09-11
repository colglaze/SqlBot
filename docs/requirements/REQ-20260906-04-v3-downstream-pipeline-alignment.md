# REQ-20260906-04：FactBindingRequest 3.0.0 下游管线对齐（Phase 2G/3/4/存储）

- 状态：`approved`（2026-09-09 用户明确批准五项设计；2026-09-06 人工审查意见修订版：handoff
  内容闭包与仓储真实性分层、usage 六元组、契约版本表、runtime/implementation 双顺序、context
  生命周期与 M0 状态口径已按审查结论修订。本次批准只表示设计已批准，不表示全部需求或实现已完成）
- 创建日期：2026-09-06
- 来源：Phase 4R Milestone 0 跨仓库基线审计发现“V3 intake 完成 ≠ 下游链路就绪”的可复现契约
  断点（[BUG-20260906-01](../bugs/BUG-20260906-01-v3-phase4r-downstream-contract-gap.md)），
  用户要求建立下游对齐计划
- 前置需求：[REQ-20260906-03](REQ-20260906-03-fact-binding-v3-intake.md)、
  [REQ-20260906-02](REQ-20260906-02-real-upstream-handoff-evidence-loop.md)、
  [REQ-20260827-03](REQ-20260827-03-project-context-metadata-resolution.md)、
  [REQ-20260828-01](REQ-20260828-01-v2-candidate-generation-input.md)、
  [REQ-20260827-01](REQ-20260827-01-sql-ast-safety-gate.md)、
  [REQ-20260906-01](REQ-20260906-01-v2-candidate-persistence.md)
- 业务决策：[BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md)
- 技术方案：[DEV-20260906-04](../architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md)
- 关联进度：[PROG-20260906](../progress/PROG-20260906.md)

## 1. 背景

SqlBot 已完成 `FactBindingRequest 3.0.0` 的离线 intake（提交 `b3e3d35`）：冻结上游 Schema、
独立严格 consumer、batch 门禁与只读 API。但 intake 之后不存在任何 V3 下游契约：

```text
V3 batch intake（已完成，readyForMetadataResolution）
  → Phase 2G V3 元数据解析        【缺失】ResolveMetadataRequestV2 只接受 FactBindingRequestV2
  → Phase 3 V3 候选生成           【缺失】GenerateSqlCandidateRequestV2 只内嵌 V2 resolution 契约
  → Phase 4 V3 静态门禁           【缺失】ValidateSqlCandidateRequestV2 / 报告均绑定 V2
  → V3 候选持久化                 【缺失】CandidateTemplateStore.save 只接受 SqlTemplateCandidateV2
  → Phase 5 V3 对齐               【缺失】ValidateSqlServerRequestV2 绑定 V2 静态链
```

V2/V3 字段集互不相交、双向严格解析必然失败、上游禁止降级（见
[REQ-20260906-03](REQ-20260906-03-fact-binding-v3-intake.md) 第 3 节），因此不存在“把 V3 塞进
V2 链”的合法路径。本需求要求建立独立的 V3 下游契约链，使 V3 请求在不丢失任何语义的前提下走完
Phase 2G → 3 → 4 → 存储，并显式划定 Phase 5 对齐的边界。

## 2. 目标

1. 冻结 V3 handoff 双层契约（2026-09-06 二轮审查分层）：**`HandoffClosureV3` 内容闭包**——
   纯计算、可序列化，携带 `ruleVersion`、`requestId`、`factCode`、`payloadSha256`、
   `batchSha256`、`contractSchemaId`、`contractSchemaSha256`、intake status 快照与精确
   payload；哈希闭包只能证明内容内部一致，**不能证明** batch 真实存在于 MongoDB、由
   RuleReader 写入、intakeStatus 来自真实 intake 或载荷属于当前批准的真实运行；从
   HTTP/CLI JSON 反序列化的 closure 不得称为 repository-verified attestation。
   **`RepositoryVerifiedHandoffV3` 仓储背书**——受信应用服务在同一次调用中通过仓储按精确
   `ruleVersion + requestId` 重读 batch、重新执行 V3 intake、选择唯一 request 并与携带
   closure/context/snapshot 逐字段逐哈希比较后构造的内部结果。Phase 2G V3 不得只接受裸
   `FactBindingRequestV3`；一切外部副作用路径（provider 调用、candidate store 写入、
   Phase 4R 真实证据包、未来真实 V3 生成入口）必须在同一次应用调用中完成仓储背书；仅
   “重新计算调用方提供的 hash”不构成背书；batch/request/payload/Schema 任一哈希不一致或
   仓储无 batch 时 provider 与 store 调用次数均为 0；客户端自报 ready 不构成证明；纯离线
   测试可携带完整合成 closure，但结论明确不构成真实仓储证明；candidate、静态报告与证据包
   的 `batchSha256`/`payloadSha256` 是追溯引用而非真实性证明，真实运行证据还必须记录
   repository verification 阶段和结果；不得用 V2 `BindingGapReport` 替代该证明。
2. 冻结 V3 项目授权上下文契约决策（`ProjectBindingContextV3` 或经证明的等价方案）：V3 规则引用
   闭包（`ruleSetId`、`ruleVersion`、`rule_ref.schemaVersion="3.0.0"` 表示所引用的 RuleReader
   规则契约版本、`sourceSha256`、`catalogDigest`、`candidatePayloadSha256`）、`requestId` 长度语义
   （≤420）、relation/column/field/entity-key/join grants、`approvalRef` 与一致的不可变版本语义
   （载荷 insert-only，生命周期用独立事件/指针表达，不原地改写）。上下文自身 `schema_version="1.0.0"`。
3. 冻结元数据快照决策：逐字段判定 `GovernedMetadataSnapshotV2` 哪些字段与 FBR 版本无关；复用
   现有模型必须证明不会把 V2 权威语义带入 V3，不能证明则定义独立 V3 快照契约。
4. 冻结下游契约版本表：`FactBindingRequest` 的 `contractVersion=3.0.0` 只属于上游交接载荷；
   context、snapshot、解析请求、解析报告、候选、静态报告与存储文档包装各自拥有独立
   `schemaVersion` 并独立演进；不因消费 FBR 3.0.0 而机械继承版本号；`ruleRef.schemaVersion`
   是唯一表达所引用 RuleReader 规则契约版本的字段；V2/V3 隔离依赖类型与引用闭包，不依赖
   相同的版本号数字。
5. 冻结 Phase 2G V3 契约：`ResolveMetadataRequestV3`（含 `HandoffClosureV3` 内容闭包）与
   `BindingResolutionReportV3`；`FactBindingRequestV3` 的 fields/filter/aggregation/
   timeRange/entity 参数确定性解析规则；完整保留 usage 六元组与 evidence 引用；明确禁止
   调用 `analyze_binding_gaps_v2`；`metadataResolved` 只表示内容与授权解析闭合，不宣称
   真实 batch 已验证。
6. 冻结 V3 候选生成契约：`GenerateSqlCandidateRequestV3`、独立 Prompt 版本、
   `SqlTemplateCandidateV3`；M3 应用服务采用**方案 a（推荐，冻结）**——直接依赖只读
   handoff repository，在 provider 调用前于服务自身完成仓储背书，再完整重算 Phase 2G V3
   闭包（备选方案 b：仅可由 M6 编排服务调用且不设独立 wire 入口，采用时须登记放弃方案 a
   的装配原因）；候选固定 `candidate / executable=false / reviewStatus=pending`；provenance
   闭包覆盖 V3 `ruleSetId`、`catalogDigest` 与 `candidatePayloadSha256`。
7. 冻结 usage 追溯语义与唯一性事实：权威 usage 身份为完整六元组（`stage`/`ruleCode`/
   `priority`/`conditionId`/`conditionPath`/`outcome`）；V3 consumer 现行重复门禁是四元组
   `(stage, ruleCode, conditionId, conditionPath)`，**同一个 `conditionId` 可以出现在不同
   stage/ruleCode/conditionPath 中**，这些 usage 合法且必须全部保留、不得合并；不得为下游
   实现方便收紧上游契约（除非另立跨仓库契约变更需求）。应用从权威请求确定性复制六元组并
   计算 canonical 摘要 `usage_traceability_sha256`（wire `usageTraceabilitySha256`，算法与
   完整排序键见 DEV 5.4 节，本名称为全仓库唯一 digest 名称）；候选与存储记录必须保留完整
   六元组或可独立解析到完整不可变载荷的强引用，仅存摘要不合格；明确 AST 只能证明参数、
   对象、列、join、`fact_value` 来源及事实查询相关位置，**不能**证明 stage/ruleCode/
   priority/outcome 业务语义；Phase 4 的检查命名为 usage traceability / reference
   integrity，不得冒充 rule semantic proof。
8. 冻结 Phase 4 V3 契约：`ValidateSqlCandidateRequestV3` 与 V3 静态报告；usage
   traceability / reference integrity 校验必须对完整六元组逐项精确一致，禁止只按
   `conditionId` 集合相等（或其加 stage/outcome 的子集）宣称覆盖；划定 SQLGlot
   parser-neutral 检查中可复用与不可复用的边界。
9. 冻结 V3 候选持久化方案：独立 V3 store（或经单独评审的受版本约束安全泛化）；不改变
   insert-only、唯一 hash、幂等与 pending 不变量；V2/V3 存储文档必须可区分，禁止同 hash/
   Schema 语义混淆。
10. 显式划定 Phase 5 边界：V3 Phase 5A 对齐不属于首个实施切片；V3 Phase 4 完成前禁止把真实
    V3 candidate 送入现有 V2 Phase 5A 入口。
11. 建立实施顺序与运行顺序的分离：实施里程碑可为 M4（静态门禁）先于 M5（存储）交付；运行
    状态顺序保持既有 Phase 4R 状态机 `candidateGenerated → candidateStored → staticPassed`
    （生成成功后先 insert-only 保存候选并记录存储 outcome，再对同一 candidate 运行
    Phase 4），静态 blocked 候选保留审计记录，不得删除、覆盖或修改；本需求不改变运行顺序。
12. 建立 V3/V2 路由显式隔离要求（无自动版本识别、无降级、无“最新”选择）并冻结测试矩阵与
    里程碑 M0–M6。
13. 固定已确认事实的消费规则：对固定 `RuleDataReferences` bundle 中实际存在的内容不再要求
    用户重复确认，不以滞后状态字段否定已有数据；工程必须将这些事实确定性转换为新版本
    handoff 与经批准的 V3 context/snapshot/grant。事实确认不等于表列授权、候选批准或真实调用
    授权；资料中确实不存在的内容才进入缺失清单。

## 3. 非目标

- 不实现任何 V3 下游代码；本需求先以 DEV-20260906-04 冻结设计，实施按里程碑另起任务；
- 不实现 Phase 4R 编排服务（`generate_real_candidate_evidence_*`）与证据包；
- 不实现 V3 Phase 5A/5B/5C 对齐；
- 不把 V3 转换、包装或降级为 V2/V1，也不把 V2 提升为 V3；
- 不修改 RuleAgent 仓库、不运行其真实 V3 持久化脚本、不建立运行时依赖；
- 不连接 MongoDB、SQL Server、DeepSeek 或飞书；私有 `RuleDataReferences` bundle 仅允许按固定
  commit/digest 做只读事实提取和证据核验，不执行其中 SQL，不把原始内容复制到公开产物；
- 不修改 V2 任何现有行为、契约与测试期望；
- 不新增 HTTP/CLI 入口（入口设计仅作为约束冻结，实现随里程碑另行任务）。

## 4. 成功与阻断语义

1. V3 下游全链路产物固定 `executable=false`、`reviewStatus=pending`；任何阶段成功不改变该
   状态。
2. V3 语义完整性不变量：`usages` 的 `stage`/`ruleCode`/`priority`/`conditionId`/
   `conditionPath`/`outcome`、examples 的 `expectedOutcome`、evidence 闭包与 warning-only
   uncertainties 从 intake 输出到候选存储全程无损；任何一环丢弃或改写即阻断。
3. 复用不变量：只允许复用经证明与规则版本无关的底层算法（canonical 哈希、标识符规范化、
   SQLGlot parser-neutral 检查逻辑等）或物理元数据 DTO；V2 请求/报告/候选契约一律不得被 V3
   链导入或包装。
4. 双向拒绝不变量：V3 链拒绝任何 V2/V1 载荷；V2 链拒绝任何 V3 载荷；仓库中不得出现任何
   V3↔V2 转换函数。
5. 副作用不变量：Phase 2G V3 为纯计算（只校验 `HandoffClosureV3` 内容闭包，`metadataResolved`
   不宣称仓储真实性）；一切外部副作用路径（provider 调用、candidate store 写入、Phase 4R
   真实证据包、未来真实 V3 生成入口）必须在同一次应用调用中完成仓储背书——按精确
   `ruleVersion + requestId` 经仓储重读 batch、重新执行 V3 intake、选择唯一 request 并与
   携带 closure/context/snapshot 逐字段逐哈希比较；仅“重新计算调用方提供的 hash”不构成
   背书；closure 的 batch/request/payload/Schema 任一哈希不一致或仓储无 batch，或 provider
   授权缺失、携带报告不一致时，provider 调用次数与 store `save` 调用次数均为 0；客户端
   自报 ready 不构成证明。
5a. 批准闭包校验失败语义（冻结）：M2 解析前九个有序检查组（DEV §5.2）按顺序 fail fast，
   任一失败即 `blocked`；失败使用稳定中性 issue code（DEV §3.5 完整列表），不泄漏 ID 实际值、
   哈希实际值、私有对象/字段或 context/snapshot/approval 原始载荷；M2 报告只记录 code；
   `validate_approval_closure_v3` 成功只证明内容闭包内部一致，不证明批准真实性，真实 M3/M6
   必须通过独立受信批准来源核验（DEV §3.6）。
6. 存储不变量：insert-only、`contentSha256` 唯一、同 hash 幂等（duplicate 不重复落库）、
   写失败不伪造成功；V3 存储文档必须带显式契约版本（包装与候选本体各自独立，见第 5.3 节
   版本表），与 V2 文档永不混淆；候选本体保留完整 usage 六元组或可独立解析到完整不可变
   载荷的强引用，仅存摘要不合格。
7. 运行顺序不变量：运行状态顺序固定为既有 Phase 4R 状态机——生成成功后先 insert-only 保存
   候选并记录存储 outcome，再对同一 candidate 运行 Phase 4；存储成功不代表静态通过；静态
   `blocked` 的已存候选不得删除、覆盖或修改。实施里程碑顺序（如 M4 代码先于 M5 交付）不
   改变该运行顺序。
8. 上游锚点不变量：V3 下游实施开始前，上游 V3 契约必须已提交到 RuleAgent 并复核 SHA-256
   （见第 8 节阻断项）。

## 5. 契约冻结要点（实施前由 DEV 逐条细化）

1. **V3 授权上下文**：`ruleSetId`（`^[A-Z][A-Z0-9_]*$`、≤120）、`ruleVersion`（≤260）、
   `requestIds`（≤420）与 `FactBindingRequestV3.requestId`（`<ruleVersion>#<factCode>`）
   闭包；`rule_ref.schemaVersion="3.0.0"` 表示所引用的 RuleReader 规则契约版本；
   `sourceSha256`/`catalogDigest`/`candidatePayloadSha256`引用闭包；context/snapshot canonical hash 可重算；
   relation/column/field/entity-key/join grants 形状与 V2 决策一致但绑定 V3 引用；
   `approvalRef` 独立于 SQL 候选审批。上下文自身 `schema_version="1.0.0"`（延续 V2 上下文先例），
   独立演进。生命周期方案（一致性冻结）：业务载荷 insert-only、不可变，新版本用
   `contextVersion+1` 新文档，旧载荷永不原地改写；active/superseded 生命周期用独立生命周期事件或
   active pointer + compare-and-swap 表达并有独立审计；M1 只实现契约与纯计算校验，不实现
   MongoDB context 仓储。
2. **元数据快照**：`GovernedMetadataSnapshotV2` 的字段均为纯物理事实（identity、状态、
   dialect、标识符大小写策略、relations/relationships、approval、自哈希），字段级与 FBR
   版本无关；但模块隔离决策（REQ-20260906-03 第 6 节）禁止 V3 代码导入 V2 consumer 模型。
   默认结论：定义独立 V3 快照契约（字段形状等价、`schema_version="1.0.0"`，逐字段登记等价性）；
   把物理 DTO 提取为版本中立共享模块属可选重构，须单独评审并证明无 V2 权威语义带入。
3. **handoff 双层契约**：`HandoffClosureV3`（`schemaVersion="1.0.0"`）携带
   `ruleVersion`/`requestId`/`factCode`/`payloadSha256`/`batchSha256`/`contractSchemaId`/
   `contractSchemaSha256`/`intakeStatus` 与精确 payload，只承载内容闭包；仓储真实性由
   `RepositoryVerifiedHandoffV3` 承担——副作用路径在同一次应用调用中经仓储重读 batch、
   重执行 intake、选择唯一 request 并逐字段逐哈希比较（见目标 1 与 DEV 5.1 节）；任一
   哈希不一致或仓储无 batch → provider/store 调用 0；纯离线测试可携带完整合成 closure
   （不构成真实仓储证明）；该证明不得用 V2 `BindingGapReport` 替代。
4. **Phase 2G V3**：`ResolveMetadataRequestV3`（`schema_version="1.0.0"`）包含
   `project_ref`、`handoff_closure`、`binding_request`、`project_context`、`metadata_snapshot`、
   **`approval_record`**（无 gap report——内容闭包/仓储背书与 intake batch 门禁即 Phase 2F
   等价物）；M2 解析前必须执行九个有序检查组（见 DEV §5.2），任一失败即 `blocked`；
   `BindingResolutionReportV3` 输出 `blocked | metadataResolved` 且固定不可执行，携带
   `batch_sha256`/`payload_sha256` 引用（追溯引用）与完整 usage 六元组追溯（含按冻结规范
   计算的 `usage_traceability_sha256`）；fields/filters/aggregation/timeRange/entity
   keyParameters 按 grants + snapshot 双命中确定性解析。
5. **V3 候选生成**：`GenerateSqlCandidateRequestV3` 内嵌 V3 resolution request + report；
   M3 按冻结的方案 a 在 provider 前完成仓储背书并重算完整 Phase 2G V3 与携带报告比对；
   独立 Prompt 版本
   （`v3.x` 命名空间，不复用 `sqlserver-fact-candidate-v2.x`）；`SqlTemplateCandidateV3` 的
   `declaredUsageCoverage` 为**完整六元组结构化条目**（`stage`/`ruleCode`/`priority`/
   `conditionId`/`conditionPath`/`outcome`，同一 `conditionId` 的多个 usage 全部保留），
   并携带 V3 `ruleSetId`、`catalogDigest`、
   `candidatePayloadSha256` provenance 闭包、`usage_traceability_sha256` 与 `handoff_refs`
   （`batchSha256`/`payloadSha256`，追溯引用而非真实性证明）。
6. **Phase 4 V3**：`ValidateSqlCandidateRequestV3` 内嵌 V3 generation request + V3 candidate；
   V3 静态报告为纯计算 `passed | blocked`，携带 `handoff_refs` 与
   `usage_traceability_sha256`；usage traceability / reference integrity 校验按完整六元组
   与请求 `usages` 逐条精确一致，禁止仅按 `conditionId` 集合相等（或 `conditionId + stage +
   outcome` 子集）宣称覆盖；SQLGlot parser-neutral 检查（语句数、只读结构、参数、对象/列、
   join、临时/外部源检测）作为版本无关算法复用，但请求/报告契约为 V3，且 AST 不推导
   stage/ruleCode/priority/outcome 业务语义。
7. **候选持久化**：默认新增独立 V3 store 端口与适配器（独立集合，如
   `sql_template_candidates_v3`）；“受版本约束的安全泛化”（同一端口按契约版本判别）仅在
   单独评审证明无混淆风险后允许；不改变 insert-only、唯一 hash、幂等、pending 不变量；
   存储文档包装与候选本体各自携带独立 `schemaVersion`（见第 8 条版本表），候选本体保留
   完整六元组；静态 blocked 的已存候选不删除、不覆盖、不修改。
8. **契约版本表**：`FactBindingRequest.contractVersion="3.0.0"` 只属于上游交接载荷；
   `HandoffClosureV3`、`ProjectBindingContextV3`、`GovernedMetadataSnapshotV3`、
   `ApprovalRecordV3`、`ResolveMetadataRequestV3`、`BindingResolutionReportV3`、
   `GenerateSqlCandidateRequestV3`、`ValidateSqlCandidateRequestV3`、
   `SqlStaticValidationReportV3` 与存储文档包装首版均为 `1.0.0`（理由：延续对应 V2 对象既有
   版本先例，自身契约独立演进）；`SqlTemplateCandidateV3` 首版为 `3.0.0`（理由：延续
   `SqlTemplateCandidateV2.schemaVersion="2.0.0"` 的候选契约先例，是候选契约自身版本、与生成
   管线世代对齐，非机械继承）；`ruleRef.schemaVersion="3.0.0"` 是唯一表达所引用 RuleReader
   规则契约版本的字段；V2/V3 隔离依赖类型与引用闭包，不依赖版本号数字。共 **12 个独立顶层契约
   对象**；`RepositoryVerifiedHandoffV3` 是内部受信结果，无 wire `schemaVersion`。
9. **Phase 5 边界**：V3 Phase 5A 契约（`ValidateSqlServerRequestV3` 等）不在首个实施切片；
   V3 Phase 4 完成并验收前，任何真实 V3 candidate 禁止进入现有 V2 Phase 5A 入口（其
   `extra=forbid` 契约本身也会拒绝）。
10. **API/CLI 与双顺序**：V3 路由显式版本化（`/api/v1/...`/`v3` 段），与 V2 路由完全隔离；
    不接受自动版本识别、内容嗅探降级或“最新”选择；当前规划任务不新增任何入口。实施顺序
    与运行顺序分离：实施里程碑可为 M4 先于 M5；运行状态顺序保持既有
    `candidateGenerated → candidateStored → staticPassed`。

## 6. 测试矩阵（实施验收的最低范围）

1. V3 happy path：合成脱敏 V3 请求（含按闭包规则构造的完整 `HandoffClosureV3`）从
   resolution 到 candidate 到 static passed 的全链路（fake provider / fake store，离线）；
   此类夹具结论明确不构成真实仓储证明；
2. V3 被 V2 契约双向拒绝：V2 请求/报告/候选/store/Phase 5A 入口对 V3 载荷全部 fail closed；
   V3 契约对 V2 载荷同样拒绝；按类型与引用闭包断言，不按版本号数字断言；
3. usage 六元组丢失与合并检测：候选 coverage 或报告中缺失 `stage`/`ruleCode`/`priority`/
   `conditionId`/`conditionPath`/`outcome` 任一字段即拒绝；仅 `conditionId` 集合相等（或
   `conditionId + stage + outcome` 子集）不构成覆盖通过；
4. **同 conditionId 多 usage 合法性**：同 `conditionId`、不同 stage/outcome 的两个 usage
   合法且不得合并；同 `conditionId`、不同 ruleCode/conditionPath 的两个 usage 合法且不得
   合并；完整六元组任一字段变化都改变 `usage_traceability_sha256`；输入数组重排不改变
   摘要（canonical 排序不变性）；完全相同的上游四元组
   `(stage, ruleCode, conditionId, conditionPath)` 按现行 consumer 规则拒绝；
5. **内容闭包与仓储真实性分离**：完全自洽但从未存在于仓储的伪造 closure 在真实 provider
   路径被仓储背书拒绝；仓储 batch hash 与调用方 closure 不一致 → provider/store 均 0
   调用；仓储无 batch → provider/store 均 0 调用；`RepositoryVerifiedHandoffV3` 仅能由
   应用服务构造（类型断言）；
6. closure/hash/reference 篡改：closure 的 payload/Schema 哈希、身份闭包或 intake status
   快照不一致；批准状态、canonical hash、`metadataSnapshotRef`、grant 引用、
   `catalogDigest`/`candidatePayloadSha256` 闭包任一破坏即阻断且 provider 与 store 零
   副作用；客户端自报 ready 不通过；
7. provider 前零调用：closure 校验失败、仓储背书失败、Phase 2G V3 失败、授权缺失、报告
   不一致时 provider 调用次数为 0；
8. store 前零调用与运行顺序：生成失败或输出门禁拒绝时 store `save` 调用次数为 0；运行
   顺序测试——生成成功后先落库并记录存储 outcome，再对同一 candidate 运行 Phase 4；静态
   blocked 的已存候选仍可回读且未被修改；
9. V2 全量回归：现有 424 项离线测试（当前基线）零行为变化；
10. 泄漏检查：日志、报告与公开 PROG 不含 SQL、参数值、对象清单、连接信息、provider 原始
    响应与秘密字段；
11. 版本独立性：第 5.8 条版本表中各对象 `schemaVersion` 绑定有契约测试；修改 FBR
    `contractVersion` 常量不改变任何下游对象断言。

## 7. 验收标准

1. 第 5 节全部冻结要点在 DEV-20260906-04 中有可实施定义，REQ/BIZ/DEV 互相引用且链接有效；
2. 里程碑 M0–M6（见 DEV-20260906-04 第 12 节）每个都有前置条件、DoD、失败语义与测试范围；
3. 第 6 节测试矩阵全部离线实现并通过；默认测试不访问 MongoDB、SQL Server、在线模型、飞书
   或私有 bundle；
4. V2 行为零变化：`uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`、
   `git diff --check` 全部通过，且 pytest 通过数不低于当前基线 424；
5. 上游 RuleAgent V3 契约已提交；提交树原始字节与运行时规范化哈希均按来源清单独立复核，
   JSON 结构与契约身份一致；任一实际内容差异都另立契约差异评审；
6. README、docs/README、ROADMAP 与当日 PROG 同步；本需求完成不把任何真实数据验证表述为
   已完成；
7. 运行顺序验收：编排路径保持 `candidateGenerated → candidateStored → staticPassed`，有
   测试证明静态 blocked 候选的存储审计记录完整且未被修改；实施顺序（M4 先于 M5 交付）
   不构成对运行顺序的更改；
8. M0 状态验收：上游 V3 契约 commit 与来源哈希登记已于 2026-09-09 完成；本组 REQ/BIZ/DEV
   已于 2026-09-09 由用户明确批准并落档，M0 整体状态为 `completed`。
   **当前状态**：M1 已完成（`completed`，2026-09-10，提交 `189daca`、`29716b0`）；
   M2 为 `in_progress`（提交 `4f0f45c`：输入门禁、column grant 解析、字段/实体键授权闭包、
   filters 物理字段解析、aggregation 授权引用解析、
   timeRange 时间字段授权解析、指定 join grant 物理授权解析已完成；
   剩余 多关系连接选择、完整 join 输出、公开编排及报告组装；
   `entityType`/`grain` 映射待澄清，见 DEV §14 开放问题第 7 项）。
   来源登记完成不等于业务表达 vNext、context/snapshot 批准或真实生成完成。

## 8. 阻断项

1. ~~上游提交与来源登记阻塞已由 2026-09-09 T0 解除~~（**已完成**：commit `bad6fd3…`，
   提交树 `0e39c7ac…`，运行时 `2c5e4603…`，来源清单已更新）。~~M0 仍需本组 REQ/BIZ/DEV
   批准并提交~~（**已由 2026-09-09 用户指令批准并落档，M0 状态见 DEV §12；Git 提交需用户
   另行明确授权**）。
2. 当前已归档 V3 交付存在已登记的业务表达缺口；受影响事实进入真实 M3/M6 前必须由
   `businessRuleReview` 基于固定私有资料生成新版本 catalog、规则和 handoff，禁止覆盖旧版本。
3. `metadataReview` 的批准 owner 已明确；M1 所需的批准记录结构（`ApprovalRecordV3`）、创建/版本
   语义及与 SqlBot V3 契约的交接方式已随五项设计获批。真实存储、生命周期和受信批准来源核验
   仍按 DEV 后续里程碑执行，不属于 M1 范围。已有事实无需用户重填，但未经批准的
   context/snapshot/grant 仍不能进入真实生成。
4. 私有资料不能直接成为授权输入：必须经过固定来源校验、确定性转换和 metadataReview 批准；
   原始资料不得直接进入 Prompt，且不得据此扩大表列或 join 范围。
5. 真实调用还要求同一次应用调用中的 MongoDB 仓储背书、精确 `ruleVersion + requestId`、
   完整 M1–M5 链路、显式 M6 入口，以及用户对当次在线 provider 调用的授权。离线 fake
   provider 开发不受在线授权阻塞。
