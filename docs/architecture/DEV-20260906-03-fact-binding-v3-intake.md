# DEV-20260906-03：FactBindingRequest 3.0.0 intake 设计与实施计划

- 状态：`in_progress`
- 创建日期：2026-09-06
- 实现需求：[REQ-20260906-03](../requirements/REQ-20260906-03-fact-binding-v3-intake.md)
- 业务决策：[BIZ-20260906-02](../decisions/BIZ-20260906-02-fact-binding-v3-authority-boundary.md)
- 前置设计：[DEV-20260828-02](DEV-20260828-02-rulereader-handoff-read-intake.md)、
  [DEV-20260827-02](DEV-20260827-02-fact-binding-v2-readiness.md)

> 本文档只做规划。文中所有文件、契约与测试均为后续实施对象；上游 MongoDB 落库与其契约文件提交
> 完成前（REQ 第 10 节阻断项），不得开始实施。

## 1. 设计结论

镜像 V2 handoff intake 的已验证架构（冻结 Schema 加载器 → 严格包装 consumer → 只读仓储 →
整批门禁 → 只读 API），为 V3 建立完全独立的模块集：新领域模型、新 batch wrapper、新仓储方法、
新 intake 服务、新 API 路由。复用既有 `canonical_sha256` 哈希规则与 `jsonschema` 运行时依赖；
不复制 RuleReader 模型，不修改任何 V2/V1 行为，不新增任何写路径。

V2 的 `analyze_binding_gaps_v2`（未决语义/gap report）不适用于 V3：V3 ready 请求没有任何
resolutionStatus 族字段、`impact` 固定 `warning`、filters 固定 `complete`，V3 intake 只做
完整性校验（Schema、身份、哈希、引用闭包），不产出 BindingGapReport。

## 2. 模块边界与文件清单

新增：

```text
src/release_sql_bot/contracts/fact-binding-request-3.0.0.schema.json
  冻结上游 Schema 副本（实施时从上游复制，锁定 SHA-256）
src/release_sql_bot/domain/fact_bindings_v3.py
  V3 consumer 契约（FactBindingRequestV3 与全部嵌套模型）
src/release_sql_bot/domain/fact_binding_handoffs_v3.py
  V3 batch wrapper 与 intake batch 契约
src/release_sql_bot/application/handoff_intake_v3.py
  V3 intake 门禁与固定 Schema 加载器
tests/fixtures/fact-binding-request-3.0.0.synthetic-ready.json
  合成脱敏 ready V3 payload fixture
tests/fixtures/fact-binding-handoff-batch-3.0.0.synthetic.json
  合成脱敏 batch wrapper fixture
tests/contract/test_fact_binding_v3_contract.py
tests/unit/test_handoff_intake_v3.py
docs/specs/fact-binding-request-3.0.0-source.json
  机器可读来源清单（$id、契约版本、上游路径、SHA-256、观察日期、fixture 路径）
```

修改：

```text
src/release_sql_bot/application/ports/handoffs.py
  新增 V3 batch 只读端口与稳定错误（独立协议，不改动 V2 协议）
src/release_sql_bot/infrastructure/database/mongodb.py
  新增 batch 精确版本只读查询方法（共享既有客户端生命周期，零写方法）
src/release_sql_bot/api/app.py
  新增 GET /api/v1/fact-binding-handoffs/v3
tests/integration/test_api.py
  新增 V3 API 200/404/502/503 与 provider 零调用用例
tests/handoff_support.py（或新增 tests/v3_handoff_support.py）
  合成 V3 payload/batch 构造辅助
```

禁止改动：`domain/fact_bindings_v2.py`、`domain/fact_binding_handoffs_v2.py`、
`application/handoff_intake_v2.py`、`application/binding_intake_v2.py` 的任何现有行为；
V1 legacy；候选生成链路。

## 3. 冻结 Schema 与加载机制

