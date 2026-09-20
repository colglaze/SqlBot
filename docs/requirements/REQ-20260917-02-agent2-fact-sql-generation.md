# REQ-20260917-02：按 Mongo 事实请求扩展 Agent2 SQL 生成

- 状态：`in_progress`。
- 来源：2026-09-17 用户要求暂时不管规则源差异，用规则生成 SQL 模板，且要 Agent2 生成能力。
- 决策：[BIZ-20260917-01](../decisions/BIZ-20260917-01-agent2-mongo-fact-generation.md)。
- 设计：[DEV-20260917-01](../architecture/DEV-20260917-01-agent2-count-exists-filter.md)。
- 基线：[REQ-20260915-01](REQ-20260915-01-v3-mongodb-generation-entry.md)、[REQ-20260916-01](REQ-20260916-01-value-encoding-result-semantics.md)。

## 目标

对当前精确规则版本的每条 FactBindingRequest，Agent2 能按该请求的 `queryRequirements` 生成独立、参数化、不可执行的 SQL 模板。不把其它规则文本中的条件补进 SQL。

## 范围

1. 现有 source + 单关系 + 实体键等值路径保持可用；有编码绑定时仍走 CASE。
2. 新增本批次已出现的三类查询形状：
   - `factKind=aggregate` 且 `aggregation.mode=compute`、`function=count`、无 GROUP BY、`distinct` 不为 true；
   - `factKind=exists` 且 `aggregation.mode=exists`；
   - source 事实带实体键等值 **以及** 额外 `gte` 比较（字段角色 `time`，值为请求中的字面量或参数）。
3. 生成器与 AST 门禁同步扩展；禁止删除现有拒绝后直接接受任意模型 SQL。
4. 字面量业务常量不得拼接进 SQL 文本；须变为绑定参数，值来自请求声明而非模型编造。
5. 复合 `source` 布尔（到款/合同/定时/合并组公式）无物理列授权时该条阻断，不在本需求中发明多表公式 SQL。
6. 公开测试只用合成对象名；真实生成仍要测试环境批准与 `--authorize-online-provider`。

## 非目标

- 本需求不单独交付整规则一条 SQL；该最终产物按 [REQ-20260917-01](REQ-20260917-01-view-equivalent-sql.md) / [BIZ-20260917-02](../decisions/BIZ-20260917-02-one-view-shaped-sql.md)。此处 COUNT / EXISTS / gte 只是组合 SQL 的能力前置。
- 不连接 SQL Server、不执行候选。
- 不把优化方案或视图里未写入当前 FBR 的过滤补进去。
- 不覆盖旧候选哈希。

## 验收

- 合成 count：`SELECT COUNT(*) AS fact_value ... WHERE 实体键 = :param` 通过；SUM/GROUP BY/WHERE 中的 COUNT 拒绝。
- 合成 exists：结果为非空布尔，无匹配为假而不是零行冒充成功（具体 SQL 形状见 DEV）。
- 合成 gte：比较列与常量均来自授权/参数绑定；模型不得改写字面量。
- 无列授权、无编码绑定且 `allowedValues` 非空：仍在 M2 阻断。
- `uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`、`git diff --check` 通过。
