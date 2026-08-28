# BIZ-20260826-02：RuleReader 最新规则版本选择

- 状态：`approved`
- 创建日期：2026-08-26
- 来源：恢复本地 MongoDB 后取得的真实集合和索引证据
- 影响需求：[REQ-20260826-03](../requirements/REQ-20260826-03-rulereader-latest-rule-integration.md)
- 替代决策：[BIZ-20260826-01](BIZ-20260826-01-mongodb-rule-read-boundary.md) 第 3、4、5 条

## 决策

1. 最新规则的稳定业务范围是 `rule_id`，不是旧模型中的 `project_id + target`。
2. 同一 `rule_id` 按 BSON 日期 `generated_at` 降序选择最新版本，`_id` 降序只作为确定性次级
   排序；每次调用都查询 MongoDB，不缓存上次结果。
3. 返回对象是 RuleReader 不可变版本包装，外层存储元数据与 `document` 中的 camelCase 载荷必须
   在规则 ID、规则版本、Schema 版本、来源哈希、状态、可执行标志和生成时间上相互一致。
4. 当前兼容的 RuleReader Schema 是真实库已存在的 `1.0.0` 和 `2.0.0`。新版本必须先新增契约与
   测试，不得按相似字段自动接受。
5. `document.rule` 的语义和演进归 RuleReader 所有；SqlBot 在本切片严格验证版本包装、关键顶层
   Shape 和 JSON 可序列化性，不把规则正文当作模型指令，也不自行补业务默认值。
6. API 使用 `ruleId` 精确查询，不提供“全库最新”或模糊匹配，避免多个规则族之间发生歧义。
7. SqlBot 运行连接必须使用数据库侧 `read` 角色。管理员账号只可用于本地一次性创建最小权限
   服务账号，不能保留为应用连接凭据。
8. 现有 `{ rule_id: 1, generated_at: -1 }` 索引满足读取路径；SqlBot 不修改 RuleReader 集合或索引。

## 原因

这些字段、索引和版本来自真实 RuleReader 存储事实。沿用旧 Schema 假设会导致空结果或错误映射；
按实际索引查询并验证双层引用，才能保证“最新”确定、可追溯且不会读取到自相矛盾的载荷。