- 从上游 `contracts/fact-binding-request-3.0.0.schema.json` 复制内容到包内副本，允许仓库 LF
  行尾规范化；实施时必须记录上游文件当时的实际字节与行尾形态，不得凭假设预填；
- 加载器完全复用 V2 机制（[application/handoff_intake_v2.py](../../src/release_sql_bot/application/handoff_intake_v2.py)
  的 `load_fact_binding_schema_v2` 模式）：读取包内副本 → 规范化还原上游行尾 → SHA-256 与
  `2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566` 比对 →
  `Draft202012Validator.check_schema` → `$id` 必须等于
  `urn:rulereader:fact-binding-request:3.0.0`；任一步失败 fail closed；
- 每个 payload 使用 `FormatChecker` 离线验证；`jsonschema` 已是运行时依赖，无新依赖。

## 4. V3 consumer contract 骨架

`FactBindingRequestV3` 及嵌套模型逐层覆盖上游 Schema（全部 `extra=forbid`、只接受 camelCase、
strict、拒绝 snake_case 回退与类型强转，风格与 V2 consumer 一致）：

```text
contractVersion=3.0.0 / status=candidate / executable=false（const）
requestId（3..420）
ruleRef:        ruleSetId / ruleVersion / schemaVersion=3.0.0 /
                sourceSha256 / catalogDigest / candidatePayloadSha256
fact:           BindableFactV3（factKind source|aggregate|exists、parameters≥1 带 role）
queryRequirements:
  entity:       entityType / grain / keyParameters(≥1) / evidenceIds(≥1)
  fields:       ≥1，无 sourceCandidate / resolutionStatus
  filters:      FilterSetV3，completeness=complete（const），item 无 resolutionStatus
  aggregation:  mode none|precomputed|compute|exists（无 unresolved），function 可空
  timeRange:    mode none|asOf|between（无 unresolved）
  result:       columnName=fact_value / cardinality=scalar
usages:         ≥1：stage / ruleCode / priority≥1 / conditionId / conditionPath /
                outcome(7 个 RuleOutcomeV3) / evidenceIds(≥1)
examples:       ≥1：exampleId / value / expectedOutcome / evidenceIds(≥1)
mappingCandidate: 复用 BindingMappingCandidateV2 形状（独立 V3 副本，不 import V2 模型）
provenance:     扁平（sourceName / sourceSha256 / sourceCharacterCount / parserVersion /
                promptVersion / provider deepseek|reviewed_import / model / relativePath?）
evidence:       顶层 ≥1：evidenceId / kind(5 种) / sourceDocument / sourcePath(^/)
uncertainties:  impact 固定 warning（const），无 category/fieldPath/resolutionHint
targetDialect=sqlserver / requiresMetadataSnapshot=true / tempTableAllowed=false（const）
```

模型级唯一性约束（与 V2 consumer 的 `reject_duplicate_local_ids` 同型）：usages.conditionId、
evidence.evidenceId、uncertainties.uncertaintyId 无重复。

## 5. batch wrapper 契约

```text
StoredFactBindingHandoffBatchV3（snake_case、extra=forbid、strict、时间必须带时区）
  _id == rule_version
  rule_version
  contract_version = "3.0.0"
  request_count >= 1
  request_ids:     按 requestId 升序，与 requests 一一对应
  batch_sha256:    ^[a-f0-9]{64}$
  created_at
  requests[]:      request_id / rule_version / fact_code / contract_version="3.0.0" /
                   payload_sha256 / created_at / payload: FactBindingRequestV3
```

intake 输出契约（不可变 Report 模型）：batch 标识、契约与 Schema 身份/哈希、`request_count`、
已验证 request 记录（payload + payload hash + created_at）；固定 `executable=false`。

## 6. 只读仓储

- 端口（独立协议，避免 V2/V3 端口互相污染）：

```python
class FactBindingHandoffBatchRepositoryV3(Protocol):
    async def get_batch_by_rule_version(
        self, rule_version: str
    ) -> StoredFactBindingHandoffBatchV3: ...
```

