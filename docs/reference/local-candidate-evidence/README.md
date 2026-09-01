# 私有字段映射与来源资料索引

原始字段映射、视图定义、规则原文、Word 设计文档和完整候选证据已收拢到私有仓库
[colglaze/RuleDataReferences](https://github.com/colglaze/RuleDataReferences)。本公开目录只保留脱敏的
固定身份和使用边界，不包含内部对象名、字段名、SQL、本机绝对路径或原始 Office 文件。

## 固定版本

- Bundle：`PROJECT_RELEASE_REFERENCE_20260901_001`
- 私有 commit：`2240e5bd18e36d17650896a10cc61e1c18e3daa0`
- Content digest：`6d403f1a110ea1699a72aec76f38be944583670f96767f5e3f44b2ac2e565160`
- 来源文件：17 个，共 433,997 字节
- 字段映射工作簿 SHA-256：
  `872706f07792a618888a13d8944d7c59f454e5554388fa2dece511abebb30242`
- 聚合派生结果：197 条字段候选、93 条依赖候选、7 条来源候选

有私有仓库权限的开发者可执行：

```powershell
git clone https://github.com/colglaze/RuleDataReferences.git
cd RuleDataReferences
git checkout 2240e5bd18e36d17650896a10cc61e1c18e3daa0
py -3.11 scripts/verify_bundle.py
```

## 信任边界

- 私有资料固定 `authority=none`、`executable=false`；
- 工作簿含公式错误、通配符和缺失定义，不能自动补全；
- 资料不得进入 Prompt、运行时授权解析、候选生成或 SQL 静态门禁；
- 任何候选都必须独立命中已批准元数据快照和项目上下文精确 grant；
- 私有仓库不得改为 public，公开项目也不得复制其原始内容；
- 本资料包不授权连接、prepare、explain 或执行 SQL，也不能清除 blocking uncertainty。

需求、决策和设计见：

- [REQ-20260828-03](../../requirements/REQ-20260828-03-local-candidate-evidence-integration.md)
- [BIZ-20260828-03](../../decisions/BIZ-20260828-03-local-candidate-evidence-boundary.md)
- [DEV-20260828-03](../../architecture/DEV-20260828-03-local-candidate-evidence-package.md)
