# BIZ-20260917-01：暂缓规则源差异，按 Mongo 事实请求生成 Agent2 模板

- 状态：`superseded`（第 2 条“不生成整规则放行清单 SQL”已被 [BIZ-20260917-02](BIZ-20260917-02-one-view-shaped-sql.md) 替代；用户于同日稍后明确最终要一条像视图的总 SQL。第 1、3、5 条关于不把其它规则文本补进 SQL、不发明复合公式的约束仍有效）。
- 影响需求：[REQ-20260915-01](../requirements/REQ-20260915-01-v3-mongodb-generation-entry.md)、[REQ-20260917-02](../requirements/REQ-20260917-02-agent2-fact-sql-generation.md)。
- 既有职责：[BIZ-20260819-01](BIZ-20260819-01-agent2-role-alignment.md)、[BIZ-20260906-01](BIZ-20260906-01-agent1-metadata-review-sqlbot-boundary.md)。
- 对照记录仍保留，不作为本阶段完成条件：[PROG-20260917](../progress/PROG-20260917.md) 中 Mongo 与优化方案/视图差异。

## 决策

1. 当前生成输入是本地 Mongo 中精确 ruleVersion 的 V3 FactBindingRequest 批次（18 条）。不把优化方案 Java、视图忠实稿或 wiki 现规则的额外条件补进 SQL。
2. ~~SqlBot 继续做 Agent 2：一条事实请求生成一条只读 SQL 模板，不生成整规则放行清单 SQL。~~ 已被 [BIZ-20260917-02](BIZ-20260917-02-one-view-shaped-sql.md) 替代。
3. 与优化方案/视图/wiki 的判定公式差异仍不在 SqlBot 内用其它文本补洞。对[一条视图形态 SQL](BIZ-20260917-02-one-view-shaped-sql.md)而言，这些差异会阻断该总 SQL，直到上游发布可展开的新版本 handoff。
4. 物理表列仍须 metadataReview 批准。handoff 上 `mappingStatus=unresolved` 不表示禁止绑定；本地测试绑定须新 context/批准版本，不得改写旧候选。
5. 请求写成 `source` 但描述为复合到款/合同/定时/合并组公式的事实：没有已批准的物理列时阻断该条，不得把描述里的公式自行写成 JOIN/算术 SQL。
6. [REQ-20260917-01](../requirements/REQ-20260917-01-view-equivalent-sql.md) 的视图逻辑等价目标不取消；同日稍后已恢复为本阶段实施目标，见 [BIZ-20260917-02](BIZ-20260917-02-one-view-shaped-sql.md)。

## 当前 Agent2 范围

以 2026-09-05 handoff 重读为准：18 条中 15 条在现有 M3（source、单关系、实体键等值）形状内，前提是有列授权；1 条已有本地测试授权并已生成；3 条现有 M3 拒绝（`gte` 过滤、`aggregate/count`、`exists`）。

上述三类是组合 SQL 可能用到的形状，不再作为本阶段交付顺序；见 [BIZ-20260917-02](BIZ-20260917-02-one-view-shaped-sql.md)。
