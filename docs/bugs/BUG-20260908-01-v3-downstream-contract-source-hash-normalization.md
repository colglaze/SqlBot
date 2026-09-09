# BUG-20260908-01：V3 下游契约来源登记与上游提交树不一致（行尾 / 提交状态）

- 状态：`completed`（来源登记问题已修复；2026-09-09 T0 登记规范化完成）
- 严重度：中（属来源登记与跨仓库换行一致性问题，非运行时放行缺陷）
- 发现日期：2026-09-08
- 修复日期：2026-09-09
- 关联 REQ：[REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
- 关联进度：[PROG-20260909](../progress/PROG-20260909.md)

## 现象

SqlBot 来源清单 `docs/specs/fact-binding-request-3.0.0-source.json` 对 RuleReader
`FactBindingRequest 3.0.0` Schema 的登记，与上游 RuleReader 当前提交树的真实字节不一致：

1. **提交状态登记过时。** 清单记录 `upstreamCommittedAtObservation: false`
   （`sourceHeadAtObservation: 65a96846…`，观察时间 2026-09-06）。RuleReader 当前
   HEAD `01ddae0` 已包含提交 **`bad6fd3`**
   （`feat: V3 contract pipeline, Schema v5 persistence, and generated-rules archive`，
   2026-09-06 23:02）——"未提交"当前不再成立。
2. **哈希对象登记偏差。** 清单 `sha256` 字段（`2c5e4603…`）与 SqlBot 冻结副本
   工作区 CRLF 字节匹配，但与上游 `bad6fd3` 提交树 LF 字节不匹配。
3. **未区分三类哈希。** 清单仅登记单一哈希，未说明它对应的是"提交树原始字节"、
   "工作区原始字节"还是"运行时规范化字节"。

## 三类哈希的实测值（2026-09-09 只读核验）

| 类别 | 字节来源 | SHA-256 |
| --- | --- | --- |
| 提交树原始字节 | `git show bad6fd3:contracts/...schema.json`（LF） | `0e39c7acd96b22fc91c3a6a228561d3db9b6368db9f062c95f970b19ec164903` |
| 工作区原始字节 | SqlBot 冻结副本（CRLF） | `2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566` |
| 运行时规范化字节 | 加载器 CRLF 规范化后 | `2c5e4603cda243a84f5ec1c47ee65d3e377598fed86b1ddab5a167a2c342c566` |

- 提交树：LF，29870 字节；工作区：CRLF，31198 字节；两端 parsed JSON 相等。
- SqlBot 加载器（`handoff_intake_v3.py:63-64`）先双 replace 统一为 CRLF 再哈希，
  因此运行时哈希对 LF/CRLF 输入均稳定。

## 修复内容

修订 `docs/specs/fact-binding-request-3.0.0-source.json`：

- 保留全部历史观察字段（`observedAt`、`sourceHeadAtObservation`、
  `upstreamCommittedAtObservation: false`、`sha256`）。
- 新增 `verifiedCommitTree`：完整 40 位 commit `bad6fd349a6ecbff190b9bd0ac1bc34a48588325`、
  `repositoryHeadAtVerification`、`byteLength=29870`、`lineEnding="LF"`、
  `sha256=0e39c7ac…`、`jsonStructureIdenticalToPackagedSchema=true`。
- 新增 `runtime`：规范化算法说明、`normalizedLineEnding="CRLF"`、
  `sha256=2c5e4603…`、`matchesFactBindingSchemaSha256V3=true`。
- 新增 `sha256Note`：明确顶层 `sha256` 对应运行时规范化哈希，不冒充提交树原始字节哈希。

同步更新 `tests/contract/test_fact_binding_v3_contract.py` 的
`test_upstream_schema_source_record_is_frozen_and_machine_readable`，
断言历史字段不变、`verifiedCommitTree` 使用完整 commit、
提交树 SHA-256/字节长度正确、`runtime.sha256 == FACT_BINDING_SCHEMA_SHA256_V3`。

## 验证证据

- `uv run pytest tests/contract/test_fact_binding_v3_contract.py`：15 passed。
- 提交树原始字节经 `git show bad6fd3:<path>` 读取，未用工作区文件冒充。
- 未修改打包 Schema、`handoff_intake_v3.py`、`FACT_BINDING_SCHEMA_SHA256_V3`。

## 仍开放

- M0 仍受业务契约 vNext、设计批准等其他前置约束；本 BUG 仅关闭来源登记问题。
- V3 上下文/快照维护者与批准记录载体仍待明确（DEV-20260906-04 开放问题 #1）。
