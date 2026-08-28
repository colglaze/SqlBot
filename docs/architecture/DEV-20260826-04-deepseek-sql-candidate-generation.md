# DEV-20260826-04：DeepSeek 单事实 SQL 候选生成设计

- 状态：`completed`
- 创建日期：2026-08-26
- 更新日期：2026-08-27
- 实现需求：[REQ-20260826-04](../requirements/REQ-20260826-04-deepseek-sql-candidate-generation.md)
- 受业务决策约束：[BIZ-20260819-01](../decisions/BIZ-20260819-01-agent2-role-alignment.md)

## 1. 模块边界

```text
domain/sql_candidates.py          模型输出载荷、最终候选及 provenance 契约
application/ports/candidates.py  provider 端口、请求/响应 DTO 和错误分类
application/prompts.py           版本化 SQL Server 单事实 Prompt
application/candidates.py        readiness、重试、严格解析、交叉校验和候选组装
infrastructure/llm/deepseek.py   DeepSeek Chat Completions HTTP 适配器
infrastructure/llm/__init__.py   按安全配置装配或显式禁用 provider
api/app.py                       候选生成 HTTP 入口和稳定错误映射
tests/fakes.py                   固定模型替身
```

领域和应用模块不导入 HTTP 客户端、DeepSeek/OpenAI SDK、FastAPI、MongoDB 或 SQL Server 驱动。
本阶段不定义 SQL executor 端口。

## 2. Provider 端口

```python
class CandidateModelProvider(Protocol):
    async def generate(self, request: CandidateModelRequest) -> CandidateModelResponse: ...
```

`CandidateModelRequest` 只包含配置模型、Prompt 版本、system/user 消息、JSON object 响应格式和最大
输出 token。`CandidateModelResponse` 只包含原始 content、provider 请求 ID、响应模型和可选系统指纹。

端口错误分为：

- `CandidateProviderTimeoutError`、`CandidateProviderRateLimitError`、
  `CandidateProviderUnavailableError`：可重试；
- `CandidateProviderRejectedError`：认证、余额、请求格式或参数错误，不可重试。

错误对象不保存响应正文，应用层也不回传底层异常文本。

## 3. Prompt 和结构化输出

首版 Prompt 常量为 `sqlserver-fact-candidate-v1`。任何会改变候选语义、输出字段或安全指令的修改
都必须提升版本。system 消息明确包含单事实、单查询、SQL Server、命名参数、只读意图、无临时表、
JSON-only 和不得伪造审核结果等约束；user 消息使用排序稳定的 JSON 传入事实绑定、上下文及输出
JSON Schema。

DeepSeek 适配器调用 `POST <baseUrl>/chat/completions`，设置：

```json
{
  "stream": false,
  "response_format": {"type": "json_object"},
  "messages": ["versioned system prompt", "stable user JSON"]
}
```

DeepSeek 官方文档要求 JSON Output 同时在 Prompt 中明确要求 JSON，并说明 JSON 模式仍可能返回空
内容，因此空内容进入受限的无效候选重试路径：

