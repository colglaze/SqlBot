# BIZ-20260828-02：RuleReader 不可变交接只读边界

- 状态：`approved`
- 创建日期：2026-08-28
- 影响需求：[REQ-20260828-02](../requirements/REQ-20260828-02-rulereader-handoff-read-intake.md)
- 前置决策：[BIZ-20260827-01](BIZ-20260827-01-fact-binding-v2-authority-boundary.md)

## 决策

1. `fact_binding_handoffs` 继续由 RuleReader 拥有数据契约、migration 和唯一写权限；SqlBot 只读取，
   不创建索引或迁移。
2. SqlBot 只接受调用方显式给出的精确 `ruleVersion`，不从交接集合选择“最新”、不猜测版本，也不读取
   Schema `1.0.0` 降级结果。
3. MongoDB 包装与 payload 都是不可信输入。包装身份、独立 Pydantic consumer、固定上游 JSON
   Schema、来源闭包和 canonical payload hash 必须全部通过后，才能形成 intake 记录。
4. 上游 Schema 以 SqlBot 包内固定副本和来源清单 SHA-256 锁定；运行时不读取兄弟仓库。上游 hash
   变化必须另建 REQ/DEV 评审，不静默替换。
5. 任一损坏记录使整批失败；不得跳过坏记录后把剩余集合报告为完整交接。
6. 任一上游 blocking uncertainty 或 SqlBot 完整性 blocking issue 使整批状态为 `blocked`。读取成功
   不表示可以生成 SQL、已授权、已批准或可执行。
7. 该只读入口不装配候选 provider，不调用模型，也不触发既有 V1/V2 候选生成服务。
8. 当前任务不生成 SQL；仓库中已存在的后续候选切片保持独立，不改变本只读边界。

## 所有权

| 数据或能力 | RuleReader | SqlBot |
| --- | --- | --- |
| `fact_binding_handoffs` migration/写入 | 唯一所有者 | 禁止 |
| `fact_binding_handoffs` 精确版本读取 | 提供不可变记录 | 只读 consumer |
| payload 逻辑事实与 uncertainty | 权威来源 | 校验、保留、阻断 |
| SQL 候选 | 不负责 | 其他独立阶段，不能由本入口触发 |
