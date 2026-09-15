# DEV-20260907-01：候选人工审核与发布设计与实施计划（Phase 6）

- 状态：`proposed`
- 创建日期：2026-09-07
- 实现需求：[REQ-20260907-01](../requirements/REQ-20260907-01-candidate-review-publish.md)
- 业务决策：[BIZ-20260907-01](../decisions/BIZ-20260907-01-review-publish-authority-boundary.md)
- 前置设计：[DEV-20260906-01](DEV-20260906-01-v2-candidate-persistence.md)、
  [DEV-20260905-01](DEV-20260905-01-restricted-sqlserver-validation.md)、
  [DEV-20260827-01](DEV-20260827-01-sql-ast-safety-gate.md)、
  [DEV-20260827-03](DEV-20260827-03-project-context-metadata-resolution.md)
- 关联规划：[DEV-20260906-02](DEV-20260906-02-real-handoff-evidence-loop-orchestration.md)、
  [DEV-20260906-04](DEV-20260906-04-v3-downstream-pipeline-alignment.md)

> 本文档只做规划，本轮任务不实现任何代码。文中全部契约、模块与测试均为后续实施对象；
> 按 ROADMAP 纪律与 REQ 第 7 节双轨门禁，机制实施（M1 起）以本文档组通过人工批准为前置，
> 真实审核/发布使用（M5）以 Phase 4R 真实闭环与开放问题确认为前置。

## 1. 设计结论

Phase 6 为"不可变候选"补上受审计的生命周期层。核心结构决策：

1. **候选载荷与生命周期彻底分离。** `SqlTemplateCandidateV2`（含其
   `reviewStatus=pending`）是永不修改的审计载荷；有效审核状态由三部分承载——
   insert-only 决策记录（唯一事实来源之一）、insert-only 生命周期事件（状态机轨迹）、
   每候选一文档的状态指针（CAS 维护的当前态快照，可随时从事件折叠重建）。该模式与
   [BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md)
   第 5 条为 context 冻结的"insert-only 载荷 + 独立生命周期载体 + CAS"完全同构，不引入
   第二种生命周期语义。
2. **审核包不持久化。** `CandidateReviewPackageV2` 由权威输入确定性组装（候选 + 生成请求 +
   静态报告 + 可选 Phase 5 证据引用），自身 `contentSha256` 可独立重算；决策与发布记录
   只登记其哈希。需要展示时重新组装，需要留档时由 CLI 导出到被 Git 忽略的本地目录
   （与 `.codex_tmp` 同级策略），不进 Mongo、不进公开 Git。
3. **并发正确性由唯一索引兜底、服务层先行校验。** 决策集合对
   `(candidateContractVersion, candidateSha256)` 建唯一索引（一版本至多一个有效决策）；
   发布集合同样唯一（一版本至多一条发布记录）。并发败者的 insert 撞唯一索引 → 确定性冲突
   结果、零写入。状态指针的 CAS 防止当前态视图分裂，事件折叠是恢复与审计的最终依据。
4. **发布记录自包含。** 完整嵌入被批准候选载荷（逐字节）与门禁所依据的全部哈希引用；
   任何事后验证不需要回读候选存储。发布记录不含任何"可执行/运行许可"字段。
5. **首切片只有本地 CLI。** 四个子命令（导出审核包、决策、发布、状态回读）+ 一个维护命令
   （指针重建）；无 HTTP、无自动决策、无批量。

**有效状态机**（由事件序列折叠派生；`reviewOpened` 可选）：

```text
pending ──reviewOpened──> underReview ──decisionRecorded──> approved | rejected | revisionRequested
   └──────────────────decisionRecorded（直接决策，同上）──┘
approved ──published──> published（终态；supersession 事件可引用但永不修改记录）
rejected / revisionRequested：该候选版本终态；修订经新版本候选走完整链路
```

终态后再决策、对不存在候选决策、跨状态跳转全部确定性拒绝且零写入。`approved` 后撤销
批准不设路径（正确处置是"不发布"；内容问题走修订新版本），该简化登记为冻结决策。

## 2. 模块边界与文件清单（规划，实施时按此冻结）

