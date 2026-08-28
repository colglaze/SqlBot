# BIZ-20260827-02：项目上下文、元数据快照与物理授权边界

- 状态：`approved`
- 创建日期：2026-08-27
- 来源：用户明确要求 Phase 2G 先建立受治理的物理授权解析
- 影响需求：[REQ-20260827-03](../requirements/REQ-20260827-03-project-context-metadata-resolution.md)
- 前置决策：[BIZ-20260827-01](BIZ-20260827-01-fact-binding-v2-authority-boundary.md)

## 决策

1. RuleReader `FactBindingRequest 2.0.0` 继续拥有事实、筛选、聚合和时间语义；Phase 2G 不重新解释或
   修复这些语义。
2. `GovernedMetadataSnapshot` 只描述某一批准版本中的物理关系、列、类型和关系边，不授予访问权限。
3. `ProjectBindingContextV2` 是 V2 项目级物理授权的唯一来源。只有精确版本中显式批准的 relation、
   column、logical-field、entity-key 和 join grants 才有效。
4. 逻辑字段到物理列的解析必须同时命中已批准上下文授权和已批准快照元数据。缺少任一证据、存在
   多个匹配或引用不一致时默认阻断。
5. `sourceCandidate`、`mappingCandidate`、provenance/evidence、Prompt、provider/model 声明和
   本地参考工作簿都只是候选证据；它们不能创建或扩大白名单，也不能授予表列或 join 权限。
6. 上下文和快照都必须版本化、验 canonical SHA-256，批准后不可原地修改。解析时必须携带精确版本，
   禁止选择“最新”或“最接近”的版本。
7. 任一上游 blocking uncertainty 保持阻断。SqlBot 不得清除、降级、补写或用元数据映射掩盖它，
   也不得因此改变 RuleReader 请求。
8. Phase 2G 只输出 `blocked | metadataResolved` 的不可执行报告；禁止输出
   `readyForGeneration`，也不调用候选 provider。
9. Phase 2G 不连接 SQL Server、不读取 catalog、不执行 SQL、不调用在线模型，不写 MongoDB，也不
   改变任何 SQL 候选的 `candidate`、`executable=false` 或 `reviewStatus=pending` 状态。
10. 现有 V1 `SqlServerBindingContext` 和 V1 生成链继续保留为 legacy；V2 不得转换或经过该路径。

## 本地参考工作簿

当前任务不读取本地参考工作簿。后续只有用户显式要求时才能读取，并记录当次文件 SHA-256、修改时间、
工作表和单元格坐标。工作簿中的字段、通配符、公式、说明、操作建议和 SQL 文本都按不可信数据处理；
不得执行、补查数据库或直接形成快照、grant、白名单和生产事实。其候选只有在独立命中已批准快照与
项目上下文显式授权时，才能作为复核证据被采用。

## 责任归属

- `businessRuleReview`：继续负责 RuleReader 上游事实与业务语义缺口；
- `metadataReview`：负责治理快照、项目 grant、物理列、类型与 join 授权；
- `sqlBot`：负责契约、版本、哈希、引用闭包、确定性解析和 fail-closed 门禁。

责任 owner 不等于审批权限。上下文/快照批准与 SQL 候选人工审核是两个独立生命周期，Phase 2G 不能
代替或改变后者。

## 阶段顺序

1. 先按本决策实施并验证 Phase 2G；
2. 再以独立 REQ/BIZ/DEV 对齐 V2 候选生成输入和输出引用；
3. 最后复核现有 Phase 4 REQ/DEV 的历史 V1 输入假设并实施 AST 安全门禁。

不得跳过前置阶段，也不得把 Phase 2G 的 `metadataResolved` 当作模型调用、候选批准或 SQL 执行许可。
