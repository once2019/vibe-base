# 项目上下文快照（context.md）

> 规则与预算见 AGENTS.md §5（快照重写｜双预算 ≤40 行且单行 ≤150 字符｜并行会话先读最新再精确片段替换）。
> 只记录当前状态，不堆历史——历史归属 docs/decisions/（索引 README.md）与 docs/progress/。

- 最后更新：2026-11-18（会话标识：worker 挂起根因与用户意图复述）

## 本会话已完成

- §3 增「用户意图先复述再动手」（决策 22）：复述成「改哪个文件或行为 + 判据」，写不出先问、一次一问
- worker 收 ANSWER 后挂起（T-074/077/078 三次同死）：worker_prompt 两通道钉死「读裁决不是回合终点」
- 派工单与启动指令补「口径有歧义先发 ASK，把推论当事实用即违规」（worker 只读这两处，不读 orchestration）
- orchestration 纪律节补「发协议消息必须确认发出」：无 messageId 即当没发；正文含反引号用单引号
- reject 收 failed 且 `--body` 作别名；换树重派时派工单点名上一分支（否则 worker 从零重做）
- 单会话最低审查口径进 coding-discipline：验收先行、反证、条件性冷启动重读
- worktree 自举：config `worktree_links` 登记 → dispatch 建树后软链，缺项与未忽略当场告警
- no-skip 放行改代码内 `env-skip` 标记（config 方案因 .vibe 不入库被推翻）；密钥不放行
- 状态机四条边都开了入口：failed→review、merge_ready→failed、waiting_answer→review
- DONE/ASK 证据先落盘再迁状态：被拒收的消息不再丢原文（T-049 靠手工捞的根因）
- 决策 22 条（含五条追记）；回归 219 项

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
