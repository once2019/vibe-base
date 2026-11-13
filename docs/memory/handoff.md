# 会话交接文档（handoff.md）

> 会话收尾必写本文件（供新会话快速恢复工作；旧"上下文 70% 触发"条款不可测量，已废）。
> 新会话第一件事=读 `docs/memory/context.md` + 本文件，再查 `docs/decisions/README.md` 索引最近 3 条。
> 规则与预算见 AGENTS.md §5（交接知识/运行态分区｜≤60 行且单行 ≤150 字符｜失效运行态条目更新时清除）。

- 撰写时间：2026-11-13（晚）
- 交接原因：技能 1.1.0 已定稿并跑通真通道；控制面连补三块（决策索引检查、派发策略、空等拦截）

## 运行态（失效即删；每条必须带失效条件）

- 源技能仓库 `/home/sun/vibe-foreman` 保留原样、未改动（失效条件：确认不再需要独立 foreman 后归档）
- 验证用仓库 `/tmp/e2e-skill` 已注册进 Orca（无 remove 子命令，会在 `orca repo list` 里长期存在）
- storyboard-maker 未 push（ahead 若干提交，其中含他人会话内容）（失效条件：人类决定推送后）

## 当前状态（历史见 docs/progress/ 与 docs/decisions/）

- 技能包 `skills/vibe-base` 1.1.0：骨架 16 文件（AGENTS.md + 14 docs + `scripts/foreman.py`），回归 137 项
- 控制面已具备：doctor 16 项（含 `decisions-index`、`orchestration-reconcile`）、批次任务与逐项验收、
  `depends-on` 前置门槛、scope 相交拦截、重派必写说明、`events` 空等拦截、`reconcile` 对账
- storyboard-maker 控制面已同步到最新；AGENTS.md 保持项目版，历史文档零丢失
- 未完成清单：空
  （zip 重打包与技能目录重装已按人类要求删除）

## 关键决策引用

- [单技能融合执行编排](../decisions/2026-11-13-单技能融合执行编排.md)｜改编排块或 AGENTS.md 相关条文前必读
- [派发策略：批次任务 + 三道机械门槛](../decisions/2026-11-13-batch-task-and-dispatch-gates.md)｜改派工/批次逻辑前必读
- [events 不再允许连环开窗口](../decisions/2026-11-13-events-no-polling-guard.md)｜改 `events`/回合粒度前必读
- [权威文档登记表与核实门槛](../decisions/2026-10-24-权威文档登记表与核实门槛.md)｜新建权威文档前必读

## 新会话必须遵守的约束

- 编排块的条文正本是 `docs/governance/orchestration.md`（与 assets 成对）；不得另建第二份编排说明
- 正本只有 `skills/vibe-base/assets/`；根侧同名文件是**符号链接**（一份实体）。例外是 5 个状态类文件
  （memory/context、memory/handoff、decisions/README、specs/README、design/README）：根=实况、模板=占位。
  `docs/design/governance-retrospective.md` 只在根（维护记录，不随 init 分发）
- 状态变更只经 `scripts/foreman.py`，不手改 `.vibe/` 下的 task.json 与 evidence
- 改 `foreman.py` 后必须跑仓库根 `python3 -m unittest discover -s tests`（`tests/` 属本仓库，不随技能分发）
