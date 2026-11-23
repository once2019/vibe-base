# Orca 原生路径 — 机制坑与人工操作

- 变更日期：2026-11-19
- 读它的条件：**`scripts/foreman.py` 报错需要人工介入**、**要用 foreman 未封装的 Orca 能力**、或**排查 worker 卡死**。日常主管循环不需要读。
- 验证状态标记：`✅`=本机跑过命令或 E2E 亲历；`📖`=orca `--help`/二进制文本确认；`⚠️`=未独立验证，勿据此下结论。

## 通道怎么选（自动）

`foreman.py` 缺省按运行时判定：在 Orca 管辖终端→orca，否则→hcom。判定 = `ORCA_TERMINAL_HANDLE` 存在
+ `terminal show` 复核存活 + `worker-list` 判断本终端是不是某个 live dispatch（是则本会话是 worker，
控制面命令会被拒绝）。`init --transport hcom|orca` 可固定，`--transport auto` 复位。
自动判定为 orca 但没绑 Run 时不回退 hcom——`doctor` 会明确要求 `run-create`。

护栏：探测结果按仓库缓存在进程内（一条命令只问一次 daemon）；每次 daemon 往返上限 15s
（`DETECT_TIMEOUT`），句柄失效时短路；外部命令超时统一降级为退出码 124 + 可读原因，不抛 Python 栈。

## 何时用 foreman 命令、何时用 Orca 原生命令

| 场景 | 用什么 |
| --- | --- |
| 建任务、派工、收消息、审 diff、合并、竣工单 | `foreman.py`（状态机 + evidence） |
| 任务间有依赖（DAG）、按 dispatch 重试 | Orca 原生命令（foreman 不封装，见下） |
| 判断 worker 卡死还是死循环 | `worker-read --source terminal --dispatch <id>` |
| 状态机与 Orca 对不上（任务停在 running 没动静） | `foreman.py reconcile`（`doctor` 里是 `orchestration-reconcile`） |

## 机制坑

| 坑 | 症状 | 正确做法 | 验证 |
| --- | --- | --- | --- |
| 没有编排身份 | `send` 报 `no_active_sender_terminal`；`check` 报 `no_active_terminal` | 在 Orca 管辖的终端里启动主管会话（`ORCA_TERMINAL_HANDLE` 由 Orca 注入）；`doctor` 的 `orca-identity` 检查该项 | ✅ |
| `check --types` 误解 | 以为它过滤返回内容 | 它是 **`--wait` 的唤醒条件**；返回的 Delivery 永远整批。原生等待要用 `--types` 且**必须含 `status`**——foreman 的 ASK 用默认类型 `status`，漏掉就永远等不到请示 | ✅📖 |
| 验收产物挡合并 | 下一个任务的 `merge` 报「工作区不干净」 | `init` 把 `.vibe` + 常见构建产物（`__pycache__/`、`*.pyc`、`.pytest_cache/`、`node_modules/`、`target/`、`dist/`、`build/`）写进 `.git/info/exclude`；老仓库重跑一次 `init` | ✅ |
| 日志无轮转 | 长期运行 `logs/*.log` 无限增长 | foreman 超过 1MB 自动轮转一份 `.1` | ✅ |
| 脚本消费主管邮件 | 用消费式 `check` 会把主管 session 的邮件拿走 | foreman 是消费式（内容落 evidence 与状态）。**foreman 模式下不要同时让 agent hooks 收信**：被唤醒后运行 `foreman.py events`，不要自己 `check` | 📖✅ |
| 长阻塞 `--wait` | 主管被锁十几分钟；唤醒注入可能被正在跑的命令吞掉 | 派完就结束回合。一次 `events` 调用 = 一轮；连续两轮空等后自己拒绝（`MAX_EMPTY_WAITS=2`）。`--timeout-ms` 没有技术硬上限 | ✅ |
| 把 `events` 当轮询 | 连着开三个窗口空转 | 按调用计数并由代码拦：连续两轮空等后第 3 次直接 fail，输出指明「结束回合、在 handoff 写一行在等什么」。有消息在等时仍放行（`has_pending`） | ✅ |
| 合并后资源没回收 | `worktree rm` 失败（目录在但 `.git` 丢了、远端终端不可达）会让任务退回 `merge_ready`，再 merge 即二次合并 | 清理不抛：`set_status(completed)` 先 `save_task`，再 release worker → 结算 orchestration task → `worktree rm --delete-branch`，每步结果写进 `task["cleanup"]`。`doctor` 的 `resource-leaks` 列出「completed 任务仍占着 worktree」与「cleanup 记录失败」 | ✅ |
| 纯消息驱动会漏状态 | 消息被裸 `check` 吃走、worker 崩溃、DONE 发送失败，任务停在 `running` | `foreman.py reconcile` 拿 Orca 的权威事实对账三条：dispatch 状态、liveness（`ready` 但 worker 已退出 = 结束回合未上报）、消息落地（inbox `read=1` 却没有对应 evidence）。`doctor` 的 `orchestration-reconcile` 每次体检都跑 | ✅ |
| worker 用原生 `orca orchestration ask` | 它会在 worker 终端里阻塞等回答，主管侧收不到 | 请示一律 `[FOREMAN:ASK]`；主管侧 `ask` 同步建原生 gate、`reply` 关掉它 | 📖✅ |
| 手工 `check --json` 排查 | `check` 是消费式的，会把 worker 的 DONE 吃掉 | 排查用 `check --peek --json` 或 `--unread`；收信一律走 `foreman.py events` | ✅ |
| `worker_done` 缺 `--task-id` / `--dispatch-id` | Orca 拒收，subject 变成 `Rejected worker_done: [FOREMAN:DONE] ...`，协议头正则匹配不到 | **两个都要带**：先 `worker-list --json` 取 taskId 与 dispatchId。`wait_message` 把 `Rejected` 前缀识别成 `rejected` 信号，`events` 直接报错而不静默超时 | ✅ |
| `worker-start --worktree current` | worker 一 `git checkout -b` 就把主管 HEAD 切走；主管的脏文件被 `add` 进提交 | 改动任务一律独立树。foreman 用 `new-top-level --repo id:<repoId>`；`new-child` 是原生替代（多一层父子血缘） | 📖⚠️ |
| `worker_done` 参数不全 | Orca 拒绝：先 `requires outcome` 再 `requires taskId`；worker 反复重试后结束回合 | 完整形态：`--type worker_done --outcome succeeded --task-id <taskId> --subject <协议头> --body <正文>`。派工单已写死 `--outcome`，并给了 `requires taskId` 时的自查补发法 | ✅ |
| worker 不上报就结束回合 | 收到 escalation「Worker stopped without reporting」 | foreman 把 escalation 落进状态机：用 payload 里的 `taskId`/`dispatchId` 反查本仓任务，转成 `[FOREMAN:ESCALATE]` 走正常分支——任务转 `failed`（可重派），已经在 `waiting_answer`/`waiting_human` 的不降级。认不出任务时报错并留档。人工处置先 `worker-read` 看它停在哪，再 `send --to dispatch:<id>` 给指引 | ✅ |
| worker 停了但终端还活 | `worker-stop` 返回 `alreadySettled` 却不杀进程，`worker-list` 仍显示 `live` | `terminal close --terminal <handle>` + `orca orchestration worker-release --dispatch <id>` 兜底 | ✅ |
| pi worker 的 transcript | `worker-read --source transcript` 在 pi 上取不到结构化记录 | 用 `--source auto` 或显式 `--source terminal`；审查只信 diff 与验收输出 | 📖⚠️ |

