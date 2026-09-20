# DEV-20260917-01：Agent2 受限 COUNT / EXISTS / 附加比较

- 状态：`in_progress`。
- 实现需求：[REQ-20260917-02](../requirements/REQ-20260917-02-agent2-fact-sql-generation.md)。
- 决策：[BIZ-20260917-01](../decisions/BIZ-20260917-01-agent2-mongo-fact-generation.md)（部分已替代）、[BIZ-20260917-02](../decisions/BIZ-20260917-02-one-view-shaped-sql.md)。

## 1. M3 范围

在现有 source 单关系、实体键等值之外，允许且仅允许：

| 形状 | 条件 | SQL 意图 |
| --- | --- | --- |
| count | `factKind=aggregate`，`mode=compute`，`function=count`，`groupByFieldIds=[]`，`distinct` 不是 true | `SELECT COUNT(*) AS fact_value FROM 授权关系 WHERE 实体键 = :param` |
| exists | `factKind=exists`，`mode=exists` | 单行布尔：有匹配为真、无匹配为假，`nullPolicy=fail` |
| extra gte | source，字段角色 `time`，`operator=gte`，值为 parameter 或绑定参数化后的声明常量 | WHERE 增加授权列的 `>= :boundConst` |

其它 factKind、SUM/AVG、GROUP BY、DISTINCT COUNT、JOIN、OR/NOT、timeRange.mode≠none 仍拒绝。

## 2. COUNT

- Prompt 版本 `sqlserver-fact-candidate-v3.3`，仅当本条为 count 形状时使用。
- `inputFieldIds` 必须可解析到授权 `factValue`，用于锁定被计数关系，不把该列投影为结果。
- M4：允许 SELECT 列表中的 `Count`；`Count` 的唯一子节点可以是 `Star`；独立 `Star`、WHERE 中的 Count、其它聚合仍禁止。
- 结果列允许没有 `sourceColumn`；改为核对 FROM 关系等于 `factValue` 的授权关系。
- 特征 `countAgg` 不得与 source 直投路径同时出现。

## 3. EXISTS（本文件先冻结形状，可后于 COUNT 落地）

优先 `SELECT CASE WHEN EXISTS (SELECT 1 FROM ... WHERE ...) THEN CAST(1 AS bit) ELSE CAST(0 AS bit) END AS fact_value` 的受限子查询，或等价的单层 COUNT 加 CASE。须单独允许这一种子查询，不得放开任意子查询。

## 4. 附加 gte（后于 COUNT 落地）

FBR 中 `value.kind=literal` 的日期/数字不得写入 SQL 字面量。生成输入增加绑定参数（名称稳定、值来自请求），AST 只允许 `:name`。现有“禁止字面量比较”的 WHERE 规则保持，对这类绑定参数放行 `gte`。

## 5. 实施顺序

本文件能力仍有效，但不再作为本阶段交付顺序。[BIZ-20260917-02](../decisions/BIZ-20260917-02-one-view-shaped-sql.md) 要求先冻结视图形态的组合契约，再按矩阵需要打开 JOIN、条件组合及本文件中的 COUNT / EXISTS / gte。

1. 组合契约与结果列（REQ-20260917-01）。
2. 按矩阵需要的 JOIN / OR 条件组合。
3. COUNT / EXISTS / gte（仅当组合计划引用这些形状）。
4. 独立候选生成与差分验收；禁止逐条 `generate-v3` 冒充总 SQL。
