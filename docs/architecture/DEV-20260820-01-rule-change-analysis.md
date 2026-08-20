# DEV-20260820-01：规则规范化、哈希与差异领域设计

- 状态：`completed`
- 创建日期：2026-08-20
- 实现需求：[REQ-20260820-01](../requirements/REQ-20260820-01-rule-change-analysis.md)
- 受业务决策约束：[BIZ-20260820-01](../decisions/BIZ-20260820-01-rule-analysis-boundary.md)

## 1. 模块与依赖

新增 `domain/rule_analysis.py`，负责规则领域模型、canonicalization、内容哈希、结构化 diff 和 SQL 规划前置门禁。模块只使用 Python 标准库与当前领域层已采用的 Pydantic，不导入 FastAPI、LangGraph、MongoDB/SQL 驱动或模型 SDK。

本切片不新增 repository。调用方以后从何处取得当前规则和基线，不影响领域函数：

```text
current ReleaseRule + optional baseline ReleaseRule
                    |
                    v
       canonicalize_rule / diff_rules
                    |
                    v
     CanonicalRuleArtifact + RuleDiff
```

## 2. Canonicalization

规则先进入与 `release-rule.schema.json` 对齐的封闭领域模型，再按以下规则序列化：

- UTF-8 JSON，`ensure_ascii=false`；
- 对象键按字典序排列，去除非语义空白；
- 枚举和时间转换为 JSON 表示；
- 缺失的可选 `null` 字段不写入，`negate=false` 等语义默认值显式写入；
- 条件数组保持原顺序，不排序；
- 禁止 NaN/Infinity 等非 JSON 数值。

内容哈希为 `sha256:` 加 canonical JSON UTF-8 字节的 64 位小写十六进制摘要。哈希覆盖完整规则载荷，不只覆盖条件树。

## 3. 条件索引与路径

深度优先遍历 `condition`，叶子路径使用 JSONPath 风格，例如 `$.condition.conditions[0]`。索引键是 `condition_id`，值包含路径和规范化叶子快照。

遍历时收集所有重复 ID；存在重复时抛出 `DuplicateConditionIdError`，异常携带排序后的重复 ID。禁止覆盖索引中的已有条件。

## 4. 结构化 diff

`diff_rules(current, baseline)` 返回：

- `baseline_mode`：`previous` 或 `none`；
- 当前/基线内容哈希；
- `added`：只在当前索引出现；
- `removed`：只在基线索引出现；
- `modified`：同一 ID 的叶子业务内容变化，包含前后路径、前后快照和变化字段；
- `logic_structure_change`：组合符、`negate`、`group_id`、嵌套、条件顺序或路径发生变化时，保存前后逻辑签名。

逻辑签名只保留组合节点结构和叶子 `condition_id`，因此叶子的字段或值修改只进入 `modified`，不会误报逻辑结构变化。新增或删除会改变已有基线的逻辑签名，同时仍分别出现在 `added`/`removed`。

无基线时当前全部叶子列入 `added`，`baseline_hash=null`，`logic_structure_change=null`。

## 5. 显式阻塞门禁

`evaluate_rule_planning_readiness` 只判断整规则 SQL 规划是否具备业务前提：

- `EXCEPTION_SET_SEMANTICS_MISSING`：没有显式选择异常集合方向；
- `TARGET_SCHEMA_MISSING`：没有版本化真实 Schema 快照；
- `TARGET_SCHEMA_INCOMPLETE`：快照没有提供任何逻辑字段；
- `RULE_FIELD_NOT_IN_SCHEMA`：规则逻辑字段不在快照中；
- `RULE_FIELD_TYPE_MISMATCH`：规则类型和快照类型不一致。

门禁不生成 SQL，也不修改 canonical/diff 结果。异常集合语义和 Schema 都没有默认值。

## 6. 测试

领域测试至少覆盖：规范表示/哈希稳定性、新增、删除、修改、逻辑结构变化、重复 `condition_id`、无基线，以及异常集合语义/真实 Schema 缺失阻塞。测试只使用合成规则和 Schema。
