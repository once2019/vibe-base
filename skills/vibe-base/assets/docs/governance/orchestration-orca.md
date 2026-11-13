# Orca 原生路径 — 机制坑与人工操作

- 变更日期：2026-11-13
- 变更原因：并入 vibe-base；去掉旧技能溯源，过程态目录对齐 `.vibe/`
- 读它的条件：**`scripts/foreman.py` 报错需要人工介入**、**要用 foreman 未封装的 Orca 能力**、或**排查 worker 卡死**。日常主管循环不需要读。
- 每条都标了验证状态：`✅实测`=本机跑过命令或 E2E 亲历；`📖查证`=orca `--help`/二进制文本确认；`⚠️待验`=从旧技能继承、未独立验证。

## 通道怎么选（自动）

`foreman.py` 缺省按运行时判定：在 Orca 管辖终端→orca，否则→hcom。判定 = `ORCA_TERMINAL_HANDLE` 存在
+ `terminal show` 复核存活（句柄可能来自已关闭的屏）+ `worker-list` 判断本终端是不是某个 live dispatch
（是则本会话是 worker，控制面命令会被拒绝）。`init --transport hcom|orca` 可固定，`--transport auto` 复位。
自动判定为 orca 但没绑 Run 时不回退 hcom——`doctor` 会明确要求 `run-create`。

探测本身有代价与护栏：结果按仓库缓存在进程内（一条命令只问一次 daemon）；每次 daemon 往返上限
15s（`DETECT_TIMEOUT`），句柄失效时直接短路；外部命令超时统一降级为退出码 124 + 可读原因
（不会抛 Python 栈）。daemon 真卡死时表现为"15s 后拿到明确结论"，而不是挂 4 分钟。

## 何时用 foreman 命令、何时用 Orca 原生命令

| 场景 | 用什么 |
| --- | --- |
| 建任务、派工、收消息、审 diff、合并、竣工单 | `foreman.py`（状态机 + evidence 是它的价值） |
| 任务间有依赖（DAG）、按 dispatch 重试 | Orca 原生命令（foreman 不封装，见下） |
| 判断 worker 卡死还是死循环 | `worker-read --source terminal --dispatch <id>` |

## 机制坑

| 坑 | 症状 | 正确做法 | 验证 |
| --- | --- | --- | --- |
| 没有编排身份 | `send` 报 `no_active_sender_terminal`；`check` 报 `no_active_terminal` —— 主管既收不到也回不了话 | 必须在 Orca 管辖的终端里启动主管会话（`ORCA_TERMINAL_HANDLE` 由 Orca 注入）。`doctor` 的 `orca-identity` 检查该项 | ✅实测（`env -u ORCA_TERMINAL_HANDLE` 复现两个错误码） |
| `check --types` 误解 | 以为它过滤返回内容 | 它是 **`--wait` 的唤醒条件**；返回的 Delivery 永远整批，不按类型过滤；不带 `--wait` 时无效。原生等待要用 `--types`，且**必须含 `status`**——foreman 的 ASK 用默认类型 `status` 发送，漏掉就永远等不到请示 | ✅实测 + 📖help 原文 |
| 验收产物挡合并 | `merge` 复跑验收在主仓库留下 `__pycache__/`、`.pytest_cache/`，下一个任务的 `merge` 报「工作区不干净」 | `init` 现在把 `.vibe` + 常见构建产物（`__pycache__/`、`*.pyc`、`.pytest_cache/`、`node_modules/`、`target/`、`dist/`、`build/` 等）写进 `.git/info/exclude`（本地生效不入库）；老仓库重跑一次 `init` 即可 | ✅E2E 亲历（第二次 merge 被挡后修复） |
| 日志无轮转 | 长期运行 `logs/*.log` 无限增长 | foreman 超过 1MB 自动轮转一份 `.1` | ✅`append_log` 单测 |
| 脚本消费主管邮件 | Orca 明确要求"只观察的脚本用 `--peek --wait`，不得 `--ack`"；用消费式 `check` 会把主管 session 的邮件拿走 | foreman 是消费式（它把内容落 evidence 与状态，信息不丢）。因此**foreman 模式下不要同时让 agent hooks 收信**：被唤醒后应运行 `foreman.py events` 而不是自己 `check`（否则会拿到空批） | 📖help 原文 + ✅E2E 亲历（唤醒后自己 check 收到的是空批） |
| 长阻塞 `--wait` | 主管被锁十几分钟；唤醒注入可能被正在跑的命令吞掉 | 派完就结束回合。**一次 `events` 调用 = 一轮**；连续两轮空等后 `events` 自己拒绝（`MAX_EMPTY_WAITS=2`），要你结束回合。**`--timeout-ms` 没有技术硬上限**（旧技能写的 120000 是 house rule，不是限制） | ✅实测（`--timeout-ms 900000` 未被拒；上限拦截有单测） |
| 把 `events` 当轮询 | 连着开三个窗口（420s / 540s / 540s）空转；旧条文只写「同一 dispatch 最多两轮」，没说「轮」是调用次数还是派工次数 | 规则改成按调用计数并由代码拦：连续两轮空等后第 3 次调用直接 fail，输出指明「结束回合、在 handoff 写一行在等什么」。有消息在等时仍放行（`has_pending` 探测），不会把主管锁在门外 | ✅实测（主管三次连开窗口的真实事故） |
| 手工 `check --json` 排查 | `check` 是消费式的：主管自己跑一次，就把 worker 的 DONE 吃掉了，`events` 随后只会等到超时（真 E2E 亲历） | 主管排查用 `check --peek --json`（只读不标记）或 `--unread`；收信一律走 `foreman.py events`，不要自己 `check` | ✅实测（吃掉一封 DONE，只能让 worker 重发） |
| `worker_done` 缺 `--task-id` | Orca 拒收，subject 变成 `Rejected worker_done: [FOREMAN:DONE] ...`，协议头正则匹配不到 | 派工 prompt 要求先 `worker-list` 取 taskId 再发；`wait_message` 现在把 `Rejected` 前缀识别成 `rejected` 信号，`events` 直接报错而不是静默超时 | ✅实测（首轮 E2E 卡在这里） |
| `worker-start --worktree current` | worker 一 `git checkout -b` 就把主管 HEAD 切走；主管的脏文件被 `add` 进提交 | 改动任务一律独立树。foreman 用 `new-top-level --repo id:<repoId>`（不依赖主管在 Orca 活跃 worktree）；`new-child` 是原生替代（多一层父子血缘） | 📖help 列出 `current|selector|new-child|new-top-level` + ⚠️切 HEAD 为旧技能记述 |
| `worker_done` 参数不全 | Orca 拒绝：`worker_done requires outcome=succeeded\|failed`；补了 outcome 后又报 `worker_done requires taskId`。worker 两次重试失败后结束回合，才触发下面的 escalation | 完整形态：`--type worker_done --outcome succeeded --task-id <taskId> --subject <协议头> --body <正文>`。`worker-list --json` 里的 `taskId`/`dispatchId` 即所需值。foreman 的派工单已写死 `--outcome`，并给了 `requires taskId` 时的自查补发法 | ✅E2E 亲历（两次拒绝原文都在 `orca orchestration inbox`） |
| worker 不上报就结束回合 | 收到 escalation「Worker stopped without reporting」 | 先 `worker-read --source terminal` 看它停在哪，再 `send --to dispatch:<id>` 给指引（它可原地续跑并补发 DONE）；两轮不动就 `worker-stop` 或 `task abandon` 后重派。注意：**escalation 是非协议消息**，foreman `events` 会把它记进 `logs/transport-skipped.log` 并在超时输出里点名提示 | ✅E2E 亲历（本条正是主案例）；✅`events` 提示已实测 |
| worker 停了但终端还活 | `worker-stop` 返回 `alreadySettled` 却不杀进程，`worker-list` 仍显示 `live` | `terminal close --terminal <handle>` + `orca orchestration worker-release --dispatch <id>` 兜底 | ✅实测（E2E 清理三次失败尝试时亲历） |
| pi worker 的 transcript | `worker-read --source transcript` 在 pi 上取不到结构化记录 | 用 `--source auto`（有 transcript 就用，否则回退带标注的终端输出）或显式 `--source terminal`；审查只信 diff 与验收输出 | 📖help（auto 回退规则）；⚠️"pi 无 transcript" 为旧技能记述，旧技能写的错误码 `provider_unsupported` 在本版 CLI 中不存在，勿据此判错 |

