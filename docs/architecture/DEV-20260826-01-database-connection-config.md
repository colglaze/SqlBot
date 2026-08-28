# DEV-20260826-01：数据库连接配置设计

- 状态：`completed`
- 创建日期：2026-08-26
- 实现需求：[REQ-20260826-01](../requirements/REQ-20260826-01-database-connection-config.md)

## 1. 配置来源与优先级

`Settings` 继续由 `pydantic-settings` 集中加载，环境变量前缀为 `RSB_`。新增仓库根目录
`.env` 作为本地开发配置源，优先级为：显式初始化值、进程环境变量、`.env`、代码默认值。
空字符串被忽略，便于模板保留未填写项。

本设计替代 [DEV-20260818-02](DEV-20260818-02-backend-skeleton.md) 第 5 节中“项目不自动读取
`.env`”的旧阶段约束；其余后端骨架边界继续有效。

`.env` 必须被 Git 忽略；`.env.example` 只包含非敏感默认值、空占位符和中文说明。

## 2. MongoDB 配置

| 设置字段 | 环境变量 | 说明 |
| --- | --- | --- |
| `mongodb_uri` | `RSB_MONGODB_URI` | 包含认证信息时按秘密处理 |
| `mongodb_database` | `RSB_MONGODB_DATABASE` | 默认 `rule_reader` |
| `mongodb_fact_binding_collection` | `RSB_MONGODB_FACT_BINDING_COLLECTION` | 默认 `fact_binding_handoffs` |
| `mongodb_tls` | `RSB_MONGODB_TLS` | 是否启用 TLS |
| `mongodb_tls_ca_file` | `RSB_MONGODB_TLS_CA_FILE` | 可选 CA 文件路径 |
| `mongodb_server_selection_timeout_seconds` | 同字段前加 `RSB_` | 服务发现超时 |
| `mongodb_connect_timeout_seconds` | 同字段前加 `RSB_` | 连接超时 |

URI、数据库和集合均非空时，安全摘要只返回 `mongodb_configured=true`。

## 3. SQL Server 配置

| 设置字段 | 说明 |
| --- | --- |
| `sqlserver_host` / `port` / `database` | 目标地址；安全摘要不回显具体值 |
| `sqlserver_auth_mode` | `sql_login` 或 `windows_integrated` |
| `sqlserver_username` / `password` | SQL 登录凭据，密码按秘密处理 |
| `sqlserver_odbc_driver` | 默认 `ODBC Driver 18 for SQL Server` |
| `sqlserver_encrypt` | 默认启用传输加密 |
| `sqlserver_trust_server_certificate` | 默认不信任未验证证书 |
| `sqlserver_read_only` | 必须为 `true` |
| `sqlserver_application_intent` | 固定为 `ReadOnly` |
| 登录/查询超时与结果上限 | 防止元数据或验证查询无限占用资源 |

当地址和数据库已填写，且认证要求满足时，安全摘要只返回
`sqlserver_configured=true`。配置完整不等同于网络可达、权限正确或连接已建立。

## 4. 安全摘要

`check-config` 可以输出布尔完整性、认证方式、驱动、加密和资源限制；不得输出以下内容：

- MongoDB URI；
- SQL Server 主机、实例、数据库、用户名或密码；
- DeepSeek API key；
- TLS CA 文件实际路径。

## 5. 启用边界

本切片只准备配置。`RSB_DATABASE_ENABLED=true` 继续由设置校验器拒绝，数据库初始化器仍返回
`disabled`。后续 adapter 切片必须先定义仓储端口、只读连接探测、驱动依赖、超时行为和测试，
再解除该保护。

## 6. 测试

- 安全默认值和环境变量读取；
- 临时 `.env` 文件读取和环境变量覆盖；
- SQL 登录与 Windows 集成认证的完整性判断；
- 安全摘要键集合以及秘密值不泄露；
- 禁止启用未实现 adapter、临时表和非只读 SQL Server 配置。
