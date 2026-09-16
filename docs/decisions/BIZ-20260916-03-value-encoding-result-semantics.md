# BIZ-20260916-03：受治理编码映射与结果基数

- 状态：`approved`（按 2026-09-16 用户已确认的规则驱动职责、视图取数基准、零行/多行返回要求落地；本文件不改写上游 FactBindingRequest 3.0.0）。
- 影响需求：[REQ-20260916-01](../requirements/REQ-20260916-01-value-encoding-result-semantics.md)、[REQ-20260915-01](../requirements/REQ-20260915-01-v3-mongodb-generation-entry.md)。
- 前置决策：[BIZ-20260916-02](BIZ-20260916-02-view-semantic-baseline.md)、[BIZ-20260916-01](BIZ-20260916-01-first-template-review-result-semantics.md)。
- 相关差异：[BUG-20260916-01](../bugs/BUG-20260916-01-first-template-view-semantic-gap.md)。

## 1. 权威分层

MongoDB 精确规则版本及其不可变 `FactBindingRequest` 决定逻辑事实、筛选、参数、`allowedValues`、结构化 `nullPolicy` 和 usages。固定私有视图只作为物理编码与取数关系的核对基准，不能把视图里的旧放行条件写入事实 SQL，也不能用视图常量改写规则比较值。

逻辑枚举与物理存储码不同时，必须有经 `metadataReview` 批准的版本化编码绑定。该绑定是项目上下文的一部分，进入内容哈希与批准闭包。Prompt、模型声明、工作簿单元格和自然语言描述都不是授权。

## 2. 编码与空值

- 映射条目必须显式列出每一对「物理码 → 逻辑值」；逻辑值必须属于当前请求的 `allowedValues`。
- 未登记的物理码不得映射为“否”或任何默认逻辑值；投影为 SQL NULL，与匹配行上的 NULL 一样留给规则引擎按 `nullPolicy` 判断。
- 结构化 `nullPolicy` 与自然语言/视图默认值冲突时，以结构化字段为准。当前所选事实为 `indeterminate`：输入 NULL 保持 NULL，禁止 `ISNULL`/`COALESCE` 把空值改写成否。
- 不得用隐式类型转换、取第一行或默认分支掩盖未知码。

当前所选 source 事实采用 `mappedCase`：按视图直接路径的物理比较码映射到请求 `allowedValues` 中对应的是/否逻辑值。详细内部码只保存在公开仓库外的审核目录。身份映射（物理码已等于逻辑值）必须同样显式登记，不能靠缺省假设。

`allowedValues` 非空但缺少对应 `factValue` 编码绑定时，M2 阻断该事实，不生成原值查询冒充已对齐。

## 3. 结果基数

上游 `FactBindingRequest 3.0.0` 的 `result.cardinality` 仍冻结为 `scalar`，本仓库不得改本地冻结 Schema 冒充上游新版本。用户确认的「无匹配零行、多条全部返回」由 SqlBot 下游结果语义绑定表达：

- `emptyMatch = emptyResultSet`：零行就是空结果集，不制造一行 NULL。
- `extraRows = returnAll`：禁止 TOP/LIMIT/DISTINCT/隐式聚合来压成一行。
- `consumerCardinality = rowset`：候选机器契约记录行集；规则引擎按行消费，不得静默取第一行、任一满足或全部满足。
- 若实体粒度为单实体而实际返回多行，SQL 仍返回全部行；这是粒度/唯一性证据，不是裁剪理由。

无结果语义绑定时，候选继续复制上游 `scalar`，但静态门禁仍禁止行数裁剪。有绑定的新候选使用候选契约 `schemaVersion=3.1.0` 与 `cardinality=rowset`。旧 `3.0.0` 候选保持原字节与 `scalar`，不继承新审核结论。

## 4. 规则变化

规则版本、请求内容、编码绑定、context/snapshot/批准记录变化后必须重读、重算、生成新候选。查询参数值只进入绑定参数。仅规则引擎侧阈值变化时，允许事实 SQL 文本相同，但引用闭包、内容哈希和 usage 六元组覆盖必须指向新版本。

## 5. 非决策

- 不把本事实升级为整规则可释放清单查询。
- 不实现 JOIN/聚合/exists，除非当前请求结构化字段确实需要。
- 不实现正式审核/发布状态机；候选仍为 `pending / executable=false`。
- 不连接 SQL Server，不把私有码写入公开测试夹具。
