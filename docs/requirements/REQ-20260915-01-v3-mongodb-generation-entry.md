# REQ-20260915-01：MongoDB 精确规则输入准备与 V3 模板导出

- 状态：`in_progress`。
- 来源：用户要求推进到能根据 MongoDB 中的规则生成 SQL 模板。
- 基线：[REQ-20260906-04](REQ-20260906-04-v3-downstream-pipeline-alignment.md)。
- 设计：[DEV-20260915-01](../architecture/DEV-20260915-01-v3-mongodb-generation-entry.md)。

## 目标

1. 增加只读 `prepare-v3` CLI：使用精确 ruleVersion/requestId 和完整已批准的 context/snapshot/approval
   材料，从 MongoDB 获取 handoff、核验有效批准及 M2，输出既有 `ResolveMetadataRequestV3`。
2. `generate-v3` 增加显式候选导出选项；输出同一次生成的完整候选与对应静态报告，便于直接查看 SQL
   模板。保持原 EvidencePackV3 不包含 SQL 的审计边界。
3. 保持真实运行的仓储背书、active pointer、provider 允许列表、有界重试和不可执行状态。
4. 真实运行前完整核对来源材料；元数据采集、正式批准和实际调用依用户当次授权执行，不能自动批准
   旧草案或用合成数据替代真实输入。

## 边界

- 不改变 V2、不降级 V3、不放宽 SQL 支持范围或门禁。
- prepare-v3 不初始化候选存储、不建索引、不调用 provider、不连接 SQL Server、不写批准记录。
- 生成步骤重新读取仓储和批准状态，不能复用 prepare 时的检查作为当次调用背书。
- 候选导出是单独的本地私有 JSON；stdout 只显示哈希、路径、状态和 issue code，不输出 SQL。
- snapshot/context 草案不允许自动填成 approved；批准记录只由 metadataReview 的正式决定产生。

## 验收

- 完整合成材料可从内存模拟的 Mongo 端口准备输入，并进入现有真实生成服务和静态门禁。
- 批次不存在/损坏、request 不匹配、批准缺失/撤销/不一致、M2 blocked 均明确拒绝；无模型/写入副作用。
- 导出的候选与证据包引用哈希精确相同；没有再次调用模型；静态失败或存储失败不伪装成功。
- 文件默认不覆盖，现有文件在模型调用前被拒绝；输出失败不伪造导出成功。
- 权威 ruff、format、pytest 和 diff 检查通过；PROG 如实区分离线实现与真实运行结果。
