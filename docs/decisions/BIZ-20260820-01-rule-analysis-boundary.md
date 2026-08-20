# BIZ-20260820-01：规则分析能力边界与显式阻塞

- 状态：`approved`
- 创建日期：2026-08-20
- 影响需求：[REQ-20260820-01](../requirements/REQ-20260820-01-rule-change-analysis.md)
- 延续决策：[BIZ-20260819-01](BIZ-20260819-01-agent2-role-alignment.md)

## 决策

1. 恢复 canonicalization、内容哈希和结构化 diff，作为与存储无关的确定性规则审计能力。
2. 此能力不改变事实级 Agent 2 主流程，也不表示 SqlBot 已重新承担整规则异常集合 SQL 生成。
3. canonical 内容哈希覆盖完整规则业务载荷；对象键顺序和表示空白不参与语义，条件数组顺序及逻辑树结构参与语义。
4. 条件身份只使用文档内稳定且唯一的 `condition_id`。重复 ID 不选择“第一个”或“最后一个”，必须拒绝整个分析。
5. 无基线是合法冷启动分析状态，明确记录 `baseline_mode=none`；当前条件可报告为新增，但不得构造虚假基线哈希或逻辑树。
6. 下游整规则 SQL 规划必须同时拿到业务明确选择的异常集合语义和版本化真实 Schema 快照。任一缺失都返回 `blocked`。
7. 允许的异常集合语义只有显式枚举值；系统不默认选择 `unreleased AND NOT(release_eligible)` 或 `unreleased AND release_eligible`。
8. 本切片不访问 MongoDB。基线的选择、读取和持久化留给后续仓储端口与适配器。

## 原因

规则规范化与差异比较不需要生产数据语义，可以安全、离线且确定性地实现；异常集合方向和真实 Schema 会直接改变 SQL 结果及访问范围，缺失时继续推进会违反默认拒绝原则。
