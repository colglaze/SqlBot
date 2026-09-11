# 路线图

路线图按可验证的事实级 Agent 2 切片推进。只有当前 Phase 的 DoD 全部满足后，才进入下一阶段。

## Phase 0：文档和边界

状态：**完成**

- 仓库治理、需求、设计、决策和进度文档可追溯；
- 2026-08-19 已用新 REQ/BIZ/DEV 替代“整规则异常集合 SQL”方向。

## Phase 1：后端骨架

状态：**完成（MongoDB 显式禁用）**

- Python、配置、日志、FastAPI 和 CLI 可运行；
- LangGraph 就绪图与数据库生命周期端口可测试；
- 数据库未配置时明确报告 `disabled`。

## Phase 2：FactBindingRequest 接入

状态：**V1 历史切片完成，已被 Phase 2F 的 V2 运行时契约替代**

- Python 固定为 `3.11.9`，包管理继续使用 `uv`；
- RuleReader Schema `2.0.0` 的 camelCase `FactBindingRequest` 可解析；
- SQL Server 上下文包含版本化元数据快照、实体键和关系白名单；
- `derived`、缺少必要上下文及临时表请求被确定性阻塞；
- `SqlTemplateCandidate` 固定为不可执行、待审核候选；
- API、契约和领域测试完全离线。

上述 V1 模型、readiness 与 API 只作 legacy 兼容记录，禁止接收、裁剪或降级 V2 payload。

### Phase 2B：确定性规则变更分析

状态：**完成**

- 基于历史 `release-rule.schema.json` 契约生成 canonical JSON 和内容哈希；
- 生成新增、删除、修改及逻辑结构变化的机器可读 diff；
- 重复 `condition_id` 被拒绝，无基线被显式标记；
- 不接入 MongoDB，不改变事实级 Agent 2 主流程；
- 异常集合语义或真实 Schema 缺失时，整规则 SQL 规划保持阻塞。

### Phase 2C：数据库配置准备

状态：**完成**

- 本地 `.env`、安全配置摘要和 MongoDB/SQL Server 连接参数已集中管理；
- 数据库只读意图、超时和结果上限有默认拒绝门禁。

### Phase 2D：MongoDB 最新规则读取

状态：**被 Phase 2E 替代（离线存储假设与真实 RuleReader 不一致）**

- 建立独立的规则仓储端口和 PyMongo 异步只读适配器；
- 每次按 `project_id + target` 查询数值 `version` 最大的规则，不使用结果缓存；
- MongoDB 载荷必须通过现有 `ReleaseRule` Schema `1.0` 严格校验；
- 不写 RuleReader 集合，不创建其索引或 migration，不改变事实级 Agent 2 主流程。

### Phase 2E：真实 RuleReader 最新规则版本接入

状态：**完成**

- 按真实 `rule_id + generated_at` 索引每次选择最新不可变版本；
- 兼容真实库中的 RuleReader Schema `1.0.0` 和 `2.0.0` 包装；
- 校验外层存储元数据与内层文档引用一致；
- 使用数据库侧最小权限只读账号完成真实 readiness 和 API 冒烟。

### Phase 2F：FactBindingRequest 2.0.0 接入与阻断分析

状态：**完成（REQ-20260827-02 / BIZ-20260827-01 / DEV-20260827-02）**

- 独立消费完整 V2 payload，不运行时依赖 RuleReader 包或兄弟仓库路径；
- 保留 query requirements、provenance/evidence、request ID 和 uncertainties，禁止向 V1 降级；
- 确定性校验版本、身份、证据与内部引用闭包；
- 六类未决语义和任一 blocking uncertainty 在 provider 前停止；
- 报告只输出 `blocked | readyForMetadataResolution` 且固定不可执行。

### Phase 2F.1：RuleReader 不可变事实交接只读接入

状态：**完成（REQ-20260828-02 / BIZ-20260828-02 / DEV-20260828-02）**

- 按调用方显式给出的精确 `ruleVersion` 只读查询 RuleReader `fact_binding_handoffs`；
- 严格校验存储包装、固定上游 JSON Schema、来源闭包与 canonical payload hash；
- 任一坏记录使整批失败，任一 blocking uncertainty 使整批 `blocked`；
- 不写交接集合、不选择最新版本、不接受 V1 降级，也不调用候选 provider 或生成 SQL。

### Phase 2G：项目上下文与受治理元数据解析

状态：**完成（REQ-20260827-03 / BIZ-20260827-02 / DEV-20260827-03）**

