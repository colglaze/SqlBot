# REQ-20260905-01：受限 SQL Server 验证

- 状态：`in_progress`（Phase 5A 已实现并通过离线验收；Phase 5B/5C 未实施）
- 创建日期：2026-09-05
- 来源：用户要求编写下一步详细计划书
- 前置需求：[REQ-20260827-01](REQ-20260827-01-sql-ast-safety-gate.md)
- 受业务决策约束：[BIZ-20260905-01](../decisions/BIZ-20260905-01-restricted-sqlserver-validation-boundary.md)
- 技术方案：[DEV-20260905-01](../architecture/DEV-20260905-01-restricted-sqlserver-validation.md)

## 1. 背景

当前 V2 链路已完成完整事实交接、项目授权与批准元数据快照解析、候选生成及 SQLGlot 静态安全门禁。
`SqlStaticValidationReportV2.status=passed` 能证明候选满足当前离线 AST 策略，但不能证明：

- 目标 SQL Server 能解析并绑定该参数化查询；
- 线上对象结构仍与批准快照一致；
- 当前账号确实只有精确对象的只读权限；
- 返回列的实际 SQL Server 类型、空值性和基数符合事实契约；
- 优化器估算成本可接受；
- 查询能在有界时间和资源内完成。

Phase 5 用真实但受控的 SQL Server 验证补足这些证据。它仍处于人工审核和发布之前，不能把候选转换
为可执行模板或生产查询。

## 2. 目标

1. 在任何数据库访问前重新证明 Phase 2G、候选和 Phase 4 报告的完整引用闭包。
2. 用预配置的非生产 profile 建立加密、最小权限、只读意图且资源受限的 SQL Server session。
3. 证明当前账号对所有授权对象可读且没有候选可利用的写入、控制或模拟能力。
4. 只读取精确授权对象的 catalog，检测批准快照与实时结构的漂移，不从实时结构新增授权。
5. 将 `:name` 占位符确定性转换为驱动参数，保持原候选 SQL 不变并记录派生关系。
6. 先验证编译和首个结果集形状，再可选验证估算计划，最后才允许有界非生产试跑。
7. 产出稳定、可重算、无业务结果泄漏且始终不可执行的验证报告。
8. 在超时、锁等待、取消失败、连接中断和结果超限时 fail closed，并释放所有资源。
9. 通过固定替身完成基础回归；真实数据库测试只使用隔离实例和合成数据。

## 3. 成功标准

Phase 5 完成后，调用方可以回答“该候选在某个精确验证 profile、某个精确批准快照和某组受控参数下，
通过了哪一级验证、使用了哪些限制、得到了哪些中性证据”。调用方仍不能据此回答“该候选已批准或可
生产执行”。

## 4. 信任模型

### 4.1 可作为权威输入的内容

- 完整 `ValidateSqlCandidateRequestV2` 及其可重算的 Phase 2G 输入；
- 应用重算得到的 `SqlStaticValidationReportV2`；
- 配置中由运维预置的 validation profile；
- SQL Server 在当前加密 session 中返回的权限、catalog、描述和计划证据；
- 应用定义的固定 validation policy、binder 和类型兼容矩阵版本。

### 4.2 不可信或只作候选证据的内容

- 模型输出及 candidate declarations；
- 调用方携带的 `passed`、权限、对象、类型或环境声明；
- 飞书文档、私有参考 bundle、工作簿、视图 SQL 和人工备注；
- `ApplicationIntent=ReadOnly`、配置中的 `read_only=true` 或用户名本身；
- parser、driver 或 SQL Server 的原始错误文本；
- 实时 catalog 中没有进入已批准快照和项目 grant 的额外对象或列。

## 5. 阶段与范围

### 5.1 Phase 5A：连接、漂移和结果描述

范围内：

