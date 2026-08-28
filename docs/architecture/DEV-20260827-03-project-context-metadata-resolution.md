# DEV-20260827-03：Phase 2G 项目上下文与受治理元数据解析设计

- 状态：`completed`
- 创建日期：2026-08-27
- 实现需求：[REQ-20260827-03](../requirements/REQ-20260827-03-project-context-metadata-resolution.md)
- 受决策约束：[BIZ-20260827-02](../decisions/BIZ-20260827-02-project-metadata-authorization-boundary.md)
- 前置设计：[DEV-20260827-02](DEV-20260827-02-fact-binding-v2-readiness.md)

## 1. 设计结论

Phase 2G 采用“语义、元数据、授权三者分离”的纯计算解析：

```text
FactBindingRequestV2          RuleReader 权威逻辑语义
BindingGapReport              Phase 2F 阻断与输入哈希
GovernedMetadataSnapshot      已治理的物理元数据事实，不授予权限
ProjectBindingContextV2       项目级显式授权，不能补业务语义
            |
            v
BindingResolutionReport       blocked | metadataResolved，永远不可执行
```

解析器只做精确集合相交和引用证明，不做相似度匹配、默认选择或模型推断：

```text
resolved physical binding
  = V2 logical requirement
  ∩ approved project grant
  ∩ approved metadata snapshot
```

任一集合缺失、多个候选、哈希/版本不一致或上游 blocking 都 fail closed。

## 2. 模块边界

计划中的最小纵向切片：

```text
domain/project_bindings_v2.py           上下文、快照、解析请求与报告契约
application/metadata_resolution_v2.py   哈希、引用、授权解析和报告编排
api/app.py                              纯计算 /v2/resolve-metadata 入口
tests/fixtures/                         合成项目、快照和解析 fixture
tests/contract/                         camelCase、extra=forbid、版本与报告契约
tests/unit/                             解析、授权、哈希、blocking 与不变性
tests/integration/                      离线 API 和 provider 零调用证明
```

新领域/应用模块不导入 V1 `fact_bindings`、V1 `candidates`、Prompt、provider、FastAPI、MongoDB/
SQL Server 驱动、SQL parser 或 RuleReader 模块。API 只负责编排和安全错误映射；领域/应用层不依赖
Web 框架。

## 3. Consumer contracts

所有 wire model 严格 camelCase、`extra=forbid`、只按 alias 接受字段，列表内稳定 ID 不允许重复。
所有报告模型不可变、按 camelCase 序列化并拒绝额外字段。

### 3.1 公共引用

```text
ProjectRef
  projectId / projectVersion

ContextRef
  contextId / contextVersion / sha256

MetadataSnapshotRef
  snapshotId / snapshotVersion / sha256

ApprovalRef
  approvalId / policyVersion / approvedAt

PhysicalColumnRef
  schemaName / relationName / columnName
```

`PhysicalColumnRef` 只接受精确两段 relation 加列名；不包含 server/database，不接受空值、通配符、
临时对象或默认 schema。`ApprovalRef` 是上下文/快照治理证据，不是 SQL 候选审核状态。

### 3.2 `ProjectBindingContextV2 1.0.0`

```text
schemaVersion = 1.0.0
contextId / contextVersion / status
projectRef
ruleRef
requestIds[]
metadataSnapshotRef
authorizationPolicyVersion
relationGrants[]
  grantId / schemaName / relationName / access=read
columnGrants[]
  grantId / relationGrantId / columnName
fieldBindingAuthorizations[]
  authorizationId / requestId / fieldId / role / columnGrantId
entityKeyAuthorizations[]
  authorizationId / requestId / parameterName / fieldId / columnGrantId
joinGrants[]
  grantId / leftColumnGrantId / rightColumnGrantId / joinType
approvalRef
contentSha256
```

`status` 契约可记录 `draft | approved | superseded`，但 resolver 只消费 `approved`。`role` 精确复用 V2
`value | entityKey | filter | groupBy | time`。`joinType` 首版仅允许显式策略批准的值；不能从关系名、
字段名或快照唯一性推断。一个 grant 只能授权其明确对象，不允许父级 grant 隐式扩大到所有列。

上下文范围必须精确包含当前 `projectRef`、`ruleRef` 与 `requestId`，并引用请求携带的同一快照版本与
哈希。批准版本的 canonical payload 不可原地改变。

