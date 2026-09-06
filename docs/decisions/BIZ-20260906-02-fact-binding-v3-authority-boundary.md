# BIZ-20260906-02：FactBindingRequest 3.0.0 intake 权威边界

- 状态：`approved`
- 创建日期：2026-09-06
- 来源：用户要求为 SqlBot 升级消费 RuleReader `FactBindingRequest 3.0.0` 建立独立边界决策
- 影响需求：[REQ-20260906-03](../requirements/REQ-20260906-03-fact-binding-v3-intake.md)
- 技术方案：[DEV-20260906-03](../architecture/DEV-20260906-03-fact-binding-v3-intake.md)
- 前置决策：[BIZ-20260827-01](BIZ-20260827-01-fact-binding-v2-authority-boundary.md)、
  [BIZ-20260828-02](BIZ-20260828-02-rulereader-handoff-read-boundary.md)、
  [BIZ-20260906-01](BIZ-20260906-01-agent1-metadata-review-sqlbot-boundary.md)
- 上游契约权威：RuleReader 仓库 `BIZ-20260905-02`（`APPROVED_PATH_B`，只读参考）

## 1. 决策

1. **承认上游路径 B 为 V3 契约权威。** RuleReader 已冻结 `FactBindingRequest 3.0.0` 并禁止静默
   降级；SqlBot 建立独立 V3 intake 是对该权威决策的合规响应，不是对上游语义的重新解释。
2. **V3 与 V2 是并列的两条运行时交接通道，V2 不被替代。** `FactBindingRequest 2.0.0` 继续是
   `rule_versions`/`fact_binding_handoffs` 上现行交接契约；`FactBindingRequest 3.0.0` 只服务于
   V3 规则版本（经 `fact_binding_handoff_batches_v3`）。两条通道的模型、intake、仓储方法与 API
   路由完全隔离，互不导入、互不转换。
3. **禁止 V3→V2/V1 降级、字段裁剪或静默转换。** 任何"让旧模型解析新载荷"或"让新载荷伪装成旧
   契约"的实现都是边界违规；旧 V2 草稿（含全部 blocking uncertainty）不得冒充 V3 规则的交接。
4. **上游集合所有权不变。** `rule_versions_v3` 与 `fact_binding_handoff_batches_v3` 由
   RuleReader 拥有数据契约、migration 和唯一写权限；SqlBot 只读，不建索引、不迁移、不写入。
5. **精确版本与整批 fail closed。** SqlBot 只接受调用方显式给出的精确 `ruleVersion`，不选择
   "最新"；batch 内任一坏 wrapper、坏 payload 或闭包失败使整批失败，不允许部分成功或跳过坏记录。
6. **V3 ready 请求中 blocking 结构性不可能。** 上游 Schema 将 uncertainty `impact` 固定为
   `warning`，只有通过全部 readiness 门禁的 ready batch 才会被上游落库；因此 SqlBot V3 intake
   的合法结果只有"已验证的 ready batch"，任何完整性失败都是错误，而不是一种 `blocked` 状态。
   V2 的六类未决语义与 `analyze_binding_gaps_v2` 不适用于 V3 形状，不得移植或裁剪复用。
7. **上游证据以冻结副本 + SHA-256 锁定。** V3 Schema 以 SqlBot 包内固定副本和来源清单登记的
   `2c5e4603…`（完整值见 REQ）锁定；运行时不读取兄弟仓库。2026-09-06 观察时上游 V3 契约文件
   尚未提交（仅存在于工作区），上游提交后必须复核哈希；不一致时另立评审，不得静默替换。
8. **授权边界不变。** V3 warning 不得被提升为授权；`mappingCandidate` 与任何候选证据仍只是
   不可信输入；intake 读取成功不表示可以生成 SQL、已批准或可执行；全部产物固定
   `executable=false`、`reviewStatus=pending`。
9. **intake 入口保持零副作用。** V3 只读入口不装配候选 provider、不调用模型、不触发任何生成
   服务，也不产生任何 MongoDB 写操作；与 [BIZ-20260828-02](BIZ-20260828-02-rulereader-handoff-read-boundary.md)
   的 V2 只读边界一致。
10. **真实数据验证与在线调用另行授权。** 对真实 MongoDB batch 的只读冒烟、以及任何后续基于 V3
    请求的生成，都必须是用户针对当次任务显式授权的独立任务。

## 2. 责任归属

- `businessRuleReview`：V3 规则语义（五阶段、优先级、多 outcome、exclusions）与 handoff 完备性
  由 Agent 1 侧权威决定；SqlBot 不修复、不补全、不覆盖；
- `metadataReview`：V3 逻辑字段到物理表列的绑定授权仍由其独立批准的上下文与快照决定；V3 请求
  携带的 `mappingCandidate` 不改变这一边界；
- `sqlBot`：契约版本、请求身份、batch/payload 哈希、evidence 闭包与自身固定安全门禁；
- 运维/用户：上游集合落库授权、真实读取授权与存储/读取账号隔离确认。

责任 owner 只表示复核与决策责任，不授予修改 RuleReader 审计记录、生产元数据或 SQL 的权限。

## 3. 对既有决策的影响

- 不替代 [BIZ-20260827-01](BIZ-20260827-01-fact-binding-v2-authority-boundary.md)、
  [BIZ-20260828-02](BIZ-20260828-02-rulereader-handoff-read-boundary.md) 与
  [BIZ-20260906-01](BIZ-20260906-01-agent1-metadata-review-sqlbot-boundary.md)；本决策把既有
  只读边界延伸到 V3 batch 通道，并新增第 2、3、6 条对双通道隔离与 ready 语义的处理决定；
- 本决策登记后，[REQ-20260906-02](../requirements/REQ-20260906-02-real-upstream-handoff-evidence-loop.md)
  第 11.2 节"交接契约版本决策未在 SqlBot 侧登记"的阻断项转为"本需求（REQ-20260906-03）完成
  实施与验收"这一可执行前置；
- 上游 `BIZ-20260905-02` 若发生变化（例如恢复可引用 V3 的 2.0.0 导出），必须以其新的已批准
  决策文档为准，本决策相应修订。
