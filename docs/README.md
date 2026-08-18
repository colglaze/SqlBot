# 文档总索引

`docs/` 是项目业务意图、技术方案、数据契约、决策和进度的事实来源。文档按职责分区，文件名前缀用于长期追溯。

## 阅读顺序

开始任何实现任务时，建议依次阅读：

1. [当前进度](progress/PROG-20260818.md)
2. 与任务对应的 [REQ](requirements/REQ-20260818-01-release-rule-sql-generator.md)
3. [业务决策](decisions/BIZ-20260818-01-rule-and-sql-lifecycle.md)
4. [总体技术设计](architecture/DEV-20260818-01-system-architecture.md)
5. 相关规格、工作流或运维文档

## 目录职责

| 目录 | 前缀/内容 | 作用 |
| --- | --- | --- |
| `requirements/` | `REQ-*` | 业务范围、用户故事、功能需求和验收标准 |
| `architecture/` | `DEV-*` | 架构、模块边界、数据流和实施设计 |
| `decisions/` | `BIZ-*` | 已确认、暂定或待确认的业务决策及理由 |
| `bugs/` | `BUG-*` | 可复现缺陷、影响范围、根因、修复与回归证据 |
| `specs/` | 契约/Schema | 规则 JSON、MongoDB 集合和机器可校验契约 |
| `workflows/` | 流程说明 | 生成、验证、审核、发布等跨模块流程 |
| `operations/` | 运维说明 | 性能、安全、超时、清理和故障处理 |
| `progress/` | `PROG-*` | 阶段状态、验证证据、阻塞和下一任务 |
| `templates/` | 文档模板 | 新增 REQ 和 PROG 时复用 |

## 当前文档

### 产品与决策

- [REQ-20260818-01：释放规则 SQL 生成器](requirements/REQ-20260818-01-release-rule-sql-generator.md)
- [BIZ-20260818-01：规则与 SQL 生命周期](decisions/BIZ-20260818-01-rule-and-sql-lifecycle.md)

### 技术与契约

- [DEV-20260818-01：总体技术设计](architecture/DEV-20260818-01-system-architecture.md)
- [规则 JSON 契约](specs/rule-contract.md)
- [机器可校验的规则 JSON Schema](specs/release-rule.schema.json)
- [MongoDB 数据模型](specs/mongodb-data-model.md)
- [SQL 生成与人工审核](workflows/sql-generation-and-review.md)
- [查询性能与临时表](operations/query-performance.md)

### 交付

- [路线图](ROADMAP.md)
- [PROG-20260818：Phase 0](progress/PROG-20260818.md)

## 文档治理规则

- 新需求：`requirements/REQ-YYYYMMDD-NN-short-name.md`。
- 复杂技术方案：`architecture/DEV-YYYYMMDD-NN-short-name.md`。
- 业务规则或流程决策：`decisions/BIZ-YYYYMMDD-NN-short-name.md`。
- 可复现缺陷：`bugs/BUG-YYYYMMDD-NN-short-name.md`，并关联引入或受影响的 REQ。
- 每个有实质进展的工作日：新增或更新 `progress/PROG-YYYYMMDD.md`。
- PROG 必须引用相关 REQ/DEV/BIZ；BIZ 和 DEV 必须说明影响的 REQ。
- 状态使用 `draft`、`proposed`、`approved`、`superseded`；被替代的文档保留并链接替代者，不删除历史。
- 规则或数据契约有破坏性变化时提升 `schema_version`，并记录迁移策略。
- 文档不得包含生产密钥、真实连接串或未经脱敏的数据。
