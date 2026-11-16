# 会话交接文档（handoff.md）

> 会话收尾必写本文件（供新会话快速恢复工作；旧"上下文 70% 触发"条款不可测量，已废）。
> 新会话第一件事=读 `docs/memory/context.md` + 本文件，再查 `docs/decisions/README.md` 索引最近 3 条。
> 规则与预算见 AGENTS.md §5（交接知识/运行态分区｜≤60 行且单行 ≤150 字符｜失效运行态条目更新时清除）。

- 撰写时间：2026-11-15
- 交接原因：events 每回合重申待拍板事项已落地；README 与 SKILL 按实际能力校正

## 运行态（失效即删；每条必须带失效条件）

- `/tmp/e2e-skill` 已注册进 Orca（无 remove 子命令，长期留在 `orca repo list`）（失效条件：Orca 支持移除后）
- storyboard-maker 未 push（ahead 若干提交，含他人会话内容）（失效条件：人类决定推送后）
- `/tmp/ub-mode` 是评估时 clone 的参考副本，未入库（失效条件：不再需要对照时删）

## 当前状态（历史见 docs/progress/ 与 docs/decisions/）

- 技能包 `skills/vibe-base` 1.2.0：骨架 17 文件（AGENTS.md + 15 docs + `scripts/foreman.py`），回归 198 项
- 控制面：doctor 18 项（含 `decisions-index`、`orchestration-reconcile`、`resource-leaks`、`quality-tools`）
- 编码纪律细则 `docs/governance/coding-discipline.md`：六条判据 + 三段顺序 + 可选质量门槛
- `events` 三条输出路径末尾重申 `waiting_human` 与未关 gate，`waiting_answer` 不算
- 未完成清单：空

## 关键决策引用

- [吸收 ub-mode 编码纪律](../decisions/2026-11-15-adopt-ub-mode-coding-discipline.md)｜改 §3 或 coding-discipline 前必读
- [单技能融合执行编排](../decisions/2026-11-13-单技能融合执行编排.md)｜改编排块或 AGENTS.md 相关条文前必读
- [派发策略：批次任务 + 三道机械门槛](../decisions/2026-11-13-batch-task-and-dispatch-gates.md)｜改派工/批次逻辑前必读
- [events 不再允许连环开窗口](../decisions/2026-11-13-events-no-polling-guard.md)｜改 `events`/回合粒度前必读
- [权威文档登记表与核实门槛](../decisions/2026-10-24-权威文档登记表与核实门槛.md)｜新建权威文档前必读
