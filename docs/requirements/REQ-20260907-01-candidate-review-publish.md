# REQ-20260907-01：SQL 候选人工审核与发布（Phase 6）

- 状态：`proposed`
- 创建日期：2026-09-07
- 来源：用户要求在"不修改代码、只做方案"的约束下推进路线图中唯一处于"待规划"状态的
  Phase 6（人工审核与候选发布）
- 前置需求：[REQ-20260906-02](REQ-20260906-02-real-upstream-handoff-evidence-loop.md)、
  [REQ-20260906-04](REQ-20260906-04-v3-downstream-pipeline-alignment.md)、
  [REQ-20260906-01](REQ-20260906-01-v2-candidate-persistence.md)、
  [REQ-20260905-01](REQ-20260905-01-restricted-sqlserver-validation.md)、
  [REQ-20260827-01](REQ-20260827-01-sql-ast-safety-gate.md)
- 受业务决策约束：[BIZ-20260907-01](../decisions/BIZ-20260907-01-review-publish-authority-boundary.md)、
  [BIZ-20260905-01](../decisions/BIZ-20260905-01-restricted-sqlserver-validation-boundary.md)、
  [BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md)、
  [BIZ-20260828-01](../decisions/BIZ-20260828-01-v2-candidate-authority-boundary.md)
- 技术方案：[DEV-20260907-01](../architecture/DEV-20260907-01-candidate-review-publish.md)
- 关联进度：[PROG-20260907](../progress/PROG-20260907.md)

## 1. 背景

当前全部生成链产物固定为 `candidate / executable=false / reviewStatus=pending`：候选 insert-only
持久化（[REQ-20260906-01](REQ-20260906-01-v2-candidate-persistence.md)）、Phase 4 静态门禁、
Phase 5A describe-only 验证都不改变候选审核状态。
[BIZ-20260905-01](../decisions/BIZ-20260905-01-restricted-sqlserver-validation-boundary.md) 第 9 条
明确"Phase 6 才拥有人工批准、驳回、revision 和发布状态机"。

仓库核心不变量（AGENTS.md 第 3 节）要求：已批准 SQL 模板的业务载荷是不可变审计记录；生命周期
状态只可按受审计状态机变化；内容修订必须创建新版本，禁止原地覆盖；未通过静态校验、受限环境
验证和人工审核的 SQL 不得发布或用于生产查询。当前仓库不存在任何承载这些语义的契约、存储或
入口——审核状态机是缺失的最后一段链路。

同时，[REQ-20260906-04](REQ-20260906-04-v3-downstream-pipeline-alignment.md) 已为
`FactBindingRequest 3.0.0` 规划独立下游契约链（M1–M6）；V3 候选一旦产生，同样需要审核与发布
通道，且 V2/V3 隔离不变量必须延伸到审核与发布层。

## 2. 目标

1. 冻结**审核包契约**：把候选、生成请求、Phase 4 静态报告、批准上下文/快照引用与可选 Phase 5
   验证证据组装为一个可独立重算哈希闭包的不可变审核包，覆盖路线图要求的展示内容——事实契约、
   元数据快照、SQL、对象范围、覆盖（usage/conditionId）、假设与告警。
2. 冻结**审核决策契约与生命周期状态机**：`approve | reject | requestRevision` 三类人工决策，
   状态机 `pending → underReview → approved | rejected | revisionRequested`（approved 之后另有
   `published` 终态），全部通过独立 insert-only 决策记录 + 生命周期事件/指针（compare-and-swap）
   表达；候选业务载荷与 `reviewStatus=pending` 契约字段永不修改。
3. 冻结**并发冲突语义**：同一候选版本至多一个有效审核结果；并发提交由确定性 CAS 裁决，败者
   得到确定性冲突结果且零写入。
4. 冻结**发布门禁与发布记录**：发布是独立于批准的门禁动作，产出自包含、不可变的发布审计记录；
   发布门禁聚合静态门禁、受限验证证据与人工批准证据，任一缺失即阻断（fail closed）。
5. 冻结**修订语义**：驳回与 revision 请求不修改、不删除原候选；修订通过新一轮生成产生新候选
   版本（新 `contentSha256`），决策记录可引用后续候选形成可追溯链。
6. 冻结**审计回读与入口边界**：首个实施切片只提供本地 CLI（审核包导出、决策提交、发布、状态
   回读）；HTTP 入口被独立认证授权设计门禁（延续
   [BIZ-20260905-01](../decisions/BIZ-20260905-01-restricted-sqlserver-validation-boundary.md)
   第 5 节），不在本需求内实现。
