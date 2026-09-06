# BIZ-20260906-01：Agent 1、metadataReview、SqlBot 与运维责任边界

- 状态：`proposed`
- 创建日期：2026-09-06
- 来源：用户要求在下一阶段计划书中明确 Agent 1、metadataReview、SqlBot 和运维的责任边界
- 影响需求：[REQ-20260906-02](../requirements/REQ-20260906-02-real-upstream-handoff-evidence-loop.md)
- 技术方案：[DEV-20260906-02](../architecture/DEV-20260906-02-real-handoff-evidence-loop-orchestration.md)
- 前置决策：[BIZ-20260827-01](BIZ-20260827-01-fact-binding-v2-authority-boundary.md)、
  [BIZ-20260827-02](BIZ-20260827-02-project-metadata-authorization-boundary.md)、
  [BIZ-20260828-01](BIZ-20260828-01-v2-candidate-authority-boundary.md)、
  [BIZ-20260828-02](BIZ-20260828-02-rulereader-handoff-read-boundary.md)、
  [BIZ-20260905-01](BIZ-20260905-01-restricted-sqlserver-validation-boundary.md)

## 1. 决策

本决策细化既有权威边界在“真实上游交接与端到端候选证据闭环”阶段的应用，不替代、不放宽任何
前置决策。

1. **Agent 1 是业务规则、事实、筛选、聚合和时间语义 owner。** 规则正文的解释、required facts、
   实体键语义、筛选全集、聚合口径、时间范围和 condition usage 语义只能由 Agent 1（及其业务规则
   复核 owner `businessRuleReview`）权威决定。SqlBot 不修复、不补全、不覆盖这些语义。
2. **metadataReview 是物理表列、实体键和 join 授权 owner。** 逻辑字段到物理表列的绑定授权、
   entity-key 授权、join 授权、`GovernedMetadataSnapshot` 的产生与批准、`ProjectBindingContextV2`
   的批准，全部由 metadataReview 负责。SqlBot 只做确定性校验与解析。
3. **SqlBot 只消费精确版本。** SqlBot 只接受调用方显式给出的精确 ruleId/ruleVersion、精确上下文
   版本与精确快照版本，不选择“最新”“最接近”或近似匹配，不代上游修复任何语义缺口。
4. **V3 blocked candidate 不能写入 V2 rule_versions。** `rule_structure_candidates_v3` 中状态为
   `validatedBlockedCandidate` 的记录（16 blocking）是 Agent 1 侧的候选恢复记录；它不是已批准
   规则版本，不得以任何形式转换、包装或提升为 V2 `rule_versions` 记录或 V2 handoff。
5. **V3 不能被无证明地转换为 FactBindingRequest 2.0.0。** `FactBindingRequest 2.0.0` 的 `ruleRef`
   固定 `schemaVersion=2.0.0`，结构上不能精确引用 V3 规则；Agent 1 侧已批准的路径 B 决策（其仓库
   `BIZ-20260905-02`）选择冻结 `FactBindingRequest 3.0.0` 并禁止静默降级。若该决策维持，SqlBot
   必须以独立 REQ/BIZ/DEV 升级 intake；在升级完成并通过验收前，端到端真实闭环保持阻断。
6. **飞书和私有 bundle 仅为非权威参考。** 飞书文档、私有 `RuleDataReferences` bundle、工作簿、
   视图 SQL 和人工备注都是不可信候选证据；它们不能创建规则版本、handoff、上下文、快照、grant、
   参数值或任何授权。
7. **实时数据库 catalog 不能创建授权。** Phase 5A 的 catalog 探测或任何未来实时 catalog 读取
   只能证明“当前目标仍匹配已批准快照”，不能生成新快照、新 grant 或扩大授权范围。快照的物理
   采集是独立授权任务，批准是 metadataReview 的人工决定。
8. **candidate persistence 不构成审批。** 候选落库（insert-only、同 hash 幂等）只是证据留存；
   `reviewStatus` 保持 `pending`。落库记录不得被解释为人工审核、批准或可执行。
9. **static passed 不构成执行许可。** Phase 4 `passed` 只证明离线 AST 策略满足；它不证明 SQL
   Server 可接受、性能可接受、业务结果正确，也不改变候选状态。整个闭环阶段所有产物固定
   `executable=false`。
