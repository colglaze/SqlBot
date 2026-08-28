# REQ-20260827-01：SQL AST 与安全门禁

- 状态：`completed`
- 创建日期：2026-08-27
- V2 复核日期：2026-08-28
- 来源：用户要求在 Phase 2G 与 V2 候选输入对齐完成后复核并实施 Phase 4
- 前置需求：[REQ-20260828-01](REQ-20260828-01-v2-candidate-generation-input.md)
- 受业务决策约束：[BIZ-20260828-01](../decisions/BIZ-20260828-01-v2-candidate-authority-boundary.md)
- 技术方案：[DEV-20260827-01](../architecture/DEV-20260827-01-sql-ast-safety-gate.md)

## 1. 背景与 V2 复核结论

Phase 2G 已产出版本化项目上下文、受治理元数据快照和确定性授权解析报告；V2 候选生成已产出带完整
审计引用和自哈希的 `SqlTemplateCandidateV2 2.0.0`。模型的 SQL、`declaredObjects`、
`declaredUsageCoverage` 和 Prompt 约束仍是不可信声明，Phase 4 必须从 AST 独立重算。

2026-08-28 复核废止原设计的三项 V1 输入假设：

1. 不再接受 `FactBindingRequest 1.0.0 + SqlServerBindingContext`；
2. 不再建立与 `GovernedMetadataSnapshot` 重复的 `SqlServerValidationMetadata`；
3. 不再使用 V1 `allowedObjects/usageCoverage` 命名或把候选 declarations 当作白名单/coverage 证明。

本阶段“passed”只表示满足本需求定义的离线静态门禁，不表示 SQL Server 会接受、业务结果正确、查询
性能可接受、候选已批准或可执行。

## 2. 目标

- 固定 SQL Server parser、方言、精确版本和 parser-neutral adapter 边界；
- 在 parser 前重算完整 Phase 2G 与 V2 candidate 引用/哈希闭包；
- 权威判定候选是否只有一条非空只读 `SELECT`，拒绝未知或越界结构；
- 从 AST 重新提取参数、物理对象和基础列，与 V2 逻辑要求、显式 grant 和同一批准快照逐项核对；
- 禁止临时对象、表变量、`SELECT INTO`、外部源、动态 SQL、跨数据库、hint 和集合运算；
- 重新证明唯一 `fact_value` 投影依赖已授权结果来源，并生成稳定 condition usage coverage；
- 输出逐项可审计且始终 `executable=false` 的报告；
- 全部测试离线完成，不连接、准备、解释或执行 SQL Server。

## 3. 可信输入

`ValidateSqlCandidateRequestV2 1.0.0` 必须携带：

```text
schemaVersion = 1.0.0
generationRequest   GenerateSqlCandidateRequestV2 1.0.0 完整载荷
candidate           SqlTemplateCandidateV2 2.0.0 完整载荷
```

校验器在 parser 前必须：

1. 从 `generationRequest.resolutionRequest` 重算 Phase 2F/2G；
2. 要求重算 report 与 `generationRequest.resolutionReport` canonical 完全一致且为
   `metadataResolved`；
3. 重算 candidate `contentSha256`、generation input SHA-256、resolution report SHA-256 与所有
   rule/request/fact/project/context/snapshot refs；
4. 要求候选仍为 `candidate / executable=false / pending / sqlserver`；
5. 原始 V2 请求不得含 blocking uncertainty；
6. 从同一 `GovernedMetadataSnapshot` 取得完整离线 relation/column schema，从 resolution report 取得
   精确授权列、结果来源和 join 端点。

任一不一致返回 `blocked` 且不得调用 parser。不能只传 ID、读取“最新”版本、查询 catalog 或选择
近似上下文。

## 4. 范围内

- 严格 camelCase、`extra=forbid` 的 V2 校验请求、parser-neutral inspection 和报告契约；
- SQLGlot `tsql` 完整 parse、作用域、对象/列 qualification 和复杂度证据；
- 单语句、只读、参数、对象、列、临时/外部源、结果和 coverage 独立门禁；
- AST 实际 relation 与候选 `declaredObjects` 差异报告；
- AST 参数与事实/候选参数、V2 filter/time/entity 参数语义交叉校验；
- AST 基础列与 Phase 2G 明确授权列、join 端点及完整快照三方交叉校验；
- `POST /api/v1/sql-candidates/v2/validate-static` 纯计算入口；
- 真实 parser 合成攻击 fixture、应用固定 inspector 和离线 API 回归。

