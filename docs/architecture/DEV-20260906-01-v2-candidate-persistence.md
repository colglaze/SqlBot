# DEV-20260906-01：V2 候选模板 MongoDB 持久化设计

- 状态：`approved`
- 创建日期：2026-09-06
- 实现需求：[REQ-20260906-01](../requirements/REQ-20260906-01-v2-candidate-persistence.md)

## 1. 设计结论

存储是生成链路末端的**单向 sink**，不参与生成编排、不改变任何响应契约：

```text
POST /api/v1/sql-candidates/v2/generate
  -> generate_and_store_sql_candidate_v2(provider, payload, store | None)
       -> generate_sql_candidate_v2(...)      既有严格输出门禁，不变
       -> store.save(candidate)               insert-only，结果只进日志
       -> 响应仍是 SqlTemplateCandidateV2     契约不变
```

store 为 `None`（开关关闭）时走原 `generate_sql_candidate_v2`，行为与现状逐字节一致。

## 2. 模块与文档边界

```text
application/ports/candidate_store.py        CandidateTemplateStore 协议 + CandidateStoreOutcome
application/candidates_v2.py                generate_and_store_sql_candidate_v2 编排
infrastructure/database/mongodb_candidates.py  AsyncMongoClient 适配器（复用现有 client 工厂模式）
application/runtime.py                      RuntimeContainer 增加 candidate_store 字段
infrastructure/database/__init__.py         build_database_resources 装配
api/app.py                                  generate 端点按开关选择编排函数
```

## 3. 集合与文档形状

- 数据库/集合：`RSB_CANDIDATE_STORE_DATABASE`（默认 `release_sql_bot`）/
  `RSB_CANDIDATE_STORE_COLLECTION`（默认 `sql_template_candidates`）；
- 启动时（initialize）`ping` + `create_index("contentSha256", unique=True)`（隐式建集合）；
  建索引/连接失败只标记 UNAVAILABLE 并输出无敏感信息告警，不阻断服务启动；
- 文档形状：

```text
{
  schemaVersion:   "1.0.0"
  contentSha256:   <候选自哈希，唯一索引>
  storedAtUtc:     <ISO-8601 UTC>
  candidate:       <SqlTemplateCandidateV2 canonical camelCase 完整载荷>
}
```

- insert-only：`insert_one` 命中唯一索引冲突 → 视为 `duplicate`，幂等成功；
  禁止 update/replace；不删除。

## 4. 端口语义

```python
class CandidateStoreStatus(StrEnum):
    STORED = "stored"  # 本次写入
    DUPLICATE = "duplicate"  # 已存在同 contentSha256，幂等
    UNAVAILABLE = "unavailable"  # 存储未启用/未就绪
    FAILED = "failed"  # 写入异常（连接/权限/超时），候选仍正常返回


class CandidateTemplateStore(Protocol):
    async def initialize(self) -> None: ...  # ping + 唯一索引；失败 -> unavailable
    async def save(self, candidate: SqlTemplateCandidateV2) -> CandidateStoreOutcome: ...
    async def close(self) -> None: ...
```

`save` 不抛异常：失败折叠为 `FAILED` outcome（日志 message 固定中文安全文案，不含 URI/SQL/凭据）。
生成结果与存储结果解耦：存储失败绝不影响 HTTP 200 与候选载荷。

## 5. 权限与安全边界

- 复用 `RSB_MONGODB_URI` 连接串；**账号需对 `release_sql_bot` 库有建集合/写权限**；
  RuleReader 库（`rule_versions`、`fact_binding_handoffs`）路径完全不经过本适配器；
- `RSB_MONGODB_READ_ONLY=true` 的只读校验仍只约束 intake 适配器语义；候选存储由独立开关与
  独立库/集合边界约束；
- 日志只允许：candidate contentSha256 前缀、存储结果、模板代码；禁止 URI、凭据、SQL 文本。

## 6. 测试

- fake pymongo 替身：成功落库文档形状、幂等 duplicate、ping/建索引失败 → UNAVAILABLE、
  insert 异常 → FAILED；
- 编排：开关关闭 store 为 None 且 save 零调用；存储异常不影响生成结果；
- 设置：开关开启但 URI 缺失 → 配置失败；库名/集合名形状校验；safe_summary 只含布尔。
