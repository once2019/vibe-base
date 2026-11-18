# 会话交接文档（handoff.md）

> 会话收尾必写本文件（供新会话快速恢复工作；旧"上下文 70% 触发"条款不可测量，已废）。
> 新会话第一件事=读 `docs/memory/context.md` + 本文件，再查 `docs/decisions/README.md` 索引最近 3 条。
> 规则与预算见 AGENTS.md §5（交接知识/运行态分区｜≤60 行且单行 ≤150 字符｜失效运行态条目更新时清除）。

- 撰写时间：2026-11-18
- 交接原因：worker 收 ANSWER 后挂起已修在 worker_prompt；§3 增用户意图复述条

## 运行态（失效即删；每条必须带失效条件）

- `/tmp/e2e-skill` 已注册进 Orca（无 remove 子命令，长期留在 `orca repo list`）（失效条件：Orca 支持移除后）
- storyboard-maker 未 push（ahead 若干提交，含他人会话内容）（失效条件：人类决定推送后）
- `/tmp/ub-mode` 是评估时 clone 的参考副本，未入库（失效条件：不再需要对照时删）

## 当前状态（历史见 docs/progress/ 与 docs/decisions/）

- 技能包 `skills/vibe-base` 1.2.0：骨架 17 文件（AGENTS.md + 15 docs + `scripts/foreman.py`），回归 219 项
- 奥卡姆剃刀：§3 一行（141 字符），判据「答不出具体损失就不加」
- 第一性原理：§3 一行（116 字符），判据「说不出由哪几条约束推出就是照搬」；决策 22 条
- `reject`：review/merge_ready/failed 都能打回，`--note` 必填（`--body` 别名），原文进日志
- 「foreman 正本在上游」经裁决不进技能正本（项目侧 AGENTS.md 不该知道技能的存在）；storyboard 保留自己的那份
- worktree 自举已落地（config `worktree_links`，默认空，不猜）；mutation_check 经复核不做（留证已由 DONE 承担，变异点发现归 mutator）
- 控制面：doctor 18 项（含 `decisions-index`、`orchestration-reconcile`、`resource-leaks`、`quality-tools`）
- 编码纪律细则 `coding-discipline.md`：八条判据 + 三段顺序 + 单会话审查口径 + 可选质量门槛
- `events` 三条输出路径末尾重申 `waiting_human` 与未关 gate，`waiting_answer` 不算
- worker 挂起根因：读裁决后产出无工具调用的消息=回合结束；worker_prompt 已钉死「唯一正确行为是继续实现」
- 换树重派时派工单自动加「上一 attempt 的成果不在你这棵树里」节，点名旧分支与 cherry-pick
- 未完成清单：空

## 关键决策引用

- [吸收 ub-mode 编码纪律](../decisions/2026-11-15-adopt-ub-mode-coding-discipline.md)｜改 §3 或 coding-discipline 前必读
- [单技能融合执行编排](../decisions/2026-11-13-单技能融合执行编排.md)｜改编排块或 AGENTS.md 相关条文前必读
- [派发策略：批次任务 + 三道机械门槛](../decisions/2026-11-13-batch-task-and-dispatch-gates.md)｜改派工/批次逻辑前必读
- [events 不再允许连环开窗口](../decisions/2026-11-13-events-no-polling-guard.md)｜改 `events`/回合粒度前必读
- [权威文档登记表与核实门槛](../decisions/2026-10-24-权威文档登记表与核实门槛.md)｜新建权威文档前必读
