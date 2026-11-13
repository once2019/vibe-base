# escalation 落进状态机：用 Orca 的 taskId/dispatchId 认领任务

- 日期：2026-11-13
- 状态：已生效
- 相关模块：`scripts/foreman.py`（`Orca.wait_message` / `find_dispatch_by_orchestration` / `cmd_events` / ESCALATE 分支 / `worker_prompt`）、`docs/governance/orchestration-orca.md`

## 决策内容

1. **escalation 不再当非协议消息跳过**。Orca 的 escalation（`Worker stopped without reporting`）payload 带 `taskId` / `dispatchId` / `stopReason`，`wait_message` 把它归一化成 `{"escalation": {...}}` 事件上行。
2. `cmd_events` 用 payload 的 Orca id 反查本仓任务（`find_dispatch_by_orchestration`：匹配 dispatch 记录里的 `orchestration_task` 与 `worker_handle`），转成 `[FOREMAN:ESCALATE] task=<T> dispatch=<D>` 走既有状态机分支。
3. ESCALATE 分支加一条例外：任务已在 `waiting_answer` / `waiting_human` 时不降级为 `failed`——那封 ASK 与 escalation 同源，worker 是在等答复，不是失败。
4. 认不出是本仓任务时 `fail`（列出 Orca id），不静默丢弃。
5. 派工 prompt 的上报命令补 `--dispatch-id`：Orca 的 `worker_done` 要 `--outcome` + `--task-id` + `--dispatch-id` 三样。

## 选择理由

- 实测 T-010：worker 发 `[FOREMAN:ASK]` 后结束回合，Orca 随即发 escalation（payload `stopReason=finished`）。两封消息都被读走（`read=1`），但都没进状态机——ASK 是被裸 `check` 旁路消费的，escalation 是被当成非协议消息记日志。结果任务停在 `running`，Orca 那边 dispatch 停在 `ready`，两边都以为对方会动。
- escalation 是**通道自己给出的权威信号**，不依赖 worker 守规矩。消息可以被旁路消费、协议头可以写错，但"worker 结束回合未上报"这件事 Orca 一定知道。把它接进状态机，等于给消息驱动补上一条不靠 worker 的兜底。
- 不降级的例外来自同一个场景：如果 ASK 正常进了状态机，任务已经是 `waiting_answer`，此时再来一条 escalation 若判 `failed`，主管会以为 worker 挂了，实际上它在等自己回话。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 保持现状：escalation 只记日志 + 超时提示 | 任务会永远停在 `running`，没有任何东西会再提起它；T-010 就是这么悬着的 |
| escalation 一律判 `failed` | 会误伤"发了 ASK 后结束回合"的正常等待，把等人变成失败重派 |
| 靠主管手工跑 `worker-list` 对账 | 依赖人记得查；且这是通道状态与状态机的结构性偏差，不是一次性事故（见下条待办：`reconcile`） |
| 用 Orca 原生 gate 替代 foreman 的 `ask` | 两套阻塞语义（gate 阻塞任务 / ASK 挂起任务状态）要重新对齐，改动面大于本次修复；先让 escalation 能兜底 |

## 影响范围

- 涉及文件/模块：`skills/vibe-base/assets/scripts/foreman.py`、`orchestration-orca.md` 两行踩坑表
- 兼容性影响：`events` 收到 escalation 时不再返回 `timed_out`，而是推进状态机并退出 0；`transport-skipped.log` 不再记录 escalation（其他非协议消息照旧）
- 实测：测试 141 → 145；四处变异全部转红（escalation 上行、taskId 反查、不降级等待、prompt 带 dispatch-id）
