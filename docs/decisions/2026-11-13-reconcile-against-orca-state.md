# 对账：拿通道的权威事实补消息驱动的盲区

- 日期：2026-11-13
- 状态：已生效
- 相关模块：`scripts/foreman.py`（`open_dispatches` / `orca_dispatch_key` / `reconcile_findings` / `cmd_reconcile` / `cli_json` / `Orca.workers(run=)` / `Orca.inbox`）、`docs/governance/orchestration.md`（对账节）、`orchestration-orca.md`

## 决策内容

1. 新增 `foreman.py reconcile`：把在途任务与 Orca 的权威事实对三条账——
   - **dispatch 状态**：Orca 报 `succeeded`/`failed`，任务却还停在 `running`；
   - **liveness**：`ready` 但 worker 已退出（结束回合未上报）；
   - **消息落地**：inbox 里 `read=1` 的 `[FOREMAN:*]` 却没有对应 evidence（被旁路消费）。
2. `doctor` 增加 `orchestration-reconcile`：每次体检跑一次；通道不可达时跳过，不判 FAIL（离线也要能体检）。
3. 判据看**派工记录本身**（有 `dispatch:ctx_*` 才算 Orca 派工），不看当前终端判定出哪种 transport——
   对账只读 Orca CLI，主管在非 Orca 终端里也要能查。
4. `inbox` 一律带 `--full`：不带时 JSON 里没有 `run_id`，无法按 run 隔离（实测）。
5. 新增 `cli_json()` 统一解析 CLI 的 `--json`：Orca 的 `--full` 是多行 pretty JSON，只取最后一行会解析失败（实测踩到）。

## 选择理由

- T-010 悬空的根因不是某一条消息丢了，而是**整个状态机只信消息**：消息被裸 `check` 吃走、worker 崩溃、
  `worker_done` 被拒，任务都会停在 `running`，没有任何东西会再提起它。
- Orca 侧对这三件事都有权威答案（worker-list 的 state/liveness，inbox 的 read 标记）。
  对账把"谁也没在看"变成"体检时必然被看见"。
- 放在 `doctor` 里而不是只做独立命令：独立命令要求人记得跑；体检是每次收尾都会跑的动作。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 只做 escalation → 状态机 | 只兜住"通道主动发 escalation"这一种；DONE 被旁路消费、worker_done 被拒都不发 escalation |
| 加消息确认（ack/重投） | 通道不提供，得自己造一套超时重投，复杂度高且与 Orca 的 delivery contract 冲突 |
| reconcile 按 transport 判定是否执行 | 主管在非 Orca 终端里就查不了；派工记录本身已经能说明这是不是 Orca 派工 |
| 把 reconcile 做成自动修复（改任务状态） | 状态迁移要人判断（DONE 的内容还得 review），自动改会把"看见问题"变成"掩盖问题" |

## 影响范围

- 涉及文件/模块：`foreman.py`、`orchestration.md`、`orchestration-orca.md`、`README.md` 命令表
- 兼容性影响：新增命令与新 doctor 项；`workers()` 增加可选 `run` 参数（默认行为不变）
- 实测：测试 145 → 158；八处变异全部转红（succeeded 分支、静默死亡分支、未读过滤、evidence 豁免、run 过滤、
  整批跳过、transport 判据、cli_json 只取末行）。真机对账跑通：storyboard-maker 84 条消息 / 39 条带协议头 / 7 个 worker