10. **真实在线 provider 调用需要用户当次明确授权。** 每一次真实模型调用（含生成失败重试产生的
    调用）都必须在用户针对当次任务的明确授权范围内；未授权时编排服务在 provider 步骤前停止。
11. **当前阶段不连接 SQL Server。** 本阶段不执行 Phase 5A 冒烟、不读取 catalog、不获取执行计划、
    不试跑；SQL Server 相关验证的全部能力保持独立开关与独立授权。
12. **人工审核与发布仍属于 Phase 6。** 本阶段不实现审核工作流、批准/驳回状态机或发布路径；
    `metadataResolved`、`candidateStored`、`staticPassed` 都不推进 Phase 6 状态。
13. **如果 Agent 1 交接仍有 blocking，本阶段必须停止。** 任一 handoff 记录损坏、blocking
    uncertainty 存在或 batch 不完整时，整批 `blocked`，provider 调用次数为 0，不生成任何候选。
14. **一个坏 handoff 使整个精确规则版本 batch 阻断。** 不允许“跳过坏记录、用剩余记录继续”；
    整批 fail closed 与 [BIZ-20260828-02](BIZ-20260828-02-rulereader-handoff-read-boundary.md)
    一致。
15. **所有内容修订创建新版本，禁止原地覆盖。** 规则版本、handoff、上下文、快照和候选都是不可变
    审计记录；任何内容变化必须创建新版本或新候选（不同 content hash），禁止 update/replace。

## 2. 责任矩阵

| 职责 | Agent 1 / businessRuleReview | metadataReview | SqlBot | 运维 / 用户 |
| --- | --- | --- | --- | --- |
| 规则语义、事实、筛选、聚合、时间 | 唯一所有者 | 复核输入 | 校验、保留、阻断 | — |
| 规则版本与 handoff 落库（MongoDB） | 唯一写权限 | — | 只读消费 | 授权写入 |
| `ProjectBindingContextV2` 产生与批准 | — | 所有者 | 校验、重算哈希 | 授权确认 |
| `GovernedMetadataSnapshot` 采集与批准 | — | 所有者 | 校验、重算哈希 | 授权采集 |
| 物理列 / 实体键 / join 授权 | — | 唯一所有者 | 确定性解析 | — |
| 候选生成调用（在线 provider） | — | — | 编排与门禁 | 当次明确授权 |
| 候选 insert-only 持久化 | — | — | 编排与门禁 | 存储账号与授权 |
| Phase 4 静态门禁 | — | — | 所有者 | — |
| 运行证据包与脱敏 PROG | — | — | 产生 | 保存位置与保留期决策 |
| 人工审核与发布 | — | 参与审核 | 不负责 | Phase 6 另行规划 |
| MongoDB 基础设施（实例、账号、网络） | 提供只读账号 | — | 不自建 | 所有者 |

责任 owner 只表示复核与决策责任，不授予修改他人审计记录、生产元数据或 SQL 的权限。

## 3. 对既有决策的影响

- 不替代 [BIZ-20260827-01](BIZ-20260827-01-fact-binding-v2-authority-boundary.md)、
  [BIZ-20260827-02](BIZ-20260827-02-project-metadata-authorization-boundary.md)、
  [BIZ-20260828-01](BIZ-20260828-01-v2-candidate-authority-boundary.md)、
  [BIZ-20260828-02](BIZ-20260828-02-rulereader-handoff-read-boundary.md)；本决策把这些边界延伸到
  真实闭环场景，并新增第 4、5 条对 V3 记录与契约版本的处理决定。
- 与 Agent 1 侧决策（其仓库 `BIZ-20260905-02`，`APPROVED_PATH_B`）的关系：本决策承认其为上游
  契约权威；SqlBot 侧的对应 intake 升级必须另行立需求，不能引用兄弟仓库文档作为实现依据。
- [BIZ-20260905-01](BIZ-20260905-01-restricted-sqlserver-validation-boundary.md) 的 Phase 5 边界
  不变；本阶段完成后才具备进入 Phase 5B 的真实候选对象。

## 4. 需要确认但不影响本决策成稿的事项

开放问题及其阻断的里程碑见
[DEV-20260906-02](../architecture/DEV-20260906-02-real-handoff-evidence-loop-orchestration.md)
第 13 节。任一未决问题在其阻断的里程碑前必须由对应 owner 明确；未确认时保持阻断，不得以合成
数据、参考资料或默认值代替。