```text
src/release_sql_bot/domain/candidate_reviews.py
  CandidateReviewPackageV2 / ReviewValidationEvidenceV2 / ReviewActorV2 /
  ReviewDecisionRecordV2 / ReviewLifecycleEventV2 / CandidateReviewStateV2 /
  PublishedTemplateRecordV2 及状态机常量
src/release_sql_bot/application/ports/review_store.py
  ReviewDecisionStore / ReviewLifecycleEventStore / ReviewStatePointerStore /
  PublishedTemplateStore 端口（insert-only + CAS 语义在端口文档注释冻结）
src/release_sql_bot/application/candidate_reviews.py
  assemble_review_package / open_review / record_decision / publish_candidate /
  read_review_state（含完整重算门禁与稳定 issue）
src/release_sql_bot/infrastructure/database/mongodb_reviews.py
  Mongo insert-only 适配器 + 状态指针 CAS 适配器（复用 mongodb_candidates 工程模式）
config/settings.py / .env.example
  RSB_REVIEW_STORE_ENABLED（默认 false）等配置键
src/release_sql_bot/__main__.py
  review-export / review-decide / review-publish / review-status / review-rebuild-pointer
  子命令（随 M4 任务实现）
tests/contract/test_candidate_reviews_contract.py
tests/unit/test_candidate_reviews.py
tests/unit/test_review_store.py
tests/integration/test_review_cli.py
tests/fixtures/*.review.synthetic.json（合成脱敏）
```

修改（仅装配点）：`__main__.py` 增加子命令；`config/settings.py`、`.env.example` 增加配置；
`tests/unit/test_cli.py` 白名单同步。禁止改动：`domain/sql_candidates_v2.py`、
`application/candidates_v2.py`、`application/sql_validation.py`、
`application/sqlserver_validation.py`、`infrastructure/database/mongodb_candidates.py`、
候选存储契约与全部既有测试期望。审核模块不得修改候选载荷任何字段。

## 3. 契约冻结

### 3.1 审核包（`CandidateReviewPackageV2`，`schemaVersion="1.0.0"`）

```text
CandidateReviewPackageV2（camelCase、extra=forbid、strict）
  schemaVersion = "1.0.0"
  candidate:               SqlTemplateCandidateV2（完整不可变载荷）
  generationRequest:       GenerateSqlCandidateRequestV2（内嵌 resolution request，
                           即同时携带 binding request、批准 context 与 snapshot）
  staticReport:            SqlStaticValidationReportV2
  validationEvidence:      ReviewValidationEvidenceV2[]（默认空；有则逐项来自
                           Phase 5 报告本地文件）
  assembledAt:             时间戳（不参与哈希）
  contentSha256:           排除自身字段后的 canonical SHA-256
```

`ReviewValidationEvidenceV2`：

```text
  validationRunId / mode（describeOnly | estimatedPlan | boundedExecution）/
  status（passed | blocked | inconclusive）/ reportSha256 / profileId /
  completedAt（不参与哈希）
```

装配门禁（任一失败 → 装配失败，产出稳定 issue，零副作用）：

1. 候选契约校验：`schemaVersion=2.0.0`、`status=candidate`、`executable=false`、
   `reviewStatus=pending`、`dialect=sqlserver`；
2. 候选自哈希重算一致；`generationInputSha256` 与所携 generation request canonical 一致；
3. 生成请求闭包：resolution report 哈希、context/snapshot 自哈希与
   `metadataSnapshotRef` 匹配（复用 Phase 2G 输入门禁的纯计算函数）；
4. 静态报告引用闭包与候选/生成请求哈希逐项一致；
5. 每项验证证据：报告可解析为 `SqlServerValidationReportV2`、`reportSha256` 重算一致、
   其 `candidateRef`（`contentSha256`/`generationInputSha256`/
   `resolutionReportSha256`/`contextSha256`/`snapshotSha256`）与包内对象逐项一致、
   `staticReportSha256` 与包内静态报告一致；
6. 候选契约版本未知（含 V3 形状）→ `CANDIDATE_CONTRACT_REJECTED` fail closed。

展示内容（ROADMAP 要求）由 CLI 从包内权威载荷确定性渲染：事实契约（`factRef`、参数、
结果契约）、元数据快照（generation request 内嵌 snapshot 的 relations/relationships）、
SQL 文本、对象范围（`declaredObjects`）、覆盖（`declaredUsageCoverage` 与静态报告
coverage 结论并排）、假设与告警（`assumptions`/`warnings`）。不设独立 presentation 契约，
避免同一内容两份哈希来源。

### 3.2 决策记录（`ReviewDecisionRecordV2`，`schemaVersion="1.0.0"`）

