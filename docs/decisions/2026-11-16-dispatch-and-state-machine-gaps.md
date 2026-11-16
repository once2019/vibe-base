# 派工与状态机的四处补漏，两条纪律进条文

- 日期：2026-11-16
- 状态：已生效
- 相关模块：`skills/vibe-base/assets/scripts/foreman.py`（`TRANSITIONS`、`cmd_review`、`cmd_reply`、`cmd_task_new`、`brief_text`）、`docs/governance/orchestration.md`

## 决策内容

按第一性原理过一遍 storyboard-maker 回流的六个优化点，采纳四处代码修复与两条纪律，其余两项不做。

代码：`TRANSITIONS["failed"]` 增 `review` 并在 `cmd_review` 开入口；`reply` 回填 `asks[].answered_at`；`task new` 输出补范围自检提醒；派工单要求遗留项写成可派单形态。

纪律：收信只用 `foreman events`（通道 `check` 会标已读、绕过状态机）；审查时按 `git log --stat` 核提交叙事与内容是否一致。

## 选择理由

从约束推，不从「报告里列了几条」推。

**约束一：状态事实源是 `task.json`，消息只是事件。** 由此推出：状态机的每条边必须有命令入口。storyboard 的 `dc63e9e` 给 `failed` 加了 `review` 边，但没有任何代码把状态推到 `review`（`set_status(task, "review")` 只出现在 DONE 分支，而 DONE 分支的前置状态集不含 `failed`）。边在表里，路走不通，实测仍只能手改 `task.json`。修法是让 `cmd_review` 接受 `failed` 并自己推回 `review`，让这条边真的有人走。

**约束二：技能不推断未登记的集合，不硬编码项目路径。** 报告提议「扫 goal 文本出现 `note_edit(` 就自动把 `pipeline/ops_log.py` 加进 scope」。T-046 的原话是「调 `ops_log.note_edit(...)`」，里面没有文件路径，靠文本扫不出来；要扫出来得知道「这个函数登记在哪个文件」，那是项目知识。所以这条在技能层不可实现，能做的是把「派工前自己核一遍范围」放在决策发生的时刻：`task new` 输出加一句自检提醒，不做事后启发式。

**约束三：每条规则必须可判定。** 「遗留项写成可派单形态」可判定（有落点、有验收命令），所以能进派工单；「注意写清楚」不可判定，不进。

**约束四：隔离的代价要配套自举，但自举动作因项目而异。** 新树缺 `.env`/`.venv` 是通用现象，具体缺什么、怎么建是项目知识。能做的只有登记制（config 里列要软链的文件），猜不得。本轮不做，见下。

**约束五：通道消息只是事件，不是事实源。** 所以 `orca orchestration check` 消费消息导致状态机卡住，正确修法是纪律（收信只走 `events`，排查用 `--peek`），不是给 `reconcile` 加自动修复：从原文解析 DONE 再推进状态机，等于让工具替主管做判断，错一次就是错账。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| `task new` 扫 goal 里的函数名/登记表名反推 scope | 需要「符号登记在哪个文件」的项目知识，技能层拿不到；T-046 的原文里根本没有文件路径，扫不出来 |
| `reconcile --repair` 从 `check --all` 原文自动补登并推进状态机 | 等于让工具解析自由文本后改状态；判错一次就把错误固化进事实源。手工补登一次（T-044 做过）成本远低于这个风险 |
| `dispatch` 时自动给新树软链 `.env`/`.venv` | 方向对（登记制），但本轮先不做：要新增 config 键与测试，且「哪些文件要带」必须由项目登记，默认必须为空，否则等于猜 |
| `scripts/mutation_check.py --task T-046` 自动跑反证并存证 | 收益真实（反证是最值钱的一步），成本也真实：要按派工单挑变异点、跑测试、还原、归档，约 150 行加测试。留作独立决策，不混进这轮补漏 |
| 给 `task set-acceptance` 加状态推进 | 本仓的实现只在 `pending` 时推进到 `ready`，不存在报告里「把 review 打成 failed」的路径；无 bug 不改 |

## 影响范围

- 涉及文件/模块：`foreman.py` 五处（状态表、`cmd_review`、`cmd_reply`、`cmd_task_new`、`brief_text`）；`orchestration.md` 两条纪律；`README.md` §2 第 8 条一句
- 兼容性影响：已初始化项目需重跑 `init --force` 或人工替换 `scripts/foreman.py`；`failed` 任务从此可直接 `review`，不必先 `dispatch`
- 后续约束：状态机新增任何边，必须同时给出触发它的命令并写进测试，否则视为未完成；`reconcile` 只探测不修状态
