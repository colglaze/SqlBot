# REQ-20260906-01：V2 候选模板 MongoDB 持久化

- 状态：`completed`
- 创建日期：2026-09-06
- 来源：用户要求把生成的 SQL 模板候选写入 MongoDB 新建集合
- 前置需求：[REQ-20260828-01](REQ-20260828-01-v2-candidate-generation-input.md)
- 受业务决策约束：[BIZ-20260905-01](../decisions/BIZ-20260905-01-restricted-sqlserver-validation-boundary.md)
- 技术方案：[DEV-20260906-01](../architecture/DEV-20260906-01-v2-candidate-persistence.md)

## 1. 背景

当前 V2 候选生成成功后只返回 HTTP 响应，没有任何持久化路径；候选一旦未被调用方保存即丢失，
后续 Phase 4/5 验证与 Phase 6 人工审核缺乏可追溯的落库基线。用户明确要求：生成成功的模板候选
写入 MongoDB 新建集合。

## 2. 目标与范围

1. 生成成功的 `SqlTemplateCandidateV2` 写入 SqlBot 自有的新 MongoDB 集合；
2. insert-only、不可变：同一 `contentSha256` 幂等（重复插入视为已存在），禁止 update/overwrite；
3. 独立开关（默认关闭），不复用 `RSB_DATABASE_ENABLED` 的语义；intake 只读边界不变；
4. 候选载荷固定 `executable=false / reviewStatus=pending`；本需求不增加任何批准/发布状态。

范围外：候选状态机、revision、批准/驳回、Phase 5 验证报告持久化、RuleReader 集合写入。

## 3. 验收标准

1. 开关关闭时行为与现状完全一致（响应契约不变、无任何 Mongo 写操作）。
2. 开关开启且 MongoDB 配置完整时：服务启动阶段在自有数据库建立集合并创建 `contentSha256`
   唯一索引；生成成功后候选完整载荷（canonical camelCase）+ 存储元数据落库。
3. 同一候选重复生成/重放 → 幂等，不产生重复文档、不报错。
4. 存储失败（连接、权限、超时）不改变生成结果，也不把失败伪装成功：日志记录安全结果，
   候选仍正常返回。
5. 不写 RuleReader 的 `rule_versions` / `fact_binding_handoffs`；不记录 URI、凭据或 SQL 到日志。
6. 离线测试（fake pymongo 替身）覆盖：成功落库、幂等、权限/连接失败、开关关闭零调用。

## 4. 配置

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `RSB_CANDIDATE_STORE_ENABLED` | `false` | 候选持久化总开关 |
| `RSB_CANDIDATE_STORE_DATABASE` | `release_sql_bot` | SqlBot 自有数据库 |
| `RSB_CANDIDATE_STORE_COLLECTION` | `sql_template_candidates` | 候选模板集合 |

开启时要求 `RSB_MONGODB_URI` 已配置（复用同一连接串）；**该账号必须对
`release_sql_bot` 库具备建集合/写权限**，RuleReader 库仍保持只读。

## 5. 实施与验收记录（2026-09-06）

- 实现提交：`255196f0534ce5c3fbd1c534bb96143f90c93d5c`
  （`feat: persist generated v2 candidates to mongodb collection`），包含本需求与技术方案文档、
  存储端口与编排、`MongoCandidateStore` insert-only 适配器、API 开关装配和离线测试。
- 验收标准对照（全部为离线验证，fake pymongo 替身，未连接真实 MongoDB）：
  1. 开关关闭时 store 为 `None`，生成走原 `generate_sql_candidate_v2`，响应契约不变、零 Mongo 调用；
  2. 开关开启时 `initialize` 执行 ping 并创建 `contentSha256` 唯一索引 `ux_content_sha256`；
     生成成功后 canonical camelCase 完整载荷与 `schemaVersion`/`storedAtUtc` 存储元数据落库；
  3. 同 `contentSha256` 重复写入命中唯一索引 → `duplicate`，幂等、不报错、不重复落库；
  4. ping/建索引失败折叠为 `unavailable`，`insert_one` 异常折叠为 `failed`，均不改变生成结果；
  5. 适配层全部写路径仅 `insert_one` 与 `create_index`，无 update/replace/delete；RuleReader 集合
     路径不经过该适配器；
  6. `tests/unit/test_candidate_store.py` 共 17 项测试覆盖成功落库、幂等、连接/权限失败、开关关闭
     零调用，并包含日志白名单测试：成功、失败、幂等日志均不含数据库名、集合名、URI、凭据、SQL。
- 日志白名单偏差收敛：初版实现的成功日志曾输出 database/collection 名称，超出
  [DEV-20260906-01](../architecture/DEV-20260906-01-v2-candidate-persistence.md) 第 5 节白名单；
  已按"收敛日志"选项改为固定安全文案，并以专项日志测试固化（见上第 6 条）。
- 全量离线回归：`uv run pytest` → `391 passed`（本节登记时点）。
- 保持开放（不随 `completed` 关闭）：真实 MongoDB integration 未执行、未连接真实 MongoDB；
  实际部署账号对本库的建集合/写权限未验证；持久化不授予任何审批或可执行语义，候选仍固定
  `executable=false / reviewStatus=pending`。
