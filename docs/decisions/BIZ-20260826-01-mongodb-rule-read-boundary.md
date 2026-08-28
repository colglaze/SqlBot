# BIZ-20260826-01：MongoDB 最新规则只读边界

- 状态：`superseded`
- 替代决策：[BIZ-20260826-02](BIZ-20260826-02-rulereader-latest-version-selection.md)
- 创建日期：2026-08-26
- 来源：用户要求借鉴 DataEase SQLBot 的分层思路，并先完成 MongoDB 最新规则读取
- 影响需求：[REQ-20260826-02](../requirements/REQ-20260826-02-mongodb-latest-rule-read.md)
- 调整决策：[BIZ-20260819-01](BIZ-20260819-01-agent2-role-alignment.md) 第 8 条的读取边界

## 决策

1. SqlBot 可以通过独立只读适配器读取 RuleReader 拥有的规则版本集合，但仍然禁止创建、修改、
   删除或激活其中的任何文档。
2. 此读取能力只为规则审计、差异分析和后续事实级流程提供输入，不恢复“把整棵规则直接翻译为
   异常集合 SQL”的旧职责。
3. “最新规则”定义为同一 `project_id + target` 范围内数值型 `version` 最大的规则；不隐式过滤
   `draft`、`active` 或 `retired`，调用方必须根据返回的 `status` 决定后续动作。
4. 每次读取都直接查询 MongoDB，不使用进程内结果缓存。查询显式按 `version` 降序、`_id`
   降序排序，不能依赖集合自然顺序。
5. 首个切片只接受与现有 `ReleaseRule` / Schema `1.0` 一致的直接规则载荷。缺字段、字段类型错误、
   重复条件 ID 或其他契约错误必须失败，禁止把不完整规则补成可用规则。
6. 集合名通过配置提供，默认 `rule_versions`。SqlBot 不拥有该集合的 migration 或索引写权限；
   所需索引由 RuleReader/数据库所有者建立并审计。
7. MongoDB 未配置、连接不可用或规则载荷无效时，不返回旧缓存，也不降级为本地默认规则。

## 原因

用户要求每次取得最新规则，因此版本排序和无缓存行为必须成为可验证契约。保持只读、严格校验和
默认拒绝，既允许 SqlBot 使用上游规则事实，又不突破双 Agent 的集合所有权及 SQL 安全边界。
