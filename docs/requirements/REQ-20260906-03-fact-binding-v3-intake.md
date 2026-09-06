# REQ-20260906-03：RuleReader FactBindingRequest 3.0.0 intake 升级

- 状态：`in_progress`
- 创建日期：2026-09-06
- 来源：用户要求为 SqlBot 升级消费 RuleReader `FactBindingRequest 3.0.0` 建立独立、可验收的
  REQ/BIZ/DEV；本需求只做契约审计与规划，不实现代码
- 前置需求：[REQ-20260827-02](REQ-20260827-02-rulereader-fact-binding-v2-intake.md)、
  [REQ-20260828-02](REQ-20260828-02-rulereader-handoff-read-intake.md)、
  [REQ-20260906-02](REQ-20260906-02-real-upstream-handoff-evidence-loop.md)
- 业务决策：[BIZ-20260906-02](../decisions/BIZ-20260906-02-fact-binding-v3-authority-boundary.md)
- 技术方案：[DEV-20260906-03](../architecture/DEV-20260906-03-fact-binding-v3-intake.md)
- 上游契约权威：RuleReader 仓库 `BIZ-20260905-02`（`APPROVED_PATH_B`，只读参考，非本仓库文档）

## 1. 背景

RuleReader（Agent 1）已按其仓库 `BIZ-20260905-02` 冻结 `FactBindingRequest 3.0.0`：V2 单根
PASS/FAIL AST 无法表达 V3 的五阶段、首个命中优先级、多 outcome、postGates 与 exclusions；
`FactBindingRequest 2.0.0` 的 `ruleRef` 固定 `schemaVersion=2.0.0`，结构上不能精确引用 V3 规则，
禁止静默降级。2026-09-06 上游已离线生成 18 条 V3 请求（`ready=true`、`blocking=0`、
`executable=false`），落库与其确认的 SqlBot intake 升级均待独立授权。
[REQ-20260906-02](REQ-20260906-02-real-upstream-handoff-evidence-loop.md) 第 5.1/11.2 节已把
"交接契约版本决策未在 SqlBot 侧登记"登记为阻断项；本需求即该决策的 SqlBot 侧载体。

## 2. 上游契约基线（2026-09-06 观察，实施前必须重新核实）

- 上游仓库：`D:\Python\pyWorkspace\RuleAgent`（RuleReader，只读参考；本次未修改、未连接其任何
  服务，未读取私有 `RuleDataReferences` bundle）；
- 观察时上游 HEAD：`65a96846b3ae991b42a8159dbbee43a9bbe15846`；**注意**：全部 V3 契约文件在该
  HEAD 下尚未提交（工作区未跟踪文件），因此"来源 commit"只能登记为上述 HEAD 加工作区观察状态，
  文件 SHA-256 是唯一可执行的冻结锚点；上游提交后必须复核哈希（见第 10 节阻断项）；
- 权威 Schema：`contracts/fact-binding-request-3.0.0.schema.json`，`$id`
  `urn:rulereader:fact-binding-request:3.0.0`，Draft 2020-12，契约版本 `3.0.0`，原始字节
  SHA-256 `2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566`；
- 伴生上游 Schema（仅作来源证据，不进入 SqlBot 运行时）：`rule-parse-result-3.0.0.schema.json`
  `c34a468d0b05c8584043912e53414a517f5eb30efd3da653ce630fb7c4932380`、
  `rule-structure-candidate-3.0.0.schema.json`
  `fafded54c6fe59644ce336033c4ea8e4b83a236118075fd3ed2d9994be7c7247`、
  `business-confirmed-fact-catalog-3.0.0.schema.json`
  `bb2446f2112f073967358c7b0c36a9bab4565ca30d35dbce24142191b50f450c`；
- 交叉复核：上游工作区 `contracts/fact-binding-request-2.0.0.schema.json` 的 SHA-256 与 SqlBot
  已冻结值 `38fec6b22511984983e7e7fbbdb40afd58aeffd51b2de8ab73fdfb187024026b`
  （[DEV-20260827-02](../architecture/DEV-20260827-02-fact-binding-v2-readiness.md)）一致，
  上游 V2 契约未漂移；