- 新的严格请求与报告契约；
- 独立 SQL Server validation port；
- validation profile 与启用配置；
- Phase 2G/4 完整重算；
- 连接、目标身份、TLS、权限和 session policy 证明；
- 只读 catalog 漂移检查；
- 参数类型校验与确定性 binder；
- `sp_describe_first_result_set` 结果描述；
- 本地 CLI；
- 固定 adapter 替身、故障注入和可选隔离 SQL Server 集成测试。

范围外：

- candidate 数据查询；
- 估算或实际执行计划；
- HTTP 在线验证入口；
- 报告持久化、人工审核、批准和发布。

### 5.2 Phase 5B：估算执行计划

只有 Phase 5A 同输入、同 target、同 policy 为 `passed` 时才允许：

- 获取 estimated XML Showplan；
- 安全解析并提取运算符、估算行数、估算成本和对象集合；
- 按 profile 限制成本、计划大小、运算符和对象漂移；
- 输出计划摘要和 plan SHA-256，不保存原始 XML。

禁止实际执行候选、获取 actual plan 或开启 `STATISTICS XML`。

### 5.3 Phase 5C：有界非生产试跑

只有 Phase 5A/5B 同一闭包都通过、用户当次明确授权且参数集被标记为合成或批准脱敏时才允许：

- 用位置参数执行原候选的确定性派生语句；
- 验证单列 `fact_value`、标量基数、类型、空值策略、超时和结果大小；
- 记录中性计数和时间证据；
- 在所有路径取消、回滚、关闭并销毁 session。

首版禁止生产、真实业务结果返回、批量运行、定时任务和并行执行。

## 6. 输入契约

计划新增 `ValidateSqlServerRequestV2 1.0.0`：

```text
schemaVersion             = 1.0.0
mode                      = describeOnly | estimatedPlan | boundedExecution
staticValidationRequest   = ValidateSqlCandidateRequestV2 完整载荷
staticValidationReport    = SqlStaticValidationReportV2 完整载荷
validationProfileId       = 配置中存在的稳定 ID
validationCase
  caseId                  = 稳定、非敏感 ID
  dataClassification      = synthetic | maskedNonProduction
  parameterBindings[]
    name
    dataType
    value                  = 仅在调用内存存在
    source                 = validationCase.<caseId>
  requestedLimits         = 可选，仅允许比 profile 更严格
```

约束：

1. `extra=forbid`、camelCase、禁止 snake_case fallback。
2. `validationProfileId` 只能选择本机/部署配置中的 profile，不能在请求中提供 host、database、username、
   password、ODBC 参数或任意连接字符串片段。
3. 参数名称集合必须与 candidate、事实声明和 Phase 4 AST 占位符集合精确一致。
4. 参数数据类型必须与事实参数一致；`unknown`、`list` 及不支持的值形态在连接前阻断。
5. 缺失必填参数、额外参数、重复名称、超长字符串、非有限数值、无时区策略的时间值均阻断。
6. `requestedLimits` 只能收紧，不能放宽 profile 上限。
7. `boundedExecution` 必须提供合成或批准脱敏参数分类；其他模式可以只提供用于编译的安全哨兵值。

## 7. 数据库访问前门禁

数据库 adapter 调用次数必须在以下任一条件出现时为 0：

1. Phase 2G 重算不是无阻断的 `metadataResolved`；
2. candidate 哈希、generation input、context、snapshot 或 resolution report 引用不一致；
3. Phase 4 重算不是 `passed`，或与携带的 static report canonical 不一致；
4. candidate 不是 `candidate / executable=false / pending / sqlserver`；
5. 存在 blocking uncertainty；
6. profile 不存在、未启用、环境为 production、TLS 策略不安全或资源限制非法；
7. mode 未在 profile 中启用；
8. 参数集合、类型、分类或值策略不满足要求。

## 8. validation profile 要求

profile 至少包含：

