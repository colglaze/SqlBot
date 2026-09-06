# BIZ-20260905-01：受限 SQL Server 验证边界

- 状态：`approved`（Phase 5A 已按本决策实现；5B/5C 边界不变）
- 创建日期：2026-09-05
- 来源：用户要求在 Phase 4 完成后编写尽可能详细的下一步计划
- 影响需求：[REQ-20260905-01](../requirements/REQ-20260905-01-restricted-sqlserver-validation.md)
- 技术方案：[DEV-20260905-01](../architecture/DEV-20260905-01-restricted-sqlserver-validation.md)
- 前置决策：[BIZ-20260828-01](BIZ-20260828-01-v2-candidate-authority-boundary.md)

## 1. 决策摘要

Phase 5 建立“受限验证证据”，不建立生产查询能力。阶段按风险递增拆分：

1. Phase 5A：连接证明、权限证明、批准快照漂移检查、参数编译和首个结果集描述；候选查询不取数。
2. Phase 5B：在 5A 通过后获取估算执行计划，候选查询仍不取数。
3. Phase 5C：在独立授权的非生产环境，以合成或脱敏参数进行有界试跑；不返回或保存业务结果值。

下一实现切片只交付 Phase 5A。Phase 5B 和 5C 必须分别满足前置完成条件后才能实施，不能在同一次
改动中通过配置顺带开启。

## 2. 权威输入与重新校验

每次受限验证必须携带完整的：

- `ValidateSqlCandidateRequestV2`；
- `SqlStaticValidationReportV2`；
- 指定验证模式和受控参数集；
- 配置中预先批准的验证目标 profile。

应用必须在连接 SQL Server 前重新执行 Phase 2G 和 Phase 4，重算 candidate、generation、context、
snapshot、resolution report 和 static report 的哈希与引用闭包。只有重算结果与携带报告完全一致且
Phase 4 为 `passed` 时，才可进入数据库适配器。

禁止只传 candidate ID、读取“最新”上下文或快照、信任调用方的 `passed` 字段，或复用历史报告跳过
重算。

## 3. 候选 SQL 与驱动语句边界

- `SqlTemplateCandidateV2.sqlTemplate` 是不可变审计载荷，Phase 5 不修改、格式化或覆盖它。
- `:name` 到 SQL Server 参数或 ODBC 位置参数的转换由独立 binder adapter 完成，形成可审计的派生
  `BoundSql`；原 SQL hash、binder 版本、派生 SQL hash 和参数顺序必须同时记录。
- session policy、固定 catalog probe、`sp_describe_first_result_set`、SHOWPLAN 开关和事务控制语句都
  是应用拥有的固定协议语句，不属于 candidate，不得拼接进 candidate 后冒充同一 SQL。
- 所有 candidate 值和系统过程参数都通过驱动绑定传入，禁止字符串插值。
- candidate 或任一审计引用变化必须创建新候选或新验证运行，禁止覆盖已有报告。

## 4. 目标环境与启用方式

- 首版只允许显式标记为 `development` 或 `staging` 的验证 profile；`production` 一律在配置阶段拒绝。
- SQL Server 验证使用独立总开关，默认关闭，不复用 MongoDB 的 `RSB_DATABASE_ENABLED`。
- `ApplicationIntent=ReadOnly` 只作为连接意图和可能的只读路由信号，不能代替数据库权限检查。
- 连接必须加密并验证服务端证书；Phase 5 启用时禁止 `TrustServerCertificate=true`。
- 账号必须对精确授权对象拥有 `SELECT`，且不能拥有影响这些对象的 DML、DDL、控制或模拟权限。
- 首版每次运行建立独立、不可复用的连接，关闭连接池，避免 session 设置泄漏到下一次验证。
- 没有已批准的 profile、环境分类、最小权限账号、参数数据分类或目标证书信任链时，默认阻断。

## 5. 入口边界

Phase 5A 首版只提供本地 CLI/application service，不新增 HTTP 在线验证端点。当前 FastAPI 服务没有
调用方身份、细粒度授权、速率限制和审计主体，直接增加网络执行入口会形成新的数据库攻击面。

后续只有在独立需求完成认证、授权、请求大小限制、并发限制和审计主体设计后，才允许增加 HTTP
入口。即使 API 后续开放，也不能接受客户端提供主机、数据库、连接串或任意 SQL。