- 存储语义权威：上游 `REQ-20260906-01` / `DEV-20260906-02`（MongoDB Schema v5）冻结了
  `rule_versions_v3` 与 `fact_binding_handoff_batches_v3` 的集合、唯一索引、单文档原子 batch、
  canonical hash 与幂等/冲突规则；其状态为"离线实现完成、真实写入待授权"。

## 3. V3 与 V2 的结构差异及不兼容原因

V3 与 V2 是**字段集互不相交**的两代契约，逐层差异如下（权威依据为上游冻结 Schema 与
SqlBot [domain/fact_bindings_v2.py](../../src/release_sql_bot/domain/fact_bindings_v2.py) 的
现行 consumer 模型）：

| 层 | V2（2.0.0） | V3（3.0.0） |
| --- | --- | --- |
| `ruleRef` | `ruleId`（自由字符串）、`ruleVersion`（≤220）、`schemaVersion`（自由）、`sourceSha256` | `ruleSetId`（`^[A-Z][A-Z0-9_]*$`，≤120）、`ruleVersion`（≤260）、`schemaVersion` 固定 `3.0.0`、`sourceSha256`、新增 `catalogDigest` 与 `candidatePayloadSha256` |
| `fact` | `factKind` 含 `derived`；必含 `defaultValue`、`derivation`；参数无 `role` | `factKind` 仅 `source\|aggregate\|exists`；无 `defaultValue`/`derivation`；参数新增 `role`（`entityKey\|filter\|timeAnchor`） |
| `usages` | 表达式侧视角：`operator`、`expressionSide`、`conditionId`、`conditionPath` | 规则结构视角：`stage`（`stateGuards\|prerequisites\|eligibility\|postGates\|exclusions`）、`ruleCode`、`priority`（≥1）、`conditionId`、`conditionPath`、`outcome`（7 个 V3 outcome）；`operator`/`expressionSide` 消失 |
| `examples` | `testCaseId`、`expectedRuleResult`（`pass\|fail`） | `exampleId`、`expectedOutcome`（7 个 V3 outcome） |
| `evidence` | 内嵌于 `provenance.evidence`，kind 4 种，minItems 2，无 `sourceDocument` | 顶层 `evidence`，kind 5 种（新增 `queryRequirement`/`sourceProvenance`/`example`），minItems 1，新增 `sourceDocument`（`ruleResult\|candidate\|catalog`） |
| `provenance` | 嵌套 `source`/`parser` + `generatedAt` + `evidence` | 扁平：`sourceName`/`sourceSha256`/`sourceCharacterCount`/`parserVersion`/`promptVersion`/`provider`/`model`/`relativePath`；无 `generatedAt`（时间字段不进任何 hash） |
| `uncertainties` | `impact` 可为 `blocking\|warning`，含 `category`/`fieldPath`/`resolutionHint` | `impact` 固定 `warning`；无 `category`/`fieldPath`/`resolutionHint` |
| `queryRequirements` | entity/fields/filters/aggregation/timeRange 携带 `resolutionStatus`/`keyResolutionStatus`/`sourceCandidate`，filters completeness 可为 `unresolved`，mode 可为 `unresolved` | ready 语义内建：无任何 resolutionStatus 字段；filters completeness 固定 `complete`；aggregation/timeRange mode 无 `unresolved`；entity `keyParameters` 必填 |
| 顶层固定标志 | `contractVersion`/`status`/`executable`/`targetDialect`/`requiresMetadataSnapshot`/`tempTableAllowed` 为自由值，由 readiness 显式校验 | 全部 const：`contractVersion=3.0.0`、`status=candidate`、`executable=false`、`targetDialect=sqlserver`、`requiresMetadataSnapshot=true`、`tempTableAllowed=false` |
| `requestId` | ≤384 | ≤420（仍为 `<ruleVersion>#<factCode>`） |