- 将 V2 逻辑实体、字段、筛选、聚合和时间要求与版本化项目上下文及元数据快照组合；
- 元数据快照只描述物理事实，项目上下文中的精确 relation/column/field/entity-key/join grants 才能
  授权；两者必须同时命中；
- source/mapping candidate、Prompt、模型和本地参考工作簿只能作为不可信候选证据，不能形成白名单；
- 输出只允许 `blocked | metadataResolved` 且固定不可执行，不清除或降级上游 blocking uncertainty；
- 已通过纯计算 API 和合成脱敏回归证明解析不访问 SQL Server、仓储或 provider；
- 后续仍需独立对齐 V2 候选生成契约，不能直接复用 V1 Prompt/生成服务。

### Phase 2H：私有字段映射与来源资料包

状态：**完成（REQ-20260828-03 / BIZ-20260828-03 / DEV-20260828-03）**

- 用户显式授权后只读收拢 17 个本地参考文件，记录仓库相对路径、SHA-256、来源修改时间和证据坐标；
- 规范化形成 197 条字段候选、93 条视图依赖和 7 条视图来源，并显式保留通配符、定义缺失与
  “待补充”状态；
- 原始资料、完整候选证据和 Schema 只提交到独立私有仓库；公开项目只保存固定 commit、bundle digest
  和聚合数量，不保存内部对象/字段或 SQL；
- 资料包不进入运行时，不创建 snapshot/grant，不调用模型、不连接数据库，也不改变 uncertainty 或
  候选审批状态。

## Phase 3：SQL 候选生成

状态：**V1 legacy 与独立 V2 输入对齐均完成
（REQ-20260828-01 / BIZ-20260828-01 / DEV-20260828-01）**

- 建立 DeepSeek provider 端口、适配器、Prompt 版本和有界重试；
- 每次只为一个非派生事实生成单条参数化 SQL Server 查询；
- 严格解析 `SqlTemplateCandidate`，拒绝自由文本、空响应和契约外字段；
- 使用固定模型替身覆盖正常、超时、限流、非法 JSON 和重试耗尽。

上述历史实现只接受 `FactBindingRequest 1.0.0`。V2 不经过此路径；V2 切片携带完整 Phase 2G 请求与
报告，使用独立 Prompt/候选契约并先重算授权闭包。交付回归只使用固定离线 provider。

## Phase 4：SQL AST 与安全门禁

状态：**完成（REQ-20260827-01 / DEV-20260827-01，2026-08-28 V2 复核版）**

- 固定 SQLGlot `30.17.x` 和 `tsql` dialect，通过 adapter 隔离具体 AST；
- 输入已替换为完整 V2 generation request + `SqlTemplateCandidateV2`，parser 前重算 Phase 2G、候选
  自哈希和全部审计引用；
- 已实现完整语句列表、只读结构、命名参数、scope-aware 对象/基础列 qualification、精确授权 join、
  禁用临时/外部源、唯一 `fact_value` 来源与 `usages.conditionId` coverage 门禁；
- 纯计算 API 与真实 parser 合成攻击回归完全离线，不连接、准备、解释或执行 SQL Server；
- `passed` 报告与候选仍固定不可执行，不改变审批状态，也不清除 blocking uncertainty。

### Phase 4R：真实上游交接与端到端候选证据闭环

状态：**已规划，未实施（REQ-20260906-02 / BIZ-20260906-01 / DEV-20260906-02，均 proposed）**

本阶段定位于 Phase 5A（已完成，不依赖本阶段）与 Phase 5B/5C 之间，是进入 5B 的前置门禁：

- 精确、不可变、可回读的 Agent 1 规则版本与完整事实级 handoff（`blockingRequestCount=0`）；
- 批准的版本匹配项目上下文与受治理元数据快照（V3 使用独立 V3 契约；缺任一即
  `blockedUpstream`，不用合成数据冒充真实业务就绪）；
- 真实 handoff intake 与 Phase 2G 重算为 `metadataResolved`；
- 单事实在线候选生成（用户当次明确授权）、insert-only 候选持久化与 Phase 4 静态 `passed`；
- 形成不含业务结果值和秘密的端到端证据包，公开 PROG 只保存脱敏哈希、数量与状态；
- 关键前置：Agent 1 侧已批准 V3（`FactBindingRequest 3.0.0`）路径；SqlBot intake 升级按
  REQ-20260906-03 / BIZ-20260906-02 / DEV-20260906-03 完成 V3 批次只读 intake 的**离线实现**
  （冻结 Schema 副本、独立 consumer、batch 门禁与只读 API，全部合成脱敏测试通过）。上游 V3
  契约提交与 T0 来源登记已经完成；真实调用仍要求同一次仓储背书、已批准 context/snapshot/grants
  和独立的在线 provider 授权。

