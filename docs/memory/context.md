# 项目上下文快照（context.md）

> 规则与预算见 AGENTS.md §5（快照重写｜双预算 ≤40 行且单行 ≤150 字符｜并行会话先读最新再精确片段替换）。
> 只记录当前状态，不堆历史——历史归属 docs/decisions/（索引 README.md）与 docs/progress/。

- 最后更新：2026-11-15（会话标识：奥卡姆剃刀命名进 §3，技能文档去重）

## 本会话已完成

- `events` 三条输出路径末尾重申 `waiting_human` 与未关 gate（带已等分钟数、选项、reply 指令）
- 技能包版本 1.1.0 → 1.2.0；回归 173 → 198 项
- README 与 SKILL 按实际能力校正：doctor 18 项、governance 八份细则、unblock 出口、可选质量门槛、安装路径
- §3「最小代码」改名奥卡姆剃刀，适用范围扩到任何新增；判据只有「删掉会发生什么」
- 编码纪律补「按应用点复制」判据（第二个应用点出现第二份实现即该收敛）
- 技能文档去重：SKILL 安装与 Tradeoff 散文、两份细则对 §3 的原话复述删掉
- 决策 17 条；storyboard-maker 本轮不动（人类明确要求）

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