不兼容原因：V2 consumer 的必填字段（`defaultValue`、`derivation`、`category`、`fieldPath`、
`resolutionStatus` 族、`operator`/`expressionSide` 等）在 V3 中不存在，V3 必填字段
（`catalogDigest`、`candidatePayloadSha256`、`stage`、`outcome`、`ruleSetId` 等）在 V2 中不存在，
双向严格 `extra=forbid` 解析必然失败；且上游决策禁止任何静默降级或字段裁剪转换。因此 V3 intake
必须是独立模型集，不能复用或扩展 V2 consumer。

## 4. V3 完整性规则

1. `ruleRef` 闭包：`ruleRef.sourceSha256 == provenance.sourceSha256`；`schemaVersion=3.0.0`；
   `catalogDigest`/`candidatePayloadSha256` 为小写 64 位十六进制（与上游 Schema pattern 一致）。
2. request identity：`requestId == <ruleVersion>#<factCode>`；batch wrapper 的
   `_id == rule_version`，request wrapper 的 `request_id == payload.requestId`、
   `rule_version == payload.ruleRef.ruleVersion`、`fact_code == payload.fact.factCode`、
   `contract_version == "3.0.0"`。
3. evidence 闭包：entity、fields、filters（含 FilterSet）、aggregation、timeRange、usages、
   examples、uncertainties 引用的每个 `evidenceId` 必须解析到顶层 `evidence` 数组；evidence ID
   无重复。
4. usages 完整性：每个 usage 的 `stage`/`outcome` 属于上游枚举；`priority >= 1`；
   `conditionId` 无重复；全部 `evidenceIds` 可解析。
5. uncertainties：payload 内不允许出现 `impact=blocking`（上游 Schema 以 const 拒绝）；warning
   原样保留，不得提升为授权或被丢弃。
6. canonical hash：payload hash 使用与上游一致的 canonical JSON（UTF-8、`sort_keys=True`、紧凑
   separators、`ensure_ascii=False`、禁 NaN）；batch hash 按上游规则对按 `requestId` 升序的
   `{"requestId","payloadSha256"}` 序列计算；时间字段不参与任何 hash。

## 5. MongoDB wrapper、batch 与所有权

- 上游集合（由 RuleReader 唯一创建和写入，SqlBot 只读）：
  `fact_binding_handoff_batches_v3`（单文档原子 batch，`_id = rule_version`，每个 ruleVersion
  至多一个 batch）与 `rule_versions_v3`（本需求范围外，见第 9 节）；
- batch 文档字段（上游 `DEV-20260906-02` 冻结）：`_id`、`rule_version`、`contract_version`、
  `request_count`、`request_ids`（按 `requestId` 升序）、`batch_sha256`、`created_at`、
  `requests[]`（每条含 `request_id`、`rule_version`、`fact_code`、`contract_version`、
  `payload_sha256`、`created_at`、`payload`）；
- SqlBot 只接受调用方显式给出的精确 `ruleVersion`，按 `{"rule_version": <exact>}` 读取，
  不选择"最新"、不猜测版本、不使用 inclusion projection 隐藏未知字段；
- SqlBot 不创建索引、不执行 migration、不 insert/update/replace/delete/upsert 任何上游集合；
  唯一索引由上游 Schema v5 migration 负责。

## 6. V2 legacy 保留与路由隔离

- 既有 V2 consumer、handoff intake、仓储方法与 API 全部保留，行为逐字节不变；V2 仍是
  `rule_versions`/`fact_binding_handoffs` 上 2.0.0 交接的现行通道；
- V3 使用独立模型集、独立 intake 服务、独立仓储方法与独立 API 路由
  （`GET /api/v1/fact-binding-handoffs/v3`）；V2 代码不得导入 V3 模块，V3 代码不得导入 V2
  consumer 或 V1 legacy；
- 双向拒绝必须有测试证明：V2 模型拒绝 V3 payload，V3 consumer 拒绝 V2 payload，两者都不提供
  任何转换、裁剪或降级函数。

