# BUG-20260901-01：V2 在线候选 coverage 声明不稳定

- 状态：`completed`（已修复并有回归证据）
- 严重度：中（阻断候选生成可用性，无安全影响）
- 发现日期：2026-09-01
- 关联 REQ：[REQ-20260828-01](../requirements/REQ-20260828-01-v2-candidate-generation-input.md)、[REQ-20260826-04](../requirements/REQ-20260826-04-deepseek-sql-candidate-generation.md)
- 影响版本：Prompt `sqlserver-fact-candidate-v2.0`（修复于 `v2.1`，见
  `src/release_sql_bot/application/prompts_v2.py`）；应用 `0.3.0` 前后

## 文档完整性说明（2026-09-05 补记）

本文档原名与编号在 2026-09-01 的在线预览会话中创建，但原始文件从未进入公开 `main`：
同日的历史脱敏改写（force-push）后被保留的文件树不包含它，被清除的旧提交
`63432ad` / `de0139d` 的文件树与本地 Git 对象、Codex 快照中也均无此文件，
`scripts/preview_synthetic_v2.py` 同样缺失（该脚本已于 2026-09-05 按当前 0.3.0 契约重新实现，
非原件恢复）。当前版本是依据当日同期记录
[PROG-20260901](../progress/PROG-20260901.md) 与现有代码事实重建的登记，未虚构任何
复现输入或在线验证证据；涉及在线调用的描述以 PROG-20260901 的原始记录为准。

## 摘要

2026-09-01 用户显式授权的合成脱敏在线预览中，连续两次生成响应均被严格输出门禁拒绝：
候选的 `declaredUsageCoverage` 未与 stable `conditionId` 集合精确一致。Prompt 升级到
`sqlserver-fact-candidate-v2.1`，新增由同一权威输入确定性投影的 `exactOutputDeclarations`，
要求模型逐项复制参数、结果、`declaredObjects` 和 `declaredUsageCoverage`；应用侧交叉校验
未放宽，修复后单次在线响应即形成合法候选。

## 最小复现步骤

原始复现依赖在线 provider 会话，且原始预览脚本未保留，无法给出可运行复现步骤。同期记录的
出现条件为：使用 `sqlserver-fact-candidate-v2.0` Prompt 对同一合成 V2 请求连续发起在线生成，
模型的 `declaredUsageCoverage` 在多次响应间不稳定（不能保证与 stable `conditionId` 集合
精确一致），被
`src/release_sql_bot/application/sql_validation.py` 的输出门禁拒绝。

## 期望行为

对同一权威输入，候选的 `declaredUsageCoverage` 必须与请求 `usages` 引用的 stable
`conditionId` 集合精确一致；不一致的候选应被拒绝且不产生副作用。

## 实际行为

`v2.0` Prompt 下两次在线响应的 coverage 声明与 stable `conditionId` 集合不一致，候选被
严格输出门禁整体拒绝，生成不可用。

## 影响与安全风险

- 可用性影响：在线生成在被修复前无法稳定产出可通过门禁的候选；
- 安全影响：无。拒绝行为发生在应用侧交叉校验，门禁按设计工作，未产生可执行 SQL，未清除
  blocking uncertainty，也未改变审批状态。

## 临时规避

无需规避。重试不能稳定修复（声明不稳定是模型行为），正确路径是 Prompt 版本升级。

## 根因

`v2.0` Prompt 只描述了输出契约，未把由权威输入唯一确定的输出逐项固定给模型；当模型对
coverage 集合的复制出现遗漏、改名或顺序变化时，应用交叉校验必然拒绝。

## 修复方案

Prompt 升级为 `sqlserver-fact-candidate-v2.1`（`SQLSERVER_CANDIDATE_PROMPT_VERSION_V2`）：
从同一权威输入确定性派生 `exactOutputDeclarations`（`declaredUsageCoverage` 取
`request.usages` 的 `conditionId` 排序集合），要求模型逐项复制、不得增删改名；候选域模型
同时只接受 `v2` 与 `v2.1` 版本。应用侧交叉校验保持不变（例如
`src/release_sql_bot/application/candidates_v2.py` 中
`candidate declaredUsageCoverage does not match V2 condition usages`），精确输出声明不构成
授权或 AST coverage 证据。

## 回归证据

- 当日同期记录（[PROG-20260901](../progress/PROG-20260901.md)）：修复后单次在线响应形成
  `candidate / executable=false / reviewStatus=pending` 候选，其合成 SQL 雏形通过 Phase 4
  SQLGlot AST 静态门禁，报告 `passed / executable=false`；全部在线调用累计 4 次，始终
  `databaseAccess=false / sqlExecuted=false`；
- 离线回归：`uv run pytest` 230 passed；`uv run ruff check .` 与
  `uv run ruff format --check .` 通过（2026-09-05 复核）。