- 适配器在 `MongoRuleStore` 生命周期内新增 V3 集合句柄（集合名沿用上游冻结值
  `fact_binding_handoff_batches_v3`；配置键与默认值在实施时按既有 `RSB_MONGODB_*` 模式扩展并
  同步 `.env.example`/README 配置表）；执行精确 `{"rule_version": rule_version}` 单文档查询，
  不使用 inclusion projection（保留未知字段以便 `extra=forbid` 发现上游异常包装）；
- 驱动错误 → 仓储不可用；包装 Pydantic 失败 → 上游记录无效；任何路径零写调用、零索引命令。

## 7. intake 门禁顺序

1. 加载并验证冻结 V3 Schema（身份 + 哈希 + 自校验）；
2. 按精确 `ruleVersion` 读取 batch；无 batch → not found；
3. batch wrapper 校验：`_id == rule_version`、`contract_version="3.0.0"`、`request_count` 与
   `request_ids`/`requests` 数量一致、`request_ids` 严格升序且无重复、时间带时区；
4. 逐 request：payload 通过冻结 JSON Schema；
5. 身份闭包：`request_id == payload.requestId == <ruleVersion>#<fact_code>`、
   `rule_version == payload.ruleRef.ruleVersion`、`fact_code == payload.fact.factCode`、
   `contract_version` 一致；request_id 与 fact_code 全批唯一；
6. 逐 request 复算 canonical payload hash == `payload_sha256`（canonical 规则与上游一致：
   UTF-8、`sort_keys`、紧凑 separators、`ensure_ascii=False`、禁 NaN）；
7. 复算 batch hash：按 `requestId` 升序的 `{"requestId","payloadSha256"}` 序列取 canonical
   SHA-256，与 `batch_sha256` 比对；
8. `ruleRef` 闭包：`ruleRef.sourceSha256 == provenance.sourceSha256`；
9. evidence 闭包：entity/fields/filters/aggregation/timeRange/usages/examples/uncertainties 的
   全部 `evidenceIds` 解析到顶层 `evidence`；
10. 全部通过前不返回部分批次；静态契约、身份、来源或哈希失败抛稳定错误（不含 payload、URI、
    驱动异常）。

## 8. batch 状态语义与 V2 的差异

- V2 intake 输出 `blocked | readyForMetadataResolution`（blocking uncertainty 驱动）；
- V3 intake 的合法结果只有"已验证 ready batch"：上游只在 16 条 readiness 门禁全 pass、
  blocking=0、`ready=true` 时落库，且 payload `impact` 固定 `warning`。任何门禁失败都是
  invalid 错误（502），不是一种可返回的 batch 状态；
- 不引入 V2 `BindingGapReport`、六类未决 code 或 `BLOCKING_UNCERTAINTY_MISSING` 语义；
  不把 warning 提升为授权或问题降级。

## 9. 合成脱敏测试矩阵

全部离线（fake 仓储 + 合成 fixture；不访问 MongoDB/SQL Server/在线模型/私有 bundle）：

1. 冻结 V3 Schema：包内副本哈希与来源清单一致、`check_schema` 通过、`$id` 正确；
2. 合成 ready payload 与 batch wrapper 双重验证（Pydantic + 固定 JSON Schema）；
3. `extra=forbid` 在根与各嵌套层拒绝额外字段；snake_case 拒绝；类型强转拒绝；
4. 双向不兼容：`FactBindingRequestV2` 拒绝 V3 fixture；`FactBindingRequestV3` 拒绝 V2 fixture；
   仓库中不存在任何 V3↔V2 转换函数（代码审查断言）；
5. batch wrapper 缺字段/额外字段/契约版本错误 → invalid；
6. request wrapper 身份闭包破坏（`_id`、`requestId`、fact_code、rule_version 任一不一致）→ invalid；
7. `payload_sha256` 篡改 → invalid；`batch_sha256` 篡改 → invalid；
8. `request_ids` 与 `requests` 数量/内容/排序不一致 → invalid；
9. 重复 request_id 或 fact_code → invalid；跨 rule_version 内容 → invalid；
10. evidence 悬空引用（entity/fields/filters/aggregation/timeRange/usages/examples/uncertainties
    任一位置）→ invalid；
