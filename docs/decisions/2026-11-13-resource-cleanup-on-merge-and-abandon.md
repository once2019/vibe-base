# 合并/放弃后的资源回收：先落盘再清理，结果结构化

- 日期：2026-11-13
- 状态：已生效
- 相关模块：`scripts/foreman.py`（`Transport.cleanup` 契约 / `Hcom.cleanup` 新实现 / `Orca.cleanup` 容错 / `cmd_merge` / `cmd_task_abandon` / `resource_leaks` + doctor 的 `resource-leaks`）

## 决策内容

1. **`cleanup()` 契约改成「绝不抛 + 返回结构化结果」**：`{"worktree_removed", "branch_deleted",
   "worker_released", "orchestration_task_settled", "notes"}`，每项 `True/False/None` + 人读 notes。
2. **merge 先落盘再清理**：`set_status(completed)` → `save_task` → `cleanup` → 写 `task["cleanup"]` → 再 `save_task`。
   第三步失败只留 `cleanup-incomplete` 日志，不动状态。
3. **hcom 路径补齐真实现**：原来继承空实现，merge 里写了两行裸 `git` 调用（`repo.git` 失败即抛）。
   现在 `worktree remove --force` 失败会先 `git worktree prune` 重试一次；目录已消失则直接 prune 元数据。
4. **abandon 分两种语义**：转 `blocked`（尝试用尽，不再重派）→ 释放 worktree/分支/worker；
   转 `failed`（可重派且复用同一棵树）→ 保留并记 `cleanup-skipped`。
5. **doctor 新增 `resource-leaks`**：扫 `archive/` 与 `tasks/` 下 `completed` 任务，
   worktree 路径仍存在、或 `task["cleanup"]` 里有 `False`，都算泄漏。

## 选择理由

- 原实现把「清理」夹在状态变更与落盘之间，且用会抛异常的 `git`/`_ok_json`：
  清理一失败，磁盘上的任务仍是 `merge_ready`，但合并提交已经在 base 上——状态与事实相反，
  再跑一次 merge 会二次合并。这比「资源没删」严重得多。
- `completed` 是终态，之后没有任何流程会回头看它。资源泄漏只能靠体检主动发现，
  所以必须有结构化字段 + doctor 检查，不能只留一行自由文本日志。
- abandon 不能无条件清理：`failed` 的重派是复用既有 worktree 的（避免重建树的成本与分支冲突），
  删了就破坏重派路径。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 清理失败仍 fail 退出 | 会让「已合并」在状态层不可见，且没有任何重试入口；失败信息应以结构化结果留着，由 doctor/reconcile 复查 |
| 清理放在 `set_status` 之前 | 清理成功但落盘失败时会留下「树没了、状态没变」，更糟 |
| abandon 一律清理 | 破坏 `failed` 重派的树复用；实测 dispatch 在 failed 态会复用既有树 |
| 加常备 `git worktree prune` | 实测目录已删时 `worktree remove` 本身就返回 0，不需要常备；只在 remove 失败后重试一次即可 |

## 影响范围

- 涉及文件/模块：`foreman.py`、`orchestration.md`、`orchestration-orca.md`、`README.md`（命令表未变）
- 兼容性影响：`cleanup()` 返回值从 `list[str]` 变为 `dict`（仅 3 处调用，均在仓内）；
  `task["cleanup"]` 是新增可选字段，旧任务缺失时 `resource_leaks` 只看 worktree 是否存在
- 实测：测试 164 → 172；五处变异全部转红（先落盘、abandon 条件、hcom prune、
  doctor 报泄漏、cleanup 记录计入泄漏）。storyboard-maker：已完成任务 22/22 无 worktree 残留

## 审查修订（同日，五轴审查发现）

1. **`cleanup` 结果缺 `dispatch`**：`resource_leaks` 读 `cleanup["dispatch"]` 定位派工，但两份实现
   都没写这个键，doctor 永远显示 `T-001/-`。三处构造函数补 `"dispatch": dispatch.get("id")`。
2. **清理结果键列表写了两遍**（`cmd_merge` 的 leftover 判定与 `resource_leaks` 的报告），
   以后加一项要改两处、漏一处 doctor 就看不见。提成 `CLEANUP_RESULT_KEYS` 常量。
3. **`Hcom.cleanup` 的判空隐晦**：`Path("")` 得到 `Path(".")`，`if str(wt) and str(wt) != "."`
   实际只有后半句起作用。改成先取原始字符串再判。
4. Hcom 类内 cleanup 前多一个空行（类内方法间是单空行）。

变异确认：Orca 侧漏 `dispatch` 由 `test_cleanup_result_carries_dispatch_id` 接住；
`CLEANUP_RESULT_KEYS` 漏 `worker_released` 由 `test_incomplete_cleanup_is_recorded_structurally` 接住
（原测试的 boom 里 worktree 也是 False，两个键任一命中都算未完成，等于没测到具体键——已改成只让
`worker_released` 失败）。
