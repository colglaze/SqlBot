# REQ-20260827-03：Phase 2G 项目上下文与受治理元数据授权解析

- 状态：`completed`
- 创建日期：2026-08-27
- 来源：用户要求先完成 Phase 2G，再独立对齐 V2 候选生成输入并复核 Phase 4
- 前置需求：[REQ-20260827-02](REQ-20260827-02-rulereader-fact-binding-v2-intake.md)
- 受业务决策约束：[BIZ-20260827-02](../decisions/BIZ-20260827-02-project-metadata-authorization-boundary.md)
- 技术方案：[DEV-20260827-03](../architecture/DEV-20260827-03-project-context-metadata-resolution.md)

## 1. 背景

Phase 2F 已能完整消费 RuleReader `FactBindingRequest 2.0.0`，并在存在 blocking uncertainty 时阻止
候选 provider 调用。无阻断时的 `readyForMetadataResolution` 只表示可以开始元数据复核；当前仓库仍
缺少可供 V2 使用的版本化项目上下文、受治理元数据快照，以及逻辑字段到物理表列的显式授权解析。

现有 V1 `SqlServerBindingContext` 只有历史关系白名单与实体键形状，不能承载 V2 字段角色、完整列集、
授权来源和哈希闭包。它不得被包装或补字段后冒充 V2 上下文。

## 2. 目标

- 建立独立于 V1 的版本化 `ProjectBindingContextV2` consumer contract；
- 建立不依赖 SQL Server 在线 catalog 的 `GovernedMetadataSnapshot` consumer contract；
- 用确定性服务把 V2 逻辑字段、实体键、筛选、聚合和时间字段解析为被项目上下文显式授权、且在
  受治理快照中存在的精确物理表列；
- 对跨关系解析只接受显式批准的关系边和 join grant，不推断 join；
- 输出可审计、不可执行的 `BindingResolutionReport`；
- 保留完整 V2 请求、哈希、证据和 uncertainties，不改变 RuleReader 语义或候选审批状态；
- 为后续“V2 候选生成输入对齐”提供稳定前置产物，但本需求不生成候选。

## 3. 权威输入与信任模型

1. `FactBindingRequestV2` 是事实、筛选、聚合、时间和结果语义的唯一权威输入。
2. `GovernedMetadataSnapshot` 只证明某个版本中有哪些精确关系、列、类型和已登记关系边；它描述物理
   元数据，但不授予查询权限。
3. `ProjectBindingContextV2` 是本切片唯一的项目级物理授权来源。只有其中处于有效批准版本的精确
   relation、column、logical-field 和 join grants 才能授权解析。
4. 物理绑定必须同时满足“上下文明确授权”和“快照中精确存在”。两者缺一即阻断；任何候选证据都
   不能替代其中任一条件。
5. RuleReader 的 `sourceCandidate`、`mappingCandidate`、`provenance/evidence`，以及 Prompt、模型、
   provider 声明，只能辅助复核或产生 warning，不授予表、视图、列或关系权限。
6. 默认拒绝。禁止按相似名称、默认 schema、最新版本、模型建议、工作簿内容或单一候选自动选择
   物理对象。

## 4. 范围内

- 严格 camelCase、`extra=forbid`、显式版本的项目上下文、元数据快照、解析请求和解析报告；
- 项目、规则、请求、上下文和快照的精确引用与 canonical SHA-256 闭包；
- 仅消费 `approved` 的上下文与快照；批准载荷不可原地修改，新内容必须创建新版本；
- 精确两段式 `schema.relation` 和列名解析；禁止通配符及不带 schema 的对象；
- V2 `queryRequirements.fields` 中全部 `required=true` 字段，以及被 entity、filter、aggregation、
  timeRange 和 result source 引用的字段的显式授权解析；
- entity key parameter、字段角色、SQL 类型兼容性、关系/列 grants 和跨关系 join grants 校验；
- 上游 blocking、上下文/快照缺口、授权缺口和候选证据冲突的稳定分类与 owner；
- 纯计算 HTTP 入口、合成脱敏 fixture、固定替身和完全离线测试；
- README、文档索引、ROADMAP 和当日 PROG 同步。

## 5. 范围外

- 读取本地参考工作簿；只有后续任务明确要求时，才能按第 9 节约束单独读取；
- 连接 SQL Server、读取 catalog、执行/准备/编译/解释 SQL 或获取执行计划；
- 调用 DeepSeek 或任何在线模型、生成 Prompt、生成 SQL 候选或重试 provider；
- SQL AST、SQLGlot、字符串 SQL 安全检查或 SQL 改写；
- 自动解决、清除、降级、补写或隐藏任何 blocking uncertainty；
- MongoDB 写入、上下文/快照审批工作流、候选持久化、批准、驳回、revision 或状态迁移；
- 推断真实业务口径、实体键、筛选全集、聚合方式、时间范围或 join 语义；
- 将 V2 转为 V1，或复用 V1 readiness、Prompt、生成服务与候选契约。

