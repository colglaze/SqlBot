# 文档总索引

`docs/` 是项目业务意图、技术方案、数据契约、决策和进度的事实来源。当前实现以事实级 Agent 2 基线为准。

## 阅读顺序

1. [当前进度](progress/PROG-20260820.md)
2. [规则分析需求](requirements/REQ-20260820-01-rule-change-analysis.md)
3. [规则分析边界决策](decisions/BIZ-20260820-01-rule-analysis-boundary.md)
4. [规则分析技术设计](architecture/DEV-20260820-01-rule-change-analysis.md)
5. [路线图](ROADMAP.md)

## 目录职责

| 目录 | 前缀/内容 | 作用 |
| --- | --- | --- |
| `requirements/` | `REQ-*` | 业务范围、功能需求和验收标准 |
| `architecture/` | `DEV-*` | 架构、模块边界、数据流和实施设计 |
| `decisions/` | `BIZ-*` | 已确认、暂定或被替代的业务决策 |
| `bugs/` | `BUG-*` | 缺陷、影响、修复和回归证据 |
| `specs/` | 契约/Schema | 机器可校验的数据契约 |
| `workflows/` | 流程说明 | 生成、验证、审核、发布流程 |
| `operations/` | 运维说明 | 性能、安全、超时和故障处理 |
| `progress/` | `PROG-*` | 阶段状态、验证证据、阻塞和下一任务 |
| `templates/` | 文档模板 | 新增 REQ 和 PROG 时复用 |

## 当前权威文档

### 产品与决策

- [REQ-20260820-01：规则规范化、内容哈希与结构化差异](requirements/REQ-20260820-01-rule-change-analysis.md)
- [BIZ-20260820-01：规则分析能力边界与显式阻塞](decisions/BIZ-20260820-01-rule-analysis-boundary.md)
- [REQ-20260819-01：Agent 2 事实绑定输入与候选模板契约](requirements/REQ-20260819-01-fact-binding-intake.md)
- [BIZ-20260819-01：Agent 2 职责与双服务边界](decisions/BIZ-20260819-01-agent2-role-alignment.md)

### 技术与交付

- [DEV-20260820-01：规则规范化、哈希与差异领域设计](architecture/DEV-20260820-01-rule-change-analysis.md)
- [DEV-20260819-01：事实绑定输入与 SQL 候选契约](architecture/DEV-20260819-01-fact-binding-contract.md)
- [DEV-20260818-02：Phase 1 后端骨架](architecture/DEV-20260818-02-backend-skeleton.md)
- [规则 JSON Schema 1.0（仅用于确定性规则分析）](specs/release-rule.schema.json)
- [路线图](ROADMAP.md)
- [PROG-20260820：规则规范化、哈希与结构化差异](progress/PROG-20260820.md)
- [PROG-20260819：事实绑定契约切片](progress/PROG-20260819.md)

## 历史文档

以下文档保留用于审计，不再指导 Agent 2 新实现：

- [REQ-20260818-01：整规则释放 SQL 生成器](requirements/REQ-20260818-01-release-rule-sql-generator.md)
- [BIZ-20260818-01：旧规则与 SQL 生命周期](decisions/BIZ-20260818-01-rule-and-sql-lifecycle.md)
- [DEV-20260818-01：旧总体技术设计](architecture/DEV-20260818-01-system-architecture.md)
- [旧规则语义说明](specs/rule-contract.md)；其中 JSON Schema 1.0 仅被新规则分析需求复用，不恢复旧 SQL 生成语义
- [旧 MongoDB 数据模型](specs/mongodb-data-model.md)
- [旧 SQL 生成与审核流程](workflows/sql-generation-and-review.md)
- [旧临时表策略](operations/query-performance.md)
- [PROG-20260818：Phase 0/1](progress/PROG-20260818.md)

## 文档治理规则

- 新需求：`requirements/REQ-YYYYMMDD-NN-short-name.md`。
- 复杂技术方案：`architecture/DEV-YYYYMMDD-NN-short-name.md`。
- 业务决策：`decisions/BIZ-YYYYMMDD-NN-short-name.md`。
- 可复现缺陷：`bugs/BUG-YYYYMMDD-NN-short-name.md`，并关联受影响 REQ。
- 每个有实质进展的工作日新增或更新 `progress/PROG-YYYYMMDD.md`。
- PROG 必须引用相关 REQ/DEV/BIZ；BIZ 和 DEV 必须说明影响的 REQ。
- 状态使用 `draft`、`proposed`、`approved`、`in_progress`、`completed`、`superseded`。
- 被替代文档保留并链接替代者，不删除历史。
- 契约有破坏性变化时提升版本并记录迁移策略。
- 文档不得包含生产密钥、真实连接串或未经脱敏的数据。
