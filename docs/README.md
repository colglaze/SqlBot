# 文档总索引

`docs/` 是项目业务意图、技术方案、数据契约、决策和进度的事实来源。当前实现以事实级 Agent 2 基线为准。

## 阅读顺序

1. [当前进度](progress/PROG-20260828.md)
2. [SQL AST 与安全门禁](requirements/REQ-20260827-01-sql-ast-safety-gate.md)
3. [SQL AST 与安全门禁设计](architecture/DEV-20260827-01-sql-ast-safety-gate.md)
4. [RuleReader 不可变事实交接只读接入](requirements/REQ-20260828-02-rulereader-handoff-read-intake.md)
5. [不可变事实交接只读边界](decisions/BIZ-20260828-02-rulereader-handoff-read-boundary.md)
6. [不可变事实交接只读接入设计](architecture/DEV-20260828-02-rulereader-handoff-read-intake.md)
7. [V2 SQL 候选生成输入对齐](requirements/REQ-20260828-01-v2-candidate-generation-input.md)
8. [V2 候选生成权威与不可执行边界](decisions/BIZ-20260828-01-v2-candidate-authority-boundary.md)
9. [V2 SQL 候选生成输入对齐设计](architecture/DEV-20260828-01-v2-candidate-generation-input.md)
10. [Phase 2G 项目上下文与受治理元数据授权解析](requirements/REQ-20260827-03-project-context-metadata-resolution.md)
11. [项目上下文、元数据快照与物理授权边界](decisions/BIZ-20260827-02-project-metadata-authorization-boundary.md)
12. [Phase 2G 项目上下文与受治理元数据解析设计](architecture/DEV-20260827-03-project-context-metadata-resolution.md)
13. [RuleReader FactBindingRequest 2.0.0 接入与阻断分析](requirements/REQ-20260827-02-rulereader-fact-binding-v2-intake.md)
14. [V2 权威与授权边界](decisions/BIZ-20260827-01-fact-binding-v2-authority-boundary.md)
15. [V2 consumer 与阻断分析设计](architecture/DEV-20260827-02-fact-binding-v2-readiness.md)
16. [DeepSeek 单事实 SQL 候选生成](requirements/REQ-20260826-04-deepseek-sql-candidate-generation.md)
17. [DeepSeek 候选生成设计](architecture/DEV-20260826-04-deepseek-sql-candidate-generation.md)
18. [真实 RuleReader 最新规则版本接入](requirements/REQ-20260826-03-rulereader-latest-rule-integration.md)
19. [路线图](ROADMAP.md)

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

- [REQ-20260828-02：RuleReader 不可变事实交接只读接入](requirements/REQ-20260828-02-rulereader-handoff-read-intake.md)
- [BIZ-20260828-02：RuleReader 不可变事实交接只读边界](decisions/BIZ-20260828-02-rulereader-handoff-read-boundary.md)
- [REQ-20260828-01：V2 SQL 候选生成输入对齐](requirements/REQ-20260828-01-v2-candidate-generation-input.md)
- [BIZ-20260828-01：V2 候选生成权威与不可执行边界](decisions/BIZ-20260828-01-v2-candidate-authority-boundary.md)
- [REQ-20260827-03：Phase 2G 项目上下文与受治理元数据授权解析](requirements/REQ-20260827-03-project-context-metadata-resolution.md)
- [BIZ-20260827-02：项目上下文、元数据快照与物理授权边界](decisions/BIZ-20260827-02-project-metadata-authorization-boundary.md)
- [REQ-20260827-02：RuleReader FactBindingRequest 2.0.0 接入与阻断分析](requirements/REQ-20260827-02-rulereader-fact-binding-v2-intake.md)
- [BIZ-20260827-01：FactBindingRequest V2 权威与授权边界](decisions/BIZ-20260827-01-fact-binding-v2-authority-boundary.md)
- [REQ-20260827-01：SQL AST 与安全门禁](requirements/REQ-20260827-01-sql-ast-safety-gate.md)
- [REQ-20260826-04：DeepSeek 单事实 SQL 候选生成](requirements/REQ-20260826-04-deepseek-sql-candidate-generation.md)
- [REQ-20260826-03：真实 RuleReader 最新规则版本接入](requirements/REQ-20260826-03-rulereader-latest-rule-integration.md)
- [BIZ-20260826-02：RuleReader 最新规则版本选择](decisions/BIZ-20260826-02-rulereader-latest-version-selection.md)
- [REQ-20260826-02：MongoDB 最新规则读取（已被替代）](requirements/REQ-20260826-02-mongodb-latest-rule-read.md)
- [BIZ-20260826-01：MongoDB 最新规则只读边界（已被替代）](decisions/BIZ-20260826-01-mongodb-rule-read-boundary.md)
- [REQ-20260826-01：数据库连接配置准备](requirements/REQ-20260826-01-database-connection-config.md)
- [REQ-20260820-01：规则规范化、内容哈希与结构化差异](requirements/REQ-20260820-01-rule-change-analysis.md)
- [BIZ-20260820-01：规则分析能力边界与显式阻塞](decisions/BIZ-20260820-01-rule-analysis-boundary.md)
- [REQ-20260819-01：Agent 2 事实绑定输入与候选模板契约](requirements/REQ-20260819-01-fact-binding-intake.md)
- [BIZ-20260819-01：Agent 2 职责与双服务边界](decisions/BIZ-20260819-01-agent2-role-alignment.md)

