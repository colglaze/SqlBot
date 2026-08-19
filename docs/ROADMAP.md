# 路线图

路线图按可验证的事实级 Agent 2 切片推进。只有当前 Phase 的 DoD 全部满足后，才进入下一阶段。

## Phase 0：文档和边界

状态：**完成**

- 仓库治理、需求、设计、决策和进度文档可追溯；
- 2026-08-19 已用新 REQ/BIZ/DEV 替代“整规则异常集合 SQL”方向。

## Phase 1：后端骨架

状态：**完成（MongoDB 显式禁用）**

- Python、配置、日志、FastAPI 和 CLI 可运行；
- LangGraph 就绪图与数据库生命周期端口可测试；
- 数据库未配置时明确报告 `disabled`。

## Phase 2：FactBindingRequest 接入

状态：**完成**

- Python 固定为 `3.11.9`，包管理继续使用 `uv`；
- RuleReader Schema `2.0.0` 的 camelCase `FactBindingRequest` 可解析；
- SQL Server 上下文包含版本化元数据快照、实体键和关系白名单；
- `derived`、缺少必要上下文及临时表请求被确定性阻塞；
- `SqlTemplateCandidate` 固定为不可执行、待审核候选；
- API、契约和领域测试完全离线。

## Phase 3：DeepSeek 候选 SQL 生成

状态：**待新 REQ/DEV**

- 建立 DeepSeek provider 端口、适配器、Prompt 版本和有界重试；
- 每次只为一个非派生事实生成单条参数化 SQL Server 查询；
- 严格解析 `SqlTemplateCandidate`，拒绝自由文本、空响应和契约外字段；
- 使用固定模型替身覆盖正常、超时、限流、非法 JSON 和重试耗尽。

## Phase 4：SQL AST 与安全门禁

状态：**待规划**

- 单语句、只读、参数化、表/列白名单和禁用临时表门禁通过；
- `usages.conditionId` 覆盖与事实结果契约一致；
- SQL Server 方言解析和安全测试不依赖生产数据库。

## Phase 5：元数据适配与受限验证

状态：**待规划**

- 独立管理 SqlBot 元数据快照、集合和 migration；
- 使用最小权限只读账号进行有超时、结果限制和取消能力的验证；
- 不读取或修改 RuleReader 的 `rule_versions`。

## Phase 6：人工审核与候选发布

状态：**待规划**

- 审核包展示事实契约、元数据快照、SQL、对象范围、覆盖、假设和告警；
- 批准、驳回、revision、并发冲突和不可变审计记录可验证；
- 未经批准的候选始终不可执行。

## 后续候选

更多 SQL 方言、临时表、正式查询调度、整规则诊断集合和通用 ChatBI 都不在当前路线图内，必须建立独立 REQ/BIZ 后再评估。