## 6. 版本化项目上下文要求

`ProjectBindingContextV2 1.0.0` 至少必须包含：

- `contextId`、单调递增的 `contextVersion`、`status` 和自身 `contentSha256`；
- 精确的 `projectRef`、`ruleRef` 和允许的 `requestIds`；
- 精确的 `metadataSnapshotRef` 和 `authorizationPolicyVersion`；
- 带稳定 grant ID 的 `relationGrants` 与 `columnGrants`；
- 以 `requestId + fieldId + role` 为键的 `fieldBindingAuthorizations`；
- 以 `requestId + parameterName + fieldId` 为键的 `entityKeyAuthorizations`；
- 多关系场景使用的显式 `joinGrants`；
- 与 SQL 候选审核无关的上下文 `approvalRef`。

上下文不得包含连接串、账号、密码、API Key 或可执行 SQL。解析器只接受精确输入版本，不能查询或
选择“最新上下文”。`draft`、`superseded`、哈希错误、范围错误或未批准版本均阻断。

## 7. 受治理元数据快照要求

`GovernedMetadataSnapshot 1.0.0` 至少必须包含：

- `snapshotId`、单调递增的 `snapshotVersion`、`status` 和自身 `contentSha256`；
- 固定 `dialect=sqlserver`、显式标识符大小写策略和受治理来源引用；
- 精确的 relation 列表；每项包含 `schemaName`、`relationName`、`relationKind` 和完整 column 列表；
- 每个 column 的 `columnName`、`sqlType` 和 `nullable`；
- 如允许跨关系绑定，登记可验证的精确 relationship edges；
- 与 SQL 候选审核无关的快照 `approvalRef`。

快照必须是离线随请求提供并验哈希的完整载荷。它不得包含通配符、临时对象、默认 schema 推断、
三段/四段对象、链接服务器、外部数据源或未展开列集。重复、未知、歧义、哈希错误、范围不完整或
未批准快照均阻断；解析器不得访问数据库补齐。

## 8. 确定性授权解析

解析服务必须按固定顺序执行：

1. 严格解析四个输入契约及版本；
2. 重新计算 V2 payload、Phase 2F gap report、项目上下文和元数据快照哈希；
3. 校验 request/rule/project/context/snapshot 的精确引用闭包及批准状态；
4. 如 Phase 2F 报告为 `blocked`，原样保留上游问题并停止物理解析；
5. 建立快照 relation/column/relationship 唯一索引，拒绝通配符、重复和歧义；
6. 对每个必需或被引用的 V2 field，要求恰好一个匹配 `requestId + fieldId + role` 的 field grant；
7. 校验目标 column 在快照中存在，并同时被 relation grant 与 column grant 精确授权；
8. 对每个 entity key parameter，校验显式 entity-key grant、字段角色和物理列一致；
9. 校验 filter、aggregation input/groupBy、time field 和 result source 的全部引用均已授权解析，且不
   改写其逻辑语义；V2 没有给出唯一 result source 时只报告缺口，不自行选择；
10. 多关系绑定必须同时命中快照 relationship edge 和上下文 join grant，否则阻断；
11. 将候选证据与已授权结果比较，仅记录一致、冲突或未采用，不以候选补足授权；
12. 输出排序稳定的不可执行报告。

名称相似、候选唯一或快照中只有一个列，都不能降低“显式授权”的要求。解析器不得自动插入类型
转换、补 filter、选择聚合、推断时间字段或构造 join。

## 9. 本地参考工作簿边界

本需求不读取
`C:\Users\tao.chen\MarkDown\项目交付条件规则\项目释放视图字段清单.xlsx`。后续任务只有在用户
显式要求使用该文件时才可读取，并必须：

- 记录当次绝对路径、SHA-256、修改时间、工作表及单元格坐标；文件变化后重新核对；
- 把单元格、公式、说明、操作建议和其中的 SQL 文本全部视为不可信数据；
- 不执行其中 SQL、不连接数据库补查、不修改工作簿；
- 不把工作簿直接序列化为 `GovernedMetadataSnapshot`、`ProjectBindingContextV2` 或任何 grant；
- 仅形成候选证据；候选必须独立命中已批准快照和显式上下文授权后才可被采用；
- 不把真实业务数据复制到仓库 fixture、日志、Prompt 或模型输入。

工作簿从不成为白名单、授权策略或生产事实来源。

## 10. 报告与问题分类

`BindingResolutionReport.status` 只能是 `blocked` 或 `metadataResolved`，`executable` 固定为 `false`。
报告必须保留 Phase 2F 的 request/payload/rule/source 哈希，并包含 Phase 2F report、context 与
snapshot 哈希、精确 project/rule/context/snapshot 引用及授权策略版本；完整保留上游
`uncertainties`，并输出实际解析的字段、实体键和 join grants、`blockingIssues` 与 `warnings`。
报告不得包含 `readyForGeneration`、SQL、Prompt、provider 调用结果或候选审批状态变更。