### 3.3 `GovernedMetadataSnapshot 1.0.0`

```text
schemaVersion = 1.0.0
snapshotId / snapshotVersion / status
dialect = sqlserver
identifierCaseSensitivity = sensitive | insensitive
capturedAt
sourceRef
  sourceKind / artifactId / artifactVersion / sha256
relations[]
  schemaName / relationName / relationKind=table|view
  columns[]
    columnName / sqlType / nullable
relationships[]
  relationshipId / leftColumn / rightColumn
approvalRef
contentSha256
```

resolver 只消费 `approved` 快照。`relations` 是该快照受治理范围内的完整列集，不是当前请求裁剪出的
列声明；快照可以包含未授权对象，因此它本身不是白名单。重复关系、重复列、空列集、重复关系边、
未知大小写策略或不完整物理引用均阻断。

首版仅支持两段式 SQL Server table/view，不支持同义词、临时对象、三段/四段对象、链接服务器、
外部源、函数型源或通配符。`sourceRef` 只能指向受治理离线产物；解析器不负责抓取该产物或数据库。

### 3.4 `ResolveMetadataRequestV2 1.0.0`

```text
schemaVersion = 1.0.0
projectRef
bindingRequest       FactBindingRequestV2 2.0.0
bindingGapReport     BindingGapReport
projectContext       ProjectBindingContextV2 1.0.0
metadataSnapshot     GovernedMetadataSnapshot 1.0.0
```

请求必须携带精确完整载荷，不能只传 ID 后从数据库取“最新”版本。它不包含候选、Prompt、provider、
SQL、执行选项或审批状态写入指令。

### 3.5 `BindingResolutionReport 1.0.0`

```text
schemaVersion = 1.0.0
status = blocked | metadataResolved
executable = false
requestId
projectRef / ruleRef / contextRef / metadataSnapshotRef
hashes
  requestSha256 / payloadSha256 / ruleSha256 / sourceSha256
  gapReportSha256 / contextSha256 / snapshotSha256
authorizationPolicyVersion
uncertainties[]                 V2 原始列表的结构化原样副本
resolvedBindings[]
  fieldId / role / physicalColumn / authorizationId / columnGrantId
  evidenceIds[]
resolvedEntityKeys[]
  parameterName / fieldId / physicalColumn / authorizationId
resultSource
  mode=column|aggregation|exists / fieldIds[] / physicalColumns[]
authorizedJoins[]
  grantId / leftColumn / rightColumn / joinType
candidateEvidenceDispositions[]
  evidencePath / disposition=consistent|conflict|notUsed / resolvedColumn
blockingIssues[] / warnings[]
```

报告不包含 `readyForGeneration`、SQL 模板、模型结果或可变 `reviewStatus`。`metadataResolved` 只证明
本报告列出的逻辑引用有唯一授权物理绑定；后续仍须建立独立 V2 候选生成需求。

## 4. Canonical hash 与不可变性

四类输入使用同一算法：camelCase JSON、键排序、紧凑分隔符、UTF-8、Unicode 原样、禁止
NaN/Infinity。上下文和快照计算 `contentSha256` 时排除自身 `contentSha256` 字段；报告哈希排除任何
运行时日志。

应用层重新计算并校验：

- `requestSha256`、`payloadSha256`、`ruleSha256` 与 `sourceSha256` 分别保持 Phase 2F 的既有语义并
  与当前 V2 请求重算值一致；
- `gapReport.requestId` 及其四项 Phase 2F 哈希与当前 V2 请求一致；
- `projectContext.contentSha256` 与重算值一致；
- `metadataSnapshot.contentSha256`、上下文 `metadataSnapshotRef.sha256` 与重算值一致；
- 上下文 `ruleRef`、`requestIds` 和项目范围精确覆盖当前请求；
- 授权策略版本与报告记录一致。

服务不得 trim、补默认值、排序后回写或改写任一输入。测试对调用前后 canonical payload 和
`uncertainties` 逐项比较。

## 5. 固定校验与解析顺序

### 5.1 契约与上游门禁

