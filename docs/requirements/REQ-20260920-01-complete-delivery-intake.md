# REQ-20260920-01：3.1.0 完整交付 intake 与一条视图形态 SQL 组合契约

- 状态：`completed`（intake 与组合契约冻结；总 SQL 未生成）
- 创建日期：2026-09-20
- 来源：用户要求打通向 Agent1 优化方案 3.1.0 完整交付的消费入口，为“一条视图形态只读 SQL”做前置；本轮不生成真实总 SQL。
- 前置需求：[REQ-20260917-01](REQ-20260917-01-view-equivalent-sql.md)、
  [REQ-20260906-03](REQ-20260906-03-fact-binding-v3-intake.md)
- 业务决策：[BIZ-20260920-01](../decisions/BIZ-20260920-01-complete-delivery-intake.md)、
  [BIZ-20260917-02](../decisions/BIZ-20260917-02-one-view-shaped-sql.md)、
  [BIZ-20260917-03](../decisions/BIZ-20260917-03-optimization-plan-authority.md)
- 技术方案：[DEV-20260920-01](../architecture/DEV-20260920-01-complete-delivery-intake.md)
- 上游接口：RuleAgent [DEV-20260917-02](../../../RuleAgent/docs/DEV-20260917-02-sqlbot-complete-delivery-intake.md)
- 可行性评估：[DEV-20260917-02](../architecture/DEV-20260917-02-agent1-optimization-handoff-feasibility.md)

## 背景

SqlBot 现有 V3 intake 只读 `fact_binding_handoff_batches_v3` 中的请求批次，`usages` 只是追溯坐标。
2026-09-05 `REPORT_RELEASE_ALL_001@20260905T172407000000Z-f285643e5b2b-82dbd05a800a`（18 条）
不得再作为优化方案生成或 SQL 编译输入。Agent1 已离线交付 Schema 3.1.0 完整闭包（tree + catalog +
result + batch），但 SqlBot 尚未实现 `selectDelivery` 门禁，也尚未冻结一条视图形态 SQL 的组合契约。
单事实 `generate-v3` 仍可保留，不能作为本目标交付物。

## 目标

1. 按精确 `ruleVersion` 与请求 `purpose` 选择并校验 3.1.0 完整交付闭包，读到五阶段规则树，而不是 usages 坐标。
2. 解析 `FactBindingRequest 3.1.0`（`cardinality=scalar|set`、双重来源哈希、运行参数声明）。
3. 冻结“一条视图形态、只读、参数化 SQL Server 候选”的结果/组合契约；本轮结束时不得声称总 SQL 已生成。

## 范围

- SqlBot 独立 consumer、只读完整交付读取、用途/哈希/payload 门禁、组合计划定义。
- 默认测试使用合成 fixture。离线交付包只做身份/哈希核验，不把私有原文写入公开仓库。

## 非目标

- 生成、校验或执行真实总 SQL；调用在线模型；连接 SQL Server；`SELECT` 或复制现行释放视图。
- 写入 MongoDB；把 unresolved 映射当成已批准；继承旧两列测试批准为完整查询授权。
- 修改 RuleAgent 生产代码；在 SqlBot 补 Agent1 公式；把 description 中的 Java/公式写成 SQL。
- 删除或绕过现有 M3/M4 对 JOIN / WHERE OR-NOT / 通用聚合与算术的拒绝门禁。
- 把 2026-09-20 公式树 3.0.0（`generated-rules/report-release-v3-formula-20260920-fix/`）升格为可消费交付。
- Schema v6 真实落库、metadataReview、覆盖旧 V3 候选哈希、提交密钥或推送。

## 功能需求

1. `selectDelivery(purpose, ruleVersion)` 读取同一版本的 tree + catalog + result + batch。
2. 哈希闭包：`sourceFile` / `parseInput` / `catalogDigest` / `candidate` / `result` / `batch` 不一致则失败。哈希算法引用 Agent1 `selectDelivery` 门禁，不在 SqlBot 发明第二套规则。
3. purpose 不匹配、缺 catalog/candidate payload、`executable=true`、旧 2026-09-05 版本用于
   `optimization-plan-generation` 或 `sql-compilation` → 确定性失败；不得回退旧 batch。
4. `evaluationDate` / 截止日 / 上线日是规则参数，不是数据库列。
5. 成员集合事实返回实体键列表；全员量词留在规则侧，禁止生成“组成员 eligible 布尔列”。
6. 组合计划写清规则树如何组合事实查询；无映射时阻断 `METADATA_REVIEW`。JOIN/OR/算术的确定性编译见
   [REQ-20260920-02](REQ-20260920-02-view-shaped-sql-compile.md)，不得删除单事实 M3/M4 门禁。

## 验收标准

1. 指定 3.1.0 报告 `ruleVersion` 时读到完整五阶段树，而不是 usages 坐标。
2. 指定 2026-09-05 版本做 `sql-compilation` 时确定性失败。
3. 缺闭包或缺 payload 时 `consumable=false` 或等价阻断，不得回退旧 batch。
4. 离线合成测试通过；Ruff 按仓库惯例通过。
5. PROG 记录命令、结果、遗留：metadataReview、Schema v6 落库、SQL Server 差分、真实总 SQL 发布均未做。
6. 完成不等于“handoff 已写入”或“总 SQL 已生成”。