7. 冻结 **V2/V3 隔离在审核与发布层的延伸**：审核/发布记录必须携带候选契约版本与内容哈希闭包，
   V2/V3 记录永不混淆，禁止任何 V3↔V2 转换。
8. 建立实施与使用的双轨门禁：离线机制实施与真实审核/发布使用分别登记前置条件，互不冒充。

## 3. 非目标

- 不实现任何代码；本需求先以
  [DEV-20260907-01](../architecture/DEV-20260907-01-candidate-review-publish.md) 冻结设计，
  实施按里程碑另起任务；
- 不新增任何 SQL 执行、取数、prepare、调度或生产查询端口；Phase 6 产物始终证明"已批准"，
  不签发运行许可；
- 不实现 HTTP 审核入口、审核工作台、多角色权限体系与批量审核；HTTP 入口的认证授权设计只冻结
  边界与前置，实现另立需求；
- 不实现任何自动或半自动批准路径；LLM 输出、静态报告或验证报告都不能产生审核决策；
- 不把 V3 转换、包装或降级为 V2/V1，也不把 V2 提升为 V3；
- 不修改候选持久化（`sql_template_candidates`）、Phase 4、Phase 5 的任何现有行为、契约与测试期望；
- 不写 RuleReader 任何集合；不连接 MongoDB、SQL Server、DeepSeek、飞书或私有
  `RuleDataReferences` bundle 完成任何真实验证；
- 不把"机制已实现"表述为"真实候选已审核/已发布"。

## 4. 角色与审核主体

- **人工审核者（reviewer）**：唯一拥有 `approve | reject | requestRevision` 决策权的主体；每个
  决策必须携带可审计的审核主体身份、时间与依据引用；
- **发布操作者（publisher）**：执行发布门禁并落发布记录的主体，可以与审核者相同，但发布门禁
  本身是独立校验，不信任决策记录自带的结论；
- **审计方（auditor）**：只读回读审核包、决策记录、生命周期事件与发布记录；
- **系统（sqlBot）**：只负责审核包装配与校验、决策服务、状态机与存储适配；永不产生决策内容，
  永不充当审核主体；
- **生成链（provider/LLM）**：与审核完全隔离；候选的 `provenance` 主体（模型、Prompt 版本）
  进入审核包供人工判断，但不能据此自动豁免任何门禁。

## 5. 功能需求

### 5.1 审核包

1. 审核包由应用从权威输入确定性组装：候选本体（按精确 `contentSha256` 从候选存储读取或由请求
   携带并经存储核验）、完整 `GenerateSqlCandidateRequestV2`、`SqlStaticValidationReportV2`、
   批准 `ProjectBindingContextV2` 与 `GovernedMetadataSnapshot` 引用闭包，以及可选的 Phase 5
   验证证据引用（validation run ID、报告哈希、模式、状态）。
2. 装配时必须完成完整引用闭包校验：候选自哈希、generation input hash、resolution report hash、
   context/snapshot 自哈希与 `metadataSnapshotRef` 匹配；任一不一致即装配失败，不产出审核包。
3. 审核包必须覆盖路线图要求的展示内容：事实契约（fact_ref、参数、结果契约）、元数据快照引用、
   SQL 文本、对象范围（declared_objects）、覆盖（`declared_usage_coverage` 与静态报告 coverage
   结论）、假设与告警（assumptions/warnings）。
4. 审核包自身携带排除自身字段后的 canonical `contentSha256`，可独立重算；时间字段不参与哈希。
5. 审核包是只读展示与决策依据对象，本身不改变任何候选状态。

### 5.2 审核决策与生命周期状态机

1. 决策类型固定 `approve | reject | requestRevision`；状态机固定
   `pending → underReview → approved | rejected | revisionRequested`，其中 `rejected` 与
   `revisionRequested` 对该候选版本是终态，`approved` 可停留并等待发布门禁，`published` 是发布
   后终态。
2. 候选本体与其存储文档的 `reviewStatus=pending` 永不修改；有效审核状态只存在于独立的决策记录、
   生命周期事件与状态指针中，下游通过候选 `contentSha256` + 候选契约版本关联。
3. 决策记录必须包含：审核主体、决策类型、所依据的审核包哈希与候选哈希、决策时间、理由的脱敏
   引用；`requestRevision` 决策可携带后续新候选的 `contentSha256` 引用（若已存在）。
