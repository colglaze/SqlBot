# DEV-20260827-01：SQL AST 与安全门禁设计

- 状态：`completed`
- 创建日期：2026-08-27
- V2 复核日期：2026-08-28
- 实现需求：[REQ-20260827-01](../requirements/REQ-20260827-01-sql-ast-safety-gate.md)
- 前置设计：[DEV-20260828-01](DEV-20260828-01-v2-candidate-generation-input.md)
- 受业务决策约束：[BIZ-20260828-01](../decisions/BIZ-20260828-01-v2-candidate-authority-boundary.md)

## 1. 设计结论与 V2 替换

Phase 4 继续选择 [SQLGlot](https://github.com/tobymao/sqlglot) `30.17.x` 官方
[`tsql` dialect](https://sqlglot.com/sqlglot/dialects/tsql.html)，依赖固定
`sqlglot>=30.17,<30.18`，锁文件保存精确补丁版本。SQLGlot minor 可能不兼容；升级必须提升 parser
fixture 基线并重跑完整安全回归。禁止导入或调用 `sqlglot.executor`。

V2 复核后的唯一输入链：

```text
GenerateSqlCandidateRequestV2
  -> ResolveMetadataRequestV2 + BindingResolutionReportV2
SqlTemplateCandidateV2 2.0.0
  -> contentSha256 + exact refs
GovernedMetadataSnapshot（来自同一 resolution request）
  -> 完整物理 schema，不授予权限
BindingResolutionReportV2
  -> 精确授权 fields/result/join columns
                 |
                 v
SqlStaticValidationReportV2（passed | blocked，永远不可执行）
```

历史 `SqlServerBindingContext`、`SqlServerValidationMetadata`、V1 `allowedObjects/usageCoverage` 全部退出
Phase 4 输入，不建立第二份物理元数据或丢字段转换。

首版固定：

```text
parserName     = sqlglot
parserVersion  = runtime exact 30.17.x
dialect        = tsql
gateVersion    = sqlserver-ast-safety-v1
parameterStyle = colon_named
```

## 2. 模块边界

```text
domain/sql_validation.py             V2 请求、parser-neutral inspection、报告与 issue
application/ports/sql_ast.py         SqlDialectInspector 协议
application/sql_validation.py        引用重算、授权门禁、coverage 与报告
infrastructure/sql/sqlglot_tsql.py   SQLGlot T-SQL parse/scope/qualification adapter
infrastructure/sql/__init__.py       只装配静态 inspector
api/app.py                            /api/v1/sql-candidates/v2/validate-static
tests/fakes.py                        应用编排固定 inspector
tests/unit/test_sqlglot_tsql.py       真实 parser 方言/攻击 fixture
tests/unit/test_sql_validation.py     V2 引用、授权、结果和 coverage
```

`domain/` 与 `application/` 不导入 SQLGlot、FastAPI、MongoDB/SQL Server driver 或网络 client。具体 AST
只存在 infrastructure adapter。Phase 4 不新增 executor、连接池、数据库配置或 SQL 改写器。

## 3. 领域请求与报告

### 3.1 `ValidateSqlCandidateRequestV2 1.0.0`

```text
schemaVersion = 1.0.0
generationRequest   GenerateSqlCandidateRequestV2
candidate           SqlTemplateCandidateV2
```

严格 camelCase、`extra=forbid`，嵌套禁止 snake_case fallback。请求携带生成时完整载荷，不通过 ID 读取
“最新”版本。

### 3.2 parser-neutral DTO

`SqlInspectionRequest`：

```text
sql / dialect=tsql / identifierCaseSensitivity
offlineSchema[]      完整 GovernedMetadataSnapshot relation/columns
gatePolicy           sqlserver-ast-safety-v1 与固定复杂度
```

`SqlInspection`：

```text
parserRef            name / exactVersion / dialect / gateVersion
statementCount / rootKind / nodeCount / maxDepth / cteCount / joinCount
physicalObjects[]    schemaName / relationName / sourceKind / expressionPath
joins[]              joinType / leftColumn / rightColumn / expressionPath
baseColumns[]        schemaName / relationName / columnName / expressionPath
placeholders[]       name / rawKind / expressionPath / enclosingClause
resultColumns[]      alias / expressionPath / sourceColumns[]
features             setOp / hint / into / temp / external / dynamic / functionSource
inspectionIssues[]
```

端口不返回 AST node、重写 SQL 或 qualifier 输出 SQL。

### 3.3 `SqlStaticValidationReportV2 1.0.0`

```text
schemaVersion / gateVersion / status=passed|blocked / executable=false
candidateRef
  contentSha256 / sqlTemplateSha256 / generationInputSha256 / resolutionReportSha256
  contextSha256 / snapshotSha256
parserRef
issues[]
inspection
  statement/root/complexity / physicalObjects / baseColumns / placeholders / resultColumns
usageCoverage[]
  conditionId / conditionPath / factCode / resultExpressionPath
```

报告只保存哈希和中性证据，不回显 SQL、Prompt、参数值、完整快照或 parser 异常。issue/report 排序稳定。

## 4. parser 前引用门禁

应用服务固定重算：

1. `resolve_metadata_v2(generationRequest.resolutionRequest)`；
2. 重算 report 与携带 `resolutionReport` 完整 canonical 等价且 `metadataResolved`；
3. candidate 自身 `contentSha256`；
4. candidate `generationInputSha256`、`resolutionRef.reportSha256`；
5. candidate rule/request/fact/project/context/snapshot refs；
6. candidate lifecycle 固定 `candidate/false/pending/sqlserver`；
7. 原 V2 blocking uncertainty 为零；
8. candidate 参数/result/declaration claims 与 generation request 仍一致。

失败输出 `REFERENCE_MISMATCH`、`CANDIDATE_HASH_MISMATCH` 或 `RESOLUTION_NOT_READY` 并立即返回，固定
inspector 替身调用次数为 0。

完整快照只用于 schema/qualification。允许关系来自 report resolved bindings 与 join endpoints；允许列
来自：

```text
resolvedBindings.physicalColumn
UNION resultSource.physicalColumns
UNION authorizedJoins.leftColumn/rightColumn
```

快照中存在但不在该集合的列不获授权。

## 5. SQLGlot adapter

### 5.1 完整 parse 和复杂度

使用：

```python
sqlglot.parse(sql, read="tsql", error_level=ErrorLevel.RAISE)
```

不丢弃 `None` 槽；只有列表长度 1 且唯一元素非空可继续。`SELECT 1;;`、`;SELECT 1`、仅分号和多
语句均阻断。解析错误统一 `SQL_PARSE_ERROR`，不传底层文本。

根必须是 `exp.Select`。限制：

```text
sqlCharacters 100000
astNodes 2000
maxDepth 32
cteCount 32
joinCount 32
physicalSources 100
```

### 5.2 禁用结构

遍历整棵 AST 拒绝 DML、DDL、MERGE、command/execute、事务、USE/SET/DECLARE、INTO/OUTPUT、set
operation、lock、query/table hint、递归 CTE、赋值式 SELECT、动态 SQL 和未知 statement/source。

临时对象同时检查 AST 属性和 object 名：`#`、`##`、`@table`、table-valued parameter、`Into`
一律阻断。函数型 source（OPENROWSET/OPENQUERY/OPENDATASOURCE/UDTF/TVF）一律阻断。

普通表达式函数使用 gate policy 明确的 SQLGlot 内建节点 allowlist；`Anonymous` 和用户定义函数默认
`SQL_FUNCTION_FORBIDDEN`。

### 5.3 scope-aware 对象

使用 SQLGlot `build_scope` 的 `selected_sources` 区分 CTE/派生表与物理 `Table`。普通物理对象必须：

- catalog/server 为空；
- db/schema 与 relation 均非空；
- 只有两段名；
- 按快照大小写策略唯一命中完整 snapshot；
- 唯一命中 Phase 2G 允许 relation；
- AST 实际集合与 candidate declarations 精确一致。

多表查询还必须从已 qualification 的 `JOIN ... ON` 等值谓词重算 join 类型和两端基础列；实际 join
集合必须与 Phase 2G `authorizedJoins` 精确一致，否则 `SQL_JOIN_NOT_ALLOWED`。分别获准的列不能自行
组合成新连接关系。

未限定、三/四段、动态、函数型或无法分类 source 均 fail closed。

### 5.4 星号与基础列

qualification 前先拒绝原始 AST 中 `Star` 和等价扩展。仅在 AST 副本上调用 SQLGlot
`qualify(..., dialect="tsql", schema=MappingSchema(...), validate_qualify_columns=True,
allow_partial_qualification=False)`。

schema map 只来自当前 `GovernedMetadataSnapshot`。unknown、ambiguous、partial qualification 均转为
安全 issue；不能退回字符串匹配。CTE/派生列必须递归追溯到基础物理列。adapter 报告每个基础列，应用
再同时核对 snapshot 存在性和 Phase 2G 精确授权列集合。

### 5.5 参数与位置

`:name` 在 `tsql` 下必须是命名 Placeholder；adapter 拒绝 `@name`、`?`、`$1` 和非精确 token。
每个 placeholder 沿祖先确认处于 WHERE 或 JOIN ON 标量表达式，而非 identifier/order/top/function/
type。字符串和注释中的文本不算参数。

应用层要求 AST 参数集合精确等于 candidate/fact 参数，并从 V2 parameter filter、time boundary 与
entity key 计算逻辑参数集合。entity key 参数必须额外命中 `resolvedEntityKeys`。

### 5.6 结果投影

顶层 `Select.expressions` 必须恰好一个显式 alias `fact_value`。adapter 返回该表达式追溯到的基础列
和参数；常量或 source columns 为空时 `SQL_RESULT_SOURCE_UNPROVEN`。

应用要求投影基础列至少包含 report `resultSource.physicalColumns`，且所有投影依赖列均已授权。对
compute aggregation 可依赖全部输入列；对 column/precomputed 必须依赖唯一 result column；exists
必须含对已授权物理 source 的存在性依赖。这里不证明运行时唯一行。

## 6. 应用门禁与 coverage

固定顺序：

1. wire/version；
2. Phase 2G/candidate 引用与 hash；
3. snapshot 和授权 schema 投影；
4. inspector parse/statement/complexity；
5. root/禁用节点/temp/external/function；
6. scope objects 与 declaration 差异；
7. star/qualification/base columns 与授权；
8. parameter syntax/position/set/logic/entity key；
9. result shape/source；
10. stable condition usage coverage；
11. 排序稳定的不可执行报告。

coverage entry 条件：

```text
input usage conditionId 唯一
AND candidate factRef 与 V2 fact 精确一致
AND 唯一 fact_value projection 通过对象/列/参数/result source 门禁
```

每项保存 input conditionPath/factCode 和同一 result expression path。candidate
`declaredUsageCoverage` 必须精确一致但不作为证据。报告不声称重新验证 operator、expressionSide、
`NOT`、null policy 或时间边界。

## 7. 稳定 issue 与 API

issue 至少包括 REQ 第 7 节代码，并按 `gateOrder + code + fieldPath + normalizedIdentifier` 排序。
adapter issue 只含安全 message 与结构路径，不含 SQL/parser 原文。

```text
POST /api/v1/sql-candidates/v2/validate-static
```

- 通过/阻断均 200；
- wire/版本错误 422；
- 未知错误 500 通用安全响应。

入口直接调用进程内 inspector；不读取 runtime candidate provider/repository，不装配 SQL Server 或网络
client，不持久化，不改变 candidate。

## 8. 测试策略

真实 SQLGlot adapter fixture：

- 合法单 relation、alias、CTE、join、重复参数、聚合、null predicate；
- 空/空槽/多语句、DML/DDL/MERGE/EXEC/USE/SET/DECLARE/INTO/OUTPUT/set op/hint；
- 未限定、三/四段、CTE 名混淆、OPENROWSET/TVF、`#`/`##`/`@table`；
- star、unknown/ambiguous/snapshot-only unauthorized column、大小写策略；
- `:name`、`@name`、`?`、`$1`、字符串/注释伪参数与非法位置；
- 结果缺失/多列/错 alias/常量、复杂度边界、Unicode/quoted identifiers。

应用/契约/API fixture：

- generation/report/context/snapshot/candidate hash 任一篡改，inspector 零调用；
- declared object/usage claim 差异；
- source/aggregate/exists 的 result source 与 stable condition coverage；
- `NOT`、null、time boundary 只验证不篡改语义；
- 报告重复稳定、始终不可执行、无 repository/provider/SQL client 调用。

应用编排可用固定 inspector；安全 fixture 必须运行真实 SQLGlot，不用 mock 替代。

## 9. 实施顺序和完成边界

1. 锁定 SQLGlot 并建立 V2 领域/端口；
2. 实现 adapter parse/feature/scope/qualification/parameter/result inspection；
3. 实现前置引用、授权、coverage 和报告；
4. 增加纯计算 API；
5. 完成契约、真实 adapter、应用和集成回归；
6. 同步 README、ROADMAP、索引与 PROG。

上述顺序已于 2026-08-28 完成。Phase 4 完成只允许进入 Phase 5 受限验证的独立规划；本阶段没有
连接 SQL Server，没有执行/准备/解释/修改候选 SQL，没有调用在线模型或读取工作簿，没有清除
blocking uncertainty，候选与报告始终不可执行。
