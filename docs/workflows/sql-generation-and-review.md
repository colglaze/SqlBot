# SQL 生成与人工审核流程

- 状态：`proposed`
- 关联：[REQ-20260818-01](../requirements/REQ-20260818-01-release-rule-sql-generator.md)

## 1. 输入包

每次运行构造不可变 `GenerationInput`：

- 当前规则及 canonical hash；
- 上一已批准规则和 SQL（允许为空）；
- 结构化规则 diff；
- 当前项目上下文和 Schema hash；
- SQL 方言及数据库能力；
- 安全策略、性能预算；
- prompt template 版本和 provider 配置标识。

任何输入在运行期间变化，原运行不自动吸收变化，而是标记过期并创建新运行。

## 2. 生成前检查

1. JSON Schema 和领域校验通过；
2. `project_id + target` 与基线一致；
3. 逻辑字段全部能映射到当前 Schema；
4. 释放状态映射、实体主键、时区和方言存在；
5. 基线 SQL 能被目标方言解析；不能解析时仍可冷启动，但必须明确放弃该基线并记录原因；
6. 规则/SQL 大小不超过 prompt 输入限制，敏感值符合 provider 数据策略。

## 3. 模型输出契约

模型应返回结构化对象，至少包括：

```text
candidate_summary
dialect
plan.kind
plan.stages[]: {stage_id, statement_type, sql_template, depends_on, cleanup}
parameters[]: {name, value_type, source_condition_id}
output_contract[]
condition_coverage[]: {condition_id, stage_id, expression_summary}
baseline_changes[]
performance_risks[]
assumptions[]
```

模型不能返回审核结论。`assumptions` 非空且涉及业务语义、Schema 或权限时，运行必须阻塞或要求人工补全上下文，不能仅靠审核“看起来合理”放行。

## 4. 校验门禁

### Gate A：结构和解析

- provider 输出符合响应 Schema；
- 所有 SQL 都能按指定方言解析；
- stage 依赖无环且清理阶段齐全；
- 参数声明与占位符一一对应。

### Gate B：安全策略

- 单查询只允许一个只读查询语句；
- 分阶段脚本只允许创建/读取/索引（若策略允许）/删除会话级临时表；
- 禁止永久 DDL、DML、动态 SQL、存储过程、跨库外部访问和未授权对象；
- 函数、表和列均在 allowlist；
- 不允许注释或标识符逃逸破坏策略。

### Gate C：规则覆盖

- 当前每个 `condition_id` 恰好有可解释映射；
- 新增/修改条件出现在候选中；
- 已删除条件不再影响结果；
- 组合树括号、NOT、NULL 和时间边界与规范规则一致；
- 输出能给出失败原因，或提供可确定计算失败原因的字段。

### Gate D：性能证据

- 静态复杂度评分已生成；
- 受限 `EXPLAIN` 成功且未超过项目预算；
- 必需时完成沙箱试跑；
- 分阶段方案的临时表行数估计、索引/分布键建议和清理路径已记录。

任一硬门禁失败，状态为 `validation_failed`，不可由模型或普通 API 改为批准。

## 5. 审核包

审核者看到的内容必须来自已哈希的同一 revision：

- 规则树和语义摘要；
- 当前 vs 基线规则 diff；
- 候选 vs 基线 SQL diff；
- 条件覆盖矩阵；
- 参数列表（敏感值遮蔽）；
- 输出列和异常原因表达；
- AST 安全检查、对象访问清单；
- `EXPLAIN` 摘要、试跑统计、告警；
- 所有模型假设；
- 临时表生命周期图（如适用）。

审核者必须填写审核身份；驳回必须给出原因，批准建议填写简短依据。不得对审核页面展示后的 SQL 做静默格式化或修改。

## 6. 批准和发布

1. 审核 API 接收 `template_id + revision + artifact_hash + decision`；
2. 服务核对状态为 `pending_review` 且哈希一致；
3. 批准写入不可变审核证据，状态变为 `approved`；
4. 发布前再次检查当前 rule/context hash 和活动父版本；
5. 事务或 compare-and-swap 更新活动版本；
6. 发布状态和 `published_at` 写入审计事件。

SQL 被编辑、参数变化、Schema 更新或校验器策略升级后，必须创建新 revision 并重新通过相关门禁与人工审核。

## 7. 驳回和再生成

驳回意见作为下一 revision 的数据输入，但不能成为绕过安全策略的指令。新 revision 保留同一 `run_id` 或创建显式关联的新 run；旧 revision 保持可审计。

## 8. 推荐测试夹具

- 新增、删除和反转条件；
- 嵌套 AND/OR/NOT；
- NULL 的四种策略；
- 日期时区、闭开区间和夏令时（若目标时区适用）；
- 一对多 JOIN 造成重复；
- 无历史基线；
- 历史 SQL 已过期或无法解析；
- 模型输出 DML、未授权表和提示注入文本；
- EXPLAIN 超时、临时表创建失败、清理失败；
- 审核时哈希不一致和并发发布冲突。