## 5. 范围外

- 连接任何 SQL Server、读取 catalog、prepare/compile/execute、`EXPLAIN`、执行计划或试跑；
- 调用在线模型、重新生成 Prompt、修改 SQL 或创建候选 revision；
- 证明结果行数、标量唯一性、性能或 RuleReader operator/`NOT`/空值/时间边界业务语义正确；
- MongoDB 写入、候选持久化、批准、驳回、发布或审批状态迁移；
- 本地参考工作簿读取或把候选证据提升为 grant/metadata；
- 临时表、跨数据库、链接服务器、同义词、用户定义函数、表值函数和其他方言；
- 清除、降级、补写或隐藏 blocking uncertainty。

## 6. 权威静态门禁

### 6.1 完整解析和只读结构

- 完整 parse 列表必须恰好包含一个非空 AST；空槽、前后空语句、第二条语句和解析恢复均阻断；
- 根节点必须是普通 `SELECT`；拒绝 DML/DDL、`MERGE`、`EXEC`、动态 SQL、事务、会话设置、声明、
  `SELECT INTO`、`OUTPUT`、集合运算、递归 CTE、查询/表 hint 和未知安全结构；
- 固定复杂度上限：SQL 字符 100000、AST 节点 2000、深度 32、CTE 32、join 32、物理源 100；
- parser 能解析不等于允许；未知 source/function/command fail closed。

### 6.2 命名参数

- 唯一允许 `:name`，名称匹配 `[A-Za-z_][A-Za-z0-9_]*`；拒绝 `@name`、`?`、`$1`、插值、注释/
  字符串伪参数；
- AST 实际参数集合必须与 candidate parameters 和 V2 fact parameters 精确相等；重复使用同一命名参数
  可接受并记录全部位置；
- 参数只能处于 `WHERE` 或 `JOIN ... ON` 的标量值表达式，不能充当 identifier、函数名、排序、TOP
  或类型；
- 每个参数必须被 V2 entity key、parameter filter 或 parameter time boundary 至少一处权威逻辑要求
  引用；entity key 参数还必须命中 Phase 2G `resolvedEntityKeys`。

### 6.3 物理对象与列

- 使用 scope-aware 解析区分 CTE/派生表与物理源；每个物理源必须精确写成 `schema.relation`，拒绝未
  限定、三/四段、跨库、动态和函数型源；
- AST 实际 relation 必须同时存在于完整批准快照和 resolution report 的已解析 relation 闭包；并与
  candidate `declaredObjects` 精确一致，但 declaration 本身不授予权限；
- 拒绝 `*`、`alias.*` 和隐式星号；每个基础列必须可唯一 qualification；
- AST 基础列必须存在于完整快照，并属于 resolution report `resolvedBindings`、`resultSource` 或
  `authorizedJoins` 端点形成的精确授权列集合。快照中的未授权列仍禁止使用；
- 标识符比较只服从快照明确的 `sensitive | insensitive`，不得使用本机/parser 默认 collation。

### 6.4 临时对象、外部源和函数

- 禁止 `#`/`##`、表变量、表值参数、`SELECT INTO` 和任何临时对象创建；
- 禁止 `OPENROWSET`、`OPENQUERY`、`OPENDATASOURCE`、外部数据源、用户定义函数、表值函数、动态
  SQL 和未知函数型 source；
- 只允许 gate policy 明确列出的内建标量/聚合表达式节点；不得靠配置临时放宽。

### 6.5 结果与 condition usage coverage

- 顶层必须恰好一个显式投影，alias 精确为 `fact_value`；拒绝常量且无授权事实来源的投影；
- 投影的基础列依赖必须包含 report `resultSource.physicalColumns` 的授权来源，并全部通过列门禁；
- candidate `declaredUsageCoverage` 必须仍与 V2 `usages.conditionId` 精确一致，但只作差异输入；
- 应用按“候选 fact ref 精确一致 + 唯一已验证 fact_value 投影 + 输入 stable usage”生成 coverage entries；
- coverage 不冒充 operator、expressionSide、`NOT`、null policy 或时间边界语义证明。

## 7. 报告与问题

`SqlStaticValidationReportV2 1.0.0` 必须包含：

