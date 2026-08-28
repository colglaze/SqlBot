# DEV-20260827-02：FactBindingRequest V2 consumer 与阻断分析设计

- 状态：`completed`
- 创建日期：2026-08-27
- 实现需求：[REQ-20260827-02](../requirements/REQ-20260827-02-rulereader-fact-binding-v2-intake.md)
- 受决策约束：[BIZ-20260827-01](../decisions/BIZ-20260827-01-fact-binding-v2-authority-boundary.md)

## 1. 模块边界

```text
domain/fact_bindings_v2.py       独立 V2 consumer、BindingGapReport 与 issue owner 契约
application/binding_intake_v2.py canonical hash、引用闭包、未决对应与报告编排
api/app.py                       纯计算 V2 analyze 入口；不读取 runtime provider
tests/fixtures/                  合成脱敏 V2 payload 与上游 Schema 来源清单
```

V2 模块不导入 V1 `application.bindings`、`application.candidates`、Prompt、provider、FastAPI、MongoDB、
SQL 驱动或 RuleReader 模块。V1 类与生成链保留 legacy 名称/别名，不能成为 V2 的转换目标。

## 2. Consumer contract

`FactBindingRequestV2` 逐层覆盖上游 `2.0.0`：

```text
requestId / contractVersion / status / ruleRef
fact / usages / mappingCandidate / examples
queryRequirements
  entity / fields / filters / aggregation / timeRange / result
provenance
  source / parser / evidence
uncertainties
targetDialect / requiresMetadataSnapshot / tempTableAllowed
```

所有对象 `extra=forbid`，只接受 camelCase alias，禁止 snake_case 回退；受控枚举、长度、正则、列表下限
和条件字段按上游 Schema 建模。版本、请求身份和固定安全标志由 readiness 再次显式检查，以便输出稳定
阻断原因。模型不会导入或动态读取 RuleReader Schema。

## 3. 确定性校验顺序

1. 校验 `contractVersion=2.0.0`、`ruleRef.schemaVersion=2.0.0`、candidate 状态和固定 SQL Server 标志；
2. 校验 `requestId == ruleRef.ruleVersion + "#" + fact.factCode`、来源哈希一致和非 derived；
3. 建立唯一 evidence registry，验证 query requirements、usage、example、uncertainty 的全部引用；
4. 建立 fact parameter、field 和 filter 唯一集合，验证 entity/filter/aggregation/timeRange 引用闭包；
5. 从 requirement 状态确定所需的六类 blocking uncertainty code，只检查上游是否已提供，不改写请求；
6. 将上游 blocking/warning 原样分类，并添加候选来源不具授权效力的稳定 warning；
7. 按 `code + fieldPath + uncertaintyId` 排序并生成报告。

前置完整性失败时仍可收集其他独立完整性问题，但不进行元数据推断、权限判断或模型调用。

## 4. 未决对应关系

| requirement 状态 | 必需 blocking code | owner |
| --- | --- | --- |
| entity key 未决 | `ENTITY_KEY_UNRESOLVED` | `businessRuleReview` |
| value field 未决 | `VALUE_FIELD_UNRESOLVED` | `metadataReview` |
| filter/entity key field 或 filter item 未决 | `FILTER_FIELD_UNRESOLVED` | `metadataReview` |
| filters completeness 未决 | `FILTER_SET_INCOMPLETE` | `businessRuleReview` |
| aggregation 未决 | `AGGREGATION_UNRESOLVED` | `businessRuleReview` |
| time range 未决 | `TIME_RANGE_UNRESOLVED` | `businessRuleReview` |

如果所需 code 不存在，或只有同 code 的 warning，则报告新增
`BLOCKING_UNCERTAINTY_MISSING` 完整性问题；原始 `uncertainties` 不被修改。

## 5. 哈希与报告

排序稳定 JSON 使用 camelCase、Unicode 原样、无非语义空白、禁止 NaN/Infinity：

- `requestSha256 = SHA-256(requestId UTF-8)`；
- `payloadSha256 = SHA-256(canonical FactBindingRequestV2 UTF-8)`，与 RuleReader handoff 的 payload
  canonicalization 对齐；
- `ruleSha256 = SHA-256(canonical ruleRef UTF-8)`；
- `sourceSha256 = provenance.source.sha256`，并与 `ruleRef.sourceSha256` 交叉校验。

`BindingGapReport`：

```text
status: blocked | readyForMetadataResolution
executable: false
requestId
hashes: requestSha256 / payloadSha256 / ruleSha256 / sourceSha256
blockingIssues[]
warnings[]
```

每个 issue 包含 `code/owner/fieldPath/message/evidenceIds/uncertaintyId`。任一 blocking issue 都使
`status=blocked`；否则只能是 `readyForMetadataResolution`。报告没有生成、执行或授权字段。

## 6. HTTP 与 provider 隔离证明

新增 `POST /api/v1/fact-bindings/v2/analyze`，请求体直接是 `FactBindingRequestV2`。入口只调用
`analyze_binding_gaps_v2`，不读取 `RuntimeContainer.candidate_provider`。集成测试向应用注入固定 provider
后提交含 blocking 的 V2 fixture，并断言返回 blocked 且 provider 调用为零，从真实路由证明 V2 路径
没有经过 V1 生成服务。

## 7. Fixture 来源

本仓库只保存合成脱敏 payload 和机器可读来源清单，不复制 RuleReader Python 模型，也不在运行时读取
兄弟仓库。来源清单记录：

- `$id=urn:rulereader:fact-binding-request:2.0.0`；
- contract version `2.0.0`；
- RuleReader 仓库相对路径 `contracts/fact-binding-request-2.0.0.schema.json`；
- 2026-08-27 读取文件的 SHA-256
  `38fec6b22511984983e7e7fbbdb40afd58aeffd51b2de8ab73fdfb187024026b`。

后续上游 hash 变化必须建立新 REQ/DEV 评审差异，不能在运行时静默接受。

## 8. 完成边界

本切片完成后，Phase 4 AST 安全实现仍不得开始消费 V2 候选；必须先完成独立的项目上下文与受治理
元数据解析，把逻辑字段解析为明确授权的物理表列，并建立新版本契约。当前代码不调用在线模型、不
连接数据库、不解析或执行 SQL。
