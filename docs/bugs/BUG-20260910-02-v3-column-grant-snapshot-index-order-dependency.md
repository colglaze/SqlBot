# BUG-20260910-02：V3 列授权解析中快照重复检测的顺序依赖缺陷

- 状态：`completed`（2026-09-10 修复完成，离线测试通过）
- 严重度：中（错误码优先级依赖关系数组顺序，可能导致错误分类错误；非放行缺陷）
- 发现日期：2026-09-10
- 关联 REQ：[REQ-20260906-04](../requirements/REQ-20260906-04-v3-downstream-pipeline-alignment.md)
- 关联设计：[DEV-20260906-04 §5.3.1](../architecture/DEV-20260906-04-v3-downstream-pipeline-alignment.md)
- 关联进度：[PROG-20260911](../progress/PROG-20260911.md)

## 现象

`_build_snapshot_index` 在遍历每个 relation 时立即检查其 columns。
因此，前面关系中的重复列会抛出 `SNAPSHOT_COLUMN_AMBIGUOUS`，
而后面关系中的重复关系键（本应抛 `SNAPSHOT_RELATION_AMBIGUOUS`）永远不会被检测到。

## 复现方式

构造一个 snapshot：
- relation A：包含重复列（如 `col1` 出现两次）
- relation B：与 A 的关系键相同（如都是 `dbo/table1`），但列本身无重复

排列 `[A, B]`：
- 预期：`SNAPSHOT_RELATION_AMBIGUOUS`（关系键重复）
- 实际（缺陷）：`SNAPSHOT_COLUMN_AMBIGUOUS`（先检查到 A 内部的列重复）

## 期望行为

按 DEV §5.3.1 步骤 2 的规定：
1. 先遍历全部 relations，检查关系键重复
2. 全部关系成功后，再遍历全部 columns，检查列键重复

关系重复应优先于列重复被检测到，不考虑关系在数组中的顺序。

## 实际行为（修复前）

列重复检测嵌入在关系遍历循环中，错误码优先级取决于数组顺序。

## 影响与安全风险

- 交付影响：错误分类不准确，可能误导调用方诊断方向
- 安全影响：无放行风险。两种错误都导致解析失败，不产生部分授权计划
- 反向风险：无

## 临时规避

无运行时规避需求。纯离线逻辑修复。

## 根因

`_build_snapshot_index` 使用单次循环同时建立 relation_index 和 column_index，
导致列检查在关系检查完成前就执行。

## 修复方式

拆分为两个独立阶段：
1. 第一阶段：遍历全部 relations，建立 relation_index，重复即抛 `SNAPSHOT_RELATION_AMBIGUOUS`
2. 第二阶段：遍历全部 relations 的 columns，建立 column_index，重复即抛 `SNAPSHOT_COLUMN_AMBIGUOUS`

### 验证

- `uv run ruff check .`：All checks passed
- `uv run ruff format --check .`：格式检查通过
- `uv run pytest tests/unit/test_metadata_resolution_v3_column_grant.py`：**passed**
- `uv run pytest`：**passed**
- `git diff --check`：通过