4. 决策服务在记录任何决策前必须重新校验：审核包哈希闭包重算一致；对包内 generation request
   重算 Phase 2G 与 Phase 4 并与包内报告 canonical 比对；任一不一致 fail closed 且零写入。
5. 每个候选版本至多一个有效决策记录；无效转换与并发败者不落库（零写入），返回稳定冲突 issue。
6. 生命周期变化只能通过 insert-only 生命周期事件与 compare-and-swap 状态指针表达（模式与
   [BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md) 第 5 条
   冻结的 context 生命周期方案一致），禁止 update/replace/delete 任何审核记录。

### 5.3 发布门禁与发布记录

1. 发布是独立门禁动作，门禁聚合（全部满足才允许）：
   - 候选契约校验：`candidate / executable=false / reviewStatus=pending`、方言、自哈希一致；
   - 审核包闭包与 Phase 2G/4 重算一致，静态报告 `passed`；
   - 有效人工决策为 `approve`；
   - 受限验证证据门禁：按当前冻结策略，Phase 5A describeOnly 对同一候选的 `passed` 报告必须
     在案；Phase 5B/5C 实施后加入强制集；证据缺失即阻断（默认 fail closed，豁免口径是开放
     问题，见第 10 节）；
   - 发布主体与审核主体符合责任边界。
2. 发布记录是新的自包含不可变审计记录：完整嵌入被批准候选载荷（逐字节）及其 `contentSha256`、
   审核包哈希、静态报告哈希、Phase 5 证据引用、决策记录引用、发布主体、发布时间与策略版本；
   `contentSha256` 可独立重算。
3. 每个候选版本至多一条发布记录（唯一性由存储保证）；发布记录创建后不可修改、不可撤销，撤销
   或替代通过显式 supersession 生命周期事件表达，旧记录原样保留。
4. 发布不改变候选存储文档；发布记录不携带任何"运行许可"字段；执行、调度与生产查询消费属于
   路线图"后续候选"，需要独立 REQ/BIZ。

### 5.4 审计回读与入口

1. 首个切片只提供本地 CLI：审核包导出、决策提交、发布、按候选哈希回读当前审核/发布状态。
2. CLI 遵循既有安全口径：报告/包文件独占创建、显式 `--overwrite` 才可覆盖；stdout 只输出白名单
   字段；不提供任何绕过门禁的参数。
3. HTTP 入口（审核工作台、在线决策、在线回读）不在本需求内；其认证、授权、请求大小限制、
   并发限制与审计主体设计完成并批准前保持阻断。

### 5.5 泄漏与脱敏

1. 审核包、决策记录与发布记录允许在受控存储中包含 SQL 文本与物理对象名（审核所必需），但：
   公开 Git、公开文档与 PROG 只保存哈希前缀、数量与状态；
   - 决策理由等自由文本的保存与脱敏策略在确认前不落库（开放问题）；
   - 参数值、连接信息、凭据、provider 原始响应永远不进入审核链任何记录。

## 6. 非功能需求

1. 全部哈希为既有 canonical SHA-256 规则（UTF-8、`sort_keys=True`、紧凑 separators、
   `ensure_ascii=False`、禁 NaN）；排除自身哈希字段；时间字段不参与哈希。
2. 全部审核/发布记录 insert-only；存储层无 update/replace/delete/bulk/drop 路径；唯一索引在
   启动时建立；同内容幂等语义与候选存储一致。
3. 决策与发布服务在任何校验失败路径上的 Mongo 写副作用为 0。
4. 审核包装配、决策与发布全部为离线可测：固定替身 + 合成脱敏 fixture，不依赖真实
   MongoDB/SQL Server/在线模型。
5. V2 行为零变化：现有测试基线（424 passed）不修改、不减少。

## 7. 与在途阶段的顺序门禁

1. 本需求的**机制实施**（契约、纯计算服务、存储适配、CLI、离线测试）以本 REQ/BIZ/DEV 通过
   人工批准 + 用户明确授权提前实施为前置；按 ROADMAP 纪律，Phase 6 排在 Phase 4R/5B/5C 之后，
   未经批准不得擅自开工。
2. **真实审核与发布使用**（对真实候选执行决策/发布）以以下全部为前置：
   - Phase 4R 真实证据闭环完成（真实候选存在且 `contentSha256` 可回读）；
   - 第 10 节开放问题中阻断真实使用的项全部由对应 owner 明确；
   - 用户对当次真实操作的明确授权。
