# BIZ-20260828-03：本地候选证据与 Git 可见性边界

- 状态：`approved`
- 创建日期：2026-08-28
- 更新日期：2026-09-01
- 来源：用户显式要求整合项目外字段映射和来源资料，以支持异地开发
- 影响需求：
  [REQ-20260828-03](../requirements/REQ-20260828-03-local-candidate-evidence-integration.md)
- 前置决策：
  [BIZ-20260827-02](BIZ-20260827-02-project-metadata-authorization-boundary.md)

## 决策

1. 用户指令授权只读收拢指定本地资料并上传到私有 GitHub 仓库；不授权 SQL Server、SQL 执行、
   在线模型、元数据批准或候选审批。
2. 原始工作簿、视图 SQL、Word、规则原文和完整字段候选只进入独立私有仓库
   `colglaze/RuleDataReferences`。
3. RuleReader 与 SqlBot 公开仓库只登记 bundle ID、私有 commit、内容摘要、来源工作簿哈希、聚合
   数量和使用边界；不登记内部对象名、字段名、SQL、本机绝对路径或未筛选备注。
4. 所有字段、对象、依赖、来源和 SQL 固定为不可信参考：`authority=none`、`executable=false`，不能
   创建或扩大 snapshot、relation/column/join grant 或白名单。
5. 工作簿公式、后台查询 SQL、操作建议、说明、业务值、通配符和错误结果不作为指令，不执行、不
   补查、不推断。
6. 私有仓库 `sources/` 保存原始字节，使用 `.gitattributes` 禁止行尾规范化；来源变化必须新建 bundle，
   不原地覆盖历史快照。
7. 完整性脚本可读取字节计算哈希和密钥模式，但只报告文件路径与规则类别，绝不输出匹配值。
8. 后续采用候选时必须由独立 metadata review 复核，并同时命中批准快照和批准上下文精确 grant。
9. Phase 5 继续为 `待规划`。资料整合不授权连接、prepare、explain 或执行任何候选 SQL。

## 可见性边界

| 内容 | 私有 RuleDataReferences | 公开 RuleReader / SqlBot |
| --- | --- | --- |
| 原始工作簿、Word、规则原文 | 允许 | 禁止 |
| 完整视图 SQL | 允许，非执行参考 | 禁止 |
| 对象/字段/依赖候选 | 允许，`authority=none` | 禁止 |
| JSON Schema、证据坐标 | 允许 | 禁止 |
| Bundle ID、commit、digest | 允许 | 允许 |
| 聚合数量、固定信任声明 | 允许 | 允许 |
| 密钥、连接串、数据库导出 | 禁止 | 禁止 |

## 变更规则

- 私有仓库不得改为 public；若误公开，按可能泄漏处理。
- 来源任何字节变化都产生新 SHA-256；更新必须新建 bundle 并重新审阅差异。
- 公开索引更新必须固定到已推送的私有 commit，不能引用未提交工作区。
- 删除当前公开分支中的内部资料优先使用正常后续提交；本次用户已单独明确授权清除误推送历史，
  因而允许仅对 SqlBot `main` 使用带精确 lease 的 force-push。其他分支和仓库不在授权范围内。
- 运行时不得自动 clone、读取或发现私有资料仓库。