1. 严格解析请求及嵌套版本；
2. 验 request、gap report、context 与 snapshot 哈希；
3. 验 `requestId`、`ruleRef`、`projectRef` 和 snapshot ref 闭包；
4. 验 `targetDialect=sqlserver`、`requiresMetadataSnapshot=true`、`tempTableAllowed=false`；
5. 如 gap report 状态为 `blocked` 或请求仍含任一 blocking uncertainty，输出
   `UPSTREAM_BINDING_BLOCKED`，复制全部上游 issues/uncertainties，并停止后续物理解析。

即使传入伪造的 `readyForMetadataResolution` gap report，应用层也会从 V2 请求重算 Phase 2F 前置
不变量；不能通过替换报告绕过 blocking。

### 5.2 快照与授权索引

1. 只接受 `approved` context/snapshot；
2. 按快照大小写策略建立唯一 relation、column 和 relationship 索引；
3. 按 grant ID 建立唯一 relation/column/join 索引；
4. 校验每个 column grant 引用已授权 relation，且物理列存在于快照；
5. 校验每个 field/entity-key/join authorization 引用存在且范围一致；
6. 拒绝通配符、重复、悬空引用、范围外授权和同一逻辑键的多个有效授权。

`insensitive` 使用 Unicode `casefold()` 比较并保留批准拼写；`sensitive` 逐码点比较。禁止读取本机或
数据库 collation，也不能用 parser 默认值决定大小写。

### 5.3 V2 逻辑引用解析

应用层先建立字段 registry，然后构造必须解析集合：

- 所有 `queryRequirements.fields[*].required=true` 的 field；
- entity key authorization 引用的 field，以及全部 `entity.keyParameters`；
- 每个 filter 的 `fieldId`；
- aggregation 的 `inputFieldIds` 与 `groupByFieldIds`；
- 非空的 `timeRange.timeFieldId`。

每个 field 必须恰好匹配一个 `requestId + fieldId + role` authorization。目标 column 必须同时命中
relation grant、column grant 和快照；field role 必须与 V2 完全一致。SQL 类型按版本化
`sqlserver-logical-type-compat-v1` 矩阵校验，未知类型或需要推测性转换时阻断，不自动插入 cast。

实体键还必须对每个 `keyParameter` 找到唯一
`requestId + parameterName + fieldId + columnGrantId` authorization，并验证 parameter 存在于
`fact.parameters`、field role 为 `entityKey`。解析器不决定哪个业务字段“应该”是键。

filter operator/value、aggregation function/distinct、time boundary/timezone 和 result contract 只被
透传与引用，不被改写。缺少业务语义应已由 Phase 2F blocking；Phase 2G 不能补齐。

result source 只按 V2 明示结构闭合：`compute` 使用全部 `inputFieldIds`，`exists` 记录 existence 模式，
`none` 或 `precomputed` 必须存在唯一的必需 `role=value` field。缺失或多个可选 value field 时报告
`RESULT_SOURCE_UNRESOLVED` 并交回 `businessRuleReview`，不能从 candidate 或授权列中任选一个。

### 5.4 跨关系闭包

如果解析结果只引用一个 relation，不需要 join grant。如果引用多个 relation，则每条参与的连接边都
必须：

1. 在快照 `relationships` 中精确存在；
2. 两端 column 都由当前上下文授权；
3. 被上下文 `joinGrants` 精确批准；
4. 形成连接全部已解析 relation 的唯一允许闭包。

不存在、未授权、多条可选路径或无法形成唯一闭包时分别报告 `JOIN_PATH_UNRESOLVED` 或
`JOIN_PATH_NOT_GRANTED`。解析器只报告已批准边，不生成 `JOIN` SQL，也不选择 join 方向或业务语义。

### 5.5 候选证据处置

`field.sourceCandidate` 与顶层 `mappingCandidate` 在授权解析完成后才可比较。只有候选自身提供可按
快照大小写策略精确解析的两段 relation 和 column 时，才允许标记一致或冲突；缺少 schema、含通配符
或存在歧义时只能标记 `notUsed`：

- 与授权列一致：记录 `consistent`，仍不提升权限；
- 与授权列冲突：记录 `conflict` warning，以授权上下文为准；
- 没有授权：记录 `notUsed`，并以 `FIELD_AUTHORIZATION_MISSING` 阻断；
- Prompt、parser/model provenance 只记录来源，不参与匹配。

不能因为候选恰好命中快照中的唯一列就创建 grant。

## 6. 报告状态和 issue owner

