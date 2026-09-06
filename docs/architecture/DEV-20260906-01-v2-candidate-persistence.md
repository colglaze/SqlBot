# DEV-20260906-01：V2 候选模板 MongoDB 持久化设计

- 状态：`completed`
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
- 日志只允许：candidate contentSha256 前缀、存储结果、模板代码；禁止 URI、凭据、SQL 文本、
  数据库/集合名称。`initialize` 成功日志必须是不含任何定位信息的固定安全文案。

## 6. 测试

- fake pymongo 替身：成功落库文档形状、幂等 duplicate、ping/建索引失败 → UNAVAILABLE、
  insert 异常 → FAILED；
- 编排：开关关闭 store 为 None 且 save 零调用；存储异常不影响生成结果；
- 设置：开关开启但 URI 缺失 → 配置失败；库名/集合名形状校验；safe_summary 只含布尔。

## 7. 实施与验收记录（2026-09-06）

- 实现提交：`255196f0534ce5c3fbd1c534bb96143f90c93d5c`
  （`feat: persist generated v2 candidates to mongodb collection`）。落地范围与本设计第 2 节
  模块清单一致：`application/ports/candidate_store.py`、`application/candidates_v2.py` 编排、
  `infrastructure/database/mongodb_candidates.py` 适配器、`application/runtime.py` 容器字段、
  `infrastructure/database/__init__.py` 装配、`api/app.py` 按开关路由，另有本设计文档与
  [REQ-20260906-01](../requirements/REQ-20260906-01-v2-candidate-persistence.md)。
- 文档形状、唯一索引、insert-only、幂等与失败降级语义均按第 3、4 节实现，未放宽：
  写路径仅 `insert_one` 与 `create_index`，无 update/replace/delete。
- 日志白名单偏差处理：初版实现的 `initialize` 成功日志输出 database/collection 名称，超出第 5 节
  白名单（DEV-20260906-02 第 6 节前置验收登记为低严重度发现，给出"收敛日志或修订白名单"两个
  选项）。本任务选定**收敛日志**：成功日志改为固定安全文案，第 5 节白名单同步明确禁止
  数据库/集合名称；`tests/unit/test_candidate_store.py` 新增 5 项日志卫生测试，断言成功、失败、
  幂等日志均不含数据库名、集合名、URI、凭据、SQL 与驱动错误细节。
- 验收证据（全部离线，未连接真实 MongoDB/SQL Server，未调用在线模型）：
  `uv run pytest tests/unit/test_candidate_store.py` → `17 passed`；`uv run pytest` →
  `391 passed`；`uv run ruff check .` 与 `uv run ruff format --check .` 通过；
  `git diff --check` 通过。
- 保持开放（不随 `completed` 关闭）：真实 MongoDB integration 测试未执行；实际部署账号对本库的
  建集合/写权限未验证；持久化不代表审批或可执行，候选仍固定
  `executable=false / reviewStatus=pending`。