2026-09-06 基线审计修订（不改写上文）：

- V3 intake 离线实现（`b3e3d35`）只解决读取与契约门禁；`FactBindingRequestV3` 尚无法进入
  Phase 2G/候选生成/静态门禁/候选存储（现有链路全部绑定 V2 契约，V3→V2 转换被禁止），
  已登记为可复现设计缺口
  [BUG-20260906-01](bugs/BUG-20260906-01-v3-phase4r-downstream-contract-gap.md)；
- V3 下游管线对齐已立项（REQ-20260906-04 / BIZ-20260906-03 / DEV-20260906-04，均
  `approved`，2026-09-09 用户批准）：独立 V3 契约链（V3 授权上下文/快照 → Phase 2G V3 →
  V3 候选生成 → V3 静态门禁 → V3 候选存储 → V3 编排与证据包，里程碑 M1–M6）插入在本阶段
  原 Milestone 1–8 之前；M0 已收口（`completed`），M1 当时已启动（`in_progress`）；
- 在该下游链完成前，不实现 Phase 4R 编排服务，不生成真实候选。2026-09-09 已完成上游 commit
  与三类哈希来源登记（提交 `e3b00b2`），原”无 commit 锚点”阻塞解除；M0 规划批次已进入提交
  `61a377f`；固定私有资料中的现有事实也已由用户确认，后续不再重复索取。当前剩余工作按职责
  分工：`businessRuleReview` 负责上游业务表达及新版本 catalog/rules/handoff；`metadataReview`
  负责已确认物理事实到版本化 context/snapshot/grants 的转换与批准；SqlBot M1 只负责契约模型
和批准内容闭包纯计算校验。M0 已因规划文档批准并落档而收口为 `completed`；M1 当时已启动（`in_progress`），
首个子任务及审核修复已完成，九组批准校验子任务已完成；handoff 契约子任务已完成（2026-09-10）：
  `handoff_closure_v3.py`（`HandoffClosureV3` + `RepositoryVerifiedHandoffV3`，构造器已改为无条件
  TypeError）、`test_handoff_closure_v3_contract.py`（76 项，含 verified 构造边界/V2 完整 fixture 拒绝/
  字段边界参数化/生命周期隔离）、`test_project_bindings_v3_contract.py`（110 项，含 6 项精确反例）；
  全量 610 passed。
  M1 已标记 `completed`（2026-09-10）；实现已提交（`189daca`、`29716b0`），
  当时本轮文档修订尚未提交。
- M2 第一子任务已完成（2026-09-11）：`validate_handoff_closure_v3.py`（内容闭包校验，
  六组有序 fail-fast 检查，36 项单元测试）。
- M2 第二子任务已完成（2026-09-11）：`usage_traceability_v3.py`（六元组追溯摘要，
  20 项单元测试）。
- M2 第三子任务已完成（2026-09-11）：`ResolveMetadataRequestV3` 请求契约
  （domain/project_bindings_v3.py，34 项契约测试）。
- M2 第四子任务已完成（2026-09-11）：`BindingResolutionReportV3` 报告契约
  （domain/project_bindings_v3.py，49 项契约测试）。
- M2 审核修复已完成（2026-09-10）：`BindingResolutionReportV3` 修复 blocked
  报告六项输出完整性（新增 aggregation/timeRange None 检查）和顶层静默规范化
  （strict=True、str_strip_whitespace=False）；15 项新增针对性测试；全量 749 passed。
  详见 [BUG-20260910-01](bugs/BUG-20260910-01-v3-binding-resolution-report-contract-gaps.md)。
- M2 第五子任务已完成（2026-09-10）：`_validate_resolution_input_v3`
  （输入门禁内部辅助函数，结构重验 + 六步内容/范围门禁，47 项单元测试）。
- M2 第六子任务已完成（2026-09-10）：`_resolve_column_grant_v3`
  （单 column grant 物理引用解析：column grant → relation grant →
  snapshot relation → snapshot column；历史快照 43 项，后补齐至 52 项；全量 839 passed 为当时记录）。
- M2 第六子任务修复已完成（2026-09-10）：修复快照重复检测顺序依赖缺陷
  （全局关系检查完成后才检查全局列），新增回归测试与双向顺序验证。