只有全部前置、快照与授权校验通过，且所有必须解析字段和 join 闭合时，状态才为
`metadataResolved`；否则为 `blocked`。两个状态的 `executable` 都固定为 `false`。

issue 按 `gateOrder + code + fieldPath + normalizedIdentifier` 稳定排序：

- `sqlBot`：契约、版本、哈希、request/rule/project/snapshot 引用和内部悬空 ID；
- `metadataReview`：快照状态、字段/实体键授权、关系/列存在性、类型、join 和候选冲突；
- `businessRuleReview`：原 Phase 2F 业务语义 blocking，原 owner 不变。

`UPSTREAM_BINDING_BLOCKED` 作为汇总 issue 不替换原始问题。warning 不能授予权限，也不能抵消
blocking issue。

## 7. API 与副作用边界

计划新增：

```text
POST /api/v1/fact-bindings/v2/resolve-metadata
```

- 合法且全部解析：200 + `metadataResolved`；
- 合法但被阻断：200 + `blocked` 和稳定 issues；
- Schema/版本错误：422 安全 validation error；
- 未知错误：500 通用错误，不泄露上下文、快照或候选证据内容。

入口只消费请求体，不读取工作簿、文件型 catalog、“最新”上下文、MongoDB 或 SQL Server。它不装配
provider、SQL parser、数据库 client 或 repository，不持久化报告，不创建 SQL 候选，也不改变任何
审批状态。

## 8. 本地参考工作簿的未来适配边界

Phase 2G 首版不实现工作簿 reader。后续若用户建立独立任务显式要求读取本地参考工作簿，应通过独立
`CandidateMetadataEvidenceReader` 端口输出不可信 evidence DTO，并记录文件 SHA-256、修改时间、
sheet/cell 坐标和解析器版本。

该 DTO 不得实现或转换为 context、snapshot、relation grant、column grant、join grant；其中公式、
说明、SQL 和通配符不执行、不展开、不补查。resolver 只能在已有批准 context/snapshot 的前提下把它
标记为 `consistent | conflict | notUsed`，不能据此扩大授权。

## 9. 测试策略

### 9.1 契约与哈希

- 合法合成上下文/快照、额外字段、snake_case、错误 schema version；
- draft/superseded 状态、重复 ID、通配符、空列集、三/四段对象；
- request、gap report、context、snapshot 任一 payload 或引用篡改；
- canonical hash 重复运行稳定，输入和 uncertainties 调用前后不变。

### 9.2 授权解析

- 单 relation 的 value/entityKey/filter/aggregation/time 字段完整解析；
- 缺失与重复 field grant、field role 不符、entity key parameter/field 不一致；
- relation/column 未授权、快照不存在、大小写 sensitive/insensitive；
- SQL 类型兼容、未知类型和需要推断性 cast；
- 两 relation 的唯一批准 join、缺 relationship、缺 join grant、多条路径；
- candidate 一致、冲突、只有 candidate 无 grant；
- 上游 blocking 与 warning，并断言原 issue/uncertainty 完整保留。

### 9.3 集成与隔离

- API 正常、422、blocked 与稳定错误映射；
- 固定 provider、Mongo repository 与 SQL client 替身调用次数始终为 `0`；
- 测试仅使用合成脱敏 fixture，不含真实项目、表列或业务数据；
- 无网络、数据库、在线模型、SQL parser 或 SQL 执行依赖。

## 10. 实施顺序与完成边界

1. 建立领域契约和 canonical hash；
2. 实现前置引用、状态与哈希门禁；
3. 实现快照/grant 索引和字段、实体键、join 授权解析；
4. 组装不可执行报告与纯计算 API；
5. 增加契约、领域、应用与集成离线测试；
6. 同步 README、索引、ROADMAP 和 PROG，将 Phase 2G 标记完成。

Phase 2G 完成后只能开始新的“V2 候选生成输入对齐”需求。该后续需求完成前不得复用 V1 Prompt 或
调用 provider；之后还必须复核 Phase 4 REQ/DEV 中的 V1 输入假设，才能实施 AST 安全门禁。

本设计已于 2026-08-28 按最小离线纵向切片实现。实现没有新增 SQL 驱动、在线模型或文件 reader；
实际验证证据见 [PROG-20260828](../progress/PROG-20260828.md)。