```text
profileId / enabled / environmentClass
server and database secret references
authMode / credential secret references
odbcDriver / encrypt / trustServerCertificate
applicationIntent / applicationName
connectTimeoutSeconds / commandTimeoutSeconds / lockTimeoutMilliseconds
queryGovernorCostLimit / maxEstimatedCost / maxEstimatedRows
maxRows / maxResultBytes / maxPlanBytes / maxConcurrentRuns
allowedModes / supportedServerMajorVersions
parameterValueHmacSecret reference
```

固定安全规则：

- `enabled=false` 为默认值；
- `environmentClass=production` 永远拒绝；
- `encrypt=true`、`trustServerCertificate=false`、`applicationIntent=ReadOnly`；
- 连接/命令/锁超时必须大于 0；
- `boundedExecution` 的 query governor、行数、字节和并发上限必须大于 0；
- `requestedLimits` 与 profile 取更严格值；
- 安全摘要只输出 profile ID、环境分类、启用模式和限制，不输出目标与秘密。

## 9. 连接与权限证明

每个运行使用一个新 session。适配器必须：

1. 构建内部连接字符串，拒绝未知关键字，关闭 pooling 和 MARS；
2. 设置应用名和只读意图，验证 TLS 策略；
3. 读取 SQL Server 产品主版本、engine edition、数据库兼容级别和 collation 分类；
4. 证明当前数据库与 profile 的预期目标一致，报告只保存 profile ID 和安全身份指纹；
5. 检查 server/database role 和精确对象的有效权限；
6. 要求所有实际对象具备 `SELECT`；
7. 检测 `INSERT`、`UPDATE`、`DELETE`、`ALTER`、`CONTROL`、`TAKE OWNERSHIP`、`IMPERSONATE`、
   `db_owner`、`db_datawriter`、`sysadmin` 等写入或越权能力，命中即阻断；
8. 为 5B 单独检查 `SHOWPLAN`，不得以提高账号角色规避权限缺失；
9. 设置固定 session policy 并回读关键设置；任何设置失败均不得继续；
10. 结束时关闭 cursor、回滚 transaction、关闭 connection，异常路径相同。

权限证明只说明当前 session 的有效权限。账号外部轮换、代理身份或未来权限变化会使历史报告失效，不能
复用旧报告跳过新运行。

## 10. 元数据快照漂移门禁

只查询 Phase 4 实际对象和 Phase 2G 授权闭包对应的 catalog。对每个 relation 检查：

- schema/relation 存在且对象类型符合批准快照；
- 所有批准列存在，名称大小写按 snapshot policy 比较；
- SQL 类型、长度、precision、scale、nullable、computed 状态与批准快照兼容；
- 关系定义或 `modify_date` 发生变化时至少告警；若快照包含定义哈希则必须精确匹配；
- 实时出现额外列时不授予使用权；若批准快照声明完整关系形状，则额外列也作为 drift 阻断；
- 缺列、改名、类型收窄/变更、空值性放宽、对象类型变化或关系不可见均阻断。

catalog 结果只用于“证明当前目标仍匹配批准版本”，不能自动生成新 snapshot、grant、映射或 candidate。

## 11. 参数绑定与类型策略

### 11.1 binder

- binder 必须依据 parser/token 证据处理占位符，禁止对 SQL 做正则全局替换；
- SQL 字符串、注释、标识符或转义内容中的冒号不得被替换；
- 重复参数保持每个实际位置，并按稳定顺序生成 ODBC bindings；
- 生成 `BoundSql` 时记录 binder 版本、原 SQL SHA-256、派生 SQL SHA-256、参数名称顺序和数量；
- 任何无法唯一绑定的 token 阻断，不能回退为字符串拼接。

### 11.2 类型矩阵

实现前必须以独立 policy 表固定 V2 类型到 SQL Server 类型族的允许关系。首版原则：

