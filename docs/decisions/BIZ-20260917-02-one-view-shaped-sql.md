# BIZ-20260917-02：最终产物是一条视图形态的只读 SQL

- 状态：`approved`（一条总 SQL 仍有效）；判定公式已改由 [BIZ-20260917-03](BIZ-20260917-03-optimization-plan-authority.md) 指定为优化方案，不再要求与现行视图 OR 支等价。
- 影响需求：[REQ-20260917-01](../requirements/REQ-20260917-01-view-equivalent-sql.md)。
- 部分替代：[BIZ-20260917-01](BIZ-20260917-01-agent2-mongo-fact-generation.md) 第 2 条（“不生成整规则放行清单 SQL”）及 [REQ-20260917-02](../requirements/REQ-20260917-02-agent2-fact-sql-generation.md) 的对应非目标。
- 不替代：[BIZ-20260916-02](BIZ-20260916-02-view-semantic-baseline.md) 的权威分工（Mongo 驱动生成，视图作取数基准，禁止用固定释放视图代替生成）。
- 不恢复：[BIZ-20260819-01](BIZ-20260819-01-agent2-role-alignment.md) 已取消的整规则异常集合默认语义。

## 决策

1. 当前这份报告释放模板的**最终可审核产物**是一条单语句、只读、参数化的 SQL Server 候选，结果形态对齐参考视图：一次查出应处理的实体及其放行相关列，而不是 18 个独立 `fact_value` 查询。
2. 单事实 Agent2 模板仍可生成、可复用，只作组合输入或对照，**不能再当作本任务的交付物**。
3. 生成仍由 MongoDB 精确规则版本驱动。参考视图只用于核对关系、连接、编码和计算；**禁止** `SELECT` 固定释放视图，也**禁止**把视图文本复制进候选后声称已生成。
4. 不恢复历史 `unreleased AND NOT(release_eligible)` 等异常集合默认语义；结果是放行清单还是其补集，必须由版本化结果契约写明，不得推定。
5. 不得把当前 18 条 FactBindingRequest 现拼成一条 SQL。handoff 没有完整 AND/OR/优先级树，复合到款/合同/定时/合并组仍是不透明布尔；缺少展开后的规则树、JOIN/列授权和组合契约时必须阻断。
6. [REQ-20260917-02](../requirements/REQ-20260917-02-agent2-fact-sql-generation.md) 的 COUNT / EXISTS / 附加比较只是组合 SQL 需要的生成能力，不是本阶段完成标准。

## 当前阻断（本轮不能产出该 SQL）

- M3/M4 仍拒绝 JOIN、WHERE 中的 OR/NOT、通用聚合与算术。
- 3.1.0 完整交付读取与组合契约见 [BIZ-20260920-01](BIZ-20260920-01-complete-delivery-intake.md)；`usages` 仍不能单独重建规则树。
- 2026-09-05 handoff 不得作为 `optimization-plan-generation` 或 `sql-compilation` 输入。
- 测试环境现有批准只覆盖所选单事实的两列，不能扩大为完整查询授权。
- 组合计划已冻结；离线确定性编译见 [REQ-20260920-02](../requirements/REQ-20260920-02-view-shaped-sql-compile.md)。
  无映射时仍不能对真实包生成 SQL；合成候选也**不是**已审核发布的总 SQL。
