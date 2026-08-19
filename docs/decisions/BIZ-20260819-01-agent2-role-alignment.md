# BIZ-20260819-01：Agent 2 职责与双服务边界

- 状态：`approved`
- 创建日期：2026-08-19
- 影响需求：[REQ-20260819-01](../requirements/REQ-20260819-01-fact-binding-intake.md)
- 替代决策：[BIZ-20260818-01](BIZ-20260818-01-rule-and-sql-lifecycle.md) 中整规则异常集合与发布范围

## 决策

1. RuleReader 和 SqlBot 是独立服务；RuleReader 是 Agent 1，SqlBot 是 Agent 2。
2. SqlBot 按单个 `factCode` 生成候选 SQL 模板，不把完整规则翻译为异常集合 SQL。
3. 跨服务 JSON 统一 camelCase 和显式契约版本。
4. SqlBot 使用 Python `3.11.9`、LangGraph、DeepSeek 和现有 `uv` 包管理方式。
5. 首个 SQL 方言是 SQL Server；会话临时表默认禁用。
6. SqlBot 只消费 RuleReader Schema `2.0.0` 导出的请求，拒绝 Schema `1.0.0`。
7. `derived` 事实由确定性 Provider 计算，不进入 SQL 模板生成。
8. SqlBot 后续可以与 RuleReader 使用同一 MongoDB 实例，但必须拥有独立集合和 migration，不能直接修改 `rule_versions`。
9. LLM 输出始终是 `candidate`；SQL AST、安全、试跑和人工审核完成前不可发布或执行。

## 被取消的暂定方向

- 不再以 `unreleased AND NOT(release_eligible)` 或 `unreleased AND release_eligible` 作为 Agent 2 的核心输出集合。
- 不再要求一个 SQL 模板覆盖整棵规则条件树。
- 不再由 SqlBot 保存或激活正式规则版本。

上述能力如未来仍有业务需要，必须以独立诊断/查询 REQ 重新评估，不能混入事实绑定 Agent。