- M2 第七子任务已完成（2026-09-10）：`_resolve_fields_and_entity_keys_v3`
  （字段绑定与实体键授权闭包；历史快照 22 项，后补齐至 26 项；全量 870 passed 为当时记录）。
- M2 列授权解析测试矩阵补齐已完成（2026-09-10）：52 项定向、848 项全量。
- M2 测试修复已完成（2026-09-10）：补强返回副本隔离（含捕获浅拷贝的辅助函数）、
  失败输入不变性（实际传入对象前后对比）、evidenceIds 顺序敏感性、
  结构异常脱敏（真实 V3 根参数化）、双向顺序证据；生产代码未修改。
  M2 第八子任务已完成（2026-09-11）：`_resolve_filters_v3`
  （filters 物理字段解析；原交付 24 项 / 898 passed，
  补强验收证据后当前 30 项 / 904 passed）。
  M2 第九子任务已完成（2026-09-11）：`_resolve_aggregation_v3`
  （aggregation 授权引用解析；原交付 25 项 / 929 passed，
  补强验收证据后当前 32 项 / 936 passed）。
  M2 第十子任务已完成（2026-09-11）：`_resolve_time_range_v3`
  （timeRange 时间字段授权解析，23 项单元测试，全量 959 passed）。
  M2 第十一子任务已完成（2026-09-11）：`_resolve_join_grant_v3`
  （指定 join grant 物理授权解析；原交付 22 项 / 981 passed，
  补强验收证据后当前 30 项 / 989 passed）。
  M2 第十二子任务已完成（2026-09-11）：`_select_join_closure_v3`
  （多关系授权连接闭包选择；原交付 18 项 / 1007 passed，
  修复门禁绕过后当前 27 项 / 1016 passed）。
  M2 仍为 `in_progress`，剩余：完整 ResolvedJoinV3/evidenceIds、执行方向语义、公开 `resolve_metadata_v3` 编排及报告组装；
  另 `entityType`/`grain` 到授权 relation 的映射待澄清（见 DEV §14 开放问题第 7 项）。
  filters/aggregation/timeRange/join-grant/join-closure 辅助函数已完成，但完整解析服务与报告组装仍未实现，M2 不标记 `completed`。
  当前代码基线详见最新 PROG。

## Phase 5：受限 SQL Server 验证

状态：**5A 已完成（离线）；5B/5C 待实施（REQ-20260905-01 / BIZ-20260905-01 / DEV-20260905-01）**

- Phase 5A 已交付（2026-09-06）：严格 V2 请求/报告契约、数据库前完整 Phase 2G/4 重算与携带报告
  比对、token 级确定性参数 binder、独立 validation switch 与非生产 profile 配置、ODBC describe-only
  adapter（加密、固定 session policy、目标身份/最小权限/批准快照漂移/`sp_describe_first_result_set`
  结果描述探测）和本地 CLI；candidate 不取数，报告始终 `executable=false`；
  真实数据库冒烟仍需按 REQ 第 21 节确认非生产目标与最小权限账号；
- Phase 5B 在 5A 同一闭包通过后获取估算计划，不获取实际计划、不执行 candidate；未实施；
- Phase 5C 只在非生产、合成/脱敏参数和用户当次明确授权下做有界试跑；未实施；
- 全部验证只提供本地 CLI，不在当前无调用方鉴权的 FastAPI 上新增 live validation 入口；
- 使用最小权限只读账号，设置连接/命令/锁等待/成本/行数/字节/并发限制和取消能力；
- 每一级都重新计算 Phase 2G/4，不在验证时临时发现、选择“最新”或扩大授权范围；
- 报告始终 `executable=false`，不改变候选审核状态；
- 最新规则继续只读查询 RuleReader 的 `rule_versions`，不修改该集合；未来 SqlBot 自有审计集合使用
  独立 migration。

进入 Phase 5B/5C 实现前仍必须按 REQ 第 21 节确认各自前置条件，并完成 Phase 4R 的真实候选证据
闭环；缺少证据时保持阻断。

## Phase 6：人工审核与候选发布

状态：**待规划**

- 审核包展示事实契约、元数据快照、SQL、对象范围、覆盖、假设和告警；
- 批准、驳回、revision、并发冲突和不可变审计记录可验证；
- 未经批准的候选始终不可执行。

## 后续候选

更多 SQL 方言、临时表、正式查询调度、整规则诊断集合和通用 ChatBI 都不在当前路线图内，必须建立独立 REQ/BIZ 后再评估。
