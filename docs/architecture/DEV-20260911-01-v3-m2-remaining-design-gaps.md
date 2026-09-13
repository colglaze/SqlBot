# DEV-20260911-01：V3 M2 剩余设计缺口分析与可实施方案（二次修订版）

- 状态：`proposed`（2026-09-11 初版提案，2026-09-11 两次修订；待用户/授权负责人审核）
- 创建日期：2026-09-11
- 实现需求：[REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
- 业务决策：[BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md)
- 前置设计：[DEV-20260906-04](DEV-20260906-04-v3-downstream-pipeline-alignment.md)
- 关联进度：[PROG-20260911](../progress/PROG-20260911.md)

> 本轮只修改文档，不修改 `src/`、`tests/`、依赖、冻结 Schema 或现有 V2 行为。
> 新方案标记为 `proposed`；既有批准决策（REQ/BIZ/DEV-20260906-04）保留 `approved` 状态。
> 不提交、不推送 Git。

## 1. 背景与范围

M2（V3 元数据解析，Phase 2G V3）当前 `in_progress`。12 个子任务已完成（内容闭包校验、
usage 追溯摘要、请求/报告契约、输入门禁、column grant 解析、字段/实体键授权闭包、
filters/aggregation/timeRange 物理字段解析、指定 join grant 解析、多关系连接闭包选择），
`application/metadata_resolution_v3.py` 已存在。

剩余设计缺口（README、DEV §12、DEV §14 开放问题第 7 项）：

1. **`entityType`/`grain` 到授权 relation 的映射**（§5.3 第 2 步）
2. **`ResolvedJoinV3.evidenceIds` 来源**（输出契约要求非空，输入 `JoinGrantV3` 无此字段）
3. **JOIN 方向与执行计划边界**（`_select_join_closure_v3` 返回按 grantId 排序集合，不代表 SQL 执行顺序；LEFT JOIN 保留侧的确定规则）
4. **公开 `resolve_metadata_v3` 编排与报告组装**（整合各步输出、错误传播、哈希重算）

本文档对每个缺口给出：当前代码证据 → 缺口 → 单一推荐方案 → 契约影响 →
成功/失败行为 → 验收用例 → 需要业务或授权负责人决定的事项。

---

## 2. 问题 A：`entityType`/`grain` 的授权映射

### 2.1 当前代码证据

**能证明的**（已实现）：

- 实体键列有授权：`_resolve_fields_and_entity_keys_v3`（`metadata_resolution_v3.py:475`）
  通过 `(requestId, parameterName)` 命中 `EntityKeyAuthorizationV3`，再经
  `fieldId → FieldBindingAuthorizationV3 → ColumnGrantV3 → RelationGrantV3 →
  SnapshotRelation/Column` 五重命中，输出 `ResolvedEntityKeyV3` 包含准确物理引用
  （`schemaName`/`relationName`/`columnName`）。
- `ResolvedEntityKeyV3.evidence_ids` 当前由 helper 从当前请求
  `EntityRequirementV3.evidence_ids` **原样复制**（`metadata_resolution_v3.py:599`），
  仅作追溯引用，**不**作为物理授权证明。
- `EntityRequirementV3`（`fact_bindings_v3.py:217`）携带 `entity_type`、`grain`、
  `key_parameters`、`evidence_ids`，结构契约已冻结。

**缺少的**：

- `entityType`/`grain` → 物理关系 `(schemaName, relationName)` 的**显式授权映射**。
  当前实体键的物理引用通过键授权链隐式推导，但 `entityType` 名称本身未参与授权判定。

### 2.2 缺口分析

当前 `_resolve_fields_and_entity_keys_v3` 的输出 `ResolvedEntityKeyV3` 包含
`schemaName`/`relationName`，但这来自键列的 column grant → relation grant 链，
不是来自 `entityType`/`grain` 的显式映射。若两个不同 `entityType` 共用同一键列关系，
当前设计无法区分它们是否都被批准访问该关系。

### 2.3 推荐方案：新增 `EntityGrainAuthorizationV3`（context 级映射，不含请求级 evidence）

**映射由谁提供**：metadataReview（与 relation/column/field/entity-key 授权同级）。

**谁批准**：随 `ProjectBindingContextV3` 版本化批准，由 `ApprovalRecordV3` 背书。
映射授权通过批准 context 的 `contentSha256` 与 `ApprovalRecordV3` 引用闭包追溯，
**不**在映射对象内携带请求级 evidence。

**如何版本化**：作为 `ProjectBindingContextV3` 新增必填字段，随 context 版本递增。
新增授权内容必须形成新 `contextVersion`、新 `contentSha256` 和新批准闭包，
**不**原地修改旧批准载荷（延续 DEV §3.2 insert-only 决策）。

**请求作用域**：`EntityGrainAuthorizationV3` 位于共享 context 中，
**不**按 requestId 区分。同一 context 中 `(entity_type, grain)` 组合唯一。
当前请求通过 `request.projectContext.projectRef` 和 `request.projectContext.metadataSnapshotRef`
绑定到特定 context 版本。验证时仅检查当前请求的 `(entity_type, grain)` 是否命中。

**缺失或歧义时阻断**：
- `(entity_type, grain)` 无映射 → `ENTITY_GRAIN_MAPPING_MISSING`
- 映射的 `relation_grant_id` 不存在 → `ENTITY_GRAIN_GRANT_INVALID`
- 映射的 relation grant 与实体键列的 relation grant 不一致 → `ENTITY_GRAIN_RELATION_MISMATCH`

### 2.4 契约影响

**新增必填字段**（`ProjectBindingContextV3`）：

```text
entity_grain_authorizations: list[EntityGrainAuthorizationV3]  (min_length=1, 必填)
```

**此字段为破坏性变化**：缺此字段时 Pydantic 报 `missing` 错误；
传空列表时报 `too_short`（`min_length=1`）；
含未知字段时 `extra=forbid` 拒绝。旧版本 context（无此字段）构造
新 `ProjectBindingContextV3` 时因 `missing` 失败，不会被误读。

**版本影响**：

| 受影响对象 | 变更内容 | 版本影响 |
|-----------|----------|----------|
| `ProjectBindingContextV3` | 新增必填字段 `entity_grain_authorizations` | **schemaVersion "1.0.0" → "1.1.0"** |
| `ApprovalRecordV3` | 无字段变化，但 context 哈希变化导致 `contextRef.sha256` 变化 | 同 schemaVersion，新 approval 记录需重新构造 |
| `ResolveMetadataRequestV3` | 无顶层字段变化（嵌套 context 内容变化） | 无 schemaVersion 变化；请求通过 `contextRef.sha256` 显式绑定 context 版本 |
| `BindingResolutionReportV3` | `contextRef.sha256` 变化，报告契约不变 | 无 schemaVersion 变化 |
| `RepositoryVerifiedHandoffV3` | 内部结果，无 wire schemaVersion | — |

**区分 `schemaVersion` 与 `contextVersion`**：
- `schemaVersion="1.1.0"` 描述契约字段集形状（新增 `entity_grain_authorizations`）
- `contextVersion` 描述业务载荷版本（具体批准的映射内容）
- 新授权内容必须创建 `contextVersion+1` 新文档，**不**原地修改旧批准载荷
- `schemaVersion` 提升由契约形状变化决定；`contextVersion` 递增由业务内容变化决定

**旧版本 context 拒绝策略**：
- 旧版 `schemaVersion="1.0.0"` 的 context 构造新版 `ProjectBindingContextV3` 时
  因 `missing entity_grain_authorizations` 失败
- 旧版 context 仍可被历史请求读取（若历史请求绑定旧 context 版本）
- 新版 context 不向后兼容旧版 schema 请求

**不改变**上游 `FactBindingRequest 3.0.0`，不做 V3↔V2 转换。

**新增嵌套模型**：

```text
EntityGrainAuthorizationV3（camelCase、extra=forbid、strict）
  entity_type: str         pattern="^[a-z][a-z0-9_]*$"    max_length=100
  grain: str               pattern="^[a-z][a-z0-9_]*$"    max_length=100
  relation_grant_id: str   pattern=_STABLE_ID_PATTERN      max_length=200
```

**唯一性与重复拒绝**：

| 条件 | 行为 |
|------|------|
| `(entity_type, grain)` 重复 | 构造时 `reject_duplicate_ids` 拒绝（ValueError） |
| `(entity_type, grain)` 不同但 `relation_grant_id` 相同 | 合法（同关系可服务多个实体类型/粒度） |

**结构层 vs 应用层校验分离**：

| 检查项 | 校验层 | 阶段 |
|--------|--------|------|
| 字段类型、格式、必填、`min_length`、`extra=forbid` | 结构层 | Pydantic 构造 |
| `(entity_type, grain)` 唯一键 | 结构层 | Pydantic `model_validator` |
| `entity_type`/`grant` 格式（正则） | 结构层 | Pydantic `field_validator` |
| **`relation_grant_id` 是否存在于 context** | **应用层** | `_resolve_entity_grain_mapping_v3` |
| **relation grant 与键列 relation grant 一致性** | **应用层** | `_resolve_entity_grain_mapping_v3` |

**关键约束**：`relation_grant_id` **不**在 context `model_validator` 中检查存在性。
不存在于 context 的 `relation_grant_id` 仍能通过结构构造，
由应用 helper 返回 `ENTITY_GRAIN_GRANT_INVALID`。

**标识符比较策略**：
- `grant_id`、`request_id` 等稳定引用 ID → **精确比较**（==，不 casefold）
- `snapshot.identifier_case_sensitivity` → **仅用于物理 schema/relation/column**
  （影响 JOIN 端点匹配、列解析），**不用于** grantId 的大小写折叠

**完整结构重验**：`resolve_metadata_v3` 通过 `model_dump` → `model_validate`
对完整请求执行序列化-重建。重建失败（含 `model_copy` 绕过注入）
映射到 `MetadataResolutionStructureStructureErrorV3`，不区分序列化失败或重建失败。

**哈希影响**：`ProjectBindingContextV3.contentSha256` 使用 `canonical_content_sha256`
（排除根级 `contentSha256`），新增字段自动参与哈希。

### 2.5 成功/失败行为

| 条件 | 行为 |
|------|------|
| `(entity_type, grain)` 有映射，relation grant 存在且与键列一致 | 成功，输出 `ResolvedEntityKeyV3`（evidence_ids 从请求 entity.evidence_ids 复制） |
| `(entity_type, grain)` 无映射 | `blocked`，`ENTITY_GRAIN_MAPPING_MISSING`，owner `metadataReview` |
| 映射的 `relation_grant_id` 不存在于 context | `blocked`，`ENTITY_GRAIN_GRANT_INVALID`，owner `metadataReview` |
| 映射的 relation grant 与键列 relation grant 不一致 | `blocked`，`ENTITY_GRAIN_RELATION_MISMATCH`，owner `metadataReview` |
| 多个实体键解析出不同 relation grant | `blocked`，`ENTITY_GRAIN_RELATION_MISMATCH`，owner `metadataReview` |

### 2.6 验收用例

1. 合法 `(entity_type, grain)` 映射 + 一致 relation grant → 成功解析
2. 缺失映射 → `ENTITY_GRAIN_MAPPING_MISSING`
3. 映射指向不存在的 relation grant → `ENTITY_GRAIN_GRANT_INVALID`
4. 映射 relation grant 与键列 relation grant 不一致 → `ENTITY_GRAIN_RELATION_MISMATCH`
5. `(entity_type, grain)` 重复 → context 构造失败（ValueError）
6. 多个实体键关系不一致 → `ENTITY_GRAIN_RELATION_MISMATCH`
7. 输入对象不变性证明
8. 异常脱敏（不泄漏 entityType/grain 实际值）
9. 大小写比较：relation grant 匹配使用 `snapshot.identifier_case_sensitivity` 策略
10. **现有合法合成 requestId 必须被新关联契约接受**（使用 V3 长度约束，见 §3）

### 2.7 需要决定的事项

| 事项 | 推荐选项 | 替代方案 |
|------|----------|----------|
| `entity_grain_authorizations` 是否 `min_length=1` 必填 | 推荐：是 | `min_length=0`（不推荐：失去显式授权意义） |
| `entityType` 是否必须与 `fact.factCode` 存在命名关联 | 不强制（由 metadataReview 把控） | 强制前缀匹配（增加不必要的耦合） |

---

## 3. 问题 B：`ResolvedJoinV3.evidenceIds` 来源

### 3.1 当前代码证据

**输出契约要求非空**：

`ResolvedJoinV3.evidence_ids: list[str] = Field(min_length=1)`
（`project_bindings_v3.py:486`）

**输入无此字段**：

`JoinGrantV3`（`project_bindings_v3.py:260`）只有：
`grant_id`、`left_column_grant_id`、`right_column_grant_id`、`join_type`。
无 `evidence_ids` 字段。

**当前实现不构造 ResolvedJoinV3**：

`_resolve_join_grant_v3`（`metadata_resolution_v3.py:894`）返回
`tuple[PhysicalColumnRefV3, PhysicalColumnRefV3, JoinTypeV3]`，
注释明确"暂不构造 ResolvedJoinV3，不伪造 evidenceIds"。

**关键约束**：context 支持多个 `requestIds`（`ProjectBindingContextV3.request_ids`，
`min_length=1`，元素 `min_length=3, max_length=420`）。
V3 requestId 格式为 `ruleVersion#factCode`（含 `#`、`@`、`.`、`-`），
**不**匹配 `_STABLE_ID_PATTERN`（不允许 `#`）。

### 3.2 缺口分析

`ResolvedJoinV3.evidence_ids` 要求 `min_length=1`，但 `JoinGrantV3` 不携带证据 ID。
证据必须来自权威输入，不能伪造、不能随意复制、不能把 grantId/relationshipId 冒充 evidenceId。

**业务需求证据** vs **物理 JOIN 授权证据**：

- **业务需求证据**：为何需要此 JOIN？应来自请求级证据，绑定到具体请求。
- **物理 JOIN 授权证据**：`JoinGrantV3` 是纯授权记录（context 级），授权由 grant 存在性
  + 快照物理引用闭合证明，**不**携带 evidence。

**核心矛盾**：物理 `JoinGrantV3` 是共享授权（context 级），而 evidence 是请求级。
不能把请求级 evidence ID 塞进共享授权模型（破坏多请求隔离）。

### 3.3 推荐方案：JOIN 证据关联进入版本化 context

**保留 `JoinGrantV3` 为纯物理授权**（context 级，不携带 evidence）。

**新增 context 级关联列表** `JoinAuthorizationEvidenceV3`：

```text
JoinAuthorizationEvidenceV3（camelCase、extra=forbid、strict）
  request_id: str              min_length=3  max_length=420
  payload_sha256: str          pattern=_SHA256_PATTERN  (64 位小写十六进制)
  join_grant_id: str           pattern=_STABLE_ID_PATTERN  max_length=200
  evidence_ids: list[str]      min_length=1
```

**request_id 契约**：

```text
request_id 字段约束：
  - 类型：str
  - min_length=3，max_length=420
  - 不匹配 _STABLE_ID_PATTERN（因其禁止 #）
  - 必须与权威请求的 requestId 逐字节一致（== 比较）
  - 禁止 strip、大小写转换、截断或重新生成
  - 引用闭包检查中与 context.request_ids 中的记录精确比较
```

**reason**：V3 requestId 格式为 `<ruleVersion>#<factCode>`（如
`SYNTH_RULE_SET@20260906T000000000000Z-a1b2c3d4e5f6#report.synthetic_amount`），
包含 `#`、`@`、`.`、`-`，不满足 `_STABLE_ID_PATTERN`（`^[A-Za-z0-9][A-Za-z0-9._:-]*$`）。
现有合成 requestId 必须被新关联契约接受。

**唯一键**：`(request_id, join_grant_id)` 组合唯一。

**重复拒绝**：同一 `(request_id, join_grant_id)` 出现多条 → 构造时拒绝（ValueError）。
同一 `request_id` 不同 `join_grant_id` → 合法。
同一 `join_grant_id` 不同 `request_id` → 合法（多请求共享同一 grant 的不同证据视角）。

**关联整体进入 context 内容哈希和批准闭包**：

`ProjectBindingContextV3` 新增必填字段：

```text
join_authorization_evidence: list[JoinAuthorizationEvidenceV3]  (min_length=1, 当 context
                                           含 join_grants 且存在需要 join 的请求时必填)
```

此字段参与 `contentSha256` 计算（通过 `canonical_content_sha256`），
并由 `ApprovalRecordV3.contextRef.sha256` 背书。

**作用域规则**：
- `request_id` 必须存在于当前 `ProjectBindingContextV3.request_ids`
- `join_grant_id` 必须存在于当前 `ProjectBindingContextV3.join_grants`
- `payload_sha256` 必须匹配当前权威 payload 的 `canonical_sha256` 重算值
- `evidence_ids` 中每个 ID 必须命中**对应请求**顶层 `evidence` 的 `evidence_id`
- 与当前请求无关的其他 `JoinAuthorizationEvidenceV3` 记录**不**被当前纯解析调用核验
- 列表自身结构、唯一性和可本地检查的 context 引用仍需校验

**当前请求选择规则**：
1. 按精确 `request_id` 选择当前记录（== 比较，不 strip/casefold）
2. 验证 `payload_sha256` 匹配当前权威 payload 的重算摘要
3. 每个选中的 join grant 必须具有唯一对应证据关联
4. 关联 evidence 必须属于该精确请求

**`resolve_metadata_v3` 签名**（删除旁路参数）：

```python
def resolve_metadata_v3(
    request: ResolveMetadataRequestV3,
) -> BindingResolutionReportV3:
```

证据关联从 `request.projectContext.join_authorization_evidence` 读取，
**不**作为独立参数传入。相同完整解析请求必须能确定性重算相同报告。

### 3.4 契约影响

**不修改 `JoinGrantV3`**：保持为纯物理授权记录。

**新增 context 级关联**（与 entity_grain_authorizations 共同构成 1.1.0）：

| 受影响对象 | 变更内容 | 版本影响 |
|-----------|----------|----------|
| `ProjectBindingContextV3` | 新增字段 `join_authorization_evidence`（**允许 `[]`**）；新增必填字段 `entity_grain_authorizations`（`min_length=1`） | **schemaVersion "1.0.0" → "1.1.0"** |
| `ApprovalRecordV3` | 同 schemaVersion；新 approval 记录需重新构造 | — |
| `ResolveMetadataRequestV3` | 内嵌 `projectContext`（无顶层 contextRef）；通过 `contextRef.sha256` 绑定版本 | 无 schemaVersion 变化 |
| `BindingResolutionReportV3` | `contextRef.sha256` 变化 | 无变化 |

**`join_authorization_evidence` 允许 `[]`**：
- 类型 `list[JoinAuthorizationEvidenceV3]`，无 `min_length`，默认空列表 `[]`
- 无 JOIN 请求（选中 join grant 集合为空）时，关联列表为 `[]`，**不阻断**
- 每条 `JoinAuthorizationEvidenceV3.evidence_ids` 仍必填（`min_length=1`）
- 是否需要证据覆盖由当前请求选中的 join grant 集合决定，**不**由 `context.joinGrants` 非空直接决定

**证据覆盖规则**：
- 选中 join 集合为空 → 不要求创建关联，`resolvedJoins=[]`
- 选中集合非空 → 每个选中 grant 必须有唯一当前请求关联，
  缺失才报 `JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND`

**旧版本 context 拒绝策略**：
- 旧版 `schemaVersion="1.0.0"` 的 context 构造新版 `ProjectBindingContextV3` 时
  因 `missing entity_grain_authorizations` 失败（注意：`join_authorization_evidence`
  允许 `[]`，不会单独触发 missing）
- 旧版 context 仍可被历史 `contextVersion` 读取（insert-only 语义，原样保留供审计）
- 新版解析服务**不**接受旧 schemaVersion 载荷（即使补齐新字段也会因版本不符被拒绝）
- 此破坏性变化**不**影响上游 `FactBindingRequest 3.0.0`

**旧版本处理策略**：
- 新版 `schemaVersion="1.1.0"` 的 context 包含 `entity_grain_authorizations`
  和 `join_authorization_evidence` 两个必填字段
- 旧版 context 若缺少任一字段 → 构造失败（`missing` 错误）
- 旧版 context 仍可被历史 `contextVersion` 读取（insert-only 语义）
- 本轮只提出设计，不修改模型

### 3.5 成功/失败行为

**始终执行**（无论是否需要 JOIN）：
- 输入门禁
- 所有物理 join grant 校验（`_select_join_closure_v3`）
- 关联记录结构、唯一性、requestId/grantId 本地引用校验

**证据覆盖判定**：
- 选中 join grant 集合为空 → 不要求关联，`resolvedJoins=[]`
- 选中集合非空 → 每个选中 grant 必须有唯一当前请求关联

| 条件 | 行为 |
|------|------|
| 关联证据全部命中对应请求 | 成功，按原顺序复制到 `ResolvedJoinV3.evidence_ids` |
| `evidence_ids` 为空列表（结构层） | wire 构造失败（`too_short`），不进入业务 error |
| **当前请求需要 JOIN 但缺关联** | `blocked`，`JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND`，owner `metadataReview` |
| **当前请求无需 JOIN（选中集合为空）** | 成功，`resolvedJoins=[]`，不要求关联记录 |
| `payload_sha256` 不匹配当前 payload | `blocked`，`JOIN_EVIDENCE_PAYLOAD_HASH_MISMATCH`，owner `metadataReview` |
| `evidence_ids` 引用不在请求 evidence 中 | `blocked`，`JOIN_GRANT_EVIDENCE_INVALID`，metadataReview` |
| `join_grant_id` 不存在于 context | `blocked`，`JOIN_GRANT_NOT_FOUND`，owner `metadataReview` |
| `request_id` 不在 context.request_ids 中 | `blocked`，`JOIN_REQUEST_NOT_IN_CONTEXT`，owner `metadataReview` |
| `(request_id, join_grant_id)` 重复 | 构造时拒绝（ValueError） |

**关于 `evidenceIds=[]` 的处理**：
- `JoinAuthorizationEvidenceV3.evidence_ids=[]` 首先触发 Pydantic `min_length=1` 构造约束
  （wire 构造失败，`too_short`）
- **不会**到达专用 `JOIN_GRANT_EVIDENCE_EMPTY` 错误码
- "选中 join 缺证据关联" 的业务错误是 `JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND`
  （关联记录不存在），与空列表结构错误分离

### 3.6 证据与授权的关系

**区分业务 evidence 与物理授权**：
- evidence 命中**不授予** JOIN 权限
- 仍须批准 context、显式 `JoinGrantV3` 和快照物理引用全部闭合
- evidence 仅提供追溯链路（为何需要此 JOIN），授权由 grant + snapshot 闭合保证

### 3.7 验收用例

**基础**：
1. 合法关联证据全部命中对应请求 → 成功，顺序保留
2. `evidence_ids` 为空列表 → wire 构造失败（`too_short`），不进入业务 error
3. `evidence_ids` 包含不存在 ID → `JOIN_GRANT_EVIDENCE_INVALID`
4. `evidence_ids` 非排序输入 → 输出顺序与输入一致，不去重
5. 重复 evidence ID → 保留重复（不去重）
6. 输入对象不变性证明
7. 异常脱敏
8. **现有合法合成 requestId（含 `#`）必须被 `JoinAuthorizationEvidenceV3` 接受**

**无 JOIN 请求**：
9. **单关系请求，`join_authorization_evidence=[]`** → 成功，`resolvedJoins=[]`
10. **单关系请求，`context.joinGrants` 有额外合法 grant** → 成功（无需关联）
11. **单关系请求，`context.joinGrants` 有非法额外 grant** → 既有门禁阻断
    （`JOIN_GRANT_NOT_FOUND` 等，不影响"无需证据"规则）

**同 context 两个请求的测试设计**：

| 场景 | 预期行为 |
|------|----------|
| 两个请求 evidence ID 相同但含义不同 | 各自独立验证，互不影响 |
| 两个请求 evidence 完全不相交 | 各自仅验证自身证据 |
| 同一请求同一 grant 两条关联（重复键） | 构造时拒绝（ValueError） |
| 关联指向不存在的 grant | `JOIN_GRANT_NOT_FOUND` |
| 关联引用其他请求的 evidence ID | `JOIN_GRANT_EVIDENCE_INVALID` |
| **当前请求 A 需要 JOIN，但只有请求 B 的关联** | `JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND`（A 缺关联） |
| **当前请求 A 需要 JOIN，关联引用 context 外的请求 C** | `JOIN_REQUEST_NOT_IN_CONTEXT`（关联指向不存在的 request_id） |
| **当前请求 A 无需 JOIN（选中集合为空），有请求 B 的关联** | 成功，`resolvedJoins=[]`，不验证 B 的 payload/evidence |
| **当前请求 payload 哈希与关联不匹配** | `JOIN_EVIDENCE_PAYLOAD_HASH_MISMATCH` |

**关键区分**：
- `JOIN_REQUEST_NOT_IN_CONTEXT`：关联引用的 `request_id` **不在** `context.request_ids` 中
- `JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND`：当前请求需要 JOIN 但无关联记录
  （其他请求如 B 的存在**不**触发此错误）
- 其他请求的 payload/evidence 内容**不**由当前纯解析调用核验

### 3.8 需要决定的事项

| 事项 | 推荐选项 | 替代方案 |
|------|----------|----------|
| evidence 关联是 context 级持久化 | 推荐：是（版本化批准） | 请求级运行时构造（不推荐：破坏确定性重算） |
| `evidence_ids` 是否必填 | 推荐：`min_length=1` | 允许空（不推荐：失去追溯能力） |
| `payload_sha256` 是否必填 | 推荐：是（绑定不可变 payload） | 省略（不推荐：无法检测 payload 篡改） |
| 是否允许重复 evidence ID | 推荐：允许（原样复制） | 去重（不推荐：改变语义） |

---

## 4. 问题 C：JOIN 方向与执行计划边界

### 4.1 当前代码证据

**`_select_join_closure_v3` 返回按 grantId 排序的集合**：

```python
return tuple(sorted(jg.grant_id for jg in candidates))
# metadata_resolution_v3.py:1127
```

该顺序是 grantId 字典序，不代表 SQL JOIN 执行顺序。

**`_resolve_join_grant_v3` 保留左右方向**：

```python
return left_physical, right_physical, JoinTypeV3(join_grant.join_type)
# metadata_resolution_v3.py:983
```

左右方向来自 join grant 自身，不因无向比较而交换。

**快照关系采用无向匹配**：

```python
# Undirected match: (left,right) or (right,left)
if (left_key == rel_left_key and right_key == rel_right_key) or (
    left_key == rel_right_key and right_key == rel_left_key
):
    matching_edges.append(rel)
# metadata_resolution_v3.py:972-975
```

### 4.2 缺口分析

M2 输出需要明确：
1. LEFT JOIN 保留侧 = 批准 `JoinGrantV3` 的 `left_column_grant_id` 端
2. 基准 relation 的选择（proposed，非已有保证）
3. JOIN 执行顺序是否由 M2 承诺

### 4.3 LEFT JOIN 保留侧规则

**核心规则**：LEFT JOIN 保留侧 = 批准 `JoinGrantV3` 的 `left_column_grant_id` 端。
由 metadataReview 在批准 grant 时确定，**不**由 BFS 起点或运行时决定。

**M2 职责**：验证 grant 存在且物理闭合；输出 `ResolvedJoinV3` 保留 grant 方向。

**M3 职责**：根据 grant 保留侧决定 SQL 表顺序。
首版**不**提供子查询或 CTE 路径；方向不兼容时 M2 阻断。

### 4.4 基准 relation（proposed，非已有保证）

`factValue` 唯一存在（consumer 闭包保证，`fact_bindings_v3.py:355`）只能证明
可以定位该字段，**不能**证明它所在关系适合作为业务查询基准。

**基准关系选择**（proposed 建议）：
- 推荐以 factValue 关系为初始基准（查询目标所在关系）
- 此选择属于 M2 实现建议，**非**既有契约保证
- 支持范围：单关系事实、多关系 INNER JOIN 链、方向兼容的 LEFT JOIN 链
- 不支持范围：方向不兼容的 LEFT JOIN 配置（M2 阻断）

### 4.5 JOIN 执行顺序（BFS 仅遍历已选边，确定性方向检查）

M2 输出 JOIN 执行顺序按 BFS 从基准 relation 遍历，
**仅遍历 `_select_join_closure_v3` 已选中的边**。

**算法**：
1. 初始化 `accumulated = {base_relation}`，队列包含 `base_relation`。
2. 从队列取出关系 `r`，按 grantId 升序遍历 `r` 的邻接边（仅已选中边）。
3. **跳过**通向已访问节点的反向边（两端都在 `accumulated` 中）。
4. 每次处理的边必须连接一个已累积关系和一个新关系。
5. 方向检查：
   - **INNER JOIN**：允许任一端位于 `accumulated`。
   - **LEFT JOIN**：**仅允许** `grant.left` 在 `accumulated` 且 `grant.right` 为新关系。
   - 反向情况（LEFT JOIN 的 `grant.left` 为新关系、`grant.right` 在 `accumulated`）
     → `JOIN_PLAN_DIRECTION_CONFLICT`，**不交换**左右端。
6. 通过检查后，将新关系加入 `accumulated` 和队列。
7. **首条边没有特殊豁免**：LEFT JOIN 同样适用方向规则。

> 本轮方向门禁仅禁止用改写（交换左右端）绕过方向检查。
> 其他 SQL 结构（子查询、CTE 等）仍按既有生成和静态门禁需求处理，
> **不**在本节扩大禁止范围。

**失败顺序**：
1. 连通性检查（`JOIN_CLOSURE_DISCONNECTED`）
2. 歧义检查（`JOIN_CLOSURE_AMBIGUOUS`）
3. 方向兼容性检查（`JOIN_PLAN_DIRECTION_CONFLICT`）

**验收例**：

| # | 场景 | 预期 |
|---|------|------|
| a | `base=A`，单条 grant `A LEFT JOIN B` | 通过：`grant.left=A` 在 accumulated，`grant.right=B` 为新 |
| b | `base=B`，同一 grant `A LEFT JOIN B` | 阻断：`grant.left=A` 为新关系，`grant.right=B` 在 accumulated → `JOIN_PLAN_DIRECTION_CONFLICT` |
| c | `base=A`，grant1 `A LEFT JOIN B`，grant2 `B LEFT JOIN C` | 两条均通过：grant1 同 (a)；grant2 `grant.left=B` 在 accumulated，`grant.right=C` 为新 |

### 4.6 A LEFT JOIN B 合成反例

**数据**：
- A（订单表）：`id={1, 2}`
- B（客户表）：`id={1}`

**正确语义**（A LEFT JOIN B，保留 A）：
```
A.id=1, B.id=1  →  匹配行
A.id=2, B.id=NULL →  保留 A.id=2（B 字段为 NULL）
```
结果：**2 行**（A 中所有行保留）。

**错误语义**（交换保留端，B LEFT JOIN A）：
```
B.id=1, A.id=1  →  匹配行
```
结果：**1 行**（A.id=2 丢失）。

**结论**：交换保留端会丢失 A 中未匹配的行（id=2），改变业务结果。
LEFT JOIN 保留端必须由 grant 固化，不可在解析/生成阶段更改。

### 4.7 确定性处理表

| 场景 | M2 处理 | 输出 |
|------|---------|------|
| 单关系（无 join） | `_select_join_closure_v3` 返回空 tuple | `resolvedJoins=[]` |
| 两关系 inner | 单 grant，BFS 顺序 | 1 条 `ResolvedJoinV3`，`joinType="inner"` |
| 两关系 left | 单 grant，BFS 顺序，保留侧 = grant left | 1 条 `ResolvedJoinV3`，`joinType="left"` |
| 多关系链（A-B-C，inner） | BFS 从 base 出发，仅遍历已选边 | 2 条 `ResolvedJoinV3`，按 BFS 顺序 |
| 方向不兼容（LEFT JOIN 的 grant.left 不在累积侧） | 方向检查触发 `JOIN_PLAN_DIRECTION_CONFLICT` | blocked |
| 环（3 关系 3 grant 全连接） | 歧义检查（candidates=3 ≠ relations-1=2） | blocked |
| 断连（关系无法全连接） | 连通性检查触发 `JOIN_CLOSURE_DISCONNECTED` | blocked |
| 多候选路径（4 关系 ≥4 grant 含环） | 歧义检查（candidates=4 ≠ relations-1=3） | blocked |

### 4.8 边界声明

连通性、BFS 和方向检查**不**证明业务结果正确性。它们仅证明：
- 授权关系图连通（物理上可连接）
- 无歧义多解
- LEFT JOIN 保留侧与 grant 一致

业务结果正确性还取决于 SQL 生成逻辑、谓词下推、NULL 语义等（M3 职责）。

### 4.9 验收用例

1. 单关系 → 空 join 列表
2. 两关系 inner → 1 条 join，BFS 顺序
3. 两关系 left → 1 条 join，保留侧 = grant left
4. 三关系链（B 在第二条边 left）→ 2 条 join，方向兼容
5. 方向冲突（LEFT JOIN 的 grant.left 不在累积侧）→ `JOIN_PLAN_DIRECTION_CONFLICT`
6. 环 → `JOIN_CLOSURE_AMBIGUOUS`
7. 断连 → `JOIN_CLOSURE_DISCONNECTED`
8. 多候选路径 → `JOIN_CLOSURE_AMBIGUOUS`
9. 输入对象不变性
10. BFS 邻接边按 grantId 升序
11. **base=A，`A LEFT JOIN B` → 通过**（grant.left=A 在 accumulated，grant.right=B 为新）
12. **base=B，同一 grant `A LEFT JOIN B` → `JOIN_PLAN_DIRECTION_CONFLICT`**
    （grant.left=A 为新关系，grant.right=B 在 accumulated）
13. **base=A，`A LEFT JOIN B` + `B LEFT JOIN C` → 两条均通过**
    （grant2: grant.left=B 在 accumulated，grant.right=C 为新）

### 4.10 需要决定的事项

| 事项 | 推荐选项 | 替代方案 |
|------|----------|----------|
| 基准关系是否固定为 factValue 关系 | 推荐：是（proposed） | 使用 entity key 关系（可能不同于 factValue 关系） |

---

## 5. 问题 D：`resolve_metadata_v3` 编排与报告组装

### 5.1 当前代码证据

**已实现的辅助函数**（`application/metadata_resolution_v3.py`）：

| 函数 | 行号 | 职责 |
|------|------|------|
| `_validate_resolution_input_v3` | 209 | 输入门禁（结构重验 + 六步内容/范围检查） |
| `_resolve_column_grant_v3` | 361 | 单 column grant 物理引用解析 |
| `_resolve_fields_and_entity_keys_v3` | 475 | 字段绑定与实体键授权闭包 |
| `_resolve_filters_v3` | 638 | filters 物理字段解析 |
| `_resolve_aggregation_v3` | 717 | aggregation 授权引用解析 |
| `_resolve_time_range_v3` | 780 | timeRange 时间字段授权解析 |
| `_resolve_join_grant_v3` | 894 | 指定 join grant 物理授权解析 |
| `_select_join_closure_v3` | 1018 | 多关系授权连接闭包选择 |

**未实现**：公开 `resolve_metadata_v3` 编排函数、entity 映射解析、join 证据关联、报告组装逻辑。

**报告契约**：`BindingResolutionReportV3`（`project_bindings_v3.py:554`）已定义，
包含 `blocked`/`metadataResolved` 内部一致性校验。

### 5.2 统一边界规则

**请求结构重验（wire 级）**：
- 输入不是 `ResolveMetadataRequestV3` → 抛出 `MetadataResolutionStructureErrorV3`
  （中性异常，仅携带稳定 code）
- 序列化失败（`warnings="error"`）→ 同上
- 此阶段在完整门禁之前，无法构造任何报告引用

**完整结构重验成功后**（进入 `_validate_resolution_input_v3`）：
- 内容闭包/范围/授权失败 → 返回 `blocked` 报告
- 能读到 `requestId` **不是** blocked 可构造的充分条件；
  还需要 `request.projectRef`、`request.bindingRequest.ruleRef` 等结构完整

**与既有 REQ/BIZ 的关系**：
- 延续 REQ-20260906-04 第 5a 节"批准闭包校验失败语义"
- 延续 BIZ-20260906-03 第 11 节"M2 报告只记录 code"
- 本边界为 proposed 变更，与既有决策一致

### 5.3 公开函数签名（无旁路参数）

```python
def resolve_metadata_v3(
    request: ResolveMetadataRequestV3,
) -> BindingResolutionReportV3:
    """V3 metadata-resolution public orchestrator (Phase 2G V3).

    Pure computation: no repository, provider, SQL, or environment access.
    Re-validates all inputs, resolves physical references, and assembles
    a BindingResolutionReportV3. On any failure, returns a blocked report
    with neutral issue codes.

    Join authorization evidence is read from
    ``request.projectContext.join_authorization_evidence`` (versioned,
    approved, deterministic). Identical resolution requests deterministically
    produce identical reports.

    Args:
        request: V3 metadata-resolution request (with schemaVersion="1.1.0"
            context carrying entity_grain_authorizations and
            join_authorization_evidence).

    Returns:
        BindingResolutionReportV3: either metadataResolved (success) or
        blocked (any check failed). executable is always False.

    Raises:
        MetadataResolutionStructureErrorV3: when the request structure is so
        damaged that a blocked report cannot be constructed. Neutral
        exception with a stable code only; never carries raw IDs, hashes,
        or payloads.
    """
```

### 5.4 门禁调用顺序

```
0. Wire 结构重验（_revalidate_request_structure）
   → 失败 → 抛出 MetadataResolutionStructureErrorV3

1. 输入门禁（_validate_resolution_input_v3）
   → 失败 → blocked (传播原 code)

2. 字段/实体键解析（_resolve_fields_and_entity_keys_v3）
   → 失败 → blocked (传播原 code)

3. filters 解析（_resolve_filters_v3）
   → 失败 → blocked (传播原 code)

4. aggregation 解析（_resolve_aggregation_v3）
   → 失败 → blocked (传播原 code)

5. timeRange 解析（_resolve_time_range_v3）
   → 失败 → blocked (传播原 code)

6. entityType/grain 映射（新增 _resolve_entity_grain_mapping_v3）
   → 失败 → blocked (ENTITY_GRAIN_* code)

7. join 解析（新增 _resolve_joins_v3，整合 _select_join_closure_v3
   + _resolve_join_grant_v3 + context.join_authorization_evidence 校验）
   → 失败 → blocked (传播原 code)

8. 报告组装

双错误共存时的返回顺序（fail-fast）：
- 步骤 1 内部：按 _validate_resolution_input_v3 原有顺序
- 步骤 2-7：按上表顺序，第一个失败即返回
- 步骤 6（entity）在步骤 7（join）之前
```

### 5.5 报告字段来源统一表

| 报告字段 | 成功来源 | 失败来源 | 说明 |
|----------|----------|----------|------|
| `schemaVersion` | 固定 `"1.0.0"` | 固定 `"1.0.0"` | 报告契约自身版本 |
| `status` | `metadataResolved` | `blocked` | — |
| `executable` | 固定 `False` | 固定 `False` | `Literal[False]` |
| `requestRef.requestId` | `bindingRequest.requestId` | `bindingRequest.requestId` | 调用方携带；不伪造 |
| `requestRef.ruleRef` | `bindingRequest.ruleRef` | `bindingRequest.ruleRef` | 调用方携带 |
| `requestRef.payloadSha256` | `handoffClosure.payloadSha256` | `handoffClosure.payloadSha256` | 调用方携带；**成功时**输入门禁已验证 == resolutionHashes.payloadSha256；**失败时两者可能不一致**（失败证据） |
| `projectRef` | `request.projectRef` | `request.projectRef` | 调用方携带 |
| `contextRef` | `projectContext`（ID+版本+sha256） | `projectContext`（ID+版本+sha256） | 调用方携带 |
| `snapshotRef` | `metadataSnapshot`（ID+版本+sha256） | `metadataSnapshot`（ID+版本+sha256） | 调用方携带 |
| `handoffRefs` | `handoffClosure` | `handoffClosure` | 调用方携带 |
| `resolutionHashes.payloadSha256` | `canonical_sha256(bindingRequest)` | `canonical_sha256(bindingRequest)` | 从 bindingRequest 独立计算；失败时可能与 requestRef.payloadSha256 不同 |
| `resolutionHashes.contextSha256` | `canonical_content_sha256(projectContext)` | `canonical_content_sha256(projectContext)` | 从 projectContext 独立计算 |
| `resolutionHashes.snapshotSha256` | `canonical_content_sha256(metadataSnapshot)` | `canonical_content_sha256(metadataSnapshot)` | 从 metadataSnapshot 独立计算 |
| `resolvedFields` | helper 输出 | `[]`（空列表） | 失败时不携带部分结果 |
| `resolvedEntityKeys` | helper 输出 | `[]`（空列表） | 同上 |
| `resolvedFilters` | helper 输出 | `[]`（空列表） | 同上 |
| `resolvedJoins` | helper 输出 | `[]`（空列表） | 同上 |
| `resolvedAggregation` | helper 输出 | `None` | 同上 |
| `resolvedTimeRange` | helper 输出 | `None` | 同上 |
| `usageTraceabilitySha256` | `compute_usage_traceability_sha256_v3(bindingRequest.usages)` | `compute_usage_traceability_sha256_v3(bindingRequest.usages)` | 从 bindingRequest.usages 独立重算 |
| `issues` | `[]`（空列表） | 至少一个 `impact="blocker"` 的 issue | — |

**关键约束**：
- **绝不使用**占位 ID、占位哈希、占位 usage 摘要
- `resolutionHashes` 在成功和失败报告中**都**从明确对象独立计算
- `usageTraceabilitySha256` 始终从 `bindingRequest.usages` 独立重算
- **只有相关输入门禁通过后**，才能称 `requestRef.payloadSha256` 与
  `resolutionHashes.payloadSha256` 已验证一致
- **失败报告中两者可能不一致**——这正是失败证据，**不**强行改成一致
- `payloadSha256` 在 `requestRef` 中来自 `handoffClosure.payloadSha256`（调用方携带）；
  在 `resolutionHashes` 中从 `bindingRequest` 计算；**不**使用 `closure.payload`

### 5.6 哈希算法与复用

| 对象 | 算法 | 计算时机 |
|------|------|----------|
| payload | `canonical_sha256(bindingRequest)` | 报告组装阶段直接计算 |
| context | `canonical_content_sha256(projectContext)` | 同上 |
| snapshot | `canonical_content_sha256(metadataSnapshot)` | 同上 |
| usage 摘要 | `compute_usage_traceability_sha256_v3` | 同上 |

**报告组装阶段独立计算**：`resolutionHashes` 中的三个摘要和
`usageTraceabilitySha256` 均在报告组装阶段**直接重新计算**，
不依赖输入门禁或解析 helper 的中间结果。这保证：
- 相同输入始终产生相同报告（确定性）
- 实现可独立验证（不依赖执行路径）

`payloadSha256` 在 `requestRef.payloadSha256`（来自 handoffClosure，调用方携带）和
`resolutionHashes.payloadSha256`（从 bindingRequest 独立计算）两处出现。
- **成功报告**：输入门禁已验证两者 canonical 一致
- **失败报告**：两者可能不同（失败证据）

**早期失败时的诊断摘要**：步骤 1-7 失败时，`blocked` 报告中的
`resolutionHashes` 和 `usageTraceabilitySha256` 仍从明确对象计算
（不依赖已完成的解析步骤）。哈希可计算**不**代表内容已通过验证、已批准或可执行。

### 5.7 错误 code 总表（从实际代码核对）

以下从 `src/release_sql_bot/application/metadata_resolution_v3.py` 实际提取的
41 个已有 code + 8 个新增 proposed code 组成。

**已有 code（41 个，来自代码 `_HANDOFF_CODES` / `_APPROVAL_CODES` /
`_METADATA_RESOLUTION_INPUT_CODES` / `_COLUMN_RESOLUTION_CODES` /
`_BINDING_RESOLUTION_CODES` / `_FILTER_RESOLUTION_CODES` /
`_JOIN_RESOLUTION_CODES` / `_JOIN_CLOSURE_CODES` 冻结集合）**：

| code | owner | message |
|------|-------|---------|
| `METADATA_RESOLUTION_INPUT_STRUCTURE_INVALID` | sqlBot | "V3 metadata-resolution request structure is invalid." |
| `HANDOFF_STRUCTURE_INVALID` | sqlBot | "V3 handoff closure structure is invalid." |
| `HANDOFF_SCHEMA_SOURCE_INVALID` | sqlBot | "V3 frozen Schema source failed to load." |
| `HANDOFF_SCHEMA_REF_MISMATCH` | sqlBot | "V3 handoff closure Schema reference mismatch." |
| `HANDOFF_PAYLOAD_SCHEMA_INVALID` | sqlBot | "V3 handoff payload does not conform to the frozen Schema." |
| `HANDOFF_IDENTITY_MISMATCH` | sqlBot | "V3 handoff closure identity does not match payload." |
| `HANDOFF_PAYLOAD_HASH_MISMATCH` | sqlBot | "V3 handoff payload content hash mismatch." |
| `APPROVAL_ID_MISMATCH` | metadataReview | "V3 approval identifier is inconsistent." |
| `APPROVAL_POLICY_MISMATCH` | metadataReview | "V3 approval policy version is inconsistent." |
| `APPROVAL_TIME_MISMATCH` | metadataReview | "V3 approval time is inconsistent." |
| `APPROVAL_CONTEXT_REF_MISMATCH` | metadataReview | "V3 approval context reference mismatch." |
| `APPROVAL_SNAPSHOT_REF_MISMATCH` | metadataReview | "V3 approval snapshot reference mismatch." |
| `APPROVAL_CONTENT_HASH_MISMATCH` | metadataReview | "V3 approval record content hash mismatch." |
| `APPROVAL_CONTEXT_NOT_APPROVED` | metadataReview | "V3 project binding context is not approved." |
| `APPROVAL_SNAPSHOT_NOT_APPROVED` | metadataReview | "V3 metadata snapshot is not approved." |
| `APPROVAL_SNAPSHOT_BINDING_MISMATCH` | metadataReview | "V3 context snapshot binding is inconsistent with approval." |
| `HANDOFF_BINDING_REQUEST_MISMATCH` | sqlBot | "V3 handoff closure payload does not match binding request." |
| `PROJECT_REF_MISMATCH` | sqlBot | "V3 project reference is inconsistent between request and context." |
| `RULE_REF_MISMATCH` | sqlBot | "V3 rule reference is inconsistent between context and binding request." |
| `REQUEST_NOT_IN_CONTEXT` | sqlBot | "V3 binding request is not within the approved context scope." |
| `COLUMN_GRANT_ID_INVALID` | metadataReview | "V3 column grant identifier format is invalid." |
| `SNAPSHOT_RELATION_AMBIGUOUS` | metadataReview | "V3 metadata snapshot contains ambiguous relation identifiers." |
| `SNAPSHOT_COLUMN_AMBIGUOUS` | metadataReview | "V3 metadata snapshot contains ambiguous column identifiers." |
| `COLUMN_GRANT_NOT_FOUND` | metadataReview | "V3 referenced column grant is not found in the approved context." |
| `RELATION_GRANT_NOT_FOUND` | metadataReview | "V3 referenced relation grant is not found in the approved context." |
| `RELATION_NOT_IN_SNAPSHOT` | metadataReview | "V3 referenced relation is not found in the approved metadata snapshot." |
| `COLUMN_NOT_IN_SNAPSHOT` | metadataReview | "V3 referenced column is not found in the approved metadata snapshot." |
| `FIELD_AUTHORIZATION_MISSING` | metadataReview | "V3 field binding authorization is missing." |
| `ENTITY_KEY_AUTHORIZATION_MISSING` | metadataReview | "V3 entity-key authorization is missing." |
| `ENTITY_KEY_AUTHORIZATION_AMBIGUOUS` | metadataReview | "V3 entity-key authorization is ambiguous." |
| `ENTITY_KEY_FIELD_NOT_FOUND` | metadataReview | "V3 entity-key field is not found in the request." |
| `ENTITY_KEY_FIELD_ROLE_MISMATCH` | metadataReview | "V3 entity-key field role is inconsistent." |
| `ENTITY_KEY_COLUMN_GRANT_MISMATCH` | metadataReview | "V3 entity-key column grant is inconsistent." |
| `FILTER_EVIDENCE_REFERENCE_INVALID` | metadataReview | "V3 filter evidence reference is invalid." |
| `JOIN_GRANT_ID_INVALID` | metadataReview | "V3 join grant identifier format is invalid." |
| `JOIN_GRANT_NOT_FOUND` | metadataReview | "V3 referenced join grant is not found in the approved context." |
| `JOIN_ENDPOINTS_IDENTICAL` | metadataReview | "V3 join grant endpoints are identical." |
| `JOIN_RELATIONSHIP_NOT_FOUND` | metadataReview | "V3 join relationship is not found in the approved snapshot." |
| `JOIN_RELATIONSHIP_AMBIGUOUS` | metadataReview | "V3 join relationship is ambiguous in the approved snapshot." |
| `JOIN_CLOSURE_DISCONNECTED` | metadataReview | "V3 required relations cannot be connected by authorized joins." |
| `JOIN_CLOSURE_AMBIGUOUS` | metadataReview | "V3 join closure is ambiguous; multiple valid closures exist." |

**新增 proposed code（8 个）**：

| code | owner | message | 触发条件 |
|------|-------|---------|----------|
| `ENTITY_GRAIN_MAPPING_MISSING` | metadataReview | "V3 entity type/grain to relation mapping is missing." | `(entity_type, grain)` 无映射 |
| `ENTITY_GRAIN_GRANT_INVALID` | metadataReview | "V3 entity type/grain mapping references an invalid relation grant." | 映射的 `relation_grant_id` 不存在 |
| `ENTITY_GRAIN_RELATION_MISMATCH` | metadataReview | "V3 entity type/grain relation mapping is inconsistent with key column." | 映射 relation grant 与键列不一致 |
| `JOIN_PLAN_DIRECTION_CONFLICT` | metadataReview | "V3 join plan direction conflicts with the approved LEFT JOIN preserved side." | LEFT JOIN 保留端不在累积侧 |
| `JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND` | metadataReview | "V3 join authorization evidence association is missing for the current request." | 当前请求无关联记录 |
| `JOIN_EVIDENCE_PAYLOAD_HASH_MISMATCH` | metadataReview | "V3 join authorization evidence payload hash mismatch." | `payload_sha256` 不匹配 |
| `JOIN_GRANT_EVIDENCE_INVALID` | metadataReview | "V3 join grant evidence reference is invalid." | evidence 不在对应请求中 |
| `JOIN_REQUEST_NOT_IN_CONTEXT` | metadataReview | "V3 join authorization evidence references a request not in context." | `request_id` 不在 context 中 |

**统计**：已有 41 + 新增 8 = **总计 49 个 code**。

### 5.8 失败分层

| 失败层级 | 触发阶段 | 结果 | 示例 |
|----------|----------|------|------|
| Wire 构造失败 | JSON → Pydantic 模型 | `ValidationError`（Pydantic 原生） | `evidence_ids=[]`（`too_short`）；缺必填字段（`missing`）；未知字段（`extra=forbid`） |
| 结构重验失败 | `_revalidate_request_structure` | `MetadataResolutionStructureErrorV3` | 根对象非 `ResolveMetadataRequestV3`；序列化失败 |
| 内容闭包/授权失败 | `_validate_resolution_input_v3` 及后续步骤 | `blocked` 报告（携带 issue code） | 批准哈希不一致；grant 缺失；evidence 悬空 |

**结构重验和 model_copy 绕过防护**：
- 即使 `JoinAuthorizationEvidenceV3` 对象已构造成功（通过 Pydantic 验证），
  `resolve_metadata_v3` 仍通过 `_revalidate_request_structure` 对**完整请求**
  执行序列化-重建（`model_dump` → `model_validate`），防止 `model_copy` 注入
- 输入门禁阶段对 context 中的关联列表执行引用闭包检查
  （`request_id` 在 `context.request_ids` 中、`join_grant_id` 在 `context.join_grants` 中）

### 5.9 `metadataResolved` 语义

`metadataResolved` 仅表示：
- 请求内容与授权解析闭合
- 所有引用的物理对象存在于快照
- 所有授权链完整

`metadataResolved` **不表示**：
- MongoDB 仓储真实性
- 批准记录的真实性
- SQL 可执行性
- 业务结果正确性

### 5.10 验收用例

1. 完整 happy path → `metadataResolved`，所有结果字段填充
2. wire 构造失败（`evidence_ids=[]`）→ Pydantic `ValidationError`
3. wire 结构损坏 → `MetadataResolutionStructureErrorV3`
4. 输入门禁失败 → `blocked`，四结果列表为空
5. 字段授权失败 → `blocked`，携带 `FIELD_AUTHORIZATION_MISSING`
6. join 歧义 → `blocked`，携带 `JOIN_CLOSURE_AMBIGUOUS`
7. entityType/grain 映射缺失 → `blocked`，携带 `ENTITY_GRAIN_MAPPING_MISSING`
8. 方向冲突 → `blocked`，携带 `JOIN_PLAN_DIRECTION_CONFLICT`
9. 当前请求缺关联 → `blocked`，携带 `JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND`
10. 请求 B 存在但当前请求 A 缺关联 → `JOIN_EVIDENCE_ASSOCIATION_NOT_FOUND`
    （**不**报 `JOIN_REQUEST_NOT_IN_CONTEXT`）
11. blocked 报告 `resolutionHashes` 可计算（从明确对象）
12. blocked 报告不携带部分结果
13. `metadataResolved` 不携带 blocker issue
14. usage 摘要可独立重算
15. 异常脱敏
16. 输入对象不变性
17. **payload 不匹配**：`bindingRequest` 被篡改（保留旧 payloadSha256）→
    输入门禁报 `HANDOFF_BINDING_REQUEST_MISMATCH`；blocked 报告中
    `requestRef.payloadSha256`（来自旧 closure）与
    `resolutionHashes.payloadSha256`（从篡改 bindingRequest 重算）不同；
    无占位值，无部分解析结果
18. **context 自哈希不匹配**：`projectContext.contentSha256` 被篡改 →
    输入门禁报 `APPROVAL_CONTEXT_REF_MISMATCH`；`resolutionHashes.contextSha256`
    从实际 projectContext 重算（可复算）；失败证据清晰

---

## 6. 后续最小编码任务清单

按"完整契约与版本 → entity helper → JOIN helper → 公开编排"排序。

> **版本约束**：禁止在两个任务中以同一个 1.1.0 版本先后定义不同必填字段集合。
> 任务 A 一次性交付完整 `schemaVersion="1.1.0"` 契约。

### 任务 A：完整 context 1.1.0 契约与版本切片

| 项目 | 内容 |
|------|------|
| 目标 | 同时新增 `EntityGrainAuthorizationV3`（必填 `min_length=1`）、`JoinAuthorizationEvidenceV3`（必填 `min_length=1` 每项）、`ProjectBindingContextV3` 两个新字段、唯一性及结构约束；`schemaVersion "1.0.0" → "1.1.0"` |
| 允许修改（生产） | `src/release_sql_bot/domain/project_bindings_v3.py`（新增两个模型 + 修改 `ProjectBindingContextV3`） |
| 允许修改（测试） | `tests/v3_metadata_support.py`（`_build_context_wire` 新增两个字段；`_make_context` 更新）；`tests/contract/test_project_bindings_v3_contract.py`（构造点 `test_project_bindings_v3_contract.py:215` 等）；`tests/unit/test_metadata_resolution_v3_join_closure.py`（构造点 `join_grants=` 参数）；`tests/unit/test_metadata_resolution_v3_join_grant.py`（grant fixture）；`tests/contract/test_metadata_resolution_v3_contract.py`（请求夹具）；`tests/contract/test_metadata_resolution_v3_report_contract.py`（报告夹具） |
| 允许修改（文档） | `docs/architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md`（§4.3 版本表、§12 状态）；`docs/progress/PROG-YYYYMMDD.md` |
| 前置 | 问题 A + B 设计方案批准 |
| 非目标 | 不修改 `JoinGrantV3`；不修改上游 `FactBindingRequest 3.0.0`；不做 V3↔V2 转换；不实现 helper 或编排 |
| 测试 | 新版本合法输入往返；缺 `entity_grain_authorizations` → `missing`；缺 `join_authorization_evidence`（当有 join 请求时）→ 语义阻断；旧版本 `1.0.0` context 构造新版模型 → 拒绝；`join_authorization_evidence=[]` 在无 JOIN 时合法；`(entity_type, grain)` 重复 → 构造拒绝；`(request_id, join_grant_id)` 重复 → 构造拒绝 |
| 完成标准 | 契约 + 所有受影响夹具更新 + 测试通过 + ruff/format/pytest + 文档同步 |

**版本绑定说明**：
- `ResolveMetadataRequestV3` **实际内嵌 `projectContext`**（无顶层 `contextRef`）；
  版本绑定依赖 `contextId + contextVersion + contentSha256` 与
  `approvalRecord.contextRef` 的完整九组校验（DEV §5.2）
- 新解析路径接受 `projectContext.schemaVersion="1.1.0"`
- 旧载荷原样保留供审计（insert-only），但新版解析服务**不**接受旧 schemaVersion 载荷
- 本切片**不**实现历史解码器；不宣称历史请求可自动走新版解析服务
- 旧 `schemaVersion` 即使补齐新字段，仍因版本不符被拒绝
- 缺字段与版本不符可能同时产生结构错误；不承诺旧载荷只会触发 `missing`

### 任务 B：entity helper

| 项目 | 内容 |
|------|------|
| 目标 | `_resolve_entity_grain_mapping_v3` 内部辅助函数 |
| 允许修改（生产） | `src/release_sql_bot/application/metadata_resolution_v3.py`（新增辅助函数 + `MetadataEntityResolutionErrorV3`） |
| 允许修改（测试） | `tests/unit/test_metadata_resolution_v3_entity_grain_mapping.py`（新增） |
| 前置 | 任务 A 完成 |
| 测试 | 合法映射成功；缺失映射；无效 relation grant；relation 不一致；重复键拒绝；多实体键不一致；输入不变性；异常脱敏；大小写策略 |

### 任务 C：JOIN helper

| 项目 | 内容 |
|------|------|
| 目标 | `_resolve_joins_v3`（BFS 仅遍历已选边，LEFT JOIN 保留端 = grant left） |
| 允许修改（生产） | `src/release_sql_bot/application/metadata_resolution_v3.py` |
| 允许修改（测试） | `tests/unit/test_metadata_resolution_v3_joins.py`（新增） |
| 前置 | 任务 A 完成 |
| 测试 | 单关系空 join；两关系 inner/left；三关系链；方向冲突阻断；环/断连/歧义；BFS 顺序确定性；证据覆盖规则；同 context 两请求隔离；无 JOIN 不要求关联 |

### 任务 D：公开编排

| 项目 | 内容 |
|------|------|
| 目标 | 公开 `resolve_metadata_v3` 函数 + 报告组装 + `MetadataResolutionStructureErrorV3` |
| 允许修改（生产） | `src/release_sql_bot/application/metadata_resolution_v3.py` |
| 允许修改（测试） | `tests/unit/test_metadata_resolution_v3.py`（编排测试） |
| 前置 | 任务 A + B + C 完成 |
| 测试 | 完整 happy path；wire 结构损坏 → 中性异常；各步骤失败 → blocked；payload 不匹配；context 自哈希不匹配；`resolutionHashes` 独立计算；`usageTraceabilitySha256` 独立重算 |

---

## 7. 文档影响

- 本设计文档：`docs/architecture/DEV-20260911-01-v3-m2-remaining-design-gaps.md`（修改，proposed）
- 上游设计：`docs/architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md`
  （§12 M2 八步表步骤 2/7/8 状态待任务 1-3 完成后更新；
   §14 开放问题第 7 项待任务 1 完成后关闭）
- REQ/BIZ 变更关联：
  - [REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
    第 7 节 M2 状态描述需更新（待实现后）
  - [BIZ-20260906-03](../decisions/BIZ-20260906-03-v3-downstream-authority-boundary.md)
    第 9 节 M2 状态描述需更新（待实现后）

---

## 8. 边界与约束

- 不修改 `src/`、`tests/`、依赖、冻结 Schema 或现有 V2 行为
- 不做 V3↔V2 转换，不跳过批准闭包或仓储背书
- 不读取 `.env` 或凭据，不连接 MongoDB、SQL Server、在线模型
- 本轮不需要读取私有资料；不复制私有对象、字段、SQL 或业务数据
- 不提交、不推送 Git
- 不新增临时成功入口，不用"暂时跳过校验"推进后续功能
- 本轮不把 M2 标记为 `completed`
- 已确认资料中的事实不要求用户重复提供
