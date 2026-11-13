# 派工单模板

- 变更日期：2026-11-13
- 变更原因：并入 vibe-base；补上“worker 不写 docs/memory/、docs/progress/”这一条与 docs 落点说明

`scripts/foreman.py` 的 `task new` / `dispatch` 按 `brief_text()` 生成实际派工单（以生成物为准，本模板说明各节的写法意图）。主管手写或修改派工单时按下述要求：

## 各节写法

- **目标与背景**：一句话目标 + 必要上下文（关联规格路径、依赖任务、上游决策）。禁止只写"做 X"而不给判据来源。
- **范围**：允许改动的路径清单（对应 `task.json.scope`）。禁止写"相关文件"；要么列路径，要么明确"全库（谨慎）"。
- **补充说明**：仓库特殊约定、必须遵循的 AGENTS.md 章节、禁止事项（超出通用红线的部分）。
- **完成定义**：一条可执行验收命令（对应 `task.json.acceptance`），必须是 worker 能在 worktree 内直接运行的完整命令；不接受"确保测试通过"这类不可执行表述。
- **协议**：固定段，不修改——消息头格式与 ASK/DONE/ESCALATE 用法。
- **红线**：固定段，不修改——含“不写 `docs/memory/` 与 `docs/progress/`”与“影响面超范围发 ASK”；两条都来自 AGENTS.md §5 记忆归属。

## 修改规则

- `task set-acceptance` 会按 `task.json` 重新生成派工单；手工改动会被覆盖，改约定请改 `task.json` 或派工前改模板。
- 派工单是 worker 的唯一事实来源：worker 不得依赖派工单之外的口头/会话上下文。
