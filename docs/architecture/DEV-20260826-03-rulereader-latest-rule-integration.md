# DEV-20260826-03：真实 RuleReader 最新规则版本接入设计

- 状态：`completed`
- 创建日期：2026-08-26
- 实现需求：[REQ-20260826-03](../requirements/REQ-20260826-03-rulereader-latest-rule-integration.md)
- 受业务决策约束：[BIZ-20260826-02](../decisions/BIZ-20260826-02-rulereader-latest-version-selection.md)

## 1. 真实存储 Shape

`rule_versions` 外层是查询和审计元数据，`document` 是 RuleReader 导出载荷：

```text
rule_id, rule_version, schema_version, source_sha256
parser_version, status, executable, generated_at, stored_at
document
  schemaVersion, ruleVersion, status, executable, generatedAt
  source { sourceName, relativePath, characterCount, sha256 }
  parser { provider, model, promptVersion, parserVersion }
  rule { ruleId, title, scope, [entityType], sourceViews, rootCondition, ... }
```

MongoDB `_id` 只用于次级排序，不进入领域返回或 API。
`entityType` 在真实 Schema `1.0.0` 中不存在、在 `2.0.0` 中必填；两种 Shape 分别校验，不做跨版本
默认值补齐。

## 2. 领域契约

新增 `domain/rule_versions.py`：

- `RuleVersionSource`、`RuleVersionParser`：严格 camelCase 来源和解析器字段；
- `RuleVersionRule`：严格规则顶层字段，复杂的条件、事实、映射和测试载荷保留为 JSON 对象数组；
- `RuleVersionDocument`：Schema、版本、状态、不可执行标志、生成时间、来源、解析器和规则；
- `StoredRuleVersion`：外层存储元数据和内层文档。

模型禁止契约外顶层字段、要求时间带时区、哈希为 64 位小写十六进制，并在模型校验器中比较
外层和内层的关键引用。规则内部任意值必须能以 JSON mode 序列化；不允许 BSON 专用对象泄漏到
API。

## 3. 查询

```javascript
findOne(
  { rule_id: ruleId },
  {
    sort: { generated_at: -1, _id: -1 },
    projection: { _id: 0, /* StoredRuleVersion 字段 */ }
  }
)
```

查询使用现有 `ix_rule_versions_rule_id_generated_at` 索引。`ruleId` 继续使用封闭字符集校验，调用方
不能传 MongoDB 运算符或任意查询文档。仓储不缓存结果。

## 4. API 迁移

旧的未发布离线接口参数 `projectId + target` 直接替换为真实契约参数 `ruleId`：

```http
GET /api/v1/rules/latest?ruleId=REPORT_RELEASE_ALL_001
```

成功返回 camelCase RuleReader 版本包装。错误码保持 `RULE_NOT_FOUND`、
`RULE_DOCUMENT_INVALID` 和 `RULE_REPOSITORY_UNAVAILABLE`。

## 5. 最小权限

使用现有本地管理员凭据只执行一次 `createUser`，创建仅拥有
`{ role: "read", db: <configured database> }` 的服务账号。随机密码只写入被 Git 忽略的 `.env`，
不进入命令输出、日志、文档或测试。创建后用新账号验证 `connectionStatus`，应用全过程不再使用
管理员连接。

## 6. 验证

- 单元测试使用两个版本的合成包装夹具验证排序、无缓存和一致性校验；
- API 测试验证 `ruleId`、camelCase 返回和稳定错误；
- 真实冒烟使用已有规则，只读取外层版本元数据对比 API，不输出完整正文；
- 权威 Ruff、format、pytest 检查保持离线可运行。
