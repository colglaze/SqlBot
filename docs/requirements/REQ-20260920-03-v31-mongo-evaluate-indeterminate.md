# REQ-20260920-03：Schema v6 只读消费、三值求值与离线引擎对齐

- 状态：`completed`
- 创建日期：2026-09-20
- 来源：用户对先前硬边界声明「全部授权」。本需求只覆盖 SqlBot 侧可验证、不发明生产事实的部分。
- 前置：[REQ-20260920-01](REQ-20260920-01-complete-delivery-intake.md)、
  [REQ-20260920-02](REQ-20260920-02-view-shaped-sql-compile.md)、
  [REQ-20260917-01](REQ-20260917-01-view-equivalent-sql.md)
- 业务决策：[BIZ-20260920-02](../decisions/BIZ-20260920-02-authorized-v31-followthrough.md)
- 技术方案：[DEV-20260920-03](../architecture/DEV-20260920-03-v31-mongo-evaluate-indeterminate.md)

## 目标

1. SqlBot **只读**消费 Agent1 Schema v6 已落库的 3.1.0 完整交付（`rule_versions_v3` +
   `fact_binding_handoff_batches_v3`），门禁仍走 `selectDelivery`。
2. 独立实现与 Agent1 相同的五阶段三值求值，用合成 `testCases` 做离线差分。
3. 视图形态 SQL 用 `1 / 0 / NULL` 命中位编码 `INDETERMINATE`；CASE WHEN 不再把 UNKNOWN 当成未命中。
4. 映射包中 `factKind=aggregate/count` 与 `exists` 编译为受限 COUNT(*) / EXISTS 形状。
5. 待消费身份改为 2026-09-20 对齐版本；2026-09-17 3.1.0 不再作为权威输入。

## 范围

- 只读 Mongo 适配器、假集合离线测试、可选本机身份核验。
- 合成映射下的编译/求值；显式 `metadataReviewApproved` 映射包仍须调用方提供。

## 非目标

- SqlBot 写入 MongoDB；覆盖 2026-09-05 confirmed 哈希。
- 把 unresolved / 旧两列测试批准升格为生产表列授权。
- 连接 SQL Server、执行真实包 SQL、把私有表列写入公开仓库。
- 修改 RuleAgent 生产代码（v6 落库已在 Agent1 完成）。
- 在线模型、写 `.env`、推送远程。

## 验收

1. 假 Mongo 文档可 `selectDelivery`；`contract_version=3.0.0` 的 batch 不能当 3.1.0 完整交付。
2. 合成案例求值与 fixture `testCases` 一致；缺事实得到 `INDETERMINATE`。
3. 编译 SQL 含 `ELSE NULL` 命中位与 `FACT_VALUE_MISSING_OR_INVALID`；AST 仍拒绝 DML/临时表。
4. COUNT(*) / EXISTS 合成形状可通过独立 AST；SUM/GROUP BY 仍阻断。
5. unresolved 映射仍 `METADATA_REVIEW`。PROG 记录未做的 SQL Server 差分与生产 metadataReview。
