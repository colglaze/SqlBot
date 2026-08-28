# BUG-20260826-01：最新规则查询与真实 RuleReader 存储契约不兼容

- 状态：`completed`
- 发现日期：2026-08-26
- 影响需求：[REQ-20260826-02](../requirements/REQ-20260826-02-mongodb-latest-rule-read.md)
- 修复需求：[REQ-20260826-03](../requirements/REQ-20260826-03-rulereader-latest-rule-integration.md)

## 现象

Phase 2D 离线实现按旧 `ReleaseRule 1.0` 假设查询
`project_id + target`，并按数值 `version` 降序排序。恢复本地 MongoDB 后只读探测发现，真实
`rule_versions` 集合没有这些字段，现有接口会查不到规则。

## 真实证据

- 集合共有 2 条文档，外层字段包含 `rule_id`、`rule_version`、`schema_version`、
  `generated_at`、`stored_at`、`source_sha256`、`parser_version`、`status`、`executable` 和
  `document`；
- `document` 是 camelCase RuleReader 导出载荷，当前同时存在 Schema `1.0.0` 和 `2.0.0`；
- 现有最新查询索引为 `{ rule_id: 1, generated_at: -1 }`；
- `rule_version` 有唯一索引，当前没有 `project_id + target + version` 索引；
- 当前连接账号角色为 `root@admin`，不符合 SqlBot 最小权限只读要求。

探测只记录字段名、类型、计数和索引，没有输出规则正文、连接 URI 或凭据。

## 根因

MongoDB 不可用时，Phase 2D 根据仓库历史 Schema `1.0` 建立了明确但尚未验证的存储假设。真实
RuleReader 已采用独立的版本包装契约，业务规则正文位于 `document.rule`。

## 修复范围

- 以 `rule_id` 为查询范围，以 `generated_at DESC, _id DESC` 选择最新版本；
- 建立真实外层包装和关键内层引用的一致性校验；
- 接口查询参数改为 `ruleId`，返回 RuleReader 版本包装而非旧 `ReleaseRule`；
- 为 SqlBot 建立并使用数据库侧只读账号；
- 保留旧文档作为审计记录，不增加兼容性猜测或静默回退。

## 回归标准

见 [REQ-20260826-03](../requirements/REQ-20260826-03-rulereader-latest-rule-integration.md) 验收标准。

## 修复与验证

- 查询已改为 `rule_id` 精确过滤和 `generated_at DESC, _id DESC` 排序，并只返回契约字段；
- Schema `1.0.0` 和 `2.0.0` 的真实存量文档均通过严格版本包装校验；
- 应用已改用仅具 `read@rule_reader` 角色的本地服务账号；
- 真实 `/ready`、连续最新规则读取、404 和响应去除 `_id` 均已验证；
- 全量静态检查和测试证据记录在 [PROG-20260826](../progress/PROG-20260826.md)。
