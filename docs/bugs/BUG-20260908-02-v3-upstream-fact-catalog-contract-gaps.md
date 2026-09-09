# BUG-20260908-02：V3 上游已归档交付与主来源业务语义存在可复现差距

- 状态：`proposed`
- 严重度：中（影响上游规则表达完整性与 M3 输入可生成性；
  属上游已归档 JSON 与主来源 Markdown 的语义差距，非运行时放行）
- 发现日期：2026-09-08
- 关联 REQ：[REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
- 关联进度：[PROG-20260908](../progress/PROG-20260908.md) §三
- 责任仓库：RuleReader（目录扩展 / 候选 vNext 版本）

## 现象

RuleReader 已归档交付 `generated-rules/report-release-v3-confirmed/`
（`business-confirmed-fact-catalog-3.0.0.json` 19 个事实、
`fact-binding-requests-3.0.0.json` 18 条请求、
`rule-structure-candidate-3.0.0.json` 5 个 stages / 19 条 active 规则）
与主来源（私有来源固定 bundle、commit、content digest、文件 SHA-256
与章节坐标见 [本地脱敏资料索引](../reference/local-candidate-evidence/README.md)）的
业务语义之间存在可复现差距。
已观察到 confirmed 候选使用单事实比较表示若干复合业务条件；
形成该交付形状的原因尚未单独核实。

## 事实数量（真实值，2026-09-08 只读核验）

| 对象 | 实际路径 | 当前值 |
| --- | --- | --- |
| 事实目录 | `generated-rules/.../business-confirmed-fact-catalog-3.0.0.json` | 19 个 facts |
| 请求列表 | `generated-rules/.../fact-binding-requests-3.0.0.json` | 18 条请求 |
| 规则候选 | `generated-rules/.../rule-structure-candidate-3.0.0.json` | 5 stages / 19 条 active rules |
| 恢复清单 | `generated-rules/.../confirmed-recovery-manifest.json` | ruleCount=19, requestCount=18, activeRuleCount=19, testCaseCount=20 |

## 六项关键事实的真实基线（来自 confirmed handoff JSON）

| factCode | factKind | dataType | nullable | nullPolicy | aggregation.mode |
| --- | --- | --- | --- | --- | --- |
| `task.project_report_flag` | source | string | true | indeterminate | none |
| `task.qc_report_flag` | source | string | true | indeterminate | none |
| `order.amount` | source | money | true | indeterminate | none |
| `release.special_application_count` | aggregate | integer | true | indeterminate | compute (function=count) |
| `report.merge_group_eligible` | source | boolean | false | fail | none |
| `task.in_oa_process` | exists | boolean | false | fail | exists |

## 已证实的差距

### A. 两个报告标志是独立 string 事实（非 boolean，非"合并"）

- **当前 JSON**：`task.project_report_flag` 与 `task.qc_report_flag` 是两个独立的
  `source / string` 事实，allowedValues=["0","1"]，nullable=true，nullPolicy=indeterminate。
- **主来源依据**：`项目报告和原始数据释放优化方案.md` §1.3 前提3（私有来源固定 bundle、
  commit、SHA-256 与章节坐标见 `docs/reference/local-candidate-evidence/README.md`）。
- **差距**：confirmed handoff 把两个标志拆成独立 string 事实，
  但未在候选条件表达式中显式编码主来源要求的组合语义。
  "待定"条件对应任一标志为空的情形——当一个为 "0"（有）另一个为 null 时，
  按主来源应判定为"有报告通过"。
  **未运行求值器，当前仅能判定为静态表达式结构差异。**
- **预期**：候选条件表达式应显式编码主来源要求的组合语义。
- **最小反例（字符串）**：`flagA="0", flagB=null` → 主来源=有报告通过；
  `flagA="1", flagB="1"` → 主来源=无报告。
- **修复责任仓库**：RuleReader（候选 vNext 版本）。
- **需新版本**：是。
- **验收**：覆盖 ("0",null)、(null,"0")、("1","1")、(null,null) 四组。

### B. 空值策略以 JSON 为准（order.amount 与特殊申请计数不是"空值→0"）

- **当前 JSON**：`order.amount`（nullable=true, nullPolicy=indeterminate）、
  `release.special_application_count`（nullable=true, nullPolicy=indeterminate）。
- **预期**：order.amount 与特殊申请计数的空值策略保持 indeterminate；
  如未来需要"空值→0"语义，单列"建议"，不写成当前事实。
- **修复责任仓库**：—（文档修正）。
- **验收**：文档与 JSON nullPolicy 一致。

### C. report.merge_group_eligible 当前仅能确认为 source boolean；物理来源未解析

- **当前 JSON**：`report.merge_group_eligible` = source / boolean / nullable=false /
  fail / aggregation=none；其 mappingCandidate 仍为 unresolved。
- **主来源依据**：§1.3 合并报告约束（私有来源坐标见参考资料索引）。
- **差距**：当前仅能确认它被声明为 source boolean；
  mappingCandidate 仍为 unresolved，物理来源及产生方式尚未确定。
  其逻辑描述包含组合业务语义，但不能把描述当作实现证据。
- **预期**：明确其物理来源与产生方式。
- **修复责任仓库**：RuleReader。
- **需新版本**：是。
- **验收**：mappingCandidate 解析完成。

### D. R1 当前 count>0 本身不是缺陷；缺口是查询要求未完整表达计数范围

- **当前 JSON**：`release.special_application_count` 的 queryRequirements
  aggregation.mode=compute、function=count；filters.items 仅含任务键过滤——
  **当前契约没有计数专用过滤字段**，因而没有结构化表达申请类型和审批状态等计数范围。
- **主来源依据**：§1.3 R1 "特殊申请已审批（计数>0）"（私有来源坐标见参考资料索引）。
- **当前行为**：count>0 本身不是缺陷；缺陷是查询要求的过滤条件不完整，
  Agent 2 无法独立判定"哪些申请应被计入"。
- **预期**：查询要求应包含申请类型、审批状态/节点类型等必要过滤；
  OA 执行层事项（触发、调度、回调写入）不属于候选查询要求范围。
- **修复责任仓库**：RuleReader（查询要求 vNext）。
- **需新版本**：是。
- **验收**：区分 (a) 类型匹配且已审批、(b) 类型匹配但未审批、
  (c) 类型不匹配、(d) 无匹配记录四组。

### E. R3 当前条件只检查特殊产品标志，缺少主来源要求的复合条件

- **当前代码/JSON**：`scripts/report_release_v3_confirmed_profile.py` stage2
  `R3_SPECIAL_PRODUCT` = `_compare("r3-special-product", "product.special_product_flag", "eq", True)`——
  R3 是单事实布尔等值比较。
- **主来源依据**：§1.3 R3、§5.2（私有来源固定 bundle、commit、SHA-256 与章节坐标见
  `docs/reference/local-candidate-evidence/README.md`）。
- **差距**：confirmed 候选的 R3 条件表达式只检查特殊产品标志，
  **缺少主来源要求的复合金额条件**。
  最小复现（脱敏）：特殊产品标志为真，但主来源要求的复合金额条件为假；
  当前候选仍会命中 READY。
  具体数值测试留给后续经授权的 RuleReader 修复任务，不写入公开 SqlBot 文档。
- **预期**：R3 条件表达式应包含主来源要求的复合条件，或暴露可查询事实。
- **修复责任仓库**：RuleReader（候选 vNext）。
- **需新版本**：是。
- **验收**：覆盖"标志为真但复合条件为假"应不命中 READY。

### F. R4 "有数据/已释放" 与日期口径（局部待决）

- **当前 JSON**：confirmed 候选 stage2 `R4_RAW_DATA_RELEASED`
  引用 `release.raw_data_released_after_cutoff`（boolean）。
- **主来源依据**：§1.3 R4（私有来源坐标见参考资料索引），
  同时存在"有数据"与"已释放"两种表述，日期口径存在歧义。
- **预期**：固定私有资料中的事实已由用户确认，不再要求用户重复提供或确认。由
  `businessRuleReview` 按指定主来源与现有证据完成版本化语义裁决；裁决进入新规则版本与
  handoff，旧载荷不覆盖。裁决完成前只阻断 R4，**不扩大为全 V3 阻塞**。
- **修复责任仓库**：RuleReader / `businessRuleReview`（基于既有证据完成工程对齐）。

### G. 已归档交付覆盖完整的报告侧 eligibility 顺序

- **当前 JSON**：confirmed 交付 ruleSetId=`REPORT_RELEASE_ALL_001`，
  5 stages / 19 条 active rules，覆盖完整报告侧顺序
  R0→R1→R9→R4→R2→R3→R8→R5→R6→R7
  （stage0=1 前提终止, stage1=6 前提, stage2=10 报告规则, stage3=1 合并组, stage4=1 OA 范围）。
- **主来源依据**：§1.3 明确报告侧完整顺序（私有来源坐标见参考资料索引）。
- **原始数据侧**（D0-D4）尚无独立交付，属工程交付范围。
- **修复责任仓库**：RuleReader。

## 修复方向（本轮仅登记，不改 Schema）

- 扩展 V3 fact catalog：新增金额快照、完工时间、合同盖章、合并组成员、业务枚举等基础事实。
- 已持久化载荷的修复必须生成新版本（catalog 新 digest + 新 handoff 版本），不覆盖旧版本。
- 固定私有资料中的事实已由用户确认，不再要求用户重复提供或确认；待办是版本化证据裁决与上游
  新版本交付。R4 保留局部待决，但现有资料已经足够，不再向用户索取事实；由
  `businessRuleReview` 按指定主来源与现有证据完成版本化语义裁决，裁决完成前只阻断 R4，
  **不扩大为全 V3 阻塞**。
- 责任边界：Agent 1 / businessRuleReview 负责业务规则、事实、筛选、聚合、时间语义；
  metadataReview 负责物理绑定、实体键、join 授权及 context/snapshot 的产生与批准；
  SqlBot 负责 V3 契约代码、确定性校验和解析。
- Phase 4 只负责 AST 安全、引用完整性和 usage traceability，不证明规则业务语义。

## 验收

- 新 catalog 版本 + 新 digest + 新 handoff 版本离线生成；
- 合成用例覆盖：报告标志四组、R3 标志为真但复合条件为假、R1 四组、R4 待裁决；
- 不修改 SqlBot 应用代码。
