# BIZ-20260907-01：候选人工审核与发布的权威边界

- 状态：`proposed`
- 创建日期：2026-09-07
- 来源：用户要求在"不修改代码、只做方案"的约束下完成 Phase 6（人工审核与候选发布）规划；
  [BIZ-20260905-01](BIZ-20260905-01-restricted-sqlserver-validation-boundary.md) 第 9 条已把
  人工批准、驳回、revision 和发布状态机的归属预留给 Phase 6
- 影响需求：[REQ-20260907-01](../requirements/REQ-20260907-01-candidate-review-publish.md)
- 技术方案：[DEV-20260907-01](../architecture/DEV-20260907-01-candidate-review-publish.md)
- 前置决策：[BIZ-20260905-01](BIZ-20260905-01-restricted-sqlserver-validation-boundary.md)、
  [BIZ-20260906-03](BIZ-20260906-03-v3-downstream-authority-boundary.md)、
  [BIZ-20260906-01](BIZ-20260906-01-agent1-metadata-review-sqlbot-boundary.md)、
  [BIZ-20260828-01](BIZ-20260828-01-v2-candidate-authority-boundary.md)、
  [BIZ-20260827-02](BIZ-20260827-02-project-metadata-authorization-boundary.md)

## 1. 决策

1. **人工审核是批准的唯一来源，系统与模型永不产生决策。** `approve | reject | requestRevision`
   只能由携带可审计身份的人工审核者提交；LLM 输出、静态报告、验证报告、候选 `provenance`
   （模型、Prompt 版本、provider request ID）都只是审核包内供人工判断的证据，任何自动或
   半自动批准路径被禁止。SqlBot 应用只负责装配审核包、校验闭包、执行状态机与存储，永不
   充当审核主体。
2. **候选业务载荷在审核与发布全程不可变。** `SqlTemplateCandidateV2` 的契约字段
   `reviewStatus=pending` 与全部载荷永不修改；有效审核状态只存在于独立 insert-only 决策记录、
   生命周期事件与状态指针中，按候选 `contentSha256` + 契约版本关联。这延续仓库核心不变量
   （已批准 SQL 模板是不可变审计记录）与 [BIZ-20260906-03](BIZ-20260906-03-v3-downstream-authority-boundary.md)
   第 5 条冻结的 context 生命周期模式（载荷 insert-only + 独立生命周期载体 + CAS），消除
   "推进状态"与"禁止原地改写"并存的可能矛盾。
3. **状态机由应用冻结并校验，不由存储隐式表达。**
   `pending → underReview → approved | rejected | revisionRequested`，`rejected` 与
   `revisionRequested` 是该候选版本终态；`approved` 经发布门禁进入 `published` 终态。非法
   转换（终态后再决策、跨状态跳转、对不存在候选决策）确定性拒绝且零写入。
4. **发布是独立门禁动作，不是批准的副作用。** 发布门禁聚合候选契约校验、审核包闭包与
   Phase 2G/4 重算一致、静态报告 `passed`、有效 `approve` 决策、受限验证证据在案与主体
   边界；任一缺失即 fail closed。发布记录是自包含不可变审计记录（完整嵌入被批准候选载荷），
   每候选版本至多一条；撤销或替代只通过显式 supersession 事件表达，旧记录原样保留。
5. **受限验证证据是发布门禁的强制项（fail closed）。** 当前冻结：Phase 5A describeOnly 对
   同一候选的 `passed` 报告必须在案；Phase 5B/5C 实施后自动加入强制集；证据缺失即阻断，
   豁免口径是开放问题，未确认前不存在绕行。
6. **修订产生新版本，永不原地修复。** `requestRevision` 与 `reject` 不修改、不删除原候选；
   修订只能通过新一轮生成运行产生新 `contentSha256` 的候选，决策记录可引用后续候选哈希
   形成可追溯修订链。禁止"批准后改 SQL 文本"的任何路径——那必然是内容修订，必须走完整
   生成、门禁、审核链。
7. **决策前强制完整重算，不信任携带证据。** 决策服务在记录任何决策前必须重算审核包哈希
   闭包、对包内 generation request 重算 Phase 2G 与 Phase 4 并与包内报告 canonical 比对；
   任一不一致零写入。该口径与 Phase 5A "数据库前完整重算"、V3 M3 "provider 前仓储背书"
   一致：每个有后果的阶段都以自身校验为准，不复用上游结论。
8. **并发冲突由确定性 CAS 裁决。** 同一候选版本至多一个有效决策、至多一条发布记录；并发
   败者得到确定性冲突结果且零写入。冲突处理不引入锁等待或重试循环。
