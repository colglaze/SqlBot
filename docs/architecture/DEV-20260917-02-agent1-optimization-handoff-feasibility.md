# DEV-20260917-02：Agent1 优化方案新交接可行性评估

- 状态：`completed`（仅可行性评估完成；下述契约扩展及新交接均未实施）。
- 日期：2026-09-17。
- 评估对象：[BIZ-20260917-03](../decisions/BIZ-20260917-03-optimization-plan-authority.md)。
- 关联需求：[REQ-20260917-01](../requirements/REQ-20260917-01-view-equivalent-sql.md)。
- 范围：只读核对 Agent1、SqlBot 代码与契约，固定私有来源身份，运行离线验证；不连接数据库，
  不发布新批次，不生成 SQL，不执行来源 Java/SQL，不修改 Agent1。

## 结论

方向可行，但尚不是可以直接交给当前脚本执行的完整实施规格。必须在 Agent1 新版本中展开
逻辑事实与条件树，不能由 SqlBot 补齐旧 18 条请求。基本算术、布尔组合、日期加法、优先级
求值和不可变持久化已有基础；完整交付引用、来源身份、集合量词和运行参数仍需设计。

“新交接到来之前阻断总 SQL”正确；新交接到来是必要条件，不是 SqlBot 立即支持总 SQL 的证明。

## 1. 必须修正或补齐的实施项

### F1：V3 集合名称与完整规则树的可读取性是两个问题

Agent1 `infrastructure/migrations.py` 定义 `rule_versions_v3`；
`infrastructure/v3_persistence.py` 将 V3 规则版本插入该集合。`rule_versions` 是 V1/V2 集合。
Agent1 `docs/PROG-20260907.md` 还记录了旧 V3 版本与 18 条 batch 的历史落库证据。因此，
在旧集合中没有发现该版本，不能推出 V3 版本未落库。本次未重读数据库，不断言当前记录存在。

更关键的是 `domain/rules/result_v3.py::RuleParseResultV3` 只带 `catalogRef`、`candidateRef`、
事实声明及案例，**不内嵌 stages 或完整 catalog**。把它写入任一集合，均不自动解决 SqlBot
完整规则树输入。必须保证同版本完整 candidate/catalog 可以按内容哈希回读，并与 result/batch
共同构成交付闭包；不能只交无法解析到载荷的摘要。

建议沿用 V3 集合并新增受控的完整交付读取契约。若集合字面名称必须是 `rule_versions`，应在
Agent1 明确新增迁移及版本分流，并升级 SqlBot reader；不能把 V3 文档直接塞进旧集合。
SqlBot 当前 `domain/rule_versions.py` 的 reader 契约只接受 1.0.0/2.0.0。

### F2：完整文件哈希与提取块哈希目前被契约要求相等

已核验固定私有 commit `2240e5bd18e36d17650896a10cc61e1c18e3daa0`、bundle digest
`6d403f1a110ea1699a72aec76f38be944583670f96767f5e3f44b2ac2e565160`；优化方案文件 SHA-256 为
`c049af189fc3689bac8e96408d9e7239a8c70b66bbcd15829e509c6d524b648f`。

旧提取器只截取报告规则块，长度 1402 字符，哈希为
`f285643e5b2bb2ec7b13861716407afda4252c2fbc81eb75a4b0bb3ba4b37c6d`，未包含 §5.2。
旧 sourceSha256 与文件不同有明确的提取块原因，不能据此称其等于 catalogDigest 或来源伪造。

Agent1 `RuleParseResultV3.validate_shape`、exporter 和持久化写前门禁都要求
`source.sourceSha256 == candidateRef.ruleBlockSha256`；ruleVersion 的来源前缀也绑定块哈希。
要执行“sourceSha256 等于优化方案文件”，需要版本化区分原始文件身份与实际解析输入身份，
并记录 §1.3、§5.1/§5.2 的选取坐标和提取器版本，同步修改上述校验。不能改一个 hash 值而
继续将 §1.3 的旧块送入解析。是否规范化行尾也须明确；本需求的文件 hash 应按固定源字节计算。