### 技术与交付

- [DEV-20260828-02：RuleReader 不可变事实交接只读接入设计](architecture/DEV-20260828-02-rulereader-handoff-read-intake.md)
- [DEV-20260828-01：V2 SQL 候选生成输入对齐设计](architecture/DEV-20260828-01-v2-candidate-generation-input.md)
- [DEV-20260827-03：Phase 2G 项目上下文与受治理元数据解析设计](architecture/DEV-20260827-03-project-context-metadata-resolution.md)
- [DEV-20260827-02：FactBindingRequest V2 consumer 与阻断分析设计](architecture/DEV-20260827-02-fact-binding-v2-readiness.md)
- [DEV-20260827-01：SQL AST 与安全门禁设计](architecture/DEV-20260827-01-sql-ast-safety-gate.md)
- [DEV-20260826-04：DeepSeek 单事实 SQL 候选生成设计（含 DataEase SQLBot 借鉴边界）](architecture/DEV-20260826-04-deepseek-sql-candidate-generation.md)
- [DEV-20260826-03：真实 RuleReader 最新规则版本接入](architecture/DEV-20260826-03-rulereader-latest-rule-integration.md)
- [BUG-20260826-01：最新规则查询与真实存储不兼容](bugs/BUG-20260826-01-rule-version-storage-shape.md)
- [DEV-20260826-02：MongoDB 最新规则读取（已被替代）](architecture/DEV-20260826-02-mongodb-latest-rule-read.md)
- [PROG-20260827：FactBindingRequest V2 接入与 Phase 2G 授权解析规划](progress/PROG-20260827.md)
- [PROG-20260828：Phase 2G 实现与后续安全链路](progress/PROG-20260828.md)
- [PROG-20260826：真实 RuleReader 接入与 DeepSeek 候选生成](progress/PROG-20260826.md)
- [DEV-20260826-01：数据库连接配置设计](architecture/DEV-20260826-01-database-connection-config.md)
- [DEV-20260820-01：规则规范化、哈希与差异领域设计](architecture/DEV-20260820-01-rule-change-analysis.md)
- [DEV-20260819-01：事实绑定输入与 SQL 候选契约](architecture/DEV-20260819-01-fact-binding-contract.md)
- [DEV-20260818-02：Phase 1 后端骨架](architecture/DEV-20260818-02-backend-skeleton.md)
- [规则 JSON Schema 1.0（仅用于确定性规则分析）](specs/release-rule.schema.json)
- [RuleReader FactBindingRequest 2.0.0 上游 Schema 来源清单](specs/fact-binding-request-2.0.0-source.json)
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
