# 执行编排细则（规划与实施分离）

> 触发：人类说"用规划与实施分离把 X 落地 / 派 worker 去做 / 并行推进这几件事"。
> **不说就不启用**：休眠期不派工、不探测编排通道、不派生 agent（`.vibe/` 空骨架随 init 备好，目录存在不等于启用）。
> 说了就启用。并行推进多个任务是常规用法，一次只跑一个只是最简单的形态。
> 红线、预算与文档归属见 AGENTS.md（§4 合并权与 Ask-first、§5 记忆归属、§6 文档落点、§7 工作流程）；本文件只写"怎么执行"。
> 理念与"什么时候不该用"见 `docs/governance/orchestration-doctrine.md`；派工单写法见 `docs/governance/dispatch-template.md`；
> Orca 排障见 `docs/governance/orchestration-orca.md`（仅人工介入时读）。控制面脚本：`scripts/foreman.py`（纯标准库）。

## 同机多仓并行

同机多仓并行是支持的用法，本节只讲名字怎么避免互相取走回执，不是在劝你串行。

`foreman_name` 是 hcom 的机器级寻址名，不是仓库标识。两个仓同时派工且名字相同，worker 的 `[FOREMAN:DONE] task=T-001` 会被另一个仓的主管取走，而两个仓的任务号都从 `T-001` 起。默认名带仓库目录名（init 时写进 `config.json`）；init 时若该名已被别的目录占用，自动改用 `<目录名>-<路径哈希4位>-foreman`。`tag` 默认也取目录名，且与名字成对（名字带哈希时 tag 也带），worker 名因此是 `<tag>-*`，`hcom kill tag:<tag>` 只影响本仓。改名用 `init --foreman-name <名字>`，改 tag 用 `init --tag <名字>`。`doctor` 的 `hcom-name` 查这个名字是否被别的目录占用、`hcom-address` 查本会话实际名与配置是否一致，任一条 FAIL 就先别派工（按 fix 改好再派）。Orca 通道按 run_id 与 worker handle 寻址，不受影响。

## 一次闭环

人类给目标 → 主管拆任务/写派工单 → worker 在独立 worktree 实现 → 主管审 diff + 跑验收 → 合并删树 → 竣工单（即 `docs/progress/` 进度文件）。

```bash
F=scripts/foreman.py
python3 $F doctor --repo .          # 体检：git / 编排通道 / agent / 会话接入 / 仓库干净 / docs 落点
python3 $F init --repo .            # 幂等建 .vibe/ 并写入 .git/info/exclude
python3 $F task new --repo . --title "实现 X" --scope "src/" --accept "npm test"
python3 $F dispatch T-001           # 建 worktree/分支并拉起 worker，然后结束回合
python3 $F events --timeout 300     # 阻塞等一条协议消息（结束回合，等通道唤醒）
python3 $F reply T-001 --body "裁决与理由" --decision-doc docs/decisions/<日期>-<主题>.md
python3 $F ask T-001 --question "..." --options "允许,不允许"   # Ask-first 阻塞门（任务挂起）
python3 $F review T-001             # 审 diff + 跑验收 + 存证据 → merge_ready / failed
python3 $F merge T-001              # --no-ff 合并 + 复跑验收 + 删树 + 归档
python3 $F report T-001             # 竣工单 → docs/progress/（入库）
```

每轮只处理一步，然后结束回合等通道唤醒；不轮询、不 sleep。约束的是回合粒度，不是并行度：多个任务可以同时在跑，每个回合仍只处理一条事件。失败一律非零退出 + stderr 说明（fail-visible），不吞错重试。

## 角色与权限

- **主管** = 人类直接对话的会话（每仓唯一；并行的是任务，不是主管）。可跑控制面命令（task / dispatch / events / reply / ask / review / merge / report）。
- **worker** = `dispatch` 拉起的子 agent（收到 `[FOREMAN:*]` 消息或读到 `running` 的派工单）。只实现、只上报；Orca 模式下跑控制面命令会被代码拒绝（hcom 模式没有这层判定，靠派工单红线约束）。
- 主管不写业务代码；worker 不做决策、不碰合并权、不 push。

## 决策档位（只有三个名字，见 AGENTS.md §3）

| 档位 | 谁拍板 | 例子 |
|---|---|---|
| 自决 | worker | 实现细节、命名、范围内小取舍 |
| 主管拍板 | 主管 | 接口形态、文件归属、测试策略、换路、重派、拆任务、扩 scope |
| Ask-first | 人类 | AGENTS.md §4 清单全部：跨模块/架构、public API/schema/数据迁移/删数据、引入依赖、改 CI/部署、测试不绿仍合并 |