9. **首切片只有本地 CLI，HTTP 入口被认证授权设计门禁。** 延续
   [BIZ-20260905-01](BIZ-20260905-01-restricted-sqlserver-validation-boundary.md) 第 5 节：
   当前 FastAPI 无调用方身份、细粒度授权与审计主体，在线审核入口（工作台、在线决策）在
   独立认证授权需求批准前保持阻断。CLI 不提供任何绕过门禁的参数（不提供 `--force`、
   `--skip-validation` 类选项）。
10. **发布记录证明"已批准"，不签发运行许可。** Phase 6 产物不携带任何"可执行"字段；执行、
    调度与生产查询消费属于路线图"后续候选"，必须独立 REQ/BIZ 后再评估。`published` 只对
    审计可见，对运行时不可见。
11. **V2/V3 隔离延伸到审核与发布层。** 审核/发布记录携带候选契约版本与内容哈希闭包；V3
    候选的审核对齐依赖 [REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
    M1–M6 完成，另立任务；此前审核/发布契约对 V3 载荷 fail closed，禁止任何 V3↔V2 转换、
    包装或降级（延续 [BIZ-20260906-03](BIZ-20260906-03-v3-downstream-authority-boundary.md)
    第 1、8 条）。
12. **机制交付与真实使用分离登记。** 离线机制（契约、纯计算服务、存储适配、CLI、离线测试）
    可在文档批准后按里程碑实施；对真实候选执行审核/发布以 Phase 4R 真实证据闭环完成、
    开放问题确认与用户当次授权为前置。机制交付不得表述为真实审核能力已可用。
13. **审核链允许受控 SQL 展示，公开产物只留哈希。** 审核包、决策记录与发布记录在受控存储
    中允许包含 SQL 文本与物理对象名（人工审核所必需）；公开 Git、公开文档与 PROG 只保存
    哈希前缀、数量与状态。参数值、连接信息、凭据、provider 原始响应永远不进入审核链任何
    记录；决策理由等自由文本的脱敏与保存策略确认前不落库。

## 2. 责任归属

- **人工审核者（reviewer）**：`approve | reject | requestRevision` 决策的唯一来源；对决策
  依据的完整性（已查看审核包全部必展内容）负责；
- **发布操作者（publisher）**：执行发布门禁并落发布记录；可以与审核者同人或分离，但发布
  门禁独立校验，不信任决策记录自带结论；
- **审计方（auditor）**：只读回读审核包、决策记录、生命周期事件与发布记录，可独立重建
  完整状态机轨迹；
- **sqlBot**：审核包/决策/发布/状态机/存储的机制实现者；对 V2 行为零变化与全部 fail-closed
  语义负责；
- **运维/用户**：审核/发布存储集合授权与账号隔离、保留期与备份策略、CLI actor 声明的可信
  边界、真实使用的当次授权。

责任 owner 只表示复核与决策责任，不授予修改已落库审计记录、生产元数据或执行 SQL 的权限。

## 3. 对既有决策的影响

- 落实 [BIZ-20260905-01](BIZ-20260905-01-restricted-sqlserver-validation-boundary.md) 第 9 条
  的预留：Phase 5 不改变候选状态，Phase 6 拥有审核与发布状态机；两者边界无重叠；
- 延伸 [BIZ-20260828-01](BIZ-20260828-01-v2-candidate-authority-boundary.md) 确立的
  `candidate / executable=false / reviewStatus=pending` 固定契约：本决策明确该字段值在候选
  载荷内永不变化，审核状态存在于独立生命周期载体——两者不冲突，前者是载荷固有声明，后者
  是应用维护的有效状态视图；
- 复用 [BIZ-20260906-03](BIZ-20260906-03-v3-downstream-authority-boundary.md) 第 5 条冻结的
  生命周期模式（insert-only 载荷 + 独立事件/指针 + CAS）作为审核状态机载体方案，不另造
  第二种生命周期语义；
- 不修改 [BIZ-20260906-01](BIZ-20260906-01-agent1-metadata-review-sqlbot-boundary.md) 的
  责任边界：SQL 候选审核属于 SqlBot 自有产物链，与 metadataReview（上下文/快照批准）和
  businessRuleReview（规则语义确认）是三个独立审批，互不替代。

## 4. 需要确认但不影响本决策成稿的事项

开放问题及其阻断范围见
[REQ-20260907-01](../requirements/REQ-20260907-01-candidate-review-publish.md) 第 10 节与
[DEV-20260907-01](../architecture/DEV-20260907-01-candidate-review-publish.md) 开放
问题表。任一未决问题在其阻断的里程碑或真实使用前必须由对应 owner 明确；未确认时保持阻断。
