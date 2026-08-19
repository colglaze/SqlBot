# DEV-20260819-01：事实绑定输入与 SQL 候选契约

- 状态：`completed`
- 创建日期：2026-08-19
- 实现需求：[REQ-20260819-01](../requirements/REQ-20260819-01-fact-binding-intake.md)
- 受业务决策约束：[BIZ-20260819-01](../decisions/BIZ-20260819-01-agent2-role-alignment.md)

## 1. 模块

```text
domain/fact_bindings.py   输入、上下文、就绪结果
domain/sql_candidates.py  候选模板、参数、结果和来源契约
application/bindings.py   确定性就绪检查
api/app.py                validate 入口
config/settings.py        DeepSeek 安全配置摘要
```

领域模块不导入 FastAPI、LangGraph、数据库驱动或模型 SDK。

## 2. 输入契约

`FactBindingRequest` 与 RuleReader `contractVersion=1.0.0` 对齐，包含：

- `ruleRef`：规则、Schema 和来源哈希；
- `fact`：事实编码、种类、数据类型、空值策略、粒度、参数和可选派生表达式；
- `usages`：条件 ID、路径、操作符和表达式侧；
- `mappingCandidate`：现有候选视图/字段和审核状态，不携带 SQL 表达式；
- `examples`：事实级输入与规则结果样例；
- 固定 `targetDialect=sqlserver`、`requiresMetadataSnapshot=true`、`tempTableAllowed=false`。

`SqlServerBindingContext` 包含不可变元数据快照引用、SQL Server 版本、实体键、允许关系和能力。连接串和凭据不进入领域对象。

## 3. 就绪检查

以下任一情况返回 `blocked`：

- 契约或规则 Schema 版本不支持；
- 事实为 `derived`；
- 事实编码、粒度、参数为空；
- 目标不是 SQL Server；
- 缺少元数据快照 ID/版本/哈希；
- 实体键或允许关系为空；
- 必填事实参数没有实体键来源，或实体键/候选映射关系不在白名单；
- 请求或上下文允许临时表。

就绪只表示可以进入后续生成阶段，不表示 SQL 已生成、校验或批准。

## 4. 候选模板契约

`SqlTemplateCandidate` 包含：

- `schemaVersion=1.0.0`、`status=candidate`、`executable=false`；
- `templateCode`、`factRef`、`ruleRef`、`contextRef`；
- `dialect=sqlserver`、单条参数化 `sqlTemplate`；
- 参数定义、标量结果列 `fact_value`、允许对象；
- 使用覆盖、假设、告警；
- DeepSeek 模型和 Prompt 版本来源；
- `reviewStatus=pending`。

本切片只定义和测试契约，不创建候选实例的生产用例。

## 5. API

```http
POST /api/v1/fact-bindings/validate
```

请求包含 `bindingRequest` 和 `context`；200 返回 `ready` 或 `blocked` 及稳定原因码。输入 Shape 错误由 FastAPI/Pydantic 返回 422。

## 6. 测试

- RuleReader 对齐示例通过输入契约；
- Schema `1.0.0`、`derived`、缺参数、缺元数据和临时表请求被阻塞；
- 候选模板拒绝非参数化/多语句/可执行状态的形状约束在后续 SQL AST 切片实现；本切片只冻结字段与状态。
- API 与配置测试完全离线。
