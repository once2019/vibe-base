# 项目上下文快照（context.md）

> 规则与预算见 AGENTS.md §5（快照重写｜双预算 ≤40 行且总 ≤3000 字符｜并行会话先读最新再精确片段替换）。
> 只记录当前状态，不堆历史——历史归属 docs/decisions/（索引 README.md）与 docs/progress/。

- 最后更新：2026-11-18（会话标识：worker 挂起根因与用户意图复述）

## 当前状态

- 回归 226 项全绿；doctor 18 项；决策 28 条（含五条追记）
- 预算：`AGENTS.md` ≤100 行 / ≤10000 字符；`context.md` ≤40 行 / ≤3000；`handoff.md` ≤60 行 / ≤4000
- 状态机四条边都有入口；DONE/ASK 证据先落盘再迁状态，被拒收不丢原文
- `reject` 收 review/merge_ready/failed，`--body` 作别名；换树重派点名旧分支
- 大改动（`--numstat` 合计 >300 行）必须跑 crapper/dryer，结果进 DONE
- 产物规范补「新增守卫的准入」三问（决策 25）；§3「不推断未登记的集合」区分契约登记与流程登记
- `dispatch --infra`：主管侧缺陷重跑不占尝试配额（决策 26）；派工单「第 N 次尝试」修正为等于 attempts
- 技能包只发规范（决策 28）：规范文档只写做法与判据，论据/推导/历史过程归 decisions；tests/test_skill_docs.py 机械守卫
- 守卫生命周期（决策 27，合并归一边界与降级阶梯）：准入三问 → 分档声明（归一/回喂/阻断）→ 举证义务 → 退出靠对照实验「关掉它谁会发现」
- 自审细节只归派工单一处；严重度两档（必须改/建议）
- 本会话九项完成的细节见 `docs/progress/2026-11-19-会话收口-worker挂起与规则减法.md`

## 进行中任务

- 无

## 下一步计划

1. storyboard-maker 是否 push 待人类决定（ahead 若干提交，含他人会话内容）
2. 技能仓自身跑 doctor 时 `state-ignored` FAIL 属语义正确（无 `.vibe/`），不修
3. 决策已 22 条，可跑一次复盘（governance-review.md）

## 阻塞问题

- 无

## 关键决策索引（只列文件名，摘要见 decisions/README.md）

- docs/decisions/2026-11-18-restate-user-intent-before-acting.md
- docs/decisions/2026-11-16-dispatch-and-state-machine-gaps.md
- docs/decisions/2026-11-15-first-principles-in-section-3.md
