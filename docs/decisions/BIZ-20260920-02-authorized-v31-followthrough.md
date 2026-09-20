# BIZ-20260920-02：授权后续切片的边界（v6 只读、不对齐生产映射）

- 状态：`approved`
- 日期：2026-09-20
- 来源：用户对先前硬边界声明「全部授权」。
- 影响：[REQ-20260920-03](../requirements/REQ-20260920-03-v31-mongo-evaluate-indeterminate.md)
- 不替代：[BIZ-20260920-01](BIZ-20260920-01-complete-delivery-intake.md) 的用途/哈希门禁，
  [BIZ-20260906-01](BIZ-20260906-01-agent1-metadata-review-sqlbot-boundary.md) 的责任分工。

## 决策

1. **待消费身份**以 Agent1 2026-09-20 对齐交付为准：
   - 报告 `REPORT_RELEASE_ALL_001@20260920T131600000000Z-c049af189fc3-6d94836af30f`
   - 原始数据 `RAW_DATA_RELEASE_ALL_001@20260920T131600000000Z-c049af189fc3-2845743f259a`
   - 来源文件 SHA-256 仍为 `c049af189fc3689bac8e96408d9e7239a8c70b66bbcd15829e509c6d524b648f`
   - 2026-09-17 的 3.1.0、2026-09-20 公式树 3.0.0、2026-09-05 confirmed 版本均不得作为
     `optimization-plan-generation` / `sql-compilation` 输入。
2. **SqlBot 只读 Schema v6**。Mongo 写入权仍只在 Agent1。禁止覆盖旧 V3 哈希。
3. **unresolved 仍不是批准。** 合成编译映射与 `metadataReviewApproved` 映射包都必须由调用方显式提供；
   不得读取 FBR `mappingCandidate` 的 unresolved 字段当表列授权，不得继承旧两列测试批准。
4. **三值逻辑**必须在求值器与 SQL 命中位中编码；不能再把 SQL UNKNOWN 当成未命中。
5. **不连接 SQL Server、不执行业务 SQL、不把私有表列写入公开仓库。** Phase 5 开关保持独立。
6. **不修改 RuleAgent 生产代码**（完整交付已落库）。不写 `.env`，不推送远程。

## 本轮仍不能声称完成

- 生产 metadataReview 物理批准
- SQL Server 上对真实包的执行与引擎差分
- 总 SQL 审核发布
