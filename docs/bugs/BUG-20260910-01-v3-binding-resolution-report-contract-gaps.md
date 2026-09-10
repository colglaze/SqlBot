# BUG-20260910-01：BindingResolutionReportV3 报告契约缺口（blocked 输出漏洞与静默规范化）

- 状态：`completed`（2026-09-10 审核修复完成，离线测试通过）
- 严重度：中（报告契约输入门禁不完整，可能导致非法 blocked 报告被接受或输入被静默改写；非运行时放行缺陷）
- 发现日期：2026-09-10
- 关联 REQ：[REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
- 关联设计：[DEV-20260906-04 §5.4](../architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md)
- 关联进度：[PROG-20260911](../progress/PROG-20260911.md)

## 现象

`BindingResolutionReportV3` 报告契约存在两类输入门禁缺口：

### 缺口 1：blocked 报告未检查全部六个结果字段

`_validate_report_consistency` 在 `status=blocked` 时仅检查四个列表字段
（`resolvedFields`、`resolvedEntityKeys`、`resolvedFilters`、`resolvedJoins`）
为空，却未检查 `resolvedAggregation` 和 `resolvedTimeRange` 必须为 `None`。

复现：向合法 blocked fixture 注入以下对象，均可成功构造报告（应当失败）：

```python
# resolvedAggregation (mode=none 或 mode=compute)
wire["resolvedAggregation"] = {
    "mode": "compute",
    "function": "sum",
    "inputFieldIds": ["factValue"],
    "groupByFieldIds": [],
    "distinct": False,
    "evidenceIds": ["ev-query-requirement"],
}

# resolvedTimeRange (mode=none 或 mode=between)
wire["resolvedTimeRange"] = {
    "mode": "between",
    "timeFieldId": "syntheticTime",
    "timeSchemaName": "dbo",
    "timeRelationName": "synthetic_table",
    "timeColumnName": "synthetic_time",
    "evidenceIds": ["ev-query-requirement"],
}
```

即使 `mode="none"`，仍是非 `None` 的解析结果对象，blocked 也应拒绝。

### 缺口 2：报告顶层静默规范化

`BindingResolutionReportV3` 继承共享 `V3ReportModel`，其配置为：
- `str_strip_whitespace=True` — 自动去除字符串两端空白；
- 未启用 `strict` — 允许 Pydantic 类型强转（如 tuple→list）。

后果：
- `usageTraceabilitySha256` 设置为前后带空格的 64 位合法摘要时，去空格后接受
  （`pattern=_SHA256_PATTERN` 正则不足以拦截，因为去空格在模式匹配之前发生）；
- `issues` 或 `resolvedFields` 传入 `tuple` 时，被静默转换为 `list` 后接受。

注：`requestRef.requestId` 传入整数时，`RequestRefV3` 继承 `_V3Base`，
后者已配置 `strict=True`；`requestRef.requestId` 的整数输入在修复前后均被拒绝，
错误位置为 `("requestRef", "requestId")`，类型为 `string_type`。
这是既有约束，不属于本轮新增修复。
**不是本轮修复内容**。

这不是新增业务语义，而是报告输入格式约束的补全。

## 期望行为

`status=blocked` 时：
- 四个列表字段必须为空；
- `resolvedAggregation` 必须为 `None`；
- `resolvedTimeRange` 必须为 `None`；
- 必须至少包含一个 blocker issue；
- `executable` 始终为 `false`。

报告输入格式约束：
- `strict=True`：拒绝 tuple→list 等类型强转；
- `str_strip_whitespace=False`：关闭字符串两端空白的自动删除，
  使 SHA-256 字段由既有格式约束（`pattern=_SHA256_PATTERN`）拒绝前后空白。
  普通说明字段是否允许空白，由其自身契约决定。

## 实际行为（修复前）

见上方"现象"描述。针对本轮漏洞的 9 项拒绝测试（blocked aggregation × 2、
blocked timeRange × 2、空白字符 × 3、tuple × 2），在旧实现上因未抛出预期
异常而失败；修复后通过。

## 影响与安全风险

- 交付影响：下游消费者可能收到携带部分解析结果的 blocked 报告，违反
  "blocked 不产生部分授权计划" 的契约不变量；
- 安全影响：无直接放行风险。blocked 报告仍固定 `executable=false`，
  不会导致 SQL 执行；但输入静默规范化可能掩盖调用方传入的格式错误，
  使后续处理环节收到意外格式的数据；
- 反向风险：若下游服务依赖报告契约的严格格式约束（如严格 SHA-256
  格式校验、列表类型契约），静默规范化会导致契约被绕过。

## 临时规避

无运行时规避需求。本修复改变 `BindingResolutionReportV3` 的构造校验行为，
不新增数据库、provider、SQL 执行或存储路径。

## 根因

- 缺口 1：`_validate_report_consistency` 最初只覆盖四个列表字段，遗漏了
  两个可选对象字段 `resolvedAggregation` 和 `resolvedTimeRange`；
- 缺口 2：`BindingResolutionReportV3` 直接继承共享 `V3ReportModel`，
  未覆盖 `strict` 和 `str_strip_whitespace` 配置以匹配报告契约的严格输入需求。

## 修复方式与验收

修复由 [PROG-20260911](../progress/PROG-20260911.md) 的审核修复承载：

1. `_validate_report_consistency` 新增两条检查：
   - `resolved_aggregation is not None` → 拒绝；
   - `resolved_time_range is not None` → 拒绝；
   - 错误消息使用稳定中性措辞 "blocked report must not carry resolved aggregation/time range"。

2. `BindingResolutionReportV3` 覆盖 `model_config`：
   - `strict=True`；
   - `str_strip_whitespace=False`；
   - 保持既有 camelCase、extra=forbid、snake_case 拒绝、frozen 和其他约束；
   - 不修改共享 `V3ReportModel`，避免影响既有 intake 报告。

3. 新增 15 项针对性测试（4 项参数化列表反例、2 项 aggregation、
   2 项 timeRange、3 项空白字符、2 项 tuple 拒绝、2 项合法 list 往返）。

### 验证

审核复跑结果（上一轮）：
- `uv run ruff check .`：All checks passed
- `uv run ruff format --check .`：格式检查通过
- `uv run pytest tests/contract/test_metadata_resolution_v3_report_contract.py`：**49 passed**
- `uv run pytest`：**749 passed**（734 基线 + 15 新增）
- `git diff --check`：通过
- 警告：`.pytest_cache` 写入权限问题（Windows 环境），与测试逻辑无关

### 文档更新

- DEV-20260906-04 §5.4 新增"报告输入格式约束"和"blocked 报告六项输出完整性"两段；
- PROG-20260911 追加审核修复记录；
- ROADMAP.md Phase 4R 状态更新；
- README.md 当前进度链接更新。