| V2 类型 | 允许 SQL Server 类型族 | 说明 |
| --- | --- | --- |
| `string` / `enum` | `char`、`varchar`、`nchar`、`nvarchar` | 长度必须有上限；deprecated LOB 默认阻断 |
| `integer` | `tinyint`、`smallint`、`int`、`bigint` | 值必须落在目标范围内 |
| `number` | `decimal`、`numeric`、`float`、`real` | 非有限浮点数阻断；记录精度风险 |
| `money` | `decimal`、`numeric`、`money`、`smallmoney` | 默认不接受 float/real |
| `boolean` | `bit` | 只接受布尔或明确的 0/1 规范化 |
| `date` | `date` | 不隐式截断 datetime |
| `datetime` | `datetime2`、`datetime`、`smalldatetime` | `datetimeoffset` 需明确时区决策 |
| `list` | 不支持 | 首版无表值参数与临时表 |
| `unknown` | 不支持 | fail closed |

precision、scale、长度、collation、时区和 nullability 的兼容规则必须版本化并有边界测试。

## 12. Phase 5A 描述门禁

使用驱动参数调用 `sp_describe_first_result_set`，将派生 T-SQL batch 与参数声明作为值绑定传入。不得把
candidate 或参数值直接拼接到系统过程调用文本。

通过必须同时满足：

1. SQL Server 能静态分析该 batch；
2. 恰好一个结果集、一个结果列；
3. 列名精确为 `fact_value`；
4. SQL Server 类型与 V2 结果类型矩阵兼容；
5. nullability 不比契约更宽；无法确定且契约要求 non-null 时阻断；
6. 来源 server/database/schema/relation/column 不越过 Phase 2G/4 精确对象闭包；
7. 不出现隐藏列、browse 附加列或动态未知结果；
8. 描述调用在命令超时内完成，输出行数和字节数受固定上限控制。

系统过程返回的底层错误、对象名和 SQL 不直接返回；映射成稳定 issue 和安全 message。

## 13. Phase 5B 估算计划门禁

Phase 5B 采用 `SET SHOWPLAN_XML ON` 获取 compile-time plan；不得使用会执行查询的 actual plan 或
`STATISTICS XML`。SHOWPLAN 开关必须在独立 batch 中设置，并在 finally 中关闭或直接销毁 connection。

计划验证至少检查：

- XML 字节数不超过上限，使用禁用 DTD/外部实体的安全解析器；
- plan 属于唯一候选 statement，statement hash 与本次派生 SQL 对应；
- plan 引用对象不超出 Phase 4 实际对象集合；
- 无远程查询、外部数据源、写入、DDL、游标、动态 SQL 或未知高风险运算符；
- 估算总成本、估算最大行数、内存授予和并行度不超过 profile policy；
- 缺少统计信息、隐式转换、扫描或高基数估算可以按 policy 阻断或告警；
- 只保存中性摘要与原始 plan SHA-256，不保存 plan XML。

估算成本是相对指标，阈值必须按验证环境校准，不能把成本值解释为实际秒数。

## 14. Phase 5C 有界试跑门禁

执行前再次确认 target、输入、static report、5A/5B report 和参数指纹均未变化。运行必须：

1. 使用 profile 中更严格的 connection、command 和 lock timeout；
2. 设置非零 query governor cost limit；
3. 使用 `READ COMMITTED`，只读取数据库现有 `READ_COMMITTED_SNAPSHOT` 状态，不修改数据库选项；
4. 在显式 transaction 中执行并在 finally 回滚；
5. 使用 driver 参数绑定，不在 SQL 中注入 `TOP`、hint、`NOLOCK` 或其他文本；
6. 标量结果最多读取 2 行以检测基数违规；全局 max rows 仍作为防御上限；
7. 流式计算结果字节数，到达上限立即取消并关闭连接；
8. 验证一列、列名、运行时类型、空值策略和标量基数；
9. 不返回原始值；报告只记录 `rowCount`、`nullCount`、`resultBytes`、类型族和持续时间；
10. 超时后调用 cancel；取消失败时强制关闭 connection，并将结果标为 `inconclusive`；
11. 禁止自动重试 candidate 执行，避免重复负载；连接前失败可按明确 policy 有界重试。