```text
  schemaVersion = "1.0.0"
  decisionId:              确定性："rev-" + candidateSha256 前 32 hex
                           （一版本至多一决策，天然唯一）
  candidateContractVersion: "2.0.0"（与候选本体 schemaVersion 精确一致）
  candidateSha256
  reviewPackageSha256:     审核者实际依据的审核包哈希（决策前重算比对）
  decision:                approve | reject | requestRevision
  actor:                   ReviewActorV2（actorId ≤120、actorRole=reviewer；
                           CLI 声明身份，可信边界见开放问题 2）
  successorCandidateSha256: 仅 requestRevision 可选携带（修订后新候选，若已存在）
  decidedAt:               时间戳（不参与哈希）
  contentSha256:           排除自身字段后的 canonical SHA-256
```

首版不落库决策理由（rationale）等自由文本（REQ 第 10 节开放问题 3 确认前）；契约未来
增加该字段属破坏性演进，须提升 `schemaVersion` 并登记迁移策略。

### 3.3 生命周期事件与状态指针

```text
ReviewLifecycleEventV2（schemaVersion="1.0.0"，insert-only）
  eventId:                确定性：candidateSha256 + ":" + sequence（每候选单调递增）
  eventType:              reviewOpened | decisionRecorded | published | superseded
  candidateContractVersion / candidateSha256
  payloadRef:             decisionId / publishId / None / supersession 引用
  actor / recordedAt（时间不参与哈希）
  contentSha256

CandidateReviewStateV2（schemaVersion="1.0.0"，每候选一文档，CAS 维护）
  candidateContractVersion / candidateSha256
  currentState:           pending | underReview | approved | rejected |
                          revisionRequested | published
  lastEventId / updatedAt（时间不参与哈希）
```

指针是事件折叠的缓存：`read_review_state` 读取后必须与事件折叠交叉校验，不一致输出
`REVIEW_STATE_POINTER_DRIFT` 并以事件折叠为准；`review-rebuild-pointer` 从事件重建指针
（维护命令，仅修复缓存，不改事件与记录）。事件 `sequence` 由服务读取当前最大值 +1 分配，
撞唯一索引 `(candidateSha256, sequence)` 即冲突失败，不重试循环。

### 3.4 发布记录（`PublishedTemplateRecordV2`，`schemaVersion="1.0.0"`）

```text
  schemaVersion = "1.0.0"
  publishId:               确定性："pub-" + candidateSha256 前 32 hex
  publishPolicyVersion:    Literal["review-publish-gate-v1"]
  candidateContractVersion: "2.0.0"
  candidate:               SqlTemplateCandidateV2（逐字节完整嵌入）
  candidateSha256 / reviewPackageSha256 / staticReportSha256
  validationEvidence:      门禁实际采信的 ReviewValidationEvidenceV2[]（原样嵌入）
  decisionRef:             decisionId + 决策记录 contentSha256
  actor:                   ReviewActorV2（actorRole=publisher）
  publishedAt:             时间戳（不参与哈希）
  contentSha256:           排除自身字段后的 canonical SHA-256
```

无 `executable`、无运行许可、无调度信息字段。supersession 只通过
`ReviewLifecycleEventV2(eventType=superseded)` 表达并引用替代记录，原记录永不修改。

### 3.5 决策与发布的重算门禁

`record_decision` 前置（任一失败零写入）：

1. 审核包按 3.1 门禁重新校验，重算 `reviewPackageSha256` 与决策记录携带值一致；
2. 对包内 generation request 完整重算 Phase 2G（`resolve_metadata_v2`）与 Phase 4
   （`validate_sql_candidate_v2`），与包内报告 canonical 比对一致且静态 `passed`
   不作为决策前提（blocked 候选同样可被 reject/requestRevision——审核对象包括失败候选）；
3. 状态校验：当前有效态 ∈ {pending, underReview}；
4. `requestRevision` 携带 successor 时，successor 候选必须已存在且自哈希一致（存在性
   校验按候选存储启用状态；离线测试用 fake store）。

`publish_candidate` 前置（任一失败零写入，逐条对应 REQ 第 8 节测试矩阵第 5 项）：

1. 候选契约校验（同 3.1 第 1 条）；
2. 审核包闭包 + Phase 2G/4 重算一致，静态报告 `passed`；
3. 存在有效 `approve` 决策（从决策存储按 candidateSha256 精确读取，不信任请求携带的
   决策记录自述）；
4. 受限验证证据门禁：至少一项 `mode=describeOnly、status=passed` 的 Phase 5A 报告在案
   且证据闭包比对通过（3.1 第 5 条规则重跑）；Phase 5B/5C 实施后强制集扩充
   （`publishPolicyVersion` 随之升级）；
5. 发布 actor 与决策 actor 满足责任边界（同人或不同人均允许，发布门禁独立重验）。

### 3.6 存储设计（SqlBot 自有库，独立 migration）

