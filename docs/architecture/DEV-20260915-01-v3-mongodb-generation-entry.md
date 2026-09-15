# DEV-20260915-01：MongoDB V3 输入准备与候选导出

- 状态：`in_progress`。
- 实现需求：[REQ-20260915-01](../requirements/REQ-20260915-01-v3-mongodb-generation-entry.md)。

## 输入准备

新增严格 `PrepareGenerationRequestV3`，schemaVersion=1.0.0，包含精确 ruleVersion、requestId、
projectContext、metadataSnapshot、approvalRecord；不接受 latest、裸规则树、未完成草案或自报已验证。

应用服务通过只读 handoff repository 执行现有 V3 intake，唯一选择 requestId；从 intake 结果构造
HandoffClosureV3 与 bindingRequest，不由调用方手工填写 payload/hash。以 context 的 projectRef
构造既有 ResolveMetadataRequestV3，运行完整 M2；核对批准端口返回的有效记录与携带记录精确一致。

输出只是可供生成步骤重新核验的 ResolveMetadataRequestV3，不能携带跨调用有效的仓储证明。
模型、候选存储和 SQL Server 均不进入准备步骤的依赖。异常输出使用固定 code，不包含原输入。

CLI `prepare-v3 --input <package.json> --output <resolve-request.json>`：读取严格输入后，只装配
MongoRuleStore 与 MongoApprovalRecordStoreV3，确保资源关闭。默认拒绝已有输出，结果写入本地文件。

## 候选导出

保持 EvidencePackV3 的原有 schema 和内容。`generate-v3 --candidate-output <private.json>` 为可选项，
在模型调用前检查目标文件冲突、与证据输出同路径、与输入同路径等情况。

通过存储端口的透明包装保留同次生成的独立候选副本，实际 save/close 仍委托真实 store，
不改变 save outcome，也不把存储结果当作安全结论。CLI 将候选存储初始化延后至生成门禁通过且取得候选之后，
首次 save 前才确保唯一哈希索引；handoff/批准失效、M2 阻断或 provider 失败时不建索引。
初始化失败降级为 `unavailable` 存储结果，继续写审计证据而不伪装静态通过。生成结束后复算候选自哈希，要求与证据包相同。
当原证据循环已形成静态结论时，以同一生成输入和候选确定性复核报告，要求状态/哈希与原包一致；
没有静态结论时保持静态报告为空。导出固定 candidate/executable=false/reviewStatus=pending。

本地导出不再次调用模型。原 EvidencePackV3 独立保存；候选导出失败不得把模型重跑当作重试写文件。
未产生候选时不伪造空模板。输出路径和错误消息不包含实际 SQL、对象列名、参数值或 provider 原文。

## 验证与授权

默认验证全部使用合成输入和固定 provider。用户已明确目标为生产 SQL Server，并授权本次仅核验
元数据；未授权查询业务记录或执行候选 SQL。已有 Excel 说明直接复用；正式 context/snapshot/approval 仍需精确
元数据证据与批准决定。真实生成状态在 PROG 记录，不以离线测试替代真实成功。
