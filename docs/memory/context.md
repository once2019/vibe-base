# 项目上下文快照（context.md）

> 规则与预算见 AGENTS.md §5（快照重写｜双预算 ≤40 行且单行 ≤150 字符｜并行会话先读最新再精确片段替换）。
> 只记录当前状态，不堆历史——历史归属 docs/decisions/（索引 README.md）与 docs/progress/。

- 最后更新：2026-11-16（会话标识：按第一性原理消化 storyboard 回流六点）

## 本会话已完成

- `events` 三条输出路径末尾重申 `waiting_human` 与未关 gate（带已等分钟数、选项、reply 指令）
- 技能包版本 1.1.0 → 1.2.0
- §3 两条原则命名：奥卡姆剃刀（删掉会发生什么）与第一性原理（由哪几条约束推出）
- 编码纪律补「按应用点复制」判据（第二个应用点出现第二份实现即该收敛）
- 状态机：failed→review、merge_ready→failed（`reject`）、waiting_answer→review 三条边都开了入口
- DONE/ASK 证据先落盘再迁状态：被拒收的消息不再丢原文（T-049 靠手工捞的根因）
- reply 回填 asks[].answered_at；task new 输出范围自检；派工单要求遗留项可派单
- 纪律两条进 orchestration：收信只走 events、提交叙事按 --stat 核
- worktree 自举：config `worktree_links` 登记 → dispatch 建树后软链，缺项与未忽略当场告警
- no-skip 放行改 config `skip_allowlist`：放行照报、密钥不放行；修同行重复计数
- 五轴复审自纠四处：helper 归位、仓外路径拒绝、asks 按 dispatch 配对、Orca 补链补测
- 决策 21 条（含三条追记）；回归 198 → 215 项

## 进行中任务

- 无

## 下一步计划

1. storyboard-maker 是否 push 待人类决定（ahead 若干提交，含他人会话内容）
2. 技能仓自身跑 doctor 时 `state-ignored` FAIL 属语义正确（无 `.vibe/`），不修

## 阻塞问题

- 无

## 关键决策索引（只列文件名，摘要见 decisions/README.md）

- docs/decisions/2026-11-15-reflow-dispatch-reflection.md
- docs/decisions/2026-11-15-adopt-ub-mode-coding-discipline.md
- docs/decisions/2026-11-13-单技能融合执行编排.md