```text
candidate_review_decisions       唯一索引 (candidateContractVersion, candidateSha256)
candidate_review_lifecycle_events 唯一索引 (candidateSha256, sequence)
candidate_review_state_pointers   唯一索引 candidateSha256；CAS 按 lastEventId
published_sql_templates           唯一索引 (candidateContractVersion, candidateSha256)
```

- 决策/事件/发布三集合严格 insert-only（无 update/replace/delete/bulk/drop，测试扫描断言，
  模式同 DEV-20260906-01 第 6 节验收）；指针集合是唯一允许 update 的文档（仅 CAS 路径）；
- 写失败折叠为 `REVIEW_STORE_UNAVAILABLE | REVIEW_STORE_FAILED`，不抛出、不伪造成功
  （语义对齐候选存储）；
- 配置沿用 `RSB_CANDIDATE_STORE_*` 模式：`RSB_REVIEW_STORE_ENABLED`（默认 false）、
  `RSB_REVIEW_STORE_DATABASE`（默认 `release_sql_bot`）、四个集合名配置键；与
  `RSB_DATABASE_ENABLED` 独立，启用校验规则对齐候选存储；
- 不写 RuleReader 任何集合，不写候选存储集合。

### 3.7 CLI 设计（M4）

```text
release-sql-bot review-export   --input <closure.json> --output <package.json> [--overwrite]
release-sql-bot review-decide   --input <closure.json> --decision <approve|reject|requestRevision>
                                 --actor-id <id> [--successor <sha256>]
release-sql-bot review-publish  --input <closure.json> --evidence <report1.json>[,report2.json]
                                 --actor-id <id> [--output <publish-record.json>]
release-sql-bot review-status   --candidate-sha256 <sha256>
release-sql-bot review-rebuild-pointer --candidate-sha256 <sha256>
```

- 输入 `closure.json` = 候选 + 生成请求 + 静态报告（可选内嵌证据），全部严格解析；
- 退出码：`0=成功`、`2=门禁 blocked（含重算不一致）`、`3=存储不可用`、
  `4=wire/config 错误`、`5=并发冲突`（对齐 validate-sqlserver 语义并扩展冲突码）；
- stdout 白名单：candidateSha256 前缀、状态、decision/publishId、issue codes、耗时、
  输出路径；不含 SQL、参数值、对象清单、连接信息；
- 输出文件独占创建，`--overwrite` 才可覆盖；不提供任何跳过门禁的参数。

### 3.8 稳定 issue codes

```text
REVIEW_PACKAGE_HASH_MISMATCH / REVIEW_PACKAGE_CLOSURE_BROKEN /
PHASE2G_RECOMPUTE_MISMATCH / PHASE4_RECOMPUTE_MISMATCH /
CANDIDATE_CONTRACT_REJECTED / REVIEW_STATE_INVALID_TRANSITION /
REVIEW_DECISION_CONFLICT / REVIEW_DECISION_DUPLICATE /
REVIEW_EVIDENCE_INVALID / REVIEW_EVIDENCE_REF_MISMATCH /
PUBLISH_GATE_STATIC_NOT_PASSED / PUBLISH_GATE_NO_APPROVAL /
PUBLISH_GATE_EVIDENCE_MISSING / PUBLISH_DUPLICATE /
REVIEW_STORE_UNAVAILABLE / REVIEW_STORE_FAILED /
REVIEW_STATE_POINTER_DRIFT / REVIEW_ACTOR_INVALID
```

## 4. 测试矩阵（与 REQ 第 8 节逐条对应）

1. 审核包 happy path + 全部闭包篡改（候选自哈希、generation input、resolution/context/
   snapshot、静态报告引用、证据 candidateRef 逐字段）→ 装配失败零副作用；
2. 决策前重算：篡改包内 generation request 或静态报告 → 决策拒绝且 fake store 写 0 次；
3. 状态机全路径：openReview→decide、直接 decide、approved→publish；终态后再决策/再发布、
   对不存在候选决策 → 拒绝零写入；
4. 并发：两决策并发 → 恰好一个生效（唯一索引），败者冲突结果，合计写副作用 = 1；
5. 发布门禁逐项阻断：静态 blocked、无 approve、5A 证据缺失、证据哈希不匹配、candidateRef
   不一致 → 零写入；全通过 → 发布记录自包含可独立重算；
6. 幂等与唯一性：重复决策/重复发布 → duplicate 语义；duplicate 不报错不重复落库；
7. insert-only 扫描：审核适配层无 update/replace/delete/bulk/drop（指针 CAS 路径单独
   断言且过滤条件含 lastEventId）；