## 15. 输出契约

计划新增 `SqlServerValidationReportV2 1.0.0`：

```text
schemaVersion / validationPolicyVersion / mode
status = passed | blocked | inconclusive
executable = false
validationRunId / startedAt / completedAt / durationMs
candidateRef / staticValidationRef / contextRef / snapshotRef
targetRef
  profileId / environmentClass / serverVersionFamily / engineEdition
  compatibilityLevel / targetIdentityFingerprint
driverRef
  adapterName / adapterVersion / odbcDriver / parameterStyle
boundSqlRef
  binderVersion / sourceSqlSha256 / boundSqlSha256 / orderedParameterNames
parameterEvidence[]
  name / dataType / source / dataClassification / valueHmacSha256
connectionEvidence
permissionEvidence
snapshotDriftEvidence
describeEvidence
planEvidence?       Phase 5B+
executionEvidence?  Phase 5C
issues[] / warnings[]
reportSha256
```

报告不得包含 SQL、Prompt、参数值、result value、连接串、host、database、username、credential、完整
catalog、完整 Showplan、driver 原始异常或服务器消息。

issue 按 `stageOrder + code + fieldPath + safeIdentifier` 稳定排序。至少定义：

```text
INPUT_REFERENCE_MISMATCH / STATIC_REPORT_MISMATCH / STATIC_GATE_NOT_PASSED
VALIDATION_DISABLED / PROFILE_NOT_FOUND / MODE_NOT_ALLOWED / PRODUCTION_TARGET_FORBIDDEN
TLS_POLICY_INVALID / DRIVER_UNAVAILABLE / CONNECTION_UNAVAILABLE / TARGET_IDENTITY_MISMATCH
SERVER_VERSION_UNSUPPORTED / SESSION_POLICY_FAILED
SELECT_PERMISSION_MISSING / WRITE_PERMISSION_PRESENT / SHOWPLAN_PERMISSION_MISSING
SNAPSHOT_OBJECT_MISSING / SNAPSHOT_COLUMN_MISSING / SNAPSHOT_TYPE_DRIFT
SNAPSHOT_NULLABILITY_DRIFT / SNAPSHOT_DEFINITION_DRIFT / SNAPSHOT_EXTRA_COLUMN
PARAMETER_MISSING / PARAMETER_UNDECLARED / PARAMETER_TYPE_MISMATCH / PARAMETER_VALUE_REJECTED
PARAMETER_BIND_FAILED / DESCRIBE_REJECTED / RESULT_SHAPE_MISMATCH
RESULT_TYPE_MISMATCH / RESULT_NULLABILITY_MISMATCH / RESULT_SOURCE_DRIFT
PLAN_XML_INVALID / PLAN_SIZE_LIMIT / PLAN_OBJECT_DRIFT / PLAN_COST_LIMIT
PLAN_ESTIMATED_ROWS_LIMIT / PLAN_OPERATOR_FORBIDDEN
EXECUTION_NOT_AUTHORIZED / EXECUTION_TIMEOUT / EXECUTION_CANCEL_FAILED / EXECUTION_ERROR
RESULT_CARDINALITY / RESULT_SIZE_LIMIT / RESULT_RUNTIME_TYPE / RESULT_NULL_POLICY
```

## 16. CLI 行为

首版计划命令：

```powershell
uv run release-sql-bot validate-sqlserver `
  --input .codex_tmp/validation-request.json `
  --output .codex_tmp/validation-report.json
```

