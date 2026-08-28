# BIZ-20260827-01：FactBindingRequest V2 权威与授权边界

- 状态：`approved`
- 创建日期：2026-08-27
- 来源：用户明确确认 RuleReader / SqlBot 的 V2 交接职责
- 影响需求：[REQ-20260827-02](../requirements/REQ-20260827-02-rulereader-fact-binding-v2-intake.md)
- 替代范围：[BIZ-20260819-01](BIZ-20260819-01-agent2-role-alignment.md) 中运行时 V1 交接假设；其余双服务
  职责和安全不变量继续有效

## 决策

1. RuleReader `FactBindingRequest 2.0.0` 是当前唯一权威运行时交接契约；V1 仅保留为历史兼容记录。
2. SqlBot 必须独立实现 V2 consumer，不运行时依赖 RuleReader Python 包、源码或兄弟仓库路径。
3. V2 payload 必须保留 `queryRequirements`、`provenance/evidence`、`requestId` 和全部
   `uncertainties`；禁止向 V1 降级、裁剪或字段丢失转换。
4. RuleReader 拥有事实定义、逻辑筛选、聚合和时间语义；SqlBot 拥有项目上下文、元数据快照和物理
   表列授权。任何一方都不能借候选证据越过另一方边界。
5. `sourceCandidate`、`mappingCandidate`、Prompt、provider/model 声明仅是待复核证据，不等于表、视图、
   列、关系或查询权限。
6. 任一 `impact=blocking` 的 uncertainty 必须在模型调用前停止。SqlBot 不得清除、降级、自动补齐或
   用默认值掩盖它。
7. 未发现 blocking 只表示可以开始受治理的元数据解析，状态名固定为
   `readyForMetadataResolution`；禁止使用 `readyForGeneration`。
8. 本切片不实现 SQL AST、不调用 DeepSeek、不连接 SQL Server、不执行 SQL，也不解决真实业务
   不确定性。

## 责任归属

- `businessRuleReview`：实体身份语义、筛选全集、聚合口径、时间范围等规则语义缺口；
- `metadataReview`：逻辑字段到受治理物理表列的解析与授权；
- `sqlBot`：契约版本、请求身份、哈希、证据和内部引用完整性，以及自身固定安全门禁。

责任 owner 只表示缺口应由谁复核，不授予修改 RuleReader 审计记录、生产元数据或 SQL 的权限。