8. 载荷不变性：全路径后候选存储文档（fake）逐字节不变；
9. V2/V3 隔离：V3 形状载荷进入审核/发布契约 → `CANDIDATE_CONTRACT_REJECTED`；
10. 泄漏：CLI stdout、日志、错误消息不含 SQL/参数值/对象名/连接信息；决策理由字段
    不存在于契约（schema 断言）；
11. V2 全量回归：现有 424 项零行为变化；
12. CLI 语义：独占创建、`--overwrite`、五类退出码、stdout 白名单（模式沿用
    test_sqlserver_validation_cli.py）。

## 5. 里程碑

### M0：文档批准与开工授权

- 前置：本 REQ/BIZ/DEV 通过人工审查并纳入提交；用户明确授权启动机制实施。
- DoD：三份文档状态更新、索引同步、PROG 登记。
- 失败语义：未批准即开工视为流程违规，实现必须回退。

### M1：领域契约

- 前置：M0。
- 内容：`domain/candidate_reviews.py` 全部模型 + 状态机常量与折叠函数。
- DoD：契约测试（camelCase、extra=forbid、snake_case 拒绝、确定性 ID、自哈希可重算、
  时间字段不参与哈希、V3 载荷拒绝）；矩阵 9。
- 失败语义：契约校验失败即构造失败，无部分对象。

### M2：应用服务（纯计算 + fake store）

- 前置：M1。
- 内容：`application/candidate_reviews.py` 装配/决策/发布/回读 + 重算门禁。
- DoD：矩阵 1–6、8 的服务层全部通过（fake store 注入）；重算门禁复用
  `resolve_metadata_v2` / `validate_sql_candidate_v2` 现有函数，不复制规则。
- 失败语义：任一门禁失败 → 稳定 issue + fake store 写 0 次。

### M3：存储适配与配置

- 前置：M2。
- 内容：`mongodb_reviews.py` + 配置键 + 生命周期装配（仅 CLI 路径，不动 FastAPI）。
- DoD：矩阵 6、7 存储层通过（fake client 模式沿用 test_candidate_store.py）；配置默认
  关闭；`check-config` 摘要不含集合定位信息（沿用日志卫生测试模式）。
- 失败语义：unavailable/failed 不抛出、不伪造成功。

### M4：CLI 与集成

- 前置：M3。
- 内容：五个子命令 + 集成测试。
- DoD：矩阵 3、5、10、12 端到端通过；`uv run ruff check .`、`format --check`、`pytest`、
  `git diff --check` 全部通过且不低于基线 424；README/docs/ROADMAP/PROG 同步。
- 失败语义：CLI 退出码按 3.7；无绕过门禁参数（测试断言不存在 force/skip 类选项）。

### M5：真实使用（独立授权，不在机制交付内）

- 前置：Phase 4R 真实证据闭环完成（真实候选存在且可回读）；REQ 第 10 节开放问题
  1–7 由对应 owner 明确；用户对当次真实操作的明确授权；审核/发布存储集合与账号隔离
  由运维确认。
- 内容：对真实候选执行首次真实审核与发布，证据脱敏登记 PROG。
- 失败语义：任何前置缺失 → 保持阻断，不得以合成候选冒充真实审核。

## 6. 开放问题

| # | 问题 | Owner | 阻断 |
| --- | --- | --- | --- |
| 1 | 发布门禁 Phase 5 证据强制集与豁免口径（当前冻结 fail closed：5A 在案强制） | 用户 + 审核 owner | M5 |
| 2 | actor 身份载体：CLI 声明身份可信边界、HTTP 认证授权方案 | 用户 + 运维 | M5、HTTP 入口 |
| 3 | 决策理由自由文本的脱敏/保存/保留策略（确认前契约不含该字段） | 审核 owner + 运维 | M5 |
| 4 | 并发败者尝试是否持久审计（当前冻结：不落库） | 审核 owner | M2 |
| 5 | 审核/发布记录保留期、备份与访问控制 | 运维 | M5 |
| 6 | supersession 事件提交者与流程 | 审核 owner | M4 后真实使用 |
| 7 | 与 Phase 4R 证据包格式的字段对齐 | sqlBot | M5 |
| 8 | 审核工作台（列表/批量/多角色/意见流）是否立项 | 用户 | 后续候选 |

## 7. 文档影响

实施各里程碑时同步更新：本 REQ/BIZ/DEV 状态、README 能力与配置表、docs/README 索引、
ROADMAP Phase 6 状态、当日 PROG（脱敏证据）。任何与本文档的偏离必须先更新文档再实现。
