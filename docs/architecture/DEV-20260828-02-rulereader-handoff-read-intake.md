# DEV-20260828-02：RuleReader 不可变事实交接只读接入设计

- 状态：`completed`
- 创建日期：2026-08-28
- 实现需求：[REQ-20260828-02](../requirements/REQ-20260828-02-rulereader-handoff-read-intake.md)
- 受决策约束：[BIZ-20260828-02](../decisions/BIZ-20260828-02-rulereader-handoff-read-boundary.md)
- 前置设计：[DEV-20260827-02](DEV-20260827-02-fact-binding-v2-readiness.md)

## 1. 模块边界

```text
contracts/fact-binding-request-2.0.0.schema.json  固定上游 Schema 副本
domain/fact_binding_handoffs_v2.py                MongoDB 包装与 intake batch 契约
application/ports/handoffs.py                     只读仓储端口与稳定错误
application/handoff_intake_v2.py                  Schema/hash/来源/批次/gap 门禁
infrastructure/database/mongodb.py                 精确 ruleVersion 只读查询
api/app.py                                         只读 intake HTTP 入口
```

领域与应用代码不导入 RuleReader 包或兄弟仓库路径。MongoDB 适配器不包含写方法；应用服务不导入 V1
readiness、Prompt 或候选生成模块。

## 2. 固定 Schema 与依赖

- 将 RuleReader `contracts/fact-binding-request-2.0.0.schema.json` 的内容副本保存到 SqlBot 包内；仓库
  可按既有格式规则将 CRLF 规范化为 LF；
- 加载时先将行尾确定性还原为上游 CRLF 字节，再计算 SHA-256 并与
  `38fec6b22511984983e7e7fbbdb40afd58aeffd51b2de8ab73fdfb187024026b` 比较，再执行
  `Draft202012Validator.check_schema`；
- 每个 payload 使用 `FormatChecker` 离线验证；
- `jsonschema[format]>=4.26,<5` 从测试依赖提升为运行时依赖并由 `uv.lock` 固定。

## 3. 包装契约

```text
StoredFactBindingHandoffV2
  _id
  request_id
  rule_version
  fact_code
  contract_version = 2.0.0
  payload_sha256
  created_at
  payload: FactBindingRequestV2
```

包装使用 RuleReader MongoDB 的 snake_case 字段，`payload` 保持严格 camelCase。包装 `extra=forbid`、
时间必须带时区，`payload_sha256` 必须为小写 64 位十六进制。

## 4. 只读仓储

现有 MongoDB 生命周期同时持有 `rule_versions` 与 `fact_binding_handoffs` collection 句柄。新增端口只
定义：

```text
list_by_rule_version(rule_version) -> tuple[StoredFactBindingHandoffV2, ...]
```

适配器执行精确 `{"rule_version": ruleVersion}` 查询并按 `fact_code/_id` 升序。为使
`extra=forbid` 能发现上游异常包装，查询保留完整顶层文档，不用 inclusion projection 隐藏未知字段；
错误路径仍不回显文档。驱动错误转换为仓储不可用；包装 Pydantic 失败转换为上游记录无效。任何路径
都不执行 insert/update/replace/delete/index/migration。

## 5. 应用门禁顺序

1. 加载并验证固定 Schema 自身及来源 SHA-256；
2. 从只读仓储列举精确规则版本；空集合返回 not found；
3. 按 `fact_code/request_id` 固定顺序并全批次预检 request ID、fact code 唯一及 rule version 一致；
4. 每条 payload 运行固定 JSON Schema；
5. 复核包装与 payload 的 request/rule/fact/contract 身份；
6. 使用共享 canonical JSON 规则复算 payload SHA-256；
7. 复核 `ruleRef.sourceSha256 == provenance.source.sha256`；
8. 复用 `analyze_binding_gaps_v2`，不改变 payload 或 uncertainty；
9. 任一 report blocked 时聚合批次 blocked，否则最多为 `readyForMetadataResolution`。

所有记录全部通过前不返回部分批次。静态契约、身份、来源或哈希错误使用安全的
`FactBindingHandoffInvalidError`；错误不包含完整 payload。

## 6. API 与 provider 隔离

```text
GET /api/v1/fact-binding-handoffs/v2?ruleVersion=<exact-version>
```

- 仓储未启用或不可用：503；
- 精确版本无记录：404；
- 任一上游记录无效：502；
- 成功：200，返回完整已验证 payload、包装 hash/时间、每条 gap report 和整批状态；
- 路由只读取 `RuntimeContainer.fact_binding_repository`，不读取 `candidate_provider`。

## 7. 测试

- 固定 Schema hash、Schema 自校验、合成 fixture 双重验证；
- 包装缺字段/额外字段/V1、身份冲突、来源冲突、hash 冲突、重复 ID/fact 和跨版本记录；
- 合法 blocking 记录保持原 uncertainty，批次 blocked 且不可执行；
- Mongo fake 断言精确 find/sort、完整包装校验与无写调用；
- API 断言 200/404/502/503 与 provider 零调用；
- 全部测试离线，不连接真实 MongoDB、SQL Server 或模型。

## 8. 实施结果

本设计已实现，完成证据见
[PROG-20260828](../progress/PROG-20260828.md#rulereader-不可变事实交接只读接入已完成)。实现未增加
交接集合写方法、migration、索引管理、V1 降级或 SQL 生成路径。
