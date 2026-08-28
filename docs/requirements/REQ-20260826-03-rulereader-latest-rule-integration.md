# REQ-20260826-03：真实 RuleReader 最新规则版本接入

- 状态：`completed`
- 创建日期：2026-08-26
- 来源：用户要求完成最新进度文档中的下一任务
- 修复缺陷：[BUG-20260826-01](../bugs/BUG-20260826-01-rule-version-storage-shape.md)
- 业务决策：[BIZ-20260826-02](../decisions/BIZ-20260826-02-rulereader-latest-version-selection.md)
- 技术方案：[DEV-20260826-03](../architecture/DEV-20260826-03-rulereader-latest-rule-integration.md)

## 背景

本地 MongoDB 已恢复，真实 `rule_reader.rule_versions` 集合和索引已可只读探测。Phase 2D 的旧
`ReleaseRule 1.0` 直接载荷假设与实际 RuleReader 版本包装不兼容，需要以真实数据契约替换。

## 目标

- 按 `rule_id` 每次读取 `generated_at` 最大的 RuleReader 版本；
- 同时兼容真实存在的 Schema `1.0.0` 和 `2.0.0` 包装；
- 严格校验存储元数据与内层文档引用一致，不输出不可信或自相矛盾的规则版本；
- 使用数据库侧只读服务账号完成真实 `/ready` 和 API 验证。

## 范围

- 新增 RuleReader 版本包装、来源、解析器和规则顶层 Shape 领域模型；
- 仓储查询改为 `{ rule_id: ruleId }` 与 `generated_at DESC, _id DESC`；
- API 改为 `GET /api/v1/rules/latest?ruleId=...`；
- 返回业务版本包装，不返回 MongoDB `_id`；
- 稳定映射未找到、存储契约无效和仓储不可用错误；
- 创建本地最小权限只读账号并更新被 Git 忽略的 `.env`；
- 使用真实现有规则做只读冒烟验证，不修改规则文档。

## 非目标

- 不修改、迁移或补写 RuleReader 规则、事实绑定、索引或 migration；
- 不把 Schema `1.0.0` 转换成 `2.0.0`，不解释完整规则语义；
- 不生成 SQL、不调用 DeepSeek、不读取 SQL Server 业务数据；
- 不在日志、响应证据或文档中记录连接凭据或完整真实规则正文。

## 验收标准

1. 同一 `rule_id` 有多个版本时返回 `generated_at` 最大者；连续两次调用均实际查询仓储。
2. 查询过滤、排序和投影与真实索引、字段一致，不再引用 `project_id`、`target` 或数值 `version`。
3. 外层与内层的规则 ID、版本、Schema、来源哈希、状态、可执行标志和生成时间不一致时返回
   `RULE_DOCUMENT_INVALID`。
4. Schema `1.0.0` 和 `2.0.0` 的真实 Shape 都通过固定夹具；未知 Schema 被拒绝。
5. 应用账号只有目标数据库 `read` 角色；真实 `/ready` 为 200，最新规则接口为 200，并确实返回
   数据库当前最新 `rule_version`。
6. API 响应不包含 MongoDB `_id`，错误与日志不包含连接目标、凭据或规则正文。
7. Ruff、format check、pytest 全部通过，BUG 关闭，README、索引、路线图和 PROG 同步。
