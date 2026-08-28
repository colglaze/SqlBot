# REQ-20260828-02：RuleReader 不可变事实交接只读接入

- 状态：`completed`
- 创建日期：2026-08-28
- 来源：用户要求 SqlBot 只读消费 RuleReader `fact_binding_handoffs` 的 `FactBindingRequest 2.0.0`
- 前置需求：[REQ-20260827-02](REQ-20260827-02-rulereader-fact-binding-v2-intake.md)
- 关联决策：[BIZ-20260828-02](../decisions/BIZ-20260828-02-rulereader-handoff-read-boundary.md)
- 技术方案：[DEV-20260828-02](../architecture/DEV-20260828-02-rulereader-handoff-read-intake.md)

## 1. 背景

Phase 2F 已能对调用方直接提交的完整 `FactBindingRequest 2.0.0` 做独立 consumer 校验和阻断分析，
但尚未读取 RuleReader 所有的 MongoDB `fact_binding_handoffs`，也没有复核持久化包装身份、
`payload_sha256` 与固定上游 JSON Schema。原始 C 任务要求的“从不可变交接集合只读 intake”因此还缺
最后一层存储边界。

## 2. 目标

- 按精确 `ruleVersion` 只读查询 `fact_binding_handoffs`，不选择“最新”版本；
- 校验 MongoDB 包装字段、完整 V2 payload、固定上游 Draft 2020-12 Schema、来源身份和 canonical
  SHA-256；
- 对每条合法记录复用 Phase 2F 的确定性 gap analysis，完整保留所有 uncertainty；
- 任一记录含 blocking issue 时，整批状态固定为 `blocked`，且该入口不读取或调用候选 provider；
- RuleReader 不恢复 V1 降级，SqlBot 不接受 `1.0.0` 或未知契约版本。

## 3. 范围内

- SqlBot 自有的交接包装 consumer、只读仓储端口和稳定错误；
- 以已记录 SHA-256 固定保存的上游 `FactBindingRequest 2.0.0` JSON Schema；
- 现有 MongoDB 生命周期中增加交接集合只读查询；
- 应用层整批校验、哈希复算、来源交叉校验、重复身份检查和 `blocked` 聚合；
- `GET /api/v1/fact-binding-handoffs/v2?ruleVersion=<exact-version>`；
- 完全离线的合成脱敏单元、契约和 API 测试。

## 4. 范围外

- 对 `fact_binding_handoffs` 创建索引、migration、insert、update、replace、delete 或 upsert；
- 修改 RuleReader 代码、规则版本、交接记录或 blocking uncertainty；
- 选择最新规则/交接、自动迁移 V1、从 HTTP 裸 payload 创建交接记录；
- 调用 DeepSeek、生成 SQL、解析 SQL AST、访问 SQL Server、读取本地参考工作簿；
- 候选持久化、审批、发布或执行。

## 5. 验收标准

1. 仓储端口只暴露按精确 `ruleVersion` 列举记录的方法；实现只发出 MongoDB `find`，没有任何写方法或
   migration。
2. 每条记录必须精确包含 `_id/request_id/rule_version/fact_code/contract_version/payload_sha256/
   created_at/payload`，未知或缺失包装字段失败。
3. `_id == request_id == payload.requestId == <ruleVersion>#<factCode>`；包装版本、事实编码、规则版本
   与 payload 必须一致。
4. payload 同时通过 SqlBot 独立 Pydantic consumer 与固定上游 Draft 2020-12 Schema；固定副本允许
   仓库执行 LF 行尾规范化，但还原上游 CRLF 字节后 SHA-256 必须为来源清单记录的
   `38fec6b22511984983e7e7fbbdb40afd58aeffd51b2de8ab73fdfb187024026b`。
5. `payload_sha256` 必须等于 RuleReader 规则下的完整 camelCase canonical JSON SHA-256；来源
   `ruleRef.sourceSha256` 与 `provenance.source.sha256` 必须一致。
6. 同一批次 request ID、fact code 必须唯一，所有记录必须属于请求的精确规则版本；任一损坏记录使
   整批 fail closed，不能返回部分成功。
7. 每条记录的 uncertainty 原样进入 Phase 2F 分析；任一 blocking issue 使批次 `blocked`，否则最多
   为 `readyForMetadataResolution`，始终 `executable=false`。
8. 只读 API 的 blocked、not found、上游记录无效和仓储不可用分别使用稳定结果或 404/502/503；错误
   不输出 payload、MongoDB URI 或底层驱动异常。
9. 注入固定候选 provider 后读取 blocking handoff，provider 调用次数仍为 `0`；V1、错误哈希、错误
   来源、额外字段和包装身份冲突均有回归。
10. 默认测试不访问网络、MongoDB、SQL Server、在线模型或本地工作簿；Ruff、format、pytest 与
    `git diff --check` 通过，README、索引、路线图和当日 PROG 同步。

## 6. 完成证据

本需求已按 [PROG-20260828](../progress/PROG-20260828.md#rulereader-不可变事实交接只读接入已完成)
完成；实现、离线回归、安全边界和权威检查证据统一记录在该进度文档，避免重复维护。
