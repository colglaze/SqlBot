# BUG-20260906-01：V3 intake 完成不构成 Phase 4R 下游链路就绪（契约断点）

- 状态：`proposed`
- 严重度：高（阻断 Phase 4R 真实闭环推进；属可复现的契约/设计缺口，非运行时安全放行缺陷）
- 发现日期：2026-09-06
- 关联 REQ：[REQ-20260906-02](../requirements/REQ-20260906-02-real-upstream-handoff-evidence-loop.md)、
  [REQ-20260906-03](../requirements/REQ-20260906-03-fact-binding-v3-intake.md)
- 修复需求：[REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
- 关联决策：[BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md)
- 关联设计：[DEV-20260906-04](../architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md)

## 现象

Phase 4R 现行文档（REQ-20260906-02 / BIZ-20260906-01 / DEV-20260906-02）把“SqlBot V3 intake
离线实现完成”当作真实上游交接链路的充分条件，并把后续阶段全部锚定在 V2 契约上。V3 intake
（提交 `b3e3d35`）只解决读取与契约门禁：`FactBindingHandoffIntakeBatchV3` /
`FactBindingRequestV3` 在 intake 出口之后没有任何可进入的下游步骤。可复现的断点如下：

1. `ResolveMetadataRequestV2.binding_request` 的类型固定为 `FactBindingRequestV2`
   （`domain/project_bindings_v2.py`）；`FactBindingRequestV3` 不能传给
   `ResolveMetadataRequestV2`，Phase 2G 对 V3 请求没有入口。
2. V3 `usages` 的 `stage`/`ruleCode`/`priority`/`outcome` 语义（五阶段、首个命中优先级、七个
   outcome）不能裁剪成 V2 的 `expressionSide`/`operator` 表达式侧视角；任何此类裁剪都违反
   [BIZ-20260906-02](../decisions/BIZ-20260906-02-fact-binding-v3-authority-boundary.md) 的
   禁止降级决策。
3. 把 V3 请求包装、转换或降级为 `FactBindingRequestV2` 的路径被上游决策
   （RuleAgent `BIZ-20260905-02`，`APPROVED_PATH_B`）与 SqlBot 决策双重禁止；不存在合法的
   V3→V2 桥。
4. 现有 V2 candidate（`SqlTemplateCandidateV2`）、静态报告（`SqlStaticValidationReportV2`）
   与 Phase 5A 输入（`ValidateSqlServerRequestV2.static_validation_request`）均绑定 V2
   generation request（`GenerateSqlCandidateRequestV2`），而后者内嵌 V2 resolution request
   与 V2 resolution report；V3 请求即使完成元数据解析也无法进入生成、门禁与验证。
5. `CandidateTemplateStore.save` 的参数类型固定为 `SqlTemplateCandidateV2`；V3 候选没有
   合法存储通道。
6. Phase 4R 现行文档（REQ-20260906-02 第 7.1/8.9 节、DEV-20260906-02 第 3 节模块复用表）仍
   直接引用 `analyze_binding_gaps_v2`、`resolve_metadata_v2` 与 V2 候选链，未为 V3 请求定义
   任何下游契约。

## 复现方式（静态可复核）

在 `main`（HEAD `b3e3d35`）工作区执行静态审查即可复现，无需运行任何代码：

- `ResolveMetadataRequestV2`（`src/release_sql_bot/domain/project_bindings_v2.py`）仅接受
  `FactBindingRequestV2`；
- `GenerateSqlCandidateRequestV2`（`src/release_sql_bot/domain/sql_candidates_v2.py`）仅内嵌
  `ResolveMetadataRequestV2` + `BindingResolutionReportV2`；
- `ValidateSqlCandidateRequestV2`（`src/release_sql_bot/domain/sql_validation.py`）仅内嵌
  `GenerateSqlCandidateRequestV2`；
- `ValidateSqlServerRequestV2`（`src/release_sql_bot/domain/sqlserver_validation.py`）仅内嵌
  `ValidateSqlCandidateRequestV2` + `SqlStaticValidationReportV2`；
- `CandidateTemplateStore.save`（`src/release_sql_bot/application/ports/candidate_store.py`）
  仅接受 `SqlTemplateCandidateV2`；
- V3 契约（`domain/fact_bindings_v3.py`）字段集与 V2 互不相交，双向严格 `extra=forbid`
  解析必然失败（`tests/contract/test_fact_binding_v3_contract.py` 已有双向拒绝测试）。

因此“V3 batch 通过 intake（`readyForMetadataResolution`）”之后，链路在进入 Phase 2G 前必然
停止：这不是运行时故障，而是下游契约缺失。

## 期望行为

`FactBindingRequestV3` 拥有独立的、语义完整的下游契约链：V3 项目授权上下文与元数据快照 →
`ResolveMetadataRequestV3`/`BindingResolutionReportV3` → `GenerateSqlCandidateRequestV3`/
`SqlTemplateCandidateV3` → `ValidateSqlCandidateRequestV3`/V3 静态报告 → V3 候选存储。V3 的
五阶段/优先级/多 outcome 语义在全链路完整保留；禁止任何 V3→V2 转换、裁剪或降级。

## 实际行为

V3 链止步于 intake；Phase 4R 文档把该状态描述为可继续推进真实闭环，形成“intake 已完成 = 链路
就绪”的过度声明。在该缺口修复前实现 Phase 4R 编排服务或生成真实候选，都会迫使实现者临时
发明未经评审的桥接（转换、裁剪或复用 V2 契约吞掉 V3 语义），这正是禁止路径。

## 影响与安全风险

- 交付影响：Phase 4R 真实闭环被阻断；`FactBindingRequest 3.0.0` 请求无法形成候选、静态证据
  或 Phase 5 验证对象。
- 安全影响：无放行风险。现有 V2 链对 V3 载荷 fail closed（严格解析拒绝），不存在把 V3 误当
  V2 执行的运行时路径；本缺陷是“文档过度声明 + 下游契约缺失”，不是安全门禁旁路。
- 反向风险：若忽视本缺陷继续按 Phase 4R 现行文档实现编排服务，将诱导实现者写出违反
  [BIZ-20260906-02](../decisions/BIZ-20260906-02-fact-binding-v3-authority-boundary.md)
  第 3 条的降级桥，属于边界违规，必须在实现前修复文档与契约规划。

## 临时规避

无运行时规避需求（链路本就 fail closed）。流程上的正确处理：以
[REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md) 建立独立
V3 下游契约链并按 [DEV-20260906-04](../architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md)
的里程碑推进；在该链完成前，不得实现 Phase 4R 编排服务，也不得生成任何真实候选。

## 根因

- REQ-20260906-02 / DEV-20260906-02 编写时（2026-09-06 上午），V3 intake 尚未立项，文档只能
  以 V2 链为锚并把契约版本决策登记为阻断项；当日下午 V3 intake 以独立 REQ/BIZ/DEV 立项并
  实现（提交 `b3e3d35`）后，Phase 4R 文档没有同步修订，残留“intake 升级完成即可继续”的
  充分条件表述。
- V3 intake 的范围刻意收窄为“读取 + 契约门禁”（REQ-20260906-03 第 9 节把下游消费列为范围
  外），但没有同时登记“下游契约链缺失”这一阻断项，导致范围外交接点悬空。

## 修复方向与验收

修复由 [REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md) /
[BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md) /
[DEV-20260906-04](../architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md) 承载
（2026-09-06 人工审查后收紧）：

1. 冻结 handoff **双层契约**：`HandoffClosureV3` 内容闭包（ruleVersion/requestId/factCode/
   payloadSha256/batchSha256/contractSchemaId/contractSchemaSha256/intake status 快照/
   精确 payload）只证明内容内部一致；`RepositoryVerifiedHandoffV3` 仓储背书由受信应用服务
   在同一次调用中经仓储重读 batch、重执行 intake、选择唯一 request 并逐字段逐哈希比较后
   构造；一切外部副作用路径（provider/store/真实证据包/未来真实生成入口）必须先背书，任一
   哈希不一致或仓储无 batch 即零调用；客户端自报 ready 不构成证明；反序列化 closure 不得
   称为 repository-verified attestation；不得用 V2 `BindingGapReport` 替代；
2. 冻结 usage 六元组（`stage`/`ruleCode`/`priority`/`conditionId`/`conditionPath`/
   `outcome`）追溯语义与唯一性事实：consumer 现行重复门禁为四元组
   `(stage, ruleCode, conditionId, conditionPath)`，同一 `conditionId` 可在不同
   stage/ruleCode/conditionPath 中多次出现，全部保留、不得合并；traceability linkage
   （应用从权威请求复制六元组并计算唯一命名的 `usage_traceability_sha256` 摘要）与
   AST-derived evidence（SQL 只能证明参数/对象/列/join/fact_value 来源等位置
   事实，不能证明规则树业务语义）分界明确；Phase 4 检查命名为 usage traceability /
   reference integrity；候选本体保留完整六元组或可解析到完整不可变载荷的强引用；
3. 冻结 V3 授权上下文（一致的 insert-only 载荷 + 独立生命周期载体方案）与元数据快照契约
   决策（复用或独立的证据要求）；
4. 冻结下游契约版本表：各下游对象 `schemaVersion` 独立演进，不机械继承 FBR 3.0.0；
5. 冻结 `ResolveMetadataRequestV3`/`BindingResolutionReportV3`、V3 候选生成契约、V3 静态
   门禁契约与 V3 候选存储方案；
6. 冻结实施顺序与运行顺序的分离（运行顺序保持既有 `candidateGenerated → candidateStored →
   staticPassed`）；
7. 冻结测试矩阵（V3↔V2 双向拒绝按类型断言、usage 六元组丢失检测、attestation 与各类哈希
   篡改阻断、provider/store 前零调用、V2 全量回归、泄漏检查、版本独立性）与里程碑 M0–M6；
   M0 按“审计子任务/整体收口”两级口径登记，上游 commit 锚点取得前 M0 整体不收口、M1 不
   开始；
8. 本缺陷关闭条件：上述契约为已批准/实施状态且有对应离线测试证明；在此之前 Phase 4R 编排
   实现保持阻断。