### F3：合并组不能用现有 scalar 或普通 all 节点冒充

FBR 3.0.0 的结果固定 `cardinality=scalar`、一个 `fact_value`；条件节点的 `all` 只表示
有限子条件的 AND，不表示遍历动态成员集合。当前没有成员作用域、量词或成员规则调用契约。

需新增版本化的组成员结果/关系契约和全员判定表达，并明确：成员唯一键、重复成员、空组、
缺成员/NULL、已释放成员、成员状态读取时点，以及成员事实如何绑定。不能把未知成员忽略后
判为全员满足。成员谓词应明确引用哪个阶段的结果，避免“本组最终放行依赖每个成员最终放行，
每个成员又依赖本组合并检查”的循环。空组口径与允许的成员终态应从既有来源裁决，不自行补值。

### F4：“命中即停”必须限定阶段，不能跳过合并组和 OA

现有 `validation_v3.py::evaluate_rule_structure_v3` 在 eligibility 首次 PASS 时只退出该阶段，
继续运行 postGates/exclusions；其它阶段命中则终止。这个区别必须进入交接的机器语义，不能
只靠执行器默认行为或一句“命中即停”。

应明确 stateGuards → prerequisites → eligibility（给定 R 顺序，首个命中）→ 合并组后置
检查 → OA 排除；前置/后置终态、无命中默认结果和未知值如何传播均须有案例。不得在 R3 等
规则初步 READY 时直接返回清单，否则后置约束失效。是否调整当前阶段行为需以完整方案为依据。

### F5：日期事实、运行参数与 derived 需要分别定义

现有表达式有 fact、literal、add/subtract/multiply/divide/coalesce/dateAdd；R3 分档可以展开
为 any/all/compare 与算术表达式，不需要新造 eligible 布尔。R8 日期等值也有表达基础。

但没有独立 parameter 表达式；FBR 参数是取数参数，不能自动变成规则树中的运行参数。
应明确截止日、上线日、统一求值日如何绑定到规则和 SQL，禁止把它们误当成待查询数据库事实。
固定求值时刻、业务时区和 date/datetime 转换必须在双方一致。

原始完工日期若不在取数阶段过滤，它的 FBR `timeRange=none` 可以合法存在；真正不合格的是
不透明“到期布尔 + none”且规则树也无日期计算。验收应检查完整日期表达与参数闭包，而非机械
要求所有日期事实 timeRange 非 none。

当前 V3 catalog 无 derivation 字段，BindableFactV3 也不接受 derived。BIZ 中“降为 derived”
不能当作现成可用路径。最小方案是将算术内联到条件树；如需要命名派生事实，须另扩展契约、
求值和依赖闭包，不能把 derivation 只留在描述里。

### F6：新版本发布入口和旧版本禁用尚未实现

Agent1 `scripts/persist_report_release_v3_delivery.py` 固定了旧 manifest hash、旧 ruleVersion、
18 条请求和 20 条案例；当前脚本会拒绝新交付。需新增精确绑定新交付身份的入口或受控 manifest
机制，保留旧身份审计及幂等/冲突校验，不通过删除信任锚或跳过验证解决。

当前规则与 batch 顺序 insert，跨文档不原子；规则成功、batch 失败靠幂等重试恢复。
完整交接必须定义可消费完成条件：tree/catalog/result/batch 均可回读且哈希、版本、依赖闭包
一致时才可使用；中途失败不能让消费者拿到半批材料。

“2026-09-05 批次不再用于本次生成”目前是文档裁决，不能声称运行时已经禁止旧批次。
SqlBot 当前生成链只重读 batch 和批准记录，未核对完整树，也未实现这一用途的版本停用门禁。
后续总 SQL 入口应明确选择新交付及用途，缺失新交付时阻断；不能回退旧批次。旧载荷保留不改写。

## 2. 业务拆分的可表达性与验收补充