- `gateVersion=sqlserver-ast-safety-v1`、`status=passed|blocked`、固定 `executable=false`；
- candidate content hash、原始 SQL UTF-8 hash、generation input/report/context/snapshot hashes；
- parser name/exact version/dialect；
- 每个独立 gate 的稳定 issue；
- AST statement/root/complexity、实际 objects/columns/placeholders/result columns；
- 重算后的 usage coverage entries。

稳定 issue 至少覆盖：

```text
REFERENCE_MISMATCH / CANDIDATE_HASH_MISMATCH / RESOLUTION_NOT_READY
SQL_PARSE_ERROR / SQL_STATEMENT_COUNT / SQL_COMPLEXITY_LIMIT
SQL_ROOT_NOT_SELECT / SQL_FORBIDDEN_NODE / SQL_SET_OPERATION / SQL_HINT_FORBIDDEN
SQL_TEMP_OBJECT / SQL_SELECT_INTO / SQL_EXTERNAL_SOURCE / SQL_FUNCTION_FORBIDDEN
SQL_SCHEMA_REQUIRED / SQL_CROSS_DATABASE / SQL_OBJECT_NOT_ALLOWED / SQL_OBJECT_CLAIM_MISMATCH
SQL_JOIN_NOT_ALLOWED
SQL_STAR_FORBIDDEN / SQL_COLUMN_UNKNOWN / SQL_COLUMN_AMBIGUOUS / SQL_COLUMN_NOT_ALLOWED
SQL_PARAMETER_SYNTAX / SQL_PARAMETER_MISSING / SQL_PARAMETER_UNDECLARED
SQL_PARAMETER_POSITION / SQL_PARAMETER_LOGIC_UNBOUND / SQL_PARAMETER_ENTITY_KEY_MISSING
SQL_RESULT_SHAPE / SQL_RESULT_SOURCE_UNPROVEN
SQL_USAGE_COVERAGE_MISMATCH / SQL_USAGE_SOURCE_UNPROVEN
```

issue 按 `gateOrder + code + fieldPath + normalizedIdentifier` 稳定排序；message 不回显 SQL 片段、parser
异常或敏感元数据。

## 8. API 行为

- 通过：200 + `passed`；
- 合法但阻断：200 + `blocked`；
- wire schema/版本错误：422；
- 未知内部错误：500 通用安全错误，不泄露 SQL/快照/parser 异常。

入口只消费请求体和进程内静态 inspector，不读取 MongoDB、文件 catalog、SQL Server 或在线模型，不
持久化报告，不改变 candidate，不提供 execute/prepare/explain 参数。

## 9. 验收标准

1. SQLGlot 版本锁定且只存在于 infrastructure adapter；domain/application 不导入 parser AST 类型。
2. parser 前完整重算 Phase 2G、candidate hash 和 refs；篡改路径 inspector 调用次数为 0。
3. 单条合法参数化只读 `SELECT` 产生完整不可执行报告；空/多语句、DML/DDL/EXEC/INTO/hint/set op/
   解析恢复均阻断。
4. 只有合法位置的 `:name` 可通过；参数集合和 V2 逻辑引用缺失/额外/不一致均阻断。
5. AST 真实对象/基础列按 scope 和 qualification 重算；未限定/跨库/CTE 混淆/星号/未知/歧义/
   快照存在但未授权列/外部源均阻断。
6. 临时表、表变量、表值参数、`SELECT INTO` 和函数型 source 均阻断。
7. 结果只允许一个有授权来源的 `fact_value`；usage entries 精确覆盖 stable condition IDs，候选声明
   缺失/额外不能通过。
8. 真实 SQLGlot fixture 覆盖正常、边界、空值、`NOT`、时间边界、CTE、join、大小写和常见绕过；
   完全离线，不调用模型或数据库。
9. README、索引、ROADMAP、PROG、Ruff、format、pytest 和 `git diff --check` 全部通过。
10. 通过后 candidate 与 report 仍 `executable=false`，没有批准、持久化、SQL 改写或执行路径。

## 10. 当前状态

V2 输入假设已于 2026-08-28 复核并完成实现。SQLGlot 锁定为 `30.17.0`，纯计算 API、真实 parser
攻击 fixture、应用引用/授权门禁和合成脱敏回归均已交付。通过与阻断报告固定不可执行；实现期间未
连接或执行 SQL Server、未调用在线模型、未读取本地参考工作簿，也未改变候选审批状态。
