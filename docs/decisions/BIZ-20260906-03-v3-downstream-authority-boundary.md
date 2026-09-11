# BIZ-20260906-03：V3 下游管线权威边界与复用决策

- 状态：`approved`（2026-09-09 用户明确批准五项设计；2026-09-06 人工审查意见修订版：handoff
  内容闭包与仓储真实性分层、usage 六元组、契约版本独立、运行顺序与 context 生命周期决策已按
  审查结论补齐。本次批准只表示设计已批准，不表示实现已完成）
- 创建日期：2026-09-06
- 来源：Phase 4R Milestone 0 跨仓库基线审计发现 V3 intake 与 V2 下游链路之间的契约断点
  （[BUG-20260906-01](../bugs/BUG-20260906-01-v3-phase4r-downstream-contract-gap.md)），
  用户要求冻结下游对齐的权威边界
- 影响需求：[REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
- 技术方案：[DEV-20260906-04](../architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md)
- 前置决策：[BIZ-20260906-02](BIZ-20260906-02-fact-binding-v3-authority-boundary.md)、
  [BIZ-20260906-01](BIZ-20260906-01-agent1-metadata-review-sqlbot-boundary.md)、
  [BIZ-20260827-02](BIZ-20260827-02-project-metadata-authorization-boundary.md)、
  [BIZ-20260828-01](BIZ-20260828-01-v2-candidate-authority-boundary.md)、
  [BIZ-20260828-02](BIZ-20260828-02-rulereader-handoff-read-boundary.md)

## 1. 决策

1. **V3 下游是独立契约链，不是 V2 链的扩展。** `FactBindingRequest 3.0.0` 的 Phase 2G、候选
   生成、静态门禁与候选持久化必须使用独立的 V3 请求/报告/候选契约；禁止把 V3 请求包装、转换
   或降级为 `FactBindingRequestV2`，禁止 V3 链导入任何 V2 请求、报告或候选契约。V2 链继续
   服务 `FactBindingRequest 2.0.0`，行为逐字节不变。
2. **复用只到“版本无关算法与物理元数据 DTO”为止。** canonical 哈希、标识符规范化、SQLGlot
   parser-neutral 检查逻辑等经证明与规则版本无关的底层算法可以复用；会吞掉 V3 语义（五阶段
   `stage`/`ruleCode`/`priority`/多 `outcome`、`catalogDigest`/`candidatePayloadSha256`
   provenance、warning-only uncertainties）的任何 V2 契约对象不得复用。
3. **V3 usage 语义不可裁剪，且区分追溯证据与 AST 证据。** 权威 usage 身份是完整六元组
   （`stage`/`ruleCode`/`priority`/`conditionId`/`conditionPath`/`outcome`），必须从 intake
   到候选存储全程无损保留；候选 coverage 与静态 coverage 校验必须对完整六元组逐条精确一致，
   禁止只按 `conditionId` 集合相等（或 `conditionId + stage + outcome` 子集）宣称覆盖。
   **唯一性事实（按现行 consumer 四元组门禁更正）**：同一 `conditionId` 可以出现在不同
   stage/ruleCode/conditionPath 中，这些 usage 合法且必须全部保留、不得合并；不得为下游
   实现方便收紧上游契约（除非另立跨仓库契约变更需求）。
   证据分界：**traceability linkage**——应用从权威 `FactBindingRequestV3` 确定性复制六元组
   并计算 canonical 摘要（唯一字段名 `usage_traceability_sha256` / wire
   `usageTraceabilitySha256`，算法与完整排序键见 DEV 5.4 节），候选、报告与存储记录可追溯
   到完整 usages；**AST-derived evidence**——SQLGlot 只能证明参数、对象、列、join、
   `fact_value` 来源及事实查询相关位置，**不能**证明事实在规则树中的 stage/ruleCode/
   priority/outcome 业务语义。Phase 4 的该检查命名为 usage traceability / reference
   integrity，不得冒充 rule semantic proof，也不得声称从 SQL 推导上述业务语义。候选本体
   必须保留完整六元组或可独立解析到完整不可变载荷的强引用，仅存 usageDigest 不合格。
4. **元数据快照：物理字段与 FBR 版本无关，但默认独立成 V3 契约。** 逐字段审查确认
   `GovernedMetadataSnapshotV2` 的字段（identity、状态、dialect、标识符大小写策略、
   relations/relationships、approval、自哈希）均为纯物理事实，不含任何 FactBindingRequest
   语义；但 [BIZ-20260906-02](BIZ-20260906-02-fact-binding-v3-authority-boundary.md) 与
   REQ-20260906-03 第 6 节确立的模块隔离规则禁止 V3 代码导入 V2 consumer 模型。因此默认
   建立字段形状等价的独立 V3 快照契约（`schema_version=”1.0.0”`，逐字段登记等价性）；
   把物理 DTO 提取为版本中立共享模块属可选重构，必须以独立评审证明”无 V2 权威语义带入”后才可采用。
5. **V3 项目授权上下文是新契约，生命周期方案一致性冻结。** `ProjectBindingContextV2` 内嵌
   V2 `ruleRef`（`ruleId` 语义）且 `requestIds` 长度上限（≤384）与 V3（≤420）不一致，不能
   承载 V3 规则引用闭包（`ruleSetId`、`rule_ref.schemaVersion="3.0.0"` 表示所引用的 RuleReader
   规则契约版本、`catalogDigest`、`candidatePayloadSha256`）。metadataReview 仍为唯一授权
   owner，但批准载荷必须使用 V3 上下文契约。上下文自身 `schema_version="1.0.0"`（延续 V2
   上下文先例），独立演进。生命周期：context 业务载荷 insert-only、不可变；新版本用
   `contextVersion+1` 新文档，旧载荷永不原地改写；active/superseded 生命周期用独立生命
   周期事件或 active pointer + compare-and-swap 表达并有独立审计；不存在“置旧记录
   superseded 且禁止 update”的矛盾路径；M1 只做契约与纯计算校验，不顺手实现 MongoDB
   context 仓储。
6. **V3 候选存储保持不可变审计不变量，默认独立通道。** insert-only、`contentSha256` 唯一、
   同 hash 幂等、写失败不伪造成功、`reviewStatus=pending` 不变；默认新增独立 V3 store 端口
   与独立集合。“受版本约束的安全泛化”（同端口按契约版本判别）仅在单独评审证明 V2/V3 文档
   永不混淆、不存在同 hash 语义冲突后才允许。
7. **Phase 5 对齐不前移。** V3 Phase 5A 契约不在 V3 下游首个实施切片内；V3 Phase 4 完成并
   验收前，禁止把任何真实 V3 candidate 送入现有 V2 Phase 5A 入口；V2 Phase 5A 的
   `extra=forbid` 契约拒绝 V3 载荷本身是正确行为，不得为其增设兼容层。
8. **路由显式隔离，无自动版本识别。** V2/V3 API 与 CLI 入口按显式版本段隔离；不接受自动
   版本识别、内容嗅探降级或“最新”选择；本决策不要求新增任何入口。
9. **上游锚点按原始字节与运行时规范化分别复核。** 上游 V3 契约必须固定到真实 commit；
   提交树原始字节哈希与 SqlBot 运行时规范化哈希分别按
   `docs/specs/fact-binding-request-3.0.0-source.json` 登记，不能互相冒充。两端 JSON 结构或契约
   身份不一致时立即停止并另立契约差异评审。2026-09-09 T0 已完成上游提交与三类哈希登记
（commit `bad6fd3…`，提交树 `0e39c7ac…`，运行时 `2c5e4603…`，提交 `e3b00b2`），来源锚点
阻塞解除；M0 规划批次已进入提交 `61a377f`；本组 REQ/BIZ/DEV 已由用户批准并落档，M0 状态为
`completed`。M1/M2 实现已提交（`189daca`、`29716b0`、`4f0f45c`）；本轮文档修订尚未提交。
10. **424 passed 是离线合成基线。** SqlBot 当前测试基线全部由合成脱敏 fixture 驱动；它证明
    契约与门禁离线行为，不构成真实数据验证，不得在任何文档或进度中表述为真实验证。
11. **Phase 2G V3 输入必须绑定 handoff 内容闭包和批准记录，外部副作用路径必须仓储背书。**
    `ResolveMetadataRequestV3` 不得只接受裸 `FactBindingRequestV3`：请求必须携带
    `HandoffClosureV3` 内容闭包（ruleVersion/requestId/factCode/payloadSha256/batchSha256/
    contractSchemaId/contractSchemaSha256/intake status 快照/精确 payload）**和**
    `ApprovalRecordV3`（九个有序检查组全部通过后，批准关系才有效）。**内容闭包与
    仓储真实性分层**：哈希闭包只能证明内容内部一致，不能证明 batch 真实存在于 MongoDB、
    由 RuleReader 写入、intakeStatus 来自真实 intake 或载荷属于当前批准的真实运行；从
    HTTP/CLI JSON 反序列化的 closure 不得称为 repository-verified attestation。仓储真实性
    由 `RepositoryVerifiedHandoffV3` 承担：一切外部副作用路径（provider 调用、candidate
    store 写入、Phase 4R 真实证据包、未来真实 V3 生成入口）必须在同一次应用调用中按精确
    `ruleVersion + requestId` 经仓储重读 batch、重新执行 V3 intake、选择唯一 request 并与
    携带 closure/context/snapshot 逐字段逐哈希比较；仅”重新计算调用方提供的 hash”不构成
    背书；任一哈希不一致或仓储无 batch 即 provider/store 零调用；客户端自报 ready 不构成
    证明。未携带 `ApprovalRecordV3` 时不得形成 `metadataResolved`；`approvalRecord` 不是
    候选审核记录，不能由客户端自报状态替代。**批准闭包校验失败语义（冻结）**：九个有序检查组
    按顺序 fail fast，任一失败即 `blocked`；失败使用稳定中性 issue code（完整列表见 DEV §3.5），
    不泄漏 ID 实际值、哈希实际值、私有对象/字段或 context/snapshot/approval 原始载荷；
    M2 报告只记录 code。`validate_approval_closure_v3` 成功只证明内容闭包内部一致，不证明批准真实性，
    真实 M3/M6 必须通过独立受信批准来源核验（DEV §3.6）。M3 生成服务采用**方案 a（冻结）**：直接依赖
    只读 handoff repository，在 provider 前于服务自身完成仓储重读与背书。纯离线测试可携带
    完整合成 closure，但结论不构成真实仓储证明；candidate、静态报告与证据包中的
    `batchSha256`/`payloadSha256` 是追溯引用而非真实性证明，真实运行证据还必须记录
    repository verification 阶段和结果；不得用 V2 `BindingGapReport` 替代。
12. **下游对象版本独立演进。** `FactBindingRequest.contractVersion="3.0.0"` 只属于上游交接
    载荷；`HandoffClosureV3`、context、snapshot、`ApprovalRecordV3`、解析请求、解析报告、生成请求、
    候选、静态报告与存储文档包装各自拥有独立 `schemaVersion` 并独立演进，不因消费 FBR 3.0.0
    机械继承版本号；`ruleRef.schemaVersion` 是唯一表达所引用 RuleReader 规则契约版本的字段；
    V2/V3 隔离依赖类型与引用闭包，不依赖版本号数字。版本首值与理由见
    [DEV-20260906-04](../architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md)
    4.3 节版本表。共 **12 个独立顶层契约对象**；`RepositoryVerifiedHandoffV3` 是内部受信结果，
    无 wire `schemaVersion`。
13. **运行状态顺序保持既有 Phase 4R 状态机。** 运行顺序冻结为
    `candidateGenerated → candidateStored → staticPassed`：生成成功后先 insert-only 保存
    候选并记录存储 outcome，再对同一 candidate 运行 Phase 4，静态 blocked 候选仍保有审计
    记录，不得删除、覆盖或修改；存储成功不代表静态通过。实施里程碑顺序（M4 静态门禁代码
    可先于 M5 存储交付）不构成对运行顺序的更改，因此无需新增改变运行顺序的决策。
14. **固定私有资料中的现有事实不再重复向用户确认。** 用户于 2026-09-09 明确确认：固定
    `RuleDataReferences` bundle 中 Excel 与其他资料实际存在的数据、字段、对象、枚举、关系、
    规则结构和执行顺序均为本项目已确认事实；资料内滞后的“待确认”“pending”等状态标签不
    推翻实际内容。实现前必须先固定 commit、bundle digest 与来源文件 SHA-256，再由
    `businessRuleReview` 将规则事实转成新版本 handoff，由 `metadataReview` 将物理事实转成
    版本化 context/snapshot/grant。该确认消除重复索取事实的需要，但不直接创建运行时授权、
    不批准候选，也不授权数据库或 provider 调用。只有固定资料中确实不存在的信息才能登记为
    待补充；资料间差异优先按用户指定的主来源与现有证据完成版本化裁决，不得要求用户重填已经
    存在的内容，也不得由模型猜测。

## 2. 责任归属

- `businessRuleReview`：V3 五阶段/优先级/多 outcome 规则语义与 handoff 完备性 owner 不变；
- `metadataReview`：V3 逻辑字段到物理表列的授权 owner 不变，但批准载体改为 V3 上下文与
  V3 快照契约；V2 批准载荷不自动等同于 V3 批准；
- `sqlBot`：V3 下游契约设计、确定性解析、门禁与存储实现；对 V2 行为零变化负责；
- 运维/用户：上游契约提交与哈希复核授权、真实落库授权、V3 存储集合授权与账号隔离确认。

固定资料的事实确认与运行授权是两件事：前者已经完成，后者仍按每个真实副作用步骤单独门禁。

责任 owner 只表示复核与决策责任，不授予修改 RuleReader 审计记录、生产元数据或 SQL 的权限。

## 3. 对既有决策的影响

- 不替代 [BIZ-20260906-02](BIZ-20260906-02-fact-binding-v3-authority-boundary.md)：该决策
  管辖 intake 通道；本决策把同样的双通道隔离、禁止降级与 fail-closed 原则延伸到下游
  （Phase 2G/3/4/存储）。
- 修订 [BIZ-20260906-01](BIZ-20260906-01-agent1-metadata-review-sqlbot-boundary.md) 第 5 条
  的推进语义：SqlBot intake 升级完成（`b3e3d35`）只满足该条的“升级”半句；“升级完成并通过
  验收”现在明确要求本决策与
  [REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
  的下游链验收，V2 语义的端到端闭环表述不适用于 V3 规则版本。
- [BIZ-20260827-02](BIZ-20260827-02-project-metadata-authorization-boundary.md) 的授权原则
  （快照只描述物理事实、grant 才授权）在 V3 下原样适用；改变的只是契约载体。

## 4. 需要确认但不影响本决策成稿的事项

开放问题及其阻断的里程碑见
[DEV-20260906-04](../architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md)
第 14 节。任一未决问题在其阻断的里程碑前必须由对应 owner 明确；未确认时保持阻断。

> **2026-09-10 历史观察（保留用于审计）**：当时 M1 契约层已完成（`completed`），
> 五个实现文件全部完成，全量 610 passed。M2 元数据解析仍为纯计算
> （不读取仓储、不注入 provider）；真实仓储背书位于后续有副作用的
> 应用服务路径（M3 生成前完成）。
>
> **当前状态**：M1 已完成（`completed`，提交 `189daca`、`29716b0`）；
> M2 为 `in_progress`（提交 `4f0f45c`：输入门禁 `_validate_resolution_input_v3`、
> column grant 解析 `_resolve_column_grant_v3`、字段/实体键授权闭包
> `_resolve_fields_and_entity_keys_v3`、filters 物理字段解析
> `_resolve_filters_v3`、aggregation 授权引用解析 `_resolve_aggregation_v3`、
> timeRange 时间字段授权解析 `_resolve_time_range_v3`、
> 指定 join grant 物理授权解析 `_resolve_join_grant_v3` 已完成；
> 剩余 多关系连接选择、完整 join 输出、公开 `resolve_metadata_v3` 编排及报告组装；
> `entityType`/`grain` 映射待澄清，见 DEV §14 开放问题第 7 项）。
> 辅助函数完成不等于完整解析服务完成，M2 不标记 `completed`。