- [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/)
- [JSON Output](https://api-docs.deepseek.com/guides/json_mode/)
- [Error Codes](https://api-docs.deepseek.com/quick_start/error_codes/)

## 4. 解析与可信组装

模型只生成 `GeneratedCandidatePayload`：

```text
templateCode, sqlTemplate, parameters, result,
allowedObjects, usageCoverage, assumptions, warnings
```

解析使用严格 camelCase、`extra=forbid` 的 Pydantic 模型。以下字段不出现在模型输出中，而由应用
侧创建：

```text
schemaVersion, status, executable, reviewStatus,
ruleRef, bindingRef, factRef, contextRef, dialect, provenance
```

`bindingRef` 保存输入 `contractVersion` 及完整 `bindingRequest` 排序稳定 camelCase JSON 的 SHA-256，
因此即使上游契约没有独立 request ID，也能确定性追溯到生成所消费的确切内容。

应用交叉校验参数契约、命名占位符、结果类型、对象白名单和 `conditionId` 精确覆盖。该检查只能
证明 JSON 候选与输入契约一致，不能证明 SQL 语法、单语句、只读或对象引用安全；这些仍由 Phase 4
AST 与安全门禁负责。

最终候选契约提升到 `schemaVersion=1.1.0`。`provenance` 记录：

```text
provider=deepseek
model                 配置中请求的模型名
responseModel         provider 响应报告的模型名
promptVersion         sqlserver-fact-candidate-v1
providerRequestId     本次成功生成运行
systemFingerprint     provider 可选模型指纹
attemptCount          本次请求实际尝试次数
maxTokens             本次模型请求的最大输出 token
responseFormat        固定 json_object
```

当前没有已持久化的 1.0.0 候选，因此不需要数据迁移；旧夹具同步升级。

## 5. 有界重试

总尝试数在调用前计算为 `maxRetries + 1`，循环不允许动态扩展。退避为确定性的有界指数间隔，测试
注入无等待 sleeper。

| 结果 | 是否重试 | 耗尽后的应用错误 |
| --- | --- | --- |
| timeout / network | 是 | `CANDIDATE_PROVIDER_UNAVAILABLE` |
| HTTP 429 | 是 | `CANDIDATE_PROVIDER_UNAVAILABLE` |
| HTTP 5xx | 是 | `CANDIDATE_PROVIDER_UNAVAILABLE` |
| 空 content / 非法 JSON / 契约不匹配 | 是 | `CANDIDATE_OUTPUT_INVALID` |
| HTTP 400 / 401 / 402 / 422 | 否 | `CANDIDATE_PROVIDER_REJECTED` |

如果一次请求先遇到无效输出、后遇到暂时性错误，则以最后一次尝试的分类返回稳定错误。任何错误
响应都不包含 provider 原文。

## 6. API 与装配

```http
POST /api/v1/sql-candidates/generate
```

请求 Shape 与验证入口相同。provider 仅在 API Key、base URL 和模型全部配置时装配；未配置返回
503。测试可以显式注入固定模型替身，不需要配置密钥。

成功响应仍为不可执行候选。输入 readiness 阻塞映射为 409 并返回确定性 issues；provider 拒绝、
无效输出和暂时不可用分别映射稳定的 502/503 错误。

## 7. 不执行保证

数据流在 `SqlTemplateCandidate` 返回处终止：

```text
FactBindingRequest -> readiness -> Prompt -> provider -> strict parse -> candidate response
```

本阶段不导入 SQL 驱动、不创建连接、不调用数据库、不保存候选，也不提供 `execute`、`validate on
database` 或 `publish` 方法。候选上的 `executable=false` 是不可覆盖的 Literal；AST、安全门禁、
受限验证和人工审核完成前不存在执行路径。

## 8. DataEase SQLBot 借鉴思路与边界

### 8.1 参考基线与许可证

参考项目为 [dataease/SQLBot](https://github.com/dataease/SQLBot)，本次分析固定到提交
[`1fdc28b6fd99fca7e2ae082a7bb2cfc736334d11`](https://github.com/dataease/SQLBot/tree/1fdc28b6fd99fca7e2ae082a7bb2cfc736334d11)，
分析日期为 2026-08-27。该项目使用带附加条件的 GPLv3 许可；本项目只借鉴公开架构思路，不复制其
源码、Prompt、模板、前端资源或品牌资产。若以后需要引入其依赖或派生实现，必须先单独完成许可证
兼容性审查并记录来源，不能把“参考思路”解释为复制授权。

主要参考入口：

- [项目 README 与产品边界](https://github.com/dataease/SQLBot/blob/1fdc28b6fd99fca7e2ae082a7bb2cfc736334d11/README.md)；
- [模型工厂与 provider 隔离](https://github.com/dataease/SQLBot/blob/1fdc28b6fd99fca7e2ae082a7bb2cfc736334d11/backend/apps/ai_model/model_factory.py)；
- [SQL 上下文组装、分步日志、AST 对象检查和执行流程](https://github.com/dataease/SQLBot/blob/1fdc28b6fd99fca7e2ae082a7bb2cfc736334d11/backend/apps/chat/task/llm.py)；
- [方言模板、Schema、术语和 SQL 示例组合](https://github.com/dataease/SQLBot/blob/1fdc28b6fd99fca7e2ae082a7bb2cfc736334d11/backend/apps/chat/models/chat_model.py)；
- [SQL 示例检索](https://github.com/dataease/SQLBot/blob/1fdc28b6fd99fca7e2ae082a7bb2cfc736334d11/backend/apps/data_training/curd/data_training.py)、
  [术语检索](https://github.com/dataease/SQLBot/blob/1fdc28b6fd99fca7e2ae082a7bb2cfc736334d11/backend/apps/terminology/curd/terminology.py)与
  [工作空间资源权限](https://github.com/dataease/SQLBot/blob/1fdc28b6fd99fca7e2ae082a7bb2cfc736334d11/backend/apps/system/schemas/permission.py)；
- [许可证原文](https://github.com/dataease/SQLBot/blob/1fdc28b6fd99fca7e2ae082a7bb2cfc736334d11/LICENSE)。

### 8.2 可借鉴能力的本项目落地

| SQLBot 公开实现中的思路 | ReleaseSQLBot 的落地方式 | 状态/阶段 |
| --- | --- | --- |
| 按数据库方言组合 Schema、基础 SQL 示例、术语、训练示例和自定义提示 | 继续以 `FactBindingRequest + TargetSchemaSnapshot + RuleReader ruleRef` 为唯一可信主上下文；Prompt 只消费类型化、版本化输入。将来引入术语或示例时必须作为独立证据引用，不能变成自由文本生产事实 | Phase 2/3 已建立主上下文；增强项待独立 REQ |
| 用关键词和向量相似度筛选术语及 SQL 示例，并受工作空间、数据源范围约束 | 后续可定义 `ContextEvidenceRetriever` 端口，检索范围必须先由项目、数据源、方言和元数据快照确定；记录证据 ID/版本、检索模型、索引版本、分数和截断原因。检索结果只改善生成，不授予表列权限 | 候选增强项，不进入 Phase 4 安全结论 |
| 通过模型工厂支持 OpenAI 兼容、Azure、vLLM 等 provider | 保留现有 `CandidateModelProvider` 端口和 DeepSeek 适配器；新增模型只增加适配器与配置，不改变领域候选、可信组装或错误分类 | Phase 3 已落地 |
| 将选表、术语筛选、示例筛选、生成、权限处理和执行记为独立操作 | 将生成、AST 解析、安全判定、执行计划、受限验证和人工审核保持为独立运行记录；每步记录输入引用、版本、结果与稳定错误码，不合并成一次不可观测模型调用 | Phase 3 已有 provenance；Phase 4—6 扩展审计 |
| 工作空间/数据源先做资源隔离，并从真实 SQL AST 提取表名，不信任模型返回的 `tables` | Phase 4 parser 输出是对象引用的权威来源；`allowedObjects` 只是候选声明，必须与 AST 表/列、元数据快照、项目范围和关系白名单逐项交叉校验。权限策略由确定性适配器执行，禁止模型自行扩大范围 | Phase 4 必须实现 |
| 生成阶段可以停止，也可以继续权限处理和查询执行，并设置查询行数限制 | 本项目固定在候选生成后停止。Phase 5 即使增加受限验证，也必须通过独立端口、最小权限只读账号、超时、最大行数和结果字节上限运行；正式执行仍不属于验证接口 | Phase 5 规划约束 |
| 利用历史轮次和上次执行错误帮助重新生成 | 后续只允许把脱敏、结构化的校验失败代码反馈给新生成运行；不得把原始数据库错误、敏感数据或旧模型推理原文重新注入。修订必须产生新候选哈希并重跑全部门禁 | 待候选 revision/审核 REQ |

### 8.3 明确不采用

- 不扩展为通用自然语言问数、连续对话、图表生成、预测、Web 嵌入或 MCP ChatBI；这些能力超出
  单事实 SQL 候选和受审计发布链路。
- 不把 RAG 相似度、术语、SQL 示例、自定义 Prompt 或模型声明当作授权来源。权限只能来自项目
  上下文、版本化元数据快照和显式策略。
- 不采用“模型生成 SQL 后直接连接数据源执行”的同一服务流程。生成、AST/安全、受限验证和人工
  审核必须物理和领域上分离，当前仍没有 SQL executor。
- 不允许权限注入、格式化或人工批准后在原候选上改写 SQL；任何 SQL 文本变化都创建新 revision，
  重新计算哈希并重新通过 AST、安全、验证和审核。
- 不复制参考项目的源码、Prompt、方言示例、模板或资源；本节只记录独立实现时采用的设计原则。

### 8.4 对后续 Phase 4 的强制输入

Phase 4 的新 REQ/DEV 必须继承以下约束：

1. AST 解析结果而非模型的 `allowedObjects` 是真实表列引用的权威来源；解析失败默认拒绝。
2. parser 与 SQL Server dialect adapter 隔离，禁止把方言分支散落在安全规则中。
3. 单语句、只读、命名参数、表列白名单、关系范围、临时表禁令和 `conditionId` 覆盖分别产生可
   审计门禁结果，不能只返回一个布尔值。
4. 任何策略性 SQL 改写都必须创建新候选 revision，禁止批准后修改 SQL 文本。
5. 本阶段仍不连接 SQL Server；真实查询验证只能在 Phase 5 的最小权限受限环境中启用。
