# REQ-20260820-01：规则规范化、内容哈希与结构化差异

- 状态：`completed`
- 创建日期：2026-08-20
- 来源：用户要求补齐 Phase 2 的确定性规则分析能力
- 关联决策：[BIZ-20260820-01](../decisions/BIZ-20260820-01-rule-analysis-boundary.md)
- 技术方案：[DEV-20260820-01](../architecture/DEV-20260820-01-rule-change-analysis.md)
- 兼容契约：[release-rule.schema.json](../specs/release-rule.schema.json)

## 1. 背景

仓库在 2026-08-19 将主流程调整为事实级 Agent 2，历史“整规则异常集合 SQL”方案被替代；但旧规则契约中已经定义的规范化、内容哈希和版本差异仍是可独立验证的确定性审计能力。当前代码只有事实绑定就绪检查，缺少这些领域逻辑。

本需求恢复规则分析能力，但不恢复未经确认的整规则 SQL 生成职责。异常集合方向和真实目标 Schema 未提供时，只阻塞下游 SQL 规划，不阻止对已通过规则契约校验的 JSON 做纯领域分析。

## 2. 范围内

- 解析 `release-rule.schema.json` 对应的规则领域对象；
- 生成稳定 canonical JSON 和 SHA-256 内容哈希；
- 保留条件数组顺序和逻辑树层级，忽略对象键顺序和输入空白；
- 按稳定 `condition_id` 生成新增、删除、修改和逻辑结构变化；
- 拒绝整棵规则树中重复的 `condition_id`；
- 无历史基线时显式返回 `baseline_mode=none`，不得伪造基线；
- 对缺失异常集合语义或真实 Schema 快照的 SQL 规划返回结构化 `blocked`；
- 完全离线的领域测试。

## 3. 范围外

- MongoDB 仓储、基线查询、索引和 migration；
- 异常集合 SQL、事实 SQL 或任何 LLM 调用；
- 猜测释放状态字段、表/列映射、时区、生产连接或异常集合的 `NOT` 方向；
- 修改 RuleReader 集合或现有事实绑定 API。

## 4. 验收标准

1. 同一规则只改变 JSON 对象键顺序或空白时，canonical JSON 和内容哈希保持一致；条件数组重排不视为等价。
2. 结构化 diff 分别报告新增、删除和叶子内容修改，修改项包含变化字段及前后快照。
3. 组合符、`negate`、分组、条件顺序或条件路径变化时报告逻辑结构变化。
4. 任意层级出现重复 `condition_id` 时确定性拒绝，并列出重复 ID。
5. 无基线时 `baseline_mode=none`、`baseline_hash=null`，当前全部条件作为新增项，逻辑结构变化不与虚构空树比较。
6. 缺少异常集合语义和/或真实 Schema 快照时，下游 SQL 规划返回稳定阻塞码；领域层不得提供默认值。
7. 领域实现不依赖 FastAPI、MongoDB/SQL 驱动、LLM SDK 或在线服务。
8. `uv run ruff check .`、`uv run ruff format --check .` 和 `uv run pytest` 全部通过。