## 6. 分级通过语义

报告状态统一为：

- `passed`：仅表示本次指定模式的所有门禁通过；
- `blocked`：确定性安全、引用、权限、漂移、参数或结果契约问题；
- `inconclusive`：连接、超时、取消、驱动或服务暂时不可用，不能形成通过结论。

所有报告始终 `executable=false`。5A 通过不代表有估算计划，5B 通过不代表已试跑，5C 通过也不代表
已人工批准、可发布或可用于生产查询。

## 7. 三个子阶段的固定边界

### 7.1 Phase 5A：描述验证

- 可执行固定连接探测、权限探测和 catalog 查询；
- 可调用 `sp_describe_first_result_set` 对参数化 batch 做静态结果描述；
- 不执行 candidate 取数，不返回结果值，不调用在线模型，不写 RuleReader 集合；
- 比较实时对象结构与输入携带的批准快照，但实时 catalog 永远不能扩大授权范围。

### 7.2 Phase 5B：估算计划

- 仅在 5A 同输入同目标通过后运行；
- 使用 SQL Server 估算计划能力，不生成实际执行计划；
- 提取成本、估算行数、运算符和访问对象的中性指标；不保存原始 XML 或 SQL 文本；
- SHOWPLAN 权限只能授予验证账号，不能附带任何写权限。

### 7.3 Phase 5C：有界试跑

- 只允许非生产目标、合成或批准的脱敏参数集；
- 设置连接、命令、锁等待、估算成本、行数、结果字节和并发上限；
- 超时后必须尝试取消并关闭连接，所有路径结束时回滚并销毁 session；
- 只记录类型、空值、行数、字节数、耗时和问题码，不返回、日志化或持久化业务值；
- Phase 5C 的开发、首次连接和每次真实目标试跑仍需用户针对当次任务明确授权。

## 8. 私有参考资料边界

飞书文件夹、私有 `RuleDataReferences` bundle、工作簿、视图文本、候选字段和来源说明仍为非权威资料。
它们不能创建 target profile、snapshot、grant、参数值或验证许可，也不能直接进入 SQL、Prompt、日志、
测试夹具或公开报告。

若后续用私有资料人工核对，应固定私有 commit、bundle digest 和来源 SHA-256，并由 metadata owner 将
确认结果发布为新的受治理快照或项目上下文版本。Phase 5 只消费该批准版本。

## 9. 审计与生命周期

- 每次运行生成唯一 `validationRunId`，报告覆盖输入哈希、模式、policy/binder/driver 版本、目标 profile
  身份、开始/结束时间、门禁证据和稳定 issue。
- 参数值、连接串、主机、数据库名、用户名、原始 SQL、原始计划、业务结果和底层异常文本不得进入
  普通日志或公开响应。
- 参数证据只记录名称、声明类型、来源分类、是否绑定和使用服务端密钥计算的值指纹；禁止对低熵值
  使用无密钥 SHA-256。
- Phase 5A 首个纵向切片可先返回不可变报告；持久化必须通过独立审计仓储端口和 SqlBot 自有集合
  migration 实现，不得写 RuleReader 的 `rule_versions` 或 `fact_binding_handoffs`。
- Phase 6 才拥有人工批准、驳回、revision 和发布状态机；Phase 5 不改变 candidate 状态。

## 10. 需要确认但不影响本计划成稿的事项

以下项目在进入相应实现前必须由责任人明确，未确认时对应路径保持阻断：

1. 验证环境的 owner、用途、环境分类和稳定 profile ID；
2. 支持的 SQL Server 主版本、版本下限和数据库兼容级别；
3. SQL 登录或 Windows 集成认证，以及凭据轮换方式；
4. 是否允许验证账号获得 `SHOWPLAN`，以及 Phase 5B 的成本阈值；
5. 是否启用 `READ_COMMITTED_SNAPSHOT`，只读取现状，不由 SqlBot 修改数据库设置；
6. 合成/脱敏参数集的 owner、版本、保留期和敏感等级；
7. 5C 的超时、锁等待、成本、并发、行数和字节上限；
8. 验证报告持久化集合、保留期、加密和访问控制；
9. 后续 HTTP 入口的认证、授权和审计主体方案。