## 7. 禁止降级与字段裁剪

1. 禁止把 V3 转换或降级成 V2/V1，包括删除 `catalogDigest`/`candidatePayloadSha256`、把
   `ruleSetId` 塞回 `ruleId`、把五阶段 usage 压缩成表达式视角、把多 outcome 压缩成 pass/fail；
2. 禁止把旧 V2 草稿（含其全部 blocking uncertainty）冒充 V3 规则的交接；
3. 禁止为"让请求通过"放宽或改写上游 Schema 的任何 const、pattern 或必填约束。

## 8. fail-closed 语义

1. 一个坏 handoff（batch wrapper、任一 request wrapper、payload Schema、身份闭包、hash 或
   evidence 闭包失败）使整批 fail closed，不得返回部分成功，不得跳过坏记录继续；
2. intake 只读入口不装配候选 provider：注入固定 provider 后，任何成功、失败或阻断路径的
   provider 调用次数都必须为 0；
3. intake 不产生任何 MongoDB 写操作：测试必须断言对上游集合零写调用、零索引命令、零 migration；
4. 读取成功不表示可以生成 SQL、已授权、已批准或可执行；全部产物固定 `executable=false`。

## 9. 范围外

- 读取 `rule_versions_v3` 规则文档、候选生成、Phase 2G/4 消费 V3 请求、Phase 4R 编排实现；
- SQL Server 访问、DeepSeek 调用、SQL 生成或执行；
- 对上游集合的任何写操作、migration 或索引管理；
- 把 V3 blocked candidate（`rule_structure_candidates_v3` 中 16 blocking 陈旧记录）转换为任何
  可消费对象；
- 私有 `RuleDataReferences` bundle 的读取或使用。

## 10. 阻断项

1. 上游 V3 契约文件尚未提交到 RuleAgent 仓库（工作区未跟踪文件）：实施冻结副本与来源清单前，
   需要上游完成提交并由用户确认；上游提交后必须重新计算 SHA-256 并与本需求登记值比对，不一致时
   另立评审，不得静默替换；
2. 上游 MongoDB Schema v5 真实落库未执行（上游状态为"离线实现完成、真实写入待授权"）：落库前
   `fact_binding_handoff_batches_v3` 恒为空，V3 intake 的真实数据验证无法进行；
3. 对真实 MongoDB 的只读验证（Milestone 完成后的冒烟）需用户显式授权的独立任务；
4. 私有参考资料被用于构造任何授权输入时直接阻断（延续 [BIZ-20260828-03](../decisions/BIZ-20260828-03-local-candidate-evidence-boundary.md)）。

## 11. 验收标准

1. 冻结的 V3 Schema 副本与机器可读来源清单进入 SqlBot 仓库；加载器复用 V2 机制（行尾规范化 →
   SHA-256 比对 → `Draft202012Validator.check_schema` → `$id` 校验），哈希不符即 fail closed；
2. 独立 V3 consumer 覆盖上游 Schema 全部约束（camelCase、`extra=forbid`、const、pattern、
   枚举、长度、列表下限），拒绝 snake_case 与类型强转；
3. batch wrapper、request wrapper、身份闭包、payload hash、batch hash、evidence 闭包与
   `ruleRef`/`provenance` 来源闭包全部校验；任一失败使整批失败且错误不含 payload 或基础设施细节；
4. 第 6、7、8 节的路由隔离、禁止降级与 fail-closed 语义全部有测试证明（含 provider 零调用与
   零写调用断言）；
5. 合成脱敏测试矩阵（见 DEV 第 9 节）全部离线通过；默认测试不访问 MongoDB、SQL Server、在线
   模型、飞书或私有 bundle；
6. `uv run ruff check .`、`uv run ruff format --check .`、`uv run pytest`、`git diff --check`
   全部通过；README、docs/README、ROADMAP 与当日 PROG 同步；
7. 本需求与其 BIZ/DEV 完成登记前，不修改 V2 任何行为，不实现 Phase 4R 编排。
