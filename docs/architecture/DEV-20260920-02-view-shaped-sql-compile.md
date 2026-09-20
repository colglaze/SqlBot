# DEV-20260920-02：视图形态 SQL 确定性编译与独立 AST

- 状态：`completed`（离线合成编译；非 SQL Server 差分、非发布）
- 日期：2026-09-20
- 实现需求：[REQ-20260920-02](../requirements/REQ-20260920-02-view-shaped-sql-compile.md)
- 业务决策：[BIZ-20260920-01](../decisions/BIZ-20260920-01-complete-delivery-intake.md)
- 不修改：`sqlglot_tsql_v3.py` 单事实白名单、M3 `generate_sql_candidate_v3`

## 1. 结论

在 3.1.0 完整交付通道上增加**第二套**编译/校验路径。输入是规则树 + 调用方显式提供的合成映射包，
不是 LLM，也不是 FBR `mappingCandidate`。`#temp`、跨库、多语句、DML 仍禁止。

无映射包 → 只返回阻断，不生成 SQL。真实优化方案包 mapping unresolved，走同一阻断。

## 2. 模块

```text
domain/view_shaped_sql_v31.py
  映射包、编译候选、静态报告
application/view_shaped_compile_v31.py
  树 → SQL 文本与绑定参数
application/view_shaped_sql_validation_v31.py
  独立 AST 编排
infrastructure/sql/sqlglot_tsql_v31_view.py
  视图形态允许集（JOIN/OR/EXISTS/CASE/算术），不复用 V3 最小白名单
```

## 3. 映射包

调用方必须给出：

- `subject`：grain 关系及实体键列
- 每个 `requiredFactCode` 的 schema/relation/column 与键列

标识符只允许 `[A-Za-z][A-Za-z0-9_]*`。禁止 `#`/`@` 临时对象。映射哈希进入候选追溯。
不读取 FBR 的 `viewName`/`viewField`。

## 4. SQL 形态

```text
SELECT
  subject.entity_key AS entity_keys,
  CASE WHEN hit_stateGuard THEN ... WHEN hit_prereq THEN ...
       WHEN hit_postGate THEN ... WHEN hit_exclusion THEN ...
       WHEN hit_eligibility THEN ... ELSE default END AS outcome,
  同序 CASE AS reasonCode,
  同序 CASE / CONCAT_WS AS matchedRuleCodes
FROM subject
OUTER APPLY (TOP 1 标量事实) AS fact_*
CROSS APPLY (各规则 0/1 命中位) AS hits
WHERE subject.key = :entityKey
```

阶段语义对齐 Agent1 `evaluate_rule_structure_v31`：stateGuards/prerequisites 命中即停；
eligibility 首个命中后继续；postGates/exclusions 可覆盖。`allMembers` 编译为
`NOT EXISTS`（空集且 `emptyCollectionPolicy=pass` 为真），成员谓词按成员键再查成员事实。

比较字面量与运行参数一律 `:name`。`description` 不进入 SQL。outcome/reason/ruleCode 使用
封闭枚举的 `N'...'` 文本，不是业务描述。

## 5. AST

允许：Join/Apply、子查询、Exists、Or、Not、比较、CASE、算术、DATEADD、COALESCE、
CONCAT_WS、TOP、DISTINCT。拒绝：非 SELECT 根、多语句、DML/DDL、临时对象、星号、UNION、
窗口、SELECT INTO、CTE、未在映射快照中的表、非法参数名。

不把 V3 `SQL_JOIN` / `SQL_OR` 从单事实检查器里删掉。

## 6. 仍阻断

- 无映射包；`factKind` 为 aggregate/exists 且本编译器未实现该形状
- 三值 `INDETERMINATE` 未按引擎完整编码（NULL 比较按 SQL 三值，CASE WHEN 只认 TRUE）
- 真实库执行与优化方案案例差分
