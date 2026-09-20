# REQ-20260920-02：视图形态 SQL 确定性编译（离线、合成映射）

- 状态：`completed`（离线合成编译与独立 AST；非 SQL Server 差分、非审核发布）
- 创建日期：2026-09-20
- 来源：用户要求在硬边界内把还能做的做完。intake 已完成；本切片按
  [REQ-20260917-01](REQ-20260917-01-view-equivalent-sql.md) 第 4 项扩展生成器与独立 AST，
  **不**删除既有单事实 M3/M4 门禁。
- 前置：[REQ-20260920-01](REQ-20260920-01-complete-delivery-intake.md)、
  [BIZ-20260920-01](../decisions/BIZ-20260920-01-complete-delivery-intake.md)、
  [BIZ-20260917-02](../decisions/BIZ-20260917-02-one-view-shaped-sql.md)
- 技术方案：[DEV-20260920-02](../architecture/DEV-20260920-02-view-shaped-sql-compile.md)

## 目标

1. 从已校验的 3.1.0 五阶段树**确定性**编译一条单语句、只读、参数化的 SQL Server 候选。
2. 结果列为应处理实体键 + `outcome` / `reasonCode` / `matchedRuleCodes`。
3. 独立 AST 校验允许本编译器发出的 JOIN / APPLY / OR / NOT / EXISTS / CASE / 算术 / `DATEADD`，
   同时继续拒绝 DDL/DML、临时表、多语句和未绑定业务字面量。
4. 缺显式编译映射时保持 `METADATA_REVIEW` 阻断；不得把 unresolved 或旧两列测试批准当成授权。

## 范围

- SqlBot 独立编译器与独立 AST 门禁；默认测试只用合成 fixture 与合成表列名。
- 真实 3.1.0 离线包只证明：无编译映射时不能出 SQL。

## 非目标

- metadataReview、Schema v6 落库、连接 SQL Server、执行业务 SQL、在线模型、`generate-v3` 总 SQL。
- 删除或放宽单事实 M3/M4 对 JOIN/OR/聚合/算术的拒绝。
- 把 description / Java 公式写成 SQL；生成“组成员 eligible 布尔列”。
- 宣称已与优化方案引擎在真实数据上差分一致，或总 SQL 已可审核发布。
- 修改 RuleAgent；把私有表列/SQL 写入公开仓库。

## 验收

1. 合成交付 + 合成映射：编译出 SQL，AST `passed`，`executable=false`，`sqlGenerated=true`。
2. 无映射或真实离线包：确定性 `METADATA_REVIEW`，不回退旧 batch，不输出私有 SQL。
3. SQL 不含条件 `description`；运行参数是占位符；`allMembers` 用成员键集合 + 规则侧量词。
4. 既有 `generate-v3` / M4 回归不得被本切片放行。
5. PROG 记录命令与遗留：metadataReview、Schema v6、SQL Server 差分、三值 INDETERMINATE 完整编码。
