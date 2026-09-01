# DEV-20260828-03：私有本地参考资料包设计

- 状态：`completed`
- 创建日期：2026-08-28
- 更新日期：2026-09-01
- 实现需求：
  [REQ-20260828-03](../requirements/REQ-20260828-03-local-candidate-evidence-integration.md)
- 受决策约束：
  [BIZ-20260828-03](../decisions/BIZ-20260828-03-local-candidate-evidence-boundary.md)
- 前置设计：
  [DEV-20260827-03 第 8 节](DEV-20260827-03-project-context-metadata-resolution.md#8-本地参考工作簿的未来适配边界)

## 1. 设计结论

本任务实现独立、私有、离线且只供人工复核的资料仓库，不实现应用运行时 reader：

```text
指定本地资料（只读）
        |
        | 逐文件 SHA-256 + 密钥模式门禁
        v
private RuleDataReferences
  sources/  原始字节快照
  derived/  非权威候选证据
  schemas/  派生物契约
  manifest  bundle 身份与摘要
        |
        | 仅公开 commit/digest/聚合数量
        v
RuleReader / SqlBot 脱敏索引（无运行时依赖）
```

不存在从私有资料到 `GovernedMetadataSnapshot`、`ProjectBindingContextV2`、grant、Prompt、SQL 候选或
执行端口的代码依赖。

## 2. 私有仓库结构

```text
RuleDataReferences/
  sources/project-release-rules/   17 个原始文件，禁止文本规范化
  derived/sqlbot/                  工作簿派生候选证据
  schemas/                         严格 JSON Schema
  scripts/build_manifest.py        新 bundle 构建工具
  scripts/verify_bundle.py         完整性与安全边界校验
  manifest.json                    受管文件与 content digest
```

`.gitattributes` 对 `sources/**` 使用 `-text`，保证 SQL、Markdown 和 Office 文件在 Git index 与 checkout
中保持来源字节。对原始快照不修正尾随空格、编码、文件名或内容。

## 3. Manifest 与内容摘要

`manifest.json` 为每个受管文件记录：

- repository-relative path；
- SHA-256 与字节数；
- 源文件观测修改时间；
- `kind` 与 `internal-confidential` 分类；
- 固定 `authority=none`、`executable=false`。

`contentDigestSha256` 对按路径排序的
`repositoryPath + NUL + sha256 + NUL + sizeBytes + LF` 记录求 SHA-256。它固定整个 bundle 内容，不含
本机目录和 Git checkout 时间。

## 4. 派生证据

私有候选证据保留：

- 来源工作簿仓库相对路径、哈希、修改时间和选定区域；
- 字段、依赖、视图来源候选；
- 稳定 candidate ID 与单元格证据坐标；
- 固定 trust flags 与质量统计。

派生 JSON 不保留本机绝对路径。公开 SqlBot 不保存候选 JSON 或其 Schema，只保存私有 commit 与 bundle
摘要。

## 5. 校验策略

`scripts/verify_bundle.py` 使用 Python 3.11 标准库完成：

1. Manifest 路径 containment、唯一性和受管文件集合核对；
2. 文件大小、SHA-256 与 content digest 复算；
3. 派生证据固定 trust flags、仓库相对路径和统计一致性核对；
4. JSON 中本机绝对用户路径泄漏检查；
5. 文本与 Office XML 常见密钥模式扫描；只输出文件路径和规则类别，不输出匹配值。

JSON Schema 另由 SqlBot 现有 Python 3.11/jsonschema 环境离线验证。提交前还验证 staged source blob 的
SHA-256，证明 Git 行尾处理没有改变来源字节。

## 6. 安全不变量

- 私有仓库可见性固定为 private；
- `classification=untrustedCandidateEvidence`；
- `authority=none`；
- `executable=false`；
- `canCreateGrant=false`；
- `canCreateMetadataSnapshot=false`；
- `canClearBlockingUncertainty=false`；
- `canChangeCandidateApproval=false`。

SqlBot 应用包 `src/` 不导入、不 clone、不加载或自动发现私有仓库。API、provider、AST inspector、
repository 和数据库生命周期均不接触资料包。

## 7. 完成边界

本资料包只改善异地开发的可达性和来源追溯。它不完成 metadata review，不改变 Phase 2G/4 结果，也
不推进 Phase 5。后续如需比较候选与批准上下文/快照，必须另建需求和显式 reader/DTO，不得直接把
私有 JSON 作为授权输入。