## foreman 未封装的 Orca 能力

| 能力 | 命令 | 何时需要 | 验证 |
| --- | --- | --- | --- |
| 任务 DAG | `worker-start --deps '[<taskId>,...]'` | foreman 的 `--depends-on` 只是「前置未完成就拒派」的门槛，不做调度、不建 DAG；真要编排级依赖顺序就在原生层做 | 📖help |
| 按 dispatch 重试 | `worker-start --retry-of <dispatchId>` | 原生路径重派；foreman 用自己的 `dispatch`（复用同一棵树），不用 `--retry-of` | 📖help |
| 读 worker 输出 | `worker-read --dispatch <id> --source auto|terminal` | 判断"卡住"还是"死循环" | 📖help |
| 停/释放 worker | `worker-stop --dispatch <id>` / `worker-release --dispatch <id>` | 放弃或收尾（foreman 的 `merge` 清理已含 release + task 结算） | ✅实测 |
| 结算 Orca task | `task-update --id <taskId> --status completed|failed|blocked|pending|ready|dispatched --result "..."` | 原生收尾；foreman 清理时自动置 completed | ✅实测（cleanup 已调用） |
| 查活跃 worker | `worker-list --json` | 排查"到底起没起来"（foreman 在 worker-start 失败时自动查一次） | ✅实测 |

## 纠正话术（排障时可直接照抄）

- **它在 `--wait` 死等**：停掉当前等待，改事件驱动——派完活结束回合，等 worker 上报再处理。
- **它用了 `--worktree current`**：工作树已被 worker 切走；收尾前先 `git checkout <base>` 再合并，并确认提交里没夹带用户正在编辑的文件；下一个任务用独立树。
- **收到「Worker stopped without reporting」**：先 `worker-read --source terminal` 看它是死循环还是真卡住，再发具体 guidance；两轮不动就 `worker-stop` 换更细的 spec 重派（foreman 用户则直接 `foreman.py dispatch`）。

## 已被证伪 / 已废弃的旧说法（勿再沿用）

| 旧说法 | 实况 |
| --- | --- |
| "同一任务 3 次失败会被 `circuit_broken`" | 本版 CLI 无 `circuit_broken`。3 次上限是 **foreman 自己的规则**（`max_attempts`），Orca 原生不强制 |
| "`check --wait --timeout-ms` 硬上限 120000" | 无技术上限；120s 是旧技能的 house rule。foreman 默认 300s，连续两轮空等即拒绝再开窗口 |
| "pi 报 `provider_unsupported`" | 本版 CLI 无此错误码；`--source auto` 会自动回退终端输出 |
| "`--worktree new-child` 是唯一正确选择" | `new-top-level` 同样安全（都避开 `current`）；差别只是父子血缘 |
