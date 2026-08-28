# DEV-20260826-02：MongoDB 最新规则读取设计

- 状态：`superseded`
- 替代设计：[DEV-20260826-03](DEV-20260826-03-rulereader-latest-rule-integration.md)
- 创建日期：2026-08-26
- 实现需求：[REQ-20260826-02](../requirements/REQ-20260826-02-mongodb-latest-rule-read.md)
- 受业务决策约束：[BIZ-20260826-01](../decisions/BIZ-20260826-01-mongodb-rule-read-boundary.md)

## 1. 设计来源与取舍

DataEase SQLBot 在参考提交
[`fcf858c`](https://github.com/dataease/SQLBot/tree/fcf858ccfcca662f4141c8300488d33acd7a2c4d)
中将 [HTTP 路由](https://github.com/dataease/SQLBot/blob/fcf858ccfcca662f4141c8300488d33acd7a2c4d/backend/apps/datasource/api/datasource.py)、
[数据源业务逻辑](https://github.com/dataease/SQLBot/blob/fcf858ccfcca662f4141c8300488d33acd7a2c4d/backend/apps/datasource/crud/datasource.py)、
[数据模型](https://github.com/dataease/SQLBot/blob/fcf858ccfcca662f4141c8300488d33acd7a2c4d/backend/apps/datasource/models/datasource.py)
和 [应用生命周期](https://github.com/dataease/SQLBot/blob/fcf858ccfcca662f4141c8300488d33acd7a2c4d/backend/main.py)
拆分。当前项目借鉴该分层方式，但继续遵守自身端口/适配器边界：API 不导入 PyMongo，领域模型
不依赖数据库驱动，MongoDB 错误不会携带连接目标或规则正文进入响应。

## 2. 模块

```text
api/app.py                              HTTP 参数和错误映射
application/rules.py                    最新规则查询用例
application/ports/rules.py              RuleRepository 端口及稳定异常
application/runtime.py                  初始化器与仓储的运行时装配
domain/rule_analysis.py                 ReleaseRule 严格领域契约
infrastructure/database/mongodb.py      PyMongo 生命周期与只读查询
infrastructure/database/__init__.py     disabled / MongoDB 资源选择
config/settings.py                      集合、超时和启用门禁
```

## 3. 查询契约

适配器对配置的规则集合执行等价查询：

```javascript
findOne(
  { project_id: projectId, target: target },
  { sort: { version: -1, _id: -1 }, projection: { /* ReleaseRule 字段 */ } }
)
```

- `project_id` 和 `target` 由封闭查询模型校验后进入过滤器，不允许调用方传任意 MongoDB 表达式；
- 显式排序保证不依赖自然顺序；`_id` 只作为数据异常时的确定性次级排序，不替代业务版本；
- 投影只取 `ReleaseRule` 业务字段，排除 `_id` 和存储元数据；
- 每次用例调用都执行 `find_one`，仓储不保存上次结果；
- 返回文档由 `ReleaseRule.model_validate` 严格校验，异常转换为不含载荷的稳定仓储错误。

## 4. 生命周期和一致性

使用一个绑定 FastAPI 事件循环的 `AsyncMongoClient`：

1. 服务启动时构造客户端，并对 `admin` 执行 `ping`；
2. 使用 primary read preference，避免为了读取“最新”而主动读取可能滞后的 secondary；
3. BSON 日期按带时区的 UTC `datetime` 解码，继续满足规则有效期必须显式带时区的领域约束；
4. 服务发现、连接和单次操作都有配置上限；
5. `ping` 失败时状态为 `unavailable`，服务就绪检查返回 503；
6. 服务关闭时关闭客户端；关闭操作可重复调用。

客户端只暴露本适配器中定义的读方法。真正的只读权限仍必须由 MongoDB 账号角色保证，配置布尔值
不能替代数据库侧授权。

## 5. 运行时装配

基础设施工厂返回 `DatabaseResources(initializer, rule_repository)`：

- `database_enabled=false`：使用现有 disabled 初始化器，仓储为 `None`；
- `database_enabled=true`：MongoDB 配置必须完整，初始化器和仓储引用同一个 MongoDB store；
- API 只从 `RuntimeContainer.rule_repository` 调用应用用例，不直接访问客户端或集合。

## 6. API 和错误

```http
GET /api/v1/rules/latest?projectId=project-001&target=project_report
```

- 成功：200，返回严格序列化的 `ReleaseRule`；
- 无匹配文档：404 / `RULE_NOT_FOUND`；
- 数据库禁用、未初始化或查询失败：503 / `RULE_REPOSITORY_UNAVAILABLE`；
- 文档违反契约：502 / `RULE_DOCUMENT_INVALID`。

错误信息不包含 URI、数据库名、集合名、MongoDB 原始错误文本或规则正文。

## 7. 测试

- 设置：默认集合和超时、启用完整性、秘密摘要；
- 适配器：启动/关闭、显式过滤和排序、无缓存二次读取、未找到、无效文档、驱动异常；
- 应用：查询参数透传和稳定异常；
- API：成功、404、502、503，以及数据库禁用时的兼容性；
- 全部数据库测试使用固定替身，不依赖在线 MongoDB。