11. `ruleRef.sourceSha256 != provenance.sourceSha256` → invalid；
12. `impact=blocking`、`filters.completeness=unresolved`、未知 `stage`/`outcome`、`factKind=derived`
    被 const/枚举拒绝（Schema 与 Pydantic 双重证明）；
13. 空 batch（无记录）→ not found；仓储不可用 → 稳定错误（不泄漏基础设施细节）；
14. Mongo fake 断言：精确 `{"rule_version": ...}` 查询、无 projection、零写调用/零索引命令；
15. API：200（完整已验证 batch）/404/502/503；注入固定候选 provider 后任何路径 provider 调用
    次数为 0；
16. 上游来源清单机器可读，且与加载器常量（`$id`、SHA-256）一致；
17. hash 时间不变性：仅修改 wrapper/`created_at` 时间字段不影响 payload/batch hash 复算结论。

## 10. API 形态

```text
GET /api/v1/fact-binding-handoffs/v3?ruleVersion=<exact-version>
```

- 仓储未启用/不可用：503；精确版本无 batch：404；任一门禁失败：502；成功：200 返回完整已验证
  batch（payload、哈希、时间）；
- 错误响应只含稳定 code 与安全文案，不输出 payload、URI、驱动异常；
- 路由只读取 V3 仓储，不读取 `candidate_provider`，不触发任何生成服务。

## 11. 迁移与兼容策略

- SqlBot 侧**无 migration、无索引管理**：集合与唯一索引由上游 Schema v5 migration 创建；
- 既有 V2 API、模型、测试与行为零改动；V1 legacy 保持只读记录；
- 上游落库前 `fact_binding_handoff_batches_v3` 恒为空，V3 入口返回 404，属预期行为，不降级、
  不回退到 V2 读取；
- 上游 Schema hash 变化：必须另立 REQ/DEV 评审差异，禁止运行时静默接受或动态加载兄弟仓库文件；
- 配置新增集合名等键时同步 `.env.example`、README 配置表与 `check-config` 安全摘要（仅布尔/名称
  白名单字段）。

## 12. 实施里程碑与 DoD

- **M0 契约冻结**：上游契约文件已提交并经用户确认；复制 Schema 副本、登记来源清单、实现加载器
  与契约测试（矩阵 1、2、16）。DoD：哈希锁定，测试通过。
- **M1 V3 consumer**：`domain/fact_bindings_v3.py` 全模型 + 矩阵 3、4、12。DoD：合成 fixture
  无损 round-trip，双向拒绝证明。
- **M2 wrapper 与仓储**：`domain/fact_binding_handoffs_v3.py`、端口、适配器方法 + 矩阵 5–9、14。
  DoD：fake 仓储全部离线通过。
- **M3 intake 与 API**：`application/handoff_intake_v3.py`、API 路由 + 矩阵 10、11、13、15、17。
  DoD：全部门禁与 API 状态码离线验证。
- **M4 收口**：全量 `uv run pytest`、ruff check/format、`git diff --check`；README、docs/README、
  ROADMAP、PROG 同步；REQ/BIZ/DEV 状态按验收结果登记。
- 全程 DoD 附加条件：不连接真实 MongoDB、不调用在线模型；真实数据冒烟为显式授权任务。

## 13. 开放问题

| # | 问题 | Owner | 影响 |
| --- | --- | --- | --- |
| 1 | `rule_versions_v3` 规则文档读取是否纳入 intake 闭包（当前规划：范围外，Phase 4R M1 另行处理） | sqlBot + 用户 | 闭包强度 |
| 2 | V3 入口是否需要与 V2 不同的错误 code 命名空间细节 | sqlBot | M3 |
| 3 | 真实 MongoDB 只读冒烟的授权与账号隔离 | 用户 + 运维 | M4 后 |
