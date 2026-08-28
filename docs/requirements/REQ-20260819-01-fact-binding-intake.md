# REQ-20260819-01：Agent 2 事实绑定输入与候选模板契约

- 状态：`superseded`
- 创建日期：2026-08-19
- 来源：用户确认 RuleReader / SqlBot 双 Agent 统一基线
- 关联决策：[BIZ-20260819-01](../decisions/BIZ-20260819-01-agent2-role-alignment.md)
- 技术方案：[DEV-20260819-01](../architecture/DEV-20260819-01-fact-binding-contract.md)
- 后续替代：[REQ-20260827-02](REQ-20260827-02-rulereader-fact-binding-v2-intake.md)；本需求实现仅作为
  `FactBindingRequest 1.0.0` legacy 兼容记录
- 替代需求：[REQ-20260818-01](REQ-20260818-01-release-rule-sql-generator.md) 中“整规则异常集合 SQL”目标

## 1. 背景

SqlBot 原方案直接读取整条规则并生成异常集合 SQL，与 RuleReader 长期方案的事实注册和按事实模板职责冲突。Agent 2 应消费 RuleReader 已通过确定性校验的事实绑定请求，并为单个未绑定事实生成候选 SQL 模板。

## 2. 本切片目标

- 固定与 RuleReader 对齐的 camelCase `FactBindingRequest` 输入契约。
- 对输入版本、事实种类、参数、粒度、来源提示和 SQL Server 上下文执行确定性就绪检查。
- 定义不可执行、待审核的 `SqlTemplateCandidate` 输出契约。
- 将首个方言固定为 SQL Server，并显式禁止临时表。
- 配置层预留 DeepSeek，但本切片不调用真实模型、不生成 SQL。

## 3. 范围内

- Python `3.11.9` 精确版本约束和现有 `uv` 工具链。
- Pydantic 输入/输出模型及 camelCase HTTP JSON。
- `POST /api/v1/fact-bindings/validate`：返回 `ready` 或结构化 `blocked` 原因。
- SQL Server 项目上下文最小契约：方言、版本、元数据快照引用、实体键、允许对象和能力。
- 候选 SQL 模板契约：参数化 SQL、结果契约、对象范围、使用覆盖、来源与审核状态。
- 离线契约、领域和 API 测试。

## 4. 范围外

- DeepSeek 真实调用、Prompt、SQL 自动生成和重试。
- SQL AST parser、`EXPLAIN`、试跑、MongoDB 和人工审核状态机。
- 临时表、生产执行和整规则异常集合查询。
- 读取或修改 RuleReader 的 MongoDB 集合。

## 5. 验收标准

1. 输入只接受 `contractVersion=1.0.0`、`schemaVersion=2.0.0` 和 `targetDialect=sqlserver`。
2. `derived` 事实被拒绝进入 SQL 绑定；缺少粒度、参数或元数据上下文时返回 `blocked`。
3. SQL Server 上下文必须声明元数据快照 ID/版本/哈希、实体键和允许对象。
4. `tempTableAllowed` 必须为 `false`；上下文声明临时表能力不能绕过该门禁。
5. `SqlTemplateCandidate` 固定为 `status=candidate`、`executable=false`、`reviewStatus=pending`，SQL 参数和结果列使用结构化契约。
6. API 不调用模型、不执行 SQL、不写数据库。
7. Ruff、format check 和 pytest 通过，文档索引和 PROG 同步。
