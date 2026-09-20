# BIZ-20260920-01：3.1.0 完整交付消费与一条总 SQL 的结果契约

- 状态：`approved`
- 日期：2026-09-20
- 影响需求：[REQ-20260920-01](../requirements/REQ-20260920-01-complete-delivery-intake.md)、
  [REQ-20260917-01](../requirements/REQ-20260917-01-view-equivalent-sql.md)
- 技术方案：[DEV-20260920-01](../architecture/DEV-20260920-01-complete-delivery-intake.md)
- 不替代：[BIZ-20260917-02](BIZ-20260917-02-one-view-shaped-sql.md) 的一条总 SQL 形态、
  [BIZ-20260917-03](BIZ-20260917-03-optimization-plan-authority.md) 的优化方案判定权威。
- 上游门禁权威：RuleAgent [DEV-20260917-02](../../../RuleAgent/docs/DEV-20260917-02-sqlbot-complete-delivery-intake.md)
  与 `purpose_v31.py`；SqlBot 不得另写哈希或用途表。

## 决策

1. **最终可审核产物**仍是单语句、只读、参数化的 SQL Server 候选。结果形态像参考视图：一次查出应处理实体及放行相关列。判定公式对齐优化方案引擎，不对齐现行释放视图 OR 支。
2. **单事实 `generate-v3` 可保留**，只作对照或未来组合输入，**不是**本目标交付物。本轮不得调用它来产出总 SQL。
3. **权威输入**只接受 Agent1 优化方案 3.1.0 完整交付：
   - 固定私有 commit `2240e5bd18e36d17650896a10cc61e1c18e3daa0`
   - 优化方案文件 SHA-256 `c049af189fc3689bac8e96408d9e7239a8c70b66bbcd15829e509c6d524b648f`
   - 报告 `REPORT_RELEASE_ALL_001@20260917T140000000000Z-c049af189fc3-74a69e146b34`
   - 原始数据 `RAW_DATA_RELEASE_ALL_001@20260917T140000000000Z-c049af189fc3-aac9288cc74f`
   - 二者均为 `draft` / `executable=false` / `purpose=optimization-plan-generation` / mapping unresolved
4. **禁止**把 2026-09-05 版本 `REPORT_RELEASE_ALL_001@20260905T172407000000Z-f285643e5b2b-82dbd05a800a` 或 batch `0df35b35…` 当作 `optimization-plan-generation` 或 `sql-compilation` 输入。
5. **禁止**把 RuleAgent `generated-rules/report-release-v3-formula-20260920-fix/` 的 3.0.0 公式树当作本切片输入。合并组仍是 exists 黑盒时，契约不是 3.1.0。
6. **用途门禁照抄 Agent1**：历史 2026-09-05 版本只允许 `historical-audit`；3.1.0 新版本允许 `optimization-plan-generation`。请求 `sql-compilation` 时，若交付 `purpose` 不允许则确定性失败。SqlBot 不把 `optimization-plan-generation` 静默改写成已批准的 SQL 编译授权。
7. **运行参数**（`evaluationDate`、截止日、上线日）是规则表达式参数 / 未来 SQL 绑定参数，不是待查询数据库列。
8. **成员集合** FBR `cardinality=set` 返回实体键列表；`allMembers` 留在规则侧。禁止生成“组成员 eligible 布尔列”。
9. **组合计划定义之后**，可用显式编译映射包做确定性视图形态 SQL 编译与独立 AST 校验，见
   [REQ-20260920-02](../requirements/REQ-20260920-02-view-shaped-sql-compile.md)。禁止把 description
   里的 Java/公式写成 SQL。既有单事实 M3/M4 对 JOIN / WHERE OR-NOT / 通用聚合与算术的拒绝**不得删除**。
10. 私有正文、内部表列、SQL、凭据不得写入 SqlBot 公开仓库、测试夹具、日志或 CLI 输出。旧两列测试批准不能继承为完整清单授权。

## 本轮明确未做

- metadataReview 物理映射批准（合成编译映射 ≠ 生产授权）
- Schema v6 真实 MongoDB 落库与只读仓储适配
- SQL Server 上的真实总 SQL 执行与优化方案差分
- 三值 INDETERMINATE 的完整 SQL 编码