- CLI 从被 Git 忽略的文件读取严格 JSON；不接受 SQL、host、credential 等命令行参数；
- 退出码：`0=passed`、`2=blocked`、`3=inconclusive/unavailable`、`4=wire/config error`；
- stdout 只输出 run ID、mode、status、issue code、耗时和报告路径；
- 输出文件使用独占创建或显式 `--overwrite`，避免静默覆盖审计证据；
- 默认 `describeOnly`，5B/5C 需要 profile 明确开启和不同命令选项；
- 不自动启动 FastAPI，不调用 DeepSeek，不读取私有参考 bundle。

## 17. API 与错误映射

本需求首版不提供 HTTP 入口。未来入口需独立通过认证授权设计后，建议语义为：

- wire/schema 错误：422；
- profile/mode/静态前置冲突：409；
- 适配器或目标不可用：503；
- 已执行完整门禁的 `passed | blocked | inconclusive`：200；
- 未知错误：500 通用消息，不泄露目标、SQL、参数或驱动异常。

## 18. 测试矩阵

### 18.1 契约与应用测试

- camelCase、额外字段、版本、枚举和嵌套 snake_case 拒绝；
- 每类引用篡改均在 adapter 前阻断，adapter 调用次数为 0；
- static report 被伪造、陈旧或不完整时阻断；
- profile 不存在/关闭/production/TLS 不安全/限制为 0 时阻断；
- mode 升级和 requested limits 放宽被拒绝；
- 报告 hash、排序、时间和可重复字段稳定；
- 全部状态固定 `executable=false`。

### 18.2 binder 与类型测试

- 单参数、多参数、重复参数和参数顺序；
- 字符串/注释/标识符中的冒号不替换；
- Unicode、日期、decimal、bool、空值和边界值；
- NaN/Infinity、超长字符串、整数溢出、未知/list 类型拒绝；
- SQL Server precision/scale/length/nullability 全矩阵；
- source/bound SQL hash 和 binder version 稳定。

### 18.3 权限与漂移测试

- 缺 SELECT、存在对象级 DML、db_datawriter、db_owner、sysadmin、IMPERSONATE；
- 快照对象缺失、列缺失/新增、类型/长度/精度/空值性/定义变更；
- 实时额外对象或列不扩大授权；
- 大小写敏感与不敏感数据库；
- driver/TLS/version/compatibility level 不匹配。

### 18.4 描述、计划与执行测试

- 描述成功、无结果、多列、别名错误、类型错误、nullable 漂移、动态结果未知；
- SHOWPLAN 权限缺失、超大/畸形 XML、XXE/DTD、对象漂移、成本/行数/运算符超限；
- 0 行、1 行、2 行、NULL、超大值、类型不匹配；
- connection timeout、command timeout、lock timeout、cancel 成功/失败、网络中断；
- cursor/transaction/connection 在每个异常点都关闭；
- 日志、报告、异常和测试快照不含 SQL、参数值、连接目标或业务结果。

### 18.5 集成测试

- 基础回归使用固定 adapter/driver，不依赖真实数据库；
- 可选 gated CI 使用隔离 SQL Server、合成 schema 和最小权限账号；
- 集成 fixture 包含权限正确、权限过大、schema drift、阻塞锁和超时场景；
- 集成测试默认跳过，只有显式环境变量和隔离实例标记齐全时运行；
- 禁止指向 production、共享业务库或使用真实业务数据。

## 19. 非功能要求

- 同 profile 并发由 semaphore 限制；默认 1；
- 连接、描述、计划、执行分别记录耗时，不输出敏感值；
- 所有超时均有大于 0 的硬上限，不能由请求关闭；
- 运行取消后在限定时间内关闭 connection；
- 适配器不使用全局可变 connection；
- secret 只在连接构建边界解密，生命周期结束后不保留；
- 报告 JSON 可 canonicalize 并计算 `sha256:` 内容哈希；
- domain/application 不依赖 pyodbc 或 SQL Server SDK 类型。

## 20. 验收标准

### Phase 5A DoD

