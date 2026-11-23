# 落在已收口或未知任务上的协议消息只记录，不阻塞队列

- 日期：2026-11-19
- 状态：已生效
- 相关模块：`scripts/foreman.py`（`apply_protocol_event`）、`tests/test_late_message.py`

## 决策内容

协议消息（DONE/ASK/ESCALATE）落在**已收口**（completed/abandoned）或**不存在**的任务上，或 ASK 落在交付后（review/merge_ready/failed）的任务上时：

1. 正文先落 `.vibe/tasks/_unparsed/evidence/`；
2. 记一行日志，`result` 带 `ignored` 与 `evidence`；
3. 继续处理同批后面的消息，不 `fail`。

`waiting_human → review` 的拒收保持 `fail`：那是人类门，不是噪声。

## 选择理由

通道是消费式的：`events` 一次读走整批，一条 `fail` 就让同批后面的 ASK/DONE 全部搁浅，而主管只看到日志里一行摘要。表现是「消息没丢，被一条噪声堵住了」——worker 等答复、主管等消息，双方都以为对方没发。

实测触发（storyboard T-146）：T-145 的重复 DONE（任务已 completed）排在前面，`events` 直接 fail 退出，后面 T-146 的 ASK 没人处理。Orca 的 `check` 已把这批标为已读，`check --peek` 显示 No messages。

技能侧有一层既有缓冲：Orca 通道把同批剩余消息全文写进 `logs/batch-unhandled.log`，所以最坏情况是「要翻日志」而不是彻底消失。缓冲不能替代修复——噪声照旧让正常消息停在日志里。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 只忽略不留正文 | 通道已消费，日志里只有摘要；ASK 的问题文本是最要紧的部分 |
| 整个 `events` 包 try/except 继续 | 状态机会停在半路（证据存了、状态没迁），且掩盖真实的非法迁移 |
| 把 `waiting_human → review` 也放开 | 一次上报就能绕过人类未关的门（既有决策） |
| 只修 DONE 分支 | ASK 在交付后到达同样会 fail；未知任务号（别仓消息、协议头笔误）同样会 fail |

## 影响范围

- 涉及文件：`scripts/foreman.py`（三处守卫）、`tests/test_late_message.py`（6 条，含两条反向哨兵）
- 兼容性影响：无状态机变更；`events` 输出多一行「已忽略」
- 后续约束：新增协议消息类型时，落到终态/未知任务的分支一律按本节处理（记一行 + 落正文 + 继续）
