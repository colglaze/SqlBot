# DEV-20260916-01：编码映射、空值与行集结果语义设计

- 状态：`implemented`（离线切片已落地；真实运行边界见第 6 节）。
- 实现需求：[REQ-20260916-01](../requirements/REQ-20260916-01-value-encoding-result-semantics.md)。
- 业务决策：[BIZ-20260916-03](../decisions/BIZ-20260916-03-value-encoding-result-semantics.md)。

## 1. 契约

### 1.1 项目上下文 1.2.0

`ProjectBindingContextV3.schemaVersion` 接受 `1.1.0` 与 `1.2.0`。

1.2.0 新增：

```text
valueEncodingBindings: []
  bindingId
  requestId
  fieldId                 # 本切片仅 factValue
  columnGrantId           # 必须与该 field 的授权列相同
  projectionKind          # identity | mappedCase
  comparisonKind          # exactString
  nullInput               # preserve
  unknownPhysical         # null
  entries[]               # physicalValue / logicalValue，均非空字符串
  sourceKind              # viewPhysicalBaseline | identityDeclared
  sourceSha256            # 64 hex；identityDeclared 也要有来源哈希

resultSemanticsBindings: []
  bindingId
  requestId
  emptyMatch              # emptyResultSet
  extraRows               # returnAll
  matchedNull             # preserve
  consumerCardinality     # rowset
  grainConflictPolicy     # surfaceAllRows
```

1.1.0 载荷不得携带上述字段；序列化时省略空字段，以保持既有 `contentSha256`。1.2.0 必须显式包含两个数组（可为空）。绑定按 `(requestId, fieldId)` / `requestId` 唯一。`mappedCase` 的 `logicalValue` 必须落入当前请求 `allowedValues`，且不得一对多。

### 1.2 解析报告

`BindingResolutionReportV3` 增加：

- `resolvedValueEncodings`：成功时带物理列坐标与条目副本；blocked 必须为空。
- `resolvedResultSemantics`：零或一个对象；blocked 必须为 null。

报告 `schemaVersion` 仍为 `1.0.0`（附加字段有默认值，旧测试夹具补空数组）。新增阻断码：

| code | owner |
| --- | --- |
| `VALUE_ENCODING_REQUIRED` | metadataReview |
| `VALUE_ENCODING_FIELD_MISMATCH` | metadataReview |
| `VALUE_ENCODING_COLUMN_MISMATCH` | metadataReview |
| `VALUE_ENCODING_VALUES_INVALID` | metadataReview |
| `VALUE_ENCODING_DUPLICATE` | metadataReview |
| `RESULT_SEMANTICS_DUPLICATE` | metadataReview |
| `RESULT_SEMANTICS_REQUEST_MISMATCH` | metadataReview |

### 1.3 候选

- `GeneratedCandidateResultV3` / `CandidateResultV3`：`cardinality` 为 `scalar | rowset`。
- 存在行集绑定的新候选：`schemaVersion=3.1.0`，`cardinality=rowset`。
- 无绑定：`schemaVersion=3.0.0`，`cardinality=scalar`（与既有切片一致）。
- 3.0.0 候选不得声明 `rowset`。旧候选审计可读，禁止原地改写。
- Prompt 版本：有编码或行集绑定时 `sqlserver-fact-candidate-v3.2`，否则 `v3.1`。
- 不需要候选存储集合迁移：insert-only 文档按 `contentSha256` 区分版本。

### 1.4 上游变更包（不在本仓库实施）

FactBindingRequest 3.0.0 的 `result.cardinality` 仍为 const `scalar`。若规则引擎要原生声明行集，应发布上游 3.1.0 并冻结新 Schema 哈希。在此之前，SqlBot 用结果语义绑定记录消费契约，禁止修改本地冻结 3.0.0 Schema。

## 2. M2

在字段/实体键解析成功之后解析编码与结果语义：

1. `allowedValues` 非空且当前 request 没有 `factValue` 编码绑定 → `VALUE_ENCODING_REQUIRED`。
2. 绑定的 `columnGrantId` 必须等于该字段已解析授权列。
3. `mappedCase`：条目逻辑值 ⊆ `allowedValues`；物理值唯一；禁止空串。
4. `identity`：每条 `physicalValue == logicalValue`，且集合等于 `allowedValues`。
5. 结果语义绑定至多一条，且 `requestId` 精确匹配。

不把 warning 级 `uncertainties` 当作编码授权。

## 3. M3

`_check_m3_scope` 不因编码 CASE 放宽 JOIN/聚合。有效结果契约：

```text
columnName/dataType/nullable/nullPolicy/unit ← 上游 result
cardinality ← 结果语义绑定的 consumerCardinality，否则上游 scalar
```

模型必须原样复制该有效结果。`mappedCase` 时 Prompt 提供确定性 `valueEncodingPlan`（物理列 + 有序 WHEN/THEN + `elseNull` + `nullInput=preserve`），要求 `fact_value` 为对该列的简单 CASE，ELSE NULL，禁止 ISNULL。

## 4. M4 / dialect adapter

SQLGlot tsql 适配器在 SELECT 列表中允许 `Case` / `If` / `Literal` / `Null`，WHERE 中仍禁止。提取 `encodingCases` 证据：输入列、字符串 WHEN/THEN 臂、ELSE 是否为 NULL。`ISNULL`/`COALESCE`/`CAST` 仍禁止。

语义校验：

- 无编码或 `identity`：`fact_value` 必须是授权列。
- `mappedCase`：必须恰好一个 encoding CASE，输入列=授权列，臂集合与绑定相等，ELSE NULL，无 ISNULL。
- 字面量必须是字符串且与绑定码逐字节一致（禁止 `WHEN 0` 对字符串码）。
- 禁止 TOP/OFFSET/DISTINCT/GROUP；行集绑定额外确认没有行数裁剪特征。
- 生成成功但语义不一致必须 `blocked`。

## 5. 测试

合成夹具使用独立逻辑/物理码，不复制私有来源。覆盖：编码正例、NULL、未知码、ELSE 默认否、ISNULL、整数文字、缺绑定、TOP、规则版本 A/B、usage 六元组、context/snapshot 混用、哈希篡改、旧 3.0.0 候选仍可读。

## 6. 真实运行边界

本切片离线完成生成/校验能力。新编码绑定属于新的 context 版本，必须由 metadataReview 重新批准后才能真实 `generate-v3`。本次不发送在线模型请求、不连接 SQL Server、不写批准。私有拟议 SQL 与矩阵不是仓储背书。
