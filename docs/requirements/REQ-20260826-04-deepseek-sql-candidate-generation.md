# REQ-20260826-04：DeepSeek 单事实 SQL 候选生成

- 状态：`completed`
- 创建日期：2026-08-26
- 来源：用户要求推进 Phase 3 DeepSeek 候选 SQL 生成
- 前置需求：[REQ-20260819-01](REQ-20260819-01-fact-binding-intake.md)
- 受业务决策约束：[BIZ-20260819-01](../decisions/BIZ-20260819-01-agent2-role-alignment.md)
- 技术方案：[DEV-20260826-04](../architecture/DEV-20260826-04-deepseek-sql-candidate-generation.md)

## 1. 背景

Phase 2 已能确定性判断单个 `FactBindingRequest` 是否具备 SQL 生成条件，并冻结了不可执行的
`SqlTemplateCandidate` 基线契约。Phase 3 需要接入 DeepSeek，但模型输出仍是不可信输入；本阶段
只能形成候选，不能把“生成成功”解释为 SQL 已通过语义、安全、数据库或人工审核。

## 2. 目标

- 建立与具体 SDK 隔离的候选模型 provider 端口和 DeepSeek HTTP 适配器；
- 使用显式版本号管理 SQL Server 单事实 Prompt，并追踪请求模型、响应模型和生成运行；
- 对超时、限流、服务端错误、空响应和非法候选执行有界重试；
- 严格解析结构化 JSON 候选，拒绝自由文本、Markdown 包裹、契约外字段和引用不一致；
- 为一个已就绪的非派生事实生成一个参数化 SQL Server `sqlTemplate` 候选；
- 提供固定模型替身，使基础回归不依赖在线模型、生产数据库或网络。

## 3. 范围内

- `POST /api/v1/sql-candidates/generate`，请求沿用一个 `bindingRequest + context`；
- DeepSeek Chat Completions 非流式 JSON Output 调用；
- Prompt 常量、Prompt 版本和稳定 JSON 输入；
- provider 请求/响应 DTO、可重试与不可重试错误分类；
- 候选载荷的严格 Pydantic 解析和应用侧交叉校验；
- `SqlTemplateCandidate` 追踪 `FactBindingRequest` 确定性哈希、配置模型、响应模型、Prompt 版本、
  provider 请求 ID、系统指纹、输出配置和实际尝试次数；
- 正常、超时、429、非法 JSON、空响应、契约不匹配和重试耗尽的离线测试。

## 4. 范围外

- SQL AST 解析、单语句语义证明、只读安全判定、表列实际引用提取和注入检测；
- 连接 SQL Server、执行计划、试跑、正式查询或任何形式的候选 SQL 执行；
- MongoDB 候选持久化、人工审核、批准、发布或版本状态机；
- 临时表、跨方言、整规则异常集合和 `derived` 事实生成；
- 从真实数据库或模型自行推断缺失的表、列、关系或业务语义。

## 5. 功能与安全要求

1. 只有现有 readiness 结果为 `ready` 的请求可以调用 provider；阻塞请求不得消耗模型调用。
2. 每次请求只携带一个事实。Prompt 只允许生成一个 SQL Server 查询模板，并要求所有事实参数
   使用命名占位符，不得把业务参数值拼进 SQL。
3. 模型输出只能包含候选载荷字段。状态、可执行标志、审核状态、规则/事实/绑定请求/上下文引用和
   provenance 由应用侧根据可信输入及 provider 元数据创建，模型不能覆盖。绑定请求引用使用排序
   稳定的 camelCase JSON SHA-256，精确定位本次输入内容。
4. 结构化解析必须拒绝空响应、非 JSON object、Markdown 代码块、字段缺失、类型错误和额外字段。
5. 候选参数名、类型、必填性和来源必须与事实参数逐项一致；`usageCoverage` 必须无重复并精确覆盖
   输入中的稳定 `conditionId`；声明的 `allowedObjects` 必须属于上下文关系白名单。
6. `deepseekMaxRetries=N` 表示首次调用之外最多再尝试 N 次，总调用次数永远不超过 `N + 1`。
   超时、网络故障、429、5xx、空响应和无效候选允许在该边界内重试；400、401、402 和 422 不重试。
7. 返回候选必须固定为 `status=candidate`、`executable=false`、`reviewStatus=pending`，并附加明确的
   “未通过 AST、安全门禁、受限验证和人工审核，不得执行”告警。
8. API、应用服务和 provider 适配器不得依赖 SQL 执行端口，也不得建立 SQL Server 连接。
9. 错误响应和日志不得包含 API Key、完整 Prompt、模型原始输出或上游错误正文。

## 6. 验收标准

1. 固定模型替身返回合法 JSON 时，生成一个 camelCase `SqlTemplateCandidate`，引用与输入一致，
   provenance 可定位绑定请求、Prompt、模型配置、模型响应和本次生成运行。
2. provider 端口不导入 DeepSeek/OpenAI SDK；DeepSeek 适配器使用 JSON Output 且不接受流式响应。
3. 超时后成功、429 后成功、非法 JSON 后成功都只在配置边界内重试；重试耗尽返回稳定错误码。
4. provider 返回额外字段、错误参数、漏掉 `conditionId`、越权对象或空内容时不能产出候选。
5. 未配置 DeepSeek 时生成 API 返回明确的 503；固定替身测试不读取本机 `.env`，不访问真实网络。
6. 候选即使看起来是合法 SQL，也没有任何代码路径可执行它；Phase 4/5/6 完成前始终不可执行。
7. Ruff、format check 和 pytest 通过，README、文档索引、路线图和 PROG 同步。