1. REQ/BIZ/DEV、README、索引、ROADMAP、PROG 同步。
2. 新契约严格、报告固定不可执行、所有引用可重算。
3. production 和不安全 TLS 在配置阶段拒绝；SQL Server 验证默认关闭。
4. static report 不是重算 `passed` 时 adapter 调用次数为 0。
5. 权限过大、SELECT 缺失和 snapshot drift 确定性阻断。
6. binder 不改原 candidate，参数全部由驱动绑定。
7. `sp_describe_first_result_set` 证明唯一 `fact_value`、类型和空值性。
8. CLI 不暴露任意 SQL/target/credential 参数，输出不泄露敏感内容。
9. 离线 Ruff、format、pytest、`git diff --check` 全部通过。
10. 可选隔离 SQL Server 冒烟有明确证据；未获授权时不运行也不影响基础 DoD。

### Phase 5B DoD

1. 只生成 estimated plan，不执行 candidate。
2. SHOWPLAN、计划大小、XML 安全、对象、成本、估算行数和运算符门禁均有测试。
3. 原始 Showplan 不写日志、报告或仓库。

### Phase 5C DoD

1. 仅非生产、合成/脱敏参数和当次明确授权可运行。
2. timeout、lock timeout、cost、row、bytes、concurrency 和 cancellation 全部生效。
3. 结果值不返回、不记录、不持久化；只输出中性证据。
4. 任何路径回滚并关闭 session，无自动执行重试。
5. 通过报告仍不可执行，人工审核和发布仍在 Phase 6。

## 21. 阻塞项

Phase 5B 实现前必须确认：SHOWPLAN 权限和成本/行数阈值。Phase 5C 实现前必须确认：合成/脱敏参数集、
资源上限和当次执行授权。缺少任一项时保持 `blocked`，不得用飞书或私有参考资料推断生产事实。

## 22. 实现状态

### Phase 5A（2026-09-06 完成，离线验收）

- 严格 `ValidateSqlServerRequestV2 1.0.0` / `SqlServerValidationReportV2 1.0.0` 契约、issue 稳定排序
  与 canonical `reportSha256` 已实现；全部报告固定 `executable=false`。
- 数据库前门禁：完整重算 Phase 2G/4、比对携带 static report canonical、校验 candidate 生命周期与
  blocking uncertainty、profile/mode/TLS/limits/参数集策略；所有防篡改路径 adapter 调用次数为 0
  （测试断言）。
- `sqlserver-token-binder-v1`：基于 SQLGlot tokenizer token span 的 `:name` → `@pN`/qmark 确定性派生；
  字符串、注释、括号标识符与 `::` 中的冒号不替换；重复参数稳定排序；binder 结果与 Phase 4 inspection
  占位符集合交叉校验；原候选字节不变。
- `sqlserver-type-policy-v1`：快照列类型解析、参数声明派生（禁止 LOB/无界类型）、实时列兼容矩阵和
  结果类型族矩阵。
- ODBC adapter（pyodbc `>=5.2,<5.3`）：固定安全关键字连接串（Encrypt=yes、TrustServerCertificate=no、
  ApplicationIntent=ReadOnly、Pooling=no、MARS_Connection=no）、固定 session policy 与 LOCK_TIMEOUT
  回读、HMAC 目标身份指纹、`fn_my_permissions`/角色/目录参数化探测、全参数绑定
  `sp_describe_first_result_set`、错误号到稳定 issue 映射；资源在所有异常路径回滚并关闭。
- CLI `validate-sqlserver`：无 SQL/host/credential 参数、输出独占创建、退出码 0/2/3/4、stdout 只含
  run ID/mode/status/issue code/耗时/路径。
- 验收证据见 [PROG-20260906](../progress/PROG-20260906.md)。真实 SQL Server 冒烟（DoD 第 10 条）按
  允许范围未执行，不影响其余 DoD。

### Phase 5B / 5C

未实施；对应 adapter 路径、配置与测试均未交付，配置仅允许 `describeOnly`。

