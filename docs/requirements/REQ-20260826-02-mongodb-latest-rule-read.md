# REQ-20260826-02：MongoDB 最新规则读取

- 状态：`superseded`
- 替代需求：[REQ-20260826-03](REQ-20260826-03-rulereader-latest-rule-integration.md)
- 创建日期：2026-08-26
- 来源：用户要求借鉴 DataEase SQLBot，并先完成 MongoDB 规则读取且每次取得最新版本
- 业务决策：[BIZ-20260826-01](../decisions/BIZ-20260826-01-mongodb-rule-read-boundary.md)
- 技术方案：[DEV-20260826-02](../architecture/DEV-20260826-02-mongodb-latest-rule-read.md)

## 背景

项目已经具备 MongoDB 连接配置和与存储无关的 `ReleaseRule` 规则分析能力，但数据库总开关仍
拒绝启用，缺少真实客户端生命周期、规则仓储端口和最新版本查询。MongoDB 中的自然顺序不能
代表业务最新版本，进程缓存也可能让调用方继续使用已被新版本替代的规则。

## 目标

- 使用官方 PyMongo 异步客户端建立受生命周期管理的只读连接。
- 按项目和目标范围读取数值 `version` 最大的规则。
- 每次请求重新查询 MongoDB，并把规则载荷交给现有领域契约严格校验。
- 提供可调用的 HTTP 读取入口和稳定的不可用、未找到、数据无效错误。

## 范围

- 新增 `RSB_MONGODB_RULE_COLLECTION` 和有界操作超时配置。
- 新增应用层规则仓储端口和最新规则查询用例。
- 新增 MongoDB 初始化器/规则仓储适配器；启动时执行 `ping`，关闭时释放客户端。
- 新增 `GET /api/v1/rules/latest?projectId=...&target=...`。
- 数据库启用但 MongoDB 配置不完整时在配置阶段失败；连接失败时就绪状态为
  `unavailable`。
- 使用测试替身验证查询过滤、显式排序、每次重新读取、契约校验和 API 错误映射。

## 最新规则定义

1. 查询范围是精确匹配的 `project_id + target`。
2. 不添加隐式状态过滤；返回规则自身的 `status`。
3. 排序固定为 `version DESC, _id DESC`，返回第一条。
4. `version` 必须最终通过 `ReleaseRule.version` 的严格正整数校验。
5. 不读取本地文件或内存中的上次成功值作为降级结果。

## 非目标

- 不写入规则集合，不创建索引或 migration，不修改规则状态。
- 不读取历史基线、不生成 SQL、不调用 DeepSeek、不连接 SQL Server。
- 不猜测 RuleReader 尚未提供的包装字段或 camelCase 存储格式；首个切片只接受当前仓库已定义的
  `ReleaseRule` Schema `1.0` 直接载荷。
- 不把 MongoDB URI、主机、凭据或规则正文写入日志。

## 验收标准

1. 相同 `project_id + target` 存在多个版本时，每次调用返回当前最大 `version`。
2. 连续两次调用之间新增更高版本时，第二次读取新版本，证明没有结果缓存。
3. 查询显式排序且最多返回一个文档，不依赖 MongoDB 自然顺序。
4. 未找到返回 404；数据库未启用/不可用返回 503；源文档违反规则契约返回 502，且错误响应不
   包含规则正文或连接信息。
5. 数据库禁用时原有 `/health`、`/ready` 和事实绑定校验行为保持兼容。
6. Ruff、format check 和 pytest 全部通过；README、文档索引、路线图和 PROG 同步。

## 风险与开放问题

- 当前配置指向的 `localhost:27017` 拒绝连接，无法在本切片中核实真实集合名、索引和文档 Shape。
- 上线前必须由 RuleReader/数据库所有者确认并建立适配查询的复合索引：
  `{ project_id: 1, target: 1, version: -1 }`。
- 若真实 RuleReader 文档不是 Schema `1.0` 直接载荷，应先提供实际脱敏样例并新增明确的存储映射
  契约，不能在适配器中猜测字段。
