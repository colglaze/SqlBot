# 释放规则 JSON 契约

机器契约见 [release-rule.schema.json](release-rule.schema.json)。本说明定义 Schema 之外的业务语义。

## 设计原则

- 一个文档只描述一个项目、一个目标类型和一个不可变版本；
- 逻辑树显式表达 `all`（AND）、`any`（OR）和 `negate`（NOT）；
- 每个叶子条件有稳定且在文档内唯一的 `condition_id`；
- 字段使用项目上下文中的逻辑字段名，不直接允许任意 SQL 表达式；
- 值保持 JSON 类型，生成 SQL 时转换为绑定参数；
- `null_policy` 明确缺失值行为，避免数据库三值逻辑造成隐式差异。

## 示例

```json
{
  "schema_version": "1.0",
  "rule_id": "report-release-rule",
  "project_id": "project-001",
  "version": 3,
  "target": "project_report",
  "status": "active",
  "effective_from": "2026-08-18T00:00:00+08:00",
  "condition": {
    "combinator": "all",
    "conditions": [
      {
        "condition_id": "report.finalized",
        "field": "report_status",
        "operator": "eq",
        "value": "FINAL",
        "value_type": "string",
        "null_policy": "fail"
      },
      {
        "condition_id": "report.qc_passed",
        "field": "qc_status",
        "operator": "in",
        "value": ["PASS", "WAIVED"],
        "value_type": "string",
        "null_policy": "fail"
      }
    ]
  },
  "metadata": {
    "created_by": "rule-owner@example.com",
    "change_reason": "Allow approved QC waivers"
  }
}
```

示例字段和值均为合成数据，不代表实际业务 Schema。

## 逻辑语义

### 组合节点

- `combinator = all`：所有子项为真才为真；对应括号化的 AND。
- `combinator = any`：任一子项为真即为真；对应括号化的 OR。
- `negate = true`：对子树的最终布尔结果取反，生成时必须保留显式括号。
- 空 `conditions` 被 Schema 禁止，避免 AND/OR 空集语义分歧。

### 叶子节点

- `field` 必须在当前 `project_context` 的逻辑字段映射中存在。
- `operator` 与 `value_type`/`value` 必须兼容。
- `is_null`、`not_null` 不携带 `value`；其他操作符必须携带值。
- `in`/`not_in` 的值必须为非空数组；`between` 必须恰有两个有序边界。
- 时间值必须使用 ISO 8601，并由项目上下文指定比较时区和闭开区间约定。

## Null 语义

`null_policy` 先处理字段为 NULL 的情况，再应用操作符：

- `fail`：NULL 使条件为 false；
- `pass`：NULL 使条件为 true；
- `exclude`：记录不参与资格集合计算，并在异常原因中单独标记；
- `error`：验证/执行应阻止并报告数据质量问题。

SQL 生成不能依赖数据库默认三值逻辑来替代上述语义。

## 版本和兼容性

- `schema_version` 只表示契约版本；`version` 表示业务规则版本。
- 同一 `project_id + target + version` 唯一且不可修改。
- 文本重排不应改变 canonical hash；数组顺序和逻辑树结构属于语义的一部分。
- 破坏性契约变化提升 `schema_version`，并提供旧版本读取/迁移策略。

## 领域校验（JSON Schema 之外）

- `condition_id` 在整棵树中唯一；
- `effective_to` 晚于 `effective_from`；
- 逻辑字段、类型和操作符与项目上下文兼容；
- `between` 边界顺序正确；
- `condition_id` 在无语义变化时跨版本保持稳定；
- 当前规则处于 `active`，或生成请求显式允许对 `draft` 做预览。