3. 真实使用门禁不阻塞离线机制的交付与测试；离线机制的交付也不得被表述为真实审核能力已可用。
4. V3 候选的审核与发布依赖 V3 下游链（[REQ-20260906-04](REQ-20260906-04-v3-downstream-pipeline-alignment.md)
   M1–M6）完成；V3 审核对齐作为后续任务，不阻塞 V2 机制实施。

## 8. 测试矩阵（实施验收的最低范围）

1. 审核包装配 happy path 与闭包篡改拒绝（候选/报告/引用任一哈希不一致 → 装配失败，零副作用）；
2. 决策服务前置重算：篡改审核包内 generation request 或静态报告 → 决策拒绝且 Mongo 写 0 次；
3. 状态机全路径：pending→underReview→approved、→rejected、→revisionRequested（含后续候选引用）、
   approved→published；非法转换（终态后再决策）全部拒绝且零写入；
4. 并发冲突：同版本两决策并发提交，恰好一个生效，败者得到确定性冲突结果且两调用合计 Mongo
   写副作用恰好为 1（仅胜者）；
5. 发布门禁逐项阻断：静态 blocked、无 approve 决策、Phase 5A 证据缺失、主体不符 → 发布失败
   零写入；全门禁满足 → 发布记录自包含且 `contentSha256` 可独立重算；
6. 幂等与唯一性：同候选版本重复决策/重复发布被唯一索引与服务双重拒绝；duplicate 不报错不重复；
7. insert-only 审计：存储适配层无任何 update/replace/delete/bulk/drop 调用（扫描断言）；
8. 载荷不变性：决策/发布全路径后候选存储文档逐字节不变，`reviewStatus=pending` 不变；
9. V2/V3 隔离：审核/发布契约对 V3 载荷 fail closed（V3 对齐另立任务前），记录可按候选契约版本
   与哈希闭包区分，禁止混淆；
10. 泄漏检查：stdout、日志、公开文档不含参数值、连接信息、凭据、provider 原始响应；理由文本
    在策略确认前不落库；
11. V2 全量回归：现有 424 项离线测试零行为变化；
12. CLI 语义：独占创建、`--overwrite`、退出码、stdout 白名单。

## 9. 验收标准

1. 第 5 节全部需求在 [DEV-20260907-01](../architecture/DEV-20260907-01-candidate-review-publish.md)
   有可实施定义（契约字段、状态机、门禁算法、集合与索引、错误码、里程碑），REQ/BIZ/DEV 互相
   引用且链接有效；
2. 第 8 节测试矩阵全部离线实现并通过；默认测试不访问 MongoDB、SQL Server、在线模型、飞书或
   私有 bundle；
3. `uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`、`git diff --check`
   全部通过，pytest 通过数不低于当前基线 424；
4. README、docs/README、ROADMAP 与当日 PROG 同步；本需求完成不把任何真实审核/发布表述为
   已完成；
5. 机制交付后，审核状态机的每一步都可以从公开审计记录（决策 + 事件 + 指针）独立重建，无需
   信任任何内存态或日志。

## 10. 风险与开放问题

| # | 问题 | Owner | 阻断 |
| --- | --- | --- | --- |
| 1 | 发布门禁中 Phase 5A/5B/5C 证据的最终强制集与豁免口径（当前冻结为 fail closed：5A 在案强制，5B/5C 实施后加入） | 用户 + 审核 owner | M4 真实使用 |
| 2 | 审核主体身份载体：本地 CLI 的 actor 声明可信边界、HTTP 入口的认证授权方案（延续 BIZ-20260905-01 第 5 节） | 用户 + 运维 | M5 真实使用、M6 |
| 3 | 决策理由等自由文本的脱敏、保存与保留策略 | 审核 owner + 运维 | M2 真实使用 |
| 4 | 并发败者尝试是否需要持久审计（当前冻结：不落库，仅服务日志） | 审核 owner | M2 |
| 5 | 审核/发布记录与生命周期事件的保留期、备份与访问控制 | 运维 | M3 真实使用 |
| 6 | supersession 事件的提交者与流程 | 审核 owner | M4 真实使用 |
| 7 | 与 Phase 4R 证据包字段格式的最终对齐（其保存位置/格式仍是 DEV-20260906-02 开放问题） | sqlBot | 真实使用 |
| 8 | 审核工作台（列表、批量、多角色、意见流）是否立项 | 用户 | 后续候选 |

以上任一开放问题在其阻断的里程碑/使用前必须由对应 owner 明确；未确认时保持阻断，不得以
默认值推进真实使用。
