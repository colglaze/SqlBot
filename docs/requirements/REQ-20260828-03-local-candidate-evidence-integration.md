# REQ-20260828-03：本地字段映射与来源资料私有整合

- 状态：`completed`
- 创建日期：2026-08-28
- 完成日期：2026-09-01
- 来源：用户要求把项目外必要本地文档，尤其数据库字段映射和来源，整合到 Git 以支持异地开发
- 受业务决策约束：
  [BIZ-20260828-03](../decisions/BIZ-20260828-03-local-candidate-evidence-boundary.md)
- 技术方案：
  [DEV-20260828-03](../architecture/DEV-20260828-03-local-candidate-evidence-package.md)
- 前置边界：
  [REQ-20260827-03 第 9 节](REQ-20260827-03-project-context-metadata-resolution.md#9-本地参考工作簿边界)

## 1. 背景

Phase 2G、独立 V2 候选生成输入和 Phase 4 已完成。本地参考资料包含字段映射、视图定义、规则原文
和设计文档，公开提交会暴露内部对象结构；只留在个人电脑又无法支持异地开发和版本追溯。

因此本任务采用独立私有仓库保存原始字节快照和派生候选证据；SqlBot 与 RuleReader 的公开仓库只
登记不含对象名、字段名、SQL 和本机绝对路径的 bundle 身份。

## 2. 目标

- 只盘点用户指定目录中的 17 个文件，不扩展扫描其他个人目录；
- 复制原始文件时保持逐文件 SHA-256，不修改工作簿、SQL、Word 或规则原文；
- 在私有仓库保存来源相对路径、哈希、字节数、来源修改时间和内容分类；
- 私有保存工作簿派生的字段、依赖和视图来源候选及严格 JSON Schema；
- 提供 Python 3.11 可重复运行的完整性、边界和密钥模式校验；
- 公开仓库只保存 bundle ID、私有 commit、内容摘要、聚合数量和固定非权威声明；
- 保持 Phase 5 为 `待规划`，不连接或验证 SQL Server。

## 3. 范围内

- 建立私有 `colglaze/RuleDataReferences` 仓库；
- 保存 17 个原始文件的不可变字节快照；
- 私有保存 197 条字段候选、93 条依赖候选、7 条来源候选和其证据坐标；
- 记录 `manifest.json`、bundle 内容摘要和来源工作簿哈希；
- 阻止本机绝对路径进入派生数据；
- 对源文件执行只返回文件路径和规则类别、不返回匹配值的密钥模式扫描；
- 同步 README、文档索引、ROADMAP、PROG 和 RuleReader 的脱敏来源登记。

## 4. 范围外

- 在公开仓库提交原始 Office 文件、完整 SQL、规则原文、对象名或字段名列表；
- 执行来源 SQL、工作簿公式或“后台查询 SQL”；
- 连接 SQL Server、MongoDB 或在线模型；
- 把私有资料转换成 `GovernedMetadataSnapshot`、`ProjectBindingContextV2` 或任意 grant；
- 清除、降级或改写 blocking uncertainty；
- 创建、批准、驳回、发布或改变任何 SQL 候选状态；
- 推断缺失定义、通配符列、字段类型、join 或业务语义。

## 5. 信任与使用规则

1. 私有资料固定 `authority=none`、`executable=false`。
2. 字段或依赖候选不得进入 Prompt、运行时授权解析、候选生成或 SQL 静态门禁输入。
3. 后续采用候选时必须独立命中批准的元数据快照和项目上下文精确 grant。
4. 文件存在、哈希稳定、名称唯一或来源完整都不是批准证据。
5. 来源变化必须建立新的受控 bundle；禁止静默覆盖历史快照。
6. 私有仓库可见性是安全边界，不得改为 public。

## 6. 交付物

- 私有仓库 `colglaze/RuleDataReferences`；
- Bundle `PROJECT_RELEASE_REFERENCE_20260901_001`；
- 私有 `sources/`、`derived/`、`schemas/`、`manifest.json` 与校验脚本；
- 本 REQ 及对应 BIZ/DEV；
- [公开脱敏索引](../reference/local-candidate-evidence/README.md)；
- README、文档索引、ROADMAP、PROG 与 RuleReader 进度更新。

## 7. 验收标准

1. 私有仓库包含 17 个源文件且 staged/checkout 字节哈希与来源一致。
2. 工作簿派生证据通过严格 JSON Schema，统计与数组长度一致。
3. Manifest 不含本机绝对路径，全部受管文件大小、SHA-256 和 bundle 摘要可复算。
4. 密钥模式扫描不输出匹配值，且提交不包含凭据、连接串或数据库导出。
5. 公开仓库当前 `main` 不包含原始资料、完整候选证据、对象名/字段名清单或 SQL；若历史提交曾
   暴露派生候选，必须记录并在获得单独授权后处理历史改写。
6. 私有资料不进入应用 `src/`，不创建 snapshot/grant，不改变 uncertainty 或候选审批状态。
7. 两个公开项目只引用固定私有 commit 和 bundle digest。
8. Ruff、format、pytest 与 `git diff --check` 通过。

## 8. 完成证据

- 私有 commit：`2240e5bd18e36d17650896a10cc61e1c18e3daa0`；
- Bundle digest：`6d403f1a110ea1699a72aec76f38be944583670f96767f5e3f44b2ac2e565160`；
- 来源文件：17 个、433,997 字节；
- 来源工作簿 SHA-256：
  `872706f07792a618888a13d8944d7c59f454e5554388fa2dece511abebb30242`；
- 规范化结果：197 条字段候选、93 条依赖候选、7 条来源候选；
- 完整验证证据记录在
  [PROG-20260901](../progress/PROG-20260901.md)。