- worker 拿不准就发 `ASK`（问题 + 选项 + 推荐项），不许猜；卡住发 `ESCALATE`。
- Ask-first 事项立阻塞门（`ask`）：任务转 `waiting_human`，主管把问题与选项原样转述给人类。
- **Ask-first 裁决必须同时落决策记录**：`reply --decision-doc docs/decisions/<日期>-<主题>.md`，文件必须已存在，否则 `reply` 拒绝。理由：`.vibe/` 会消失，`docs/decisions/` 不会（AGENTS.md §5 决策纪律）。
- `review` 是第二道网：有 Ask-first 裁决但决策记录缺失或已被删 → 检查项 `docs-decisions` 失败，不许 merge。

## 过程态与文档落点（唯一事实源分工）

| 落点 | 内容 | 生命周期 | 入 git |
|---|---|---|---|
| `.vibe/tasks/<id>/task.json` | 任务状态机、scope、验收命令、裁决记录、docs 落点登记 | 过程态；合并后归档到 `.vibe/archive/` | 否 |
| `.vibe/tasks/<id>/brief.md` | 派工单（worker 的唯一事实来源） | 同上 | 否 |
| `.vibe/tasks/<id>/evidence/` | 验收输出、diffstat、协议消息全文 | **审查期**的唯一事实源 | 否 |
| `docs/progress/<日期>-<slug>-<任务id>.md` | 竣工单：任务描述 / 完成状态 / 验证方式 / 遗留问题 | **归档后**的唯一事实源 | 是 |
| `docs/decisions/<日期>-<主题>.md` | Ask-first 裁决与架构决策 | 永久 | 是 |

- **证据两段制**：审查期只认 `evidence/`；归档后只认 `docs/progress/` 的「验证方式」节（可复现命令 + 关键输出 + 覆盖数 n/N）。竣工单不再另存一份。
- 状态词映射（两套词表显式对齐，避免"进行中"两种说法）：`pending / ready / dispatched / running / waiting_answer / waiting_human / review / merge_ready / failed` → 进行中；`completed` → 已完成；`blocked` → 已阻塞。
- `task new` 时即登记落点 `task.json.docs.progress`（不靠推断，AGENTS.md §3），`report` 只写这个路径；落点带任务 id，同一天同名任务不会互相覆盖。

## 记忆归属（与 AGENTS.md §5 一致）

- `docs/memory/` 由主管独占写入；worker 不写 `docs/memory/`，也不写 `docs/progress/`——它的结论走 DONE 正文。
- 在跑任务登记在 `docs/memory/handoff.md` 运行态节：`T-001 在等 D-001 的 DONE`；失效条件写"合并或 abandon 即删"。
- 并行任务的 `context.md` 由主管在合并后统一更新；禁止 worker 各自整文件覆盖。

## 审查只信证据

`review` 六项：`scope`（越界）、`secrets`、`no-skip`（跳测试/`--no-verify` 痕迹）、`non-empty`、`acceptance`（复跑）、`docs-decisions`（决策记录）。前五项可自动判定，第六项是治理门槛。自动全绿 ≠ 符合意图：主管仍须读 diff 与提交叙事，人工复核项随 review 输出列出。

## 失败语义

- 第 1/2 次失败 = 任务可修：修因后重新 `dispatch`（复用同一棵 worktree，主管看到的是累计 diff）。
- 第 3 次失败 = 问题在拆解或定义层面：转 `blocked` 交人类，不再自动重派。
- 合并后复跑验收不过 = 保持 `merge_ready`，人工决定 revert 或修复，不自动重试。

## 前置与安装（Ask-first）

- 编排通道 hcom / Orca 属 AGENTS.md §4「安装系统级依赖」：只提示安装命令，不自动装。
- transport 缺省自动判定（Orca 管辖终端→orca，否则 hcom）；`init --transport hcom|orca` 可固定。
- worker 的 agent 默认跟随主管会话（进程祖先链判定），`--agent` 显式指定优先。

## 纪律

- 不手工编辑 `.vibe/` 下的状态与证据；状态变更只经 `scripts/foreman.py`。
- 控制面动作一律走 `scripts/foreman.py`，不手拼 hcom/orca 命令；worker 只用派工单给定的模板发协议消息。
- 状态唯一事实源是 `task.json`；通道消息只是事件。
- 与 AGENTS.md 冲突时以 AGENTS.md 为准；本文件不自立红线。
