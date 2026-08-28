# BIZ-20260828-01：V2 候选生成权威与不可执行边界

- 状态：`approved`
- 创建日期：2026-08-28
- 来源：用户要求独立完成 V2 候选生成输入对齐后再实施 Phase 4
- 影响需求：[REQ-20260828-01](../requirements/REQ-20260828-01-v2-candidate-generation-input.md)
- 前置决策：[BIZ-20260827-02](BIZ-20260827-02-project-metadata-authorization-boundary.md)

## 决策

1. V2 生成输入必须携带完整 Phase 2G request 与 report；只传 ID、只传解析列、选择“最新”上下文或
   快照、包装 V1 context 都无效。
2. 生成服务必须重算 Phase 2F/2G。只有精确一致的 `metadataResolved` report 才可进入 provider；模型
   和调用者不能声明或伪造 readiness。
3. Prompt 只消费 RuleReader 权威逻辑语义与 Phase 2G 明确授权结果。source/mapping candidate、Prompt、
   provider/model 声明、本地工作簿和模型输出都不授予权限。
4. V2 使用独立 Prompt、生成请求和候选版本，禁止转为 V1 或复用 V1 readiness/Prompt/candidate。
5. 模型只能提出 SQL 与最小声明。状态、审核、执行标志、引用、哈希和 provenance 由应用侧可信组装。
6. `declaredObjects` 与 `declaredUsageCoverage` 只是候选声明；Phase 4 必须从 AST 重算真实对象、参数、
   结果和 usage coverage，不能信任声明。
7. 每个 V2 候选固定 `candidate / executable=false / pending`。生成服务不能批准、拒绝、持久化、发布、
   执行或修改候选审批状态。
8. SQL 文本或任一审计字段变化都产生不同内容哈希；禁止批准后原地改写。
9. 本需求交付仅使用固定离线 provider，不调用在线模型，不连接 SQL Server，不读取本地参考工作簿。
10. V2 候选需求完成后才允许复核并实施 Phase 4；Phase 4 仍不连接或执行 SQL Server。

## 责任边界

- `businessRuleReview` 继续拥有 RuleReader 事实、筛选、聚合、时间及 blocking uncertainty；
- `metadataReview` 继续拥有项目 grant、快照、物理列和 join 授权；
- `sqlBot` 负责完整输入重算、Prompt 投影、严格输出解析、引用/哈希闭包和候选不可执行状态；
- 模型不拥有任何审批、授权或安全结论。
