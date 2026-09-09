# 缺陷记录

## 已登记缺陷

- [BUG-20260908-02：V3 上游已归档交付与主来源业务语义存在可复现差距](BUG-20260908-02-v3-upstream-fact-catalog-contract-gaps.md)——上游已归档交付（19 事实/18 请求/5 stages）与主来源业务语义的差距：报告标志为独立 string、R3 仅检查标志缺少复合条件、R1 计数过滤不完整、merge_group 物理来源未解析、R4 日期口径待裁决。
- [BUG-20260908-01：V3 下游契约来源登记与上游提交树不一致（行尾 / 提交状态）](BUG-20260908-01-v3-downstream-contract-source-hash-normalization.md)——上游 V3 契约已提交于 RuleReader `bad6fd3`（"未提交"阻塞解除），但提交树为 LF 字节（`0e39c7ac…`），SqlBot 冻结副本为 CRLF（`2c5e4603…`），JSON 结构相同；来源清单需三类登记。
- [BUG-20260901-01：V2 在线候选 coverage 声明不稳定](BUG-20260901-01-v2-live-provider-coverage-declaration.md)——已修复；原始文件未进入公开历史，现版本为 2026-09-05 依据同期记录重建，见文档内"文档完整性说明"。

发现可复现缺陷后，复制 [`BUG-template.md`](../templates/BUG-template.md)，命名为 `BUG-YYYYMMDD-NN-short-name.md` 并放在本目录。每个 BUG 必须关联引入或受影响的 REQ，并记录：

- 复现步骤和最小输入；
- 期望与实际行为；
- 影响的规则/SQL/方言/版本；
- 严重度、临时规避和安全影响；
- 根因、修复方案和回归证据。
