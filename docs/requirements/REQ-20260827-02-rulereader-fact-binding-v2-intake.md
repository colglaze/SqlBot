# REQ-20260827-02：RuleReader FactBindingRequest 2.0.0 接入与阻断分析

- 状态：`completed`
- 创建日期：2026-08-27
- 来源：用户要求以 RuleReader `FactBindingRequest 2.0.0` 作为当前权威交接契约
- 关联决策：[BIZ-20260827-01](../decisions/BIZ-20260827-01-fact-binding-v2-authority-boundary.md)
- 技术方案：[DEV-20260827-02](../architecture/DEV-20260827-02-fact-binding-v2-readiness.md)
- 替代范围：[REQ-20260819-01](REQ-20260819-01-fact-binding-intake.md) 中面向运行时的
  `FactBindingRequest 1.0.0` intake；旧实现仅保留为历史兼容路径

## 1. 背景

RuleReader 已将运行时事实交接冻结为严格 camelCase 的 `FactBindingRequest 2.0.0`，并停止输出
`1.0.0`。SqlBot 当前仍以 V1 模型和 V1 readiness 驱动 DeepSeek 候选生成，无法完整保留
`queryRequirements`、`provenance/evidence`、`requestId` 与 `uncertainties`，也无法证明 blocking
不确定性在模型调用前被阻断。

## 2. 目标

- 在 SqlBot 领域层建立不依赖 RuleReader 包或兄弟仓库路径的独立 V2 consumer contract；
- 完整保留 V2 事实、逻辑查询要求、来源证据和不确定性，不经过 V1 降级转换；
- 以纯计算、确定性的 intake/readiness 服务校验版本、身份、证据和引用闭包；
- 对未决实体键、字段、筛选、聚合和时间语义形成稳定的 `BindingGapReport`；
- 任一 `impact=blocking` 的上游不确定性都使报告为 `blocked`，且 V2 分析路径不调用候选模型；
- 无阻断时只允许进入后续元数据解析，不能宣称已可生成、可执行或已授权。

## 3. 权威边界

1. RuleReader `FactBindingRequest 2.0.0` 是当前权威事实交接契约；`1.0.0` 只作 legacy 记录。
2. 禁止把 V2 降级、裁剪或丢字段转换成 V1；V2 真实路径不得经过 V1 readiness、Prompt 或生成服务。
3. `queryRequirements`、`provenance`、`evidence`、`requestId` 和 `uncertainties` 必须原样保留。
4. RuleReader 负责事实、筛选、聚合和时间语义；SqlBot 负责项目上下文、元数据快照及物理表列授权。
5. `sourceCandidate`、`mappingCandidate`、Prompt、provider/model 声明都只是候选证据，不授予表列权限。
6. 不得自动删除、降级、补写或把 blocking uncertainty 解释为已解决。

## 4. 范围内

- 严格 camelCase、`extra=forbid` 的 `FactBindingRequestV2` 及全部嵌套 consumer 模型；
- `requestId == <ruleVersion>#<factCode>`、规则/契约版本和固定 SQL Server 安全标志校验；
- 全部 `evidenceIds` 对 `provenance.evidence` 的解析校验；
- 字段、筛选、聚合、时间范围和参数引用闭包校验；
- `derived` 事实阻断；
- 未决 requirement 与同编码 blocking uncertainty 的对应校验；
- 结构化、不可执行的 `BindingGapReport` 和确定性哈希；
- 合成脱敏 fixture、上游 Schema 来源记录、固定 provider 替身及离线测试；
- 一个只执行 V2 阻断分析的 HTTP 入口，用于证明该路径不调用既有 provider。

## 5. 范围外

- SQL AST、SQLGlot 或其他 parser；
- DeepSeek/在线模型调用、Prompt 生成或 SQL 候选生成；
- SQL Server 连接、catalog 读取、执行计划、准备、试跑或正式执行；
- 真实业务不确定性解析、自动默认值、自动授权或人工审核替代；
- MongoDB 写入、RuleReader 文件修改或生产系统访问。

## 6. 报告与分类

`BindingGapReport.status` 只能是 `blocked` 或 `readyForMetadataResolution`，`executable` 固定为
`false`。报告包含请求标识及 request ID、完整 payload、ruleRef 和 source 的确定性 SHA-256；包含
`blockingIssues` 与 `warnings`，每项 owner 只能为 `businessRuleReview`、`metadataReview` 或 `sqlBot`。
报告不得输出 `readyForGeneration`。

业务缺口至少覆盖：

- `ENTITY_KEY_UNRESOLVED`；
- `VALUE_FIELD_UNRESOLVED`；
- `FILTER_FIELD_UNRESOLVED`；
- `FILTER_SET_INCOMPLETE`；
- `AGGREGATION_UNRESOLVED`；
- `TIME_RANGE_UNRESOLVED`。

## 7. 验收标准

1. RuleReader 提供的合法未决 V2 形状可被独立 consumer 完整解析并按 camelCase 无损序列化；额外字段和
   snake_case 输入被拒绝。
2. 错误契约版本、错误 rule schema、伪造 `requestId`、未知 `evidenceId` 和悬空字段引用均确定性阻断。
3. 每个 unresolved requirement 必须能找到对应编码且 `impact=blocking` 的 uncertainty；缺失时报告
   `BLOCKING_UNCERTAINTY_MISSING`，但不修改原请求。
4. 每个现有 blocking uncertainty 原样映射为 blocking issue；warning 只进入 warnings，不能被提升为
   授权或被静默丢弃。
5. 任一 blocking 时，注入应用的固定候选 provider 调用次数为 `0`。
6. 无 blocking 时状态只能为 `readyForMetadataResolution`；候选来源与解析器来源仍产生“非授权”警告。
7. V1 模型和旧测试保留并明确标记 legacy；V2 分析代码不导入 V1 readiness、Prompt 或生成模块。
8. fixture 为合成、脱敏数据，并记录上游 Schema `$id`、版本、仓库相对路径和 SHA-256。
9. README、文档索引、ROADMAP 与当日 PROG 同步；Phase 4 明确依赖本切片和后续元数据解析完成。
10. `py -m uv run ruff check .`、`py -m uv run ruff format --check .`、`py -m uv run pytest` 和
    `git diff --check` 全部通过。
