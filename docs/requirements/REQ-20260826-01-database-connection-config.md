# REQ-20260826-01：数据库连接配置准备

- 状态：`completed`
- 创建日期：2026-08-26
- 来源：用户提供规则对象字段清单，并要求把连接数据库所需参数写入可填写配置文件
- 技术方案：[DEV-20260826-01](../architecture/DEV-20260826-01-database-connection-config.md)

## 背景

SqlBot 已明确需要从 RuleReader 的 MongoDB 读取 `FactBindingRequest`，并在只读 SQL Server
连接上采集真实元数据。当前设置只预留了统一的数据库启用开关，没有 MongoDB、SQL Server
或只读安全限制的具体参数，也不会读取本地配置文件。

## 目标

- 提供一个可直接填写的本地 `.env` 配置文件和可提交的 `.env.example` 模板。
- 集中定义 MongoDB 规则交接读取参数和 SQL Server 元数据读取参数。
- 通过 `check-config` 仅显示非敏感的配置完整性，不回显 URI、用户名、密码、主机或数据库名。
- 保持数据库适配器默认禁用；本需求不建立真实网络连接。

## 范围

- 本地 `.env` 自动加载，进程环境变量继续使用 `RSB_` 前缀并覆盖文件值。
- MongoDB URI、数据库、事实绑定集合、TLS 和连接超时配置。
- SQL Server 地址、端口、数据库、认证、ODBC 驱动、加密、只读意图和资源限制配置。
- DeepSeek 与现有服务配置继续使用同一文件。
- `.env` 被 Git 忽略，`.env.example` 可以提交且不包含真实秘密。

## 非目标

- 不实现 MongoDB 或 SQL Server adapter。
- 不探测网络、VPN、防火墙、驱动安装状态或账号权限。
- 不执行表格中的元数据 SQL，不连接生产环境，不生成 SQL 模板。
- 不把连接串或凭据放入领域模型、日志、API 响应或版本库。

## 功能需求

1. 空白占位值应被忽略，使用户可以逐项填写而不导致无关字段解析失败。
2. MongoDB 配置完整性至少要求 URI、数据库名和事实绑定集合名。
3. SQL Server 配置完整性至少要求主机、数据库和与认证方式匹配的凭据；Windows
   集成认证不要求用户名和密码。
4. SQL Server 必须保持只读意图；首个切片继续禁止临时表。
5. 查询超时、登录超时、最大行数和最大结果字节数必须有安全上限。
6. `RSB_DATABASE_ENABLED=true` 在 adapter 实现前继续明确失败。

## 验收标准

1. 用户可以在仓库根目录 `.env` 中填写全部连接参数。
2. `Settings` 能从 `.env` 读取配置，进程环境变量优先。
3. `uv run release-sql-bot check-config` 能报告 MongoDB 和 SQL Server 是否已填写完整，且不泄露秘密或连接目标。
4. 不允许关闭 SQL Server 只读模式或启用临时表。
5. Ruff、format check 和 pytest 全部通过；README、文档索引和 PROG 同步。

## 风险与开放问题

- SQL Server 认证方式当前只配置 `sql_login` 和 `windows_integrated`；Azure AD 等方式需要在 adapter 需求中单独确认。
- 账号是否确实为数据库侧最小权限只读账号，必须在后续连接探测中验证，不能仅凭配置声明。
- SQL Server schema、完整字段、实体键和关系白名单仍需通过真实元数据快照和业务材料补全。