## foreman 未封装的 Orca 能力

| 能力 | 命令 | 何时需要 | 验证 |
| --- | --- | --- | --- |
| 任务 DAG | `worker-start --deps '[<taskId>,...]'` | foreman 的 `--depends-on` 只是「前置未完成就拒派」的门槛，不做调度、不建 DAG；要编排级依赖顺序在原生层做 | 📖 |
| 按 dispatch 重试 | `worker-start --retry-of <dispatchId>` | 原生路径重派；foreman 用自己的 `dispatch`（复用同一棵树），不用 `--retry-of` | 📖 |
| 读 worker 输出 | `worker-read --dispatch <id> --source auto\|terminal` | 判断"卡住"还是"死循环" | 📖 |
| 停/释放 worker | `worker-stop --dispatch <id>` / `worker-release --dispatch <id>` | 放弃或收尾（foreman 的 `merge` 清理已含 release + task 结算） | ✅ |
| 结算 Orca task | `task-update --id <taskId> --status completed\|failed\|blocked\|pending\|ready\|dispatched --result "..."` | 原生收尾；foreman 清理时自动置 completed | ✅ |
| 查活跃 worker | `worker-list --json` | 排查"到底起没起来"（foreman 在 worker-start 失败时自动查一次） | ✅ |

## 纠正话术（排障时可直接照抄）

- **它在 `--wait` 死等**：停掉当前等待，改事件驱动——派完活结束回合，等 worker 上报再处理。
- **它用了 `--worktree current`**：工作树已被 worker 切走；收尾前先 `git checkout <base>` 再合并，并确认提交里没夹带用户正在编辑的文件；下一个任务用独立树。
- **收到「Worker stopped without reporting」**：先 `worker-read --source terminal` 看它是死循环还是真卡住，再发具体 guidance；两轮不动就 `worker-stop` 换更细的 spec 重派（foreman 用户直接 `foreman.py dispatch`）。

## 已被证伪 / 已废弃的旧说法（勿再沿用）

| 旧说法 | 实况 |
| --- | --- |
| "同一任务 3 次失败会被 `circuit_broken`" | 本版 CLI 无 `circuit_broken`。3 次上限是 **foreman 自己的规则**（`max_attempts`） |
| "`check --wait --timeout-ms` 硬上限 120000" | 无技术上限。foreman 默认 300s，连续两轮空等即拒绝再开窗口 |
| "pi 报 `provider_unsupported`" | 本版 CLI 无此错误码；`--source auto` 会自动回退终端输出 |
| "`--worktree new-child` 是唯一正确选择" | `new-top-level` 同样安全（都避开 `current`）；差别只是父子血缘 |