| 要求 | 可行性 | 实施时必须补足 |
| --- | --- | --- |
| R1 申请类型、已完成节点计数 | 现有 filters/aggregation 能描述基本结构 | 计数实体、重复审批节点是否去重、无匹配为 0 与失败/未知的区别；逻辑关系不能全留给物理 JOIN 猜测 |
| R3 特殊产品与分档到款 | 现有算术及 all/any 可展开 | 每档端点、金额类型/精度、NULL、标志真但金额不足；不能只复用模型自己生成的正例 |
| R4 标志与截止日 | 现有条件树可比较 | 截止日运行绑定、日期包含边界及未知值；不把共享日期事实预过滤得无法服务其它规则 |
| R5/R6/R7 金额与合同分支 | 现有基本表达式可展开 | §5.1 金额粒度/组成、押金是否已包含、各支括号、精度、五支合同的独立与重叠用例 |
| R8 日期等值与例外支 | dateAdd + eq 有基础 | N-1/N/N+1、时间分量、时区、上线日、无主服务支；不能把 eq 改成 gte |
| 编码与报告有无 | 可在 catalog/规则树新版本修订 | 清除冲突须靠新来源裁决及案例；不能只删除 warning。一有一空属于“任一有则通过”，不是剩余待定分支 |
| 独立 D0–D4 | ruleSetId 模型允许独立身份 | 独立前提、树、版本、目录引用、batch、案例及单细胞分支；不得算入报告覆盖率 |
| 组成员全员满足 | 当前契约不足 | 见 F3；需契约升级及跨实体依赖验证 |

报告标志建议覆盖“有/无/NULL”完整 3×3 矩阵，再增加未知码和字段缺失；四组案例是最低
回归，不是完整真值表。D 规则树目前固定五阶段且每阶段至少一条，若原始数据没有某阶段，
应设计合法空阶段或独立阶段约束，不能填造永不命中的业务规则来满足 Schema。

业务 ruleVersion、catalogVersion/digest 与 Schema 版本分别管理：只改条件内容不一定改
Schema；新增集合结果、运行参数、交付引用或来源字段则需双方版本化协同，禁止把扩展字段
静默塞进冻结的 3.0.0 契约。

## 3. 可实施顺序

1. Agent1 先冻结来源双重身份、完整交付闭包、运行参数与集合/量词契约；新 REQ/DEV 明确
   集合选择、阶段行为及新 manifest 入口，不修改旧批次。
2. 从指定方案形成完整规则树与新事实目录，执行独立人工定义的边界/反例测试，导出新 FBR；
   全部引用、参数、案例闭包通过后再持久化并完整回读。
3. metadataReview 按精确新版本批准物理映射；旧两列测试批准不能继承为完整清单授权。
4. SqlBot 接入完整交付和总 SQL 组合契约，扩展生成与 AST/语义检查，以 Agent1 离线求值
   结果作为差分基准。不能只升级读取规则集合后直接调用现有单事实生成器。

## 4. 本次验证证据与限制

- 私有固定 commit 核验通过、工作区无改动；`verify_bundle.py` 通过：17 来源、2 派生文件，
  digest 与登记一致。未输出原始内部对象、字段或 SQL。
- Agent1 离线定向回归：
  `.venv/Scripts/python.exe -m pytest tests/unit/test_rule_domain_v3.py tests/unit/test_rule_result_contract_v3.py tests/unit/test_fact_binding_contract_v3.py tests/unit/test_v3_persistence.py tests/unit/test_persist_report_release_v3_delivery.py -q`
  → **55 passed in 2.58s**。
- 合成探针验证 multiply 表达式能进入 V3 条件并求值；低/高金额分别为 WAITING_CONDITIONS/READY。
  这是通用表达能力证明，不是 R3 真实分档公式验收。
- 结构探针确认 result 不内嵌 stages/catalog、无成员量词、FBR 固定 scalar；旧提取块不含 §5.2，
  块 hash 与固定文件 hash 不同。
- 未运行 Java、未独立执行完整新 R/D 规则、未验证当前数据库记录、未执行 SQL；本评估不声称
  所有方案公式已逐条通过业务验收。现有测试通过不表示这些新能力已经实现。