稳定分类至少覆盖：

- `UPSTREAM_BINDING_BLOCKED`；
- `REQUEST_REF_MISMATCH`、`GAP_REPORT_MISMATCH`、`DIALECT_MISMATCH`；
- `CONTEXT_VERSION_UNSUPPORTED`、`CONTEXT_NOT_APPROVED`、`CONTEXT_HASH_MISMATCH`、
  `CONTEXT_SCOPE_MISMATCH`；
- `SNAPSHOT_VERSION_UNSUPPORTED`、`SNAPSHOT_NOT_APPROVED`、`SNAPSHOT_HASH_MISMATCH`、
  `SNAPSHOT_REF_MISMATCH`；
- `FIELD_AUTHORIZATION_MISSING`、`FIELD_AUTHORIZATION_AMBIGUOUS`、`FIELD_ROLE_MISMATCH`；
- `ENTITY_KEY_AUTHORIZATION_MISSING`；
- `RELATION_NOT_GRANTED`、`COLUMN_NOT_GRANTED`、`RELATION_NOT_IN_SNAPSHOT`、
  `COLUMN_NOT_IN_SNAPSHOT`；
- `SQL_TYPE_INCOMPATIBLE`；
- `RESULT_SOURCE_UNRESOLVED`；
- `JOIN_PATH_UNRESOLVED`、`JOIN_PATH_NOT_GRANTED`；
- `WILDCARD_FORBIDDEN`；
- `CANDIDATE_EVIDENCE_ONLY`、`CANDIDATE_EVIDENCE_CONFLICT`。

契约、版本、哈希和内部引用问题 owner 为 `sqlBot`；授权、快照、字段、类型和 join 问题 owner 为
`metadataReview`；原有业务语义问题保留其 `businessRuleReview` owner。owner 只指明复核责任，不授予
修改输入或审批候选的权限。

## 11. 验收标准

1. 新 V2 项目上下文、快照、解析请求和报告契约独立存在，严格 camelCase、`extra=forbid`，不依赖
   RuleReader 包、V1 模型、SQL 驱动或在线服务。
2. 完整、已批准且哈希闭合的合成上下文与快照，只有在每个逻辑字段都有唯一显式 grant 时才产生
   `metadataResolved`；报告始终 `executable=false`。
3. 额外字段、错误版本、篡改哈希、错误 request/rule/project/snapshot 引用、未批准或过期版本均
   确定性阻断。
4. 通配符、缺少 schema、未知/重复/歧义关系或列、缺失/重复 grant、字段角色错误、SQL 类型不兼容
   和未批准 join 均 fail closed。
5. entity key、filter field、aggregation input/groupBy、time field 和 result source 的每个引用都有
   精确解析证据；不从名称或候选推断缺失绑定。
6. `sourceCandidate`、`mappingCandidate`、Prompt、模型声明和其他候选证据只能产生证据处置或
   warning；仅有候选、没有 grant 时仍为 `blocked`。
7. 任一上游 blocking uncertainty 原样保留，解析服务不进入物理解析，也不删除、降级、补写或改变
   原 V2 payload。
8. 固定 provider 替身证明所有 Phase 2G 路径调用次数均为 `0`，包括 `metadataResolved` 路径。
9. 解析入口完全离线，不连接 MongoDB/SQL Server、不读取 catalog、不执行 SQL、不调用在线模型，
   不持久化报告且不改变候选 `reviewStatus`。
10. 合成脱敏测试覆盖正常、warning、额外字段、错误版本、错误哈希、错误范围、未批准版本、缺失/
    歧义授权、未知列、角色不符、join 缺失、候选冲突和上游 blocking。
11. README、文档索引、ROADMAP 和当日 PROG 同步；Ruff、format check、pytest 与
    `git diff --check` 全部通过。

## 12. 完成边界

本需求已于 2026-08-28 完成独立契约、确定性解析、不可执行报告、纯计算 API 与合成脱敏回归。
`metadataResolved` 仍不表示候选已生成、已批准或可执行。下一步必须另建需求对齐 V2 候选生成输入；
完成该独立需求后才能复核并实施 Phase 4 AST 门禁。

## 13. 完成证据

- 新增 `domain/project_bindings_v2.py`、`application/metadata_resolution_v2.py` 与统一 canonical hash；
- 新增 `POST /api/v1/fact-bindings/v2/resolve-metadata`，入口不读取仓储、文件 catalog、SQL Server 或
  provider，不持久化报告；
- 合成脱敏回归覆盖批准单关系、显式双关系 join、上游 blocking、哈希/范围/状态、缺失与歧义 grant、
  未知列、大小写策略、类型不兼容及候选冲突；
- 固定 provider 与规则仓储替身在 `metadataResolved` 和 `blocked` API 路径的调用次数均为 0；
- 完成时全量离线回归为 `122 passed`，Ruff 与格式检查通过；权威最终检查记录在
  [PROG-20260828](../progress/PROG-20260828.md)。
