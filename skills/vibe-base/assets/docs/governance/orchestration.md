# 执行编排细则（规划与实施分离）

> 触发：人类说"用规划与实施分离把 X 落地 / 派 worker 去做 / 并行推进这几件事"。
> **不说就不启用**：休眠期不派工、不探测编排通道、不派生 agent（`.vibe/` 空骨架随 init 备好，目录存在不等于启用）。
> 说了就启用。并行推进多个任务是常规用法，一次只跑一个只是最简单的形态。
> 红线、预算与文档归属见 AGENTS.md（§4 合并权与 Ask-first、§5 记忆归属、§6 文档落点、§7 工作流程）；本文件只写"怎么执行"。
> 理念与"什么时候不该用"见 `docs/governance/orchestration-doctrine.md`；
> Orca 排障见 `docs/governance/orchestration-orca.md`（仅人工介入时读）。控制面脚本：`scripts/foreman.py`（纯标准库）。

## 同机多仓并行

同机多仓并行是支持的用法，本节只讲名字怎么避免互相取走回执，不是在劝你串行。

`foreman_name` 是 hcom 的机器级寻址名，不是仓库标识。两个仓同时派工且名字相同，worker 的 `[FOREMAN:DONE] task=T-001` 会被另一个仓的主管取走，而两个仓的任务号都从 `T-001` 起。默认名带仓库目录名（init 时写进 `config.json`）；init 时若该名已被别的目录占用，自动改用 `<目录名>-<路径哈希4位>-foreman`。`tag` 默认也取目录名，且与名字成对（名字带哈希时 tag 也带），worker 名因此是 `<tag>-*`，`hcom kill tag:<tag>` 只影响本仓。改名用 `init --foreman-name <名字>`，改 tag 用 `init --tag <名字>`。`doctor` 的 `hcom-name` 查这个名字是否被别的目录占用、`hcom-address` 查本会话实际名与配置是否一致，任一条 FAIL 就先别派工（按 fix 改好再派）。Orca 通道按 run_id 与 worker handle 寻址，不受影响。

## 一次闭环

人类给目标 → 主管拆任务/写派工单 → worker 在独立 worktree 实现并自审 → 主管审 diff + 复跑验收 → 合并删树 → 竣工单（即 `docs/progress/` 进度文件）。

一次派工跨两个回合。`events` 是**第二个回合的入口**，不是派工之后的下一步：

```bash
F=scripts/foreman.py
# 回合一：派工。做完就结束回合，不 sleep、不开等待窗口
python3 $F doctor --repo .          # 体检：git / 编排通道 / agent / 会话接入 / 仓库干净 / docs 落点
python3 $F task new --repo . --title "实现 X" --scope "src/" --accept "npm test"
python3 $F dispatch T-001           # 建 worktree/分支并拉起 worker → 结束回合

# 回合二：被通道唤醒后（终端出现 "You have 1 orchestration message"）才跑
python3 $F events --timeout 300     # 消费这一条：落 evidence、推进状态机
python3 $F review T-001             # DONE → 审 diff + 跑验收 + 存证据
python3 $F merge T-001              # --no-ff 合并 + 复跑验收 + 删树 + 归档
python3 $F report T-001             # 竣工单 → docs/progress/（入库）
```

中途的 `reply`（答复 ASK）、`ask`（Ask-first 阻塞门）都插在回合二里。

- **`events` 是消费唤醒的手段，不是制造等待的手段**。主管空闲时消息自己会叫醒它，主动开窗口只是把自己锁住。
- 一次 `events` 调用算一轮。**连续两轮空等后 `events` 直接拒绝**（`MAX_EMPTY_WAITS`），要你结束回合；空等超时的输出会提示在 `handoff.md` 运行态写一行「在等什么」。
- 不要用 `orca orchestration check` 代替 `events`：`check` 是消费式的，会把消息拿走，`events` 随后只等到超时。
- 每轮只处理一条事件，处理完再决定下一步；约束的是回合粒度，不是并行度——多个任务可以同时在跑。失败一律非零退出 + stderr 说明（fail-visible），不吞错重试。

## 派工单

`task new` 按 `brief_text()` 生成派工单，worker 只认这一份。两条硬判据：**范围必须列路径**（禁写"相关文件"，否则等于全库），**验收必须是一条可执行命令**（禁写"确保测试通过"，worker 会自己编一个）。有前置任务或重派时，生成物另带「前置任务」「重派说明」两节。其余各节写法见生成物本身。

## 派发策略

派工不是把任务丢出去。`task new` 到 `dispatch` 之间主管要做五个决定，每个都有机械判据兜底。

### 拆到什么粒度

一个任务 = 一次能审完的改动：diff 控制在 300 行内，主管五分钟能读完并说清"这符合要求"。拆的口子是**可独立验收**（自带验收命令、合并后主干仍绿），不是按文件拆：改 5 个文件做成一件事，是一个任务；做三件事各改 2 个文件，是三个任务。

拆不动的信号：验收命令写不出来（要什么还没想清，先回去问人类）、范围列不出路径（边界没定）、要跨仓库改（不在本仓编排范围内）。

### 批次：一串依赖项当一个任务

意图和规格拆完，常得到一串有顺序的子项。逐个派工意味着每个子项一轮 `review` + `merge`，主管被这一串锁住；而这条链本来就跑不成并行，拆开派并不换来并行度。把整条链当一个任务派出去，worker 在同一棵树里逐项做完，主管只在整批结束时审一次。

`task new` 用重复的 `--item` / `--accept` 描述子项，两者按顺序配对：

```bash
python3 scripts/foreman.py task new --repo . --title "EP1 三处修" \
  --scope "pipeline/" --scope "references/" \
  --item "改 audit 硬不变量" --accept "pytest tests/test_audit.py -q" \
  --item "改 prose 规则 9.5" --accept "pytest tests/test_prose.py -q" \
  --item "补回归用例" --accept "pytest tests/test_regression.py -q"
```

- 派工单多出「子项」节：按顺序做、每项一个 commit、逐项跑完再下一项，DONE 正文列出每个子项对应的 commit。
- `review` 逐项跑验收，每项一份证据（`acceptance-<dispatch>-NN.log`），检查项叫 `acceptance:1 <标题>`，失败时直接点名第几项。
- `merge` 后复跑同样逐项；竣工单列子项表（子项 / 验收命令 / 结果）。
- 子项之间可以有依赖，这是批次的本意；批次之间仍按下一节那两条判据决定并行还是串行。

该进批次：同一模块、彼此同质、有先后关系、单批 diff 在 300 行上下。
不该进批次：会触发 Ask-first 的（一个子项卡住整批，不如单独派）、跨模块架构改动（方向错要到很晚才发现）、超出单个 worker 上下文的大活（做到第五项时它已经忘了第一项的约定）。

代价：整批只有一次 review，diff 更长，主管读得粗一点；第 3 项失败整批回退（重派在同一棵树里接着做，已提交的项不用重做）；批次 scope 是各子项的并集，与别的任务撞车的概率比单项高。

### 谁来拆

默认主管拆。拆分的产物就是派工单的两条硬判据（范围、验收），而这两条正是审查的锚点：范围由 worker 自己定，`review` 的 `scope` 检查就恒真（自己划的圈自己遵守）；验收由 worker 自己定，`acceptance` 检查也恒真，等于自证清白。`dispatch` 的 scope 相交拦截与 `depends_on` 门槛同样依赖主管定的范围与依赖，下放拆分会让这几道门一起失效。

要下放，走两段式，别把拆分直接塞进实施任务：

1. 先派一个**拆分/调研**任务：范围写"只读，产出方案文档"，验收写"方案文档存在且列出子项与各自验收命令"，产出是 DONE 正文里的子项清单。
2. 主管审这份清单（顺序对不对、粒度合不合适、验收命令能不能跑、有没有会触发 Ask-first 的项），再用 `task new --item … --accept …` 建实施批次派出去。

可以真下放的情形：子项彼此独立、验收口径统一（一条全局命令覆盖，比如 `mypy` 全绿）、拆法是显然的（按目录或按文件批量）。这时让 worker 自己分块，省一次往返。

### 并行还是串行

可以并行：scope 不相交、验收互不依赖。必须串行：改同一批文件（合并必冲突），或后者的产物依赖前者（接口、契约、表结构）。

两条判据由代码判定，不靠主管记性：

- `task new --depends-on T-001`：前置未 `completed` 时 `dispatch` 直接拒绝，报出前置当前状态。它只做门槛，不做调度（不建 DAG、不自动触发后续任务）。
- `dispatch` 比对在跑任务（`dispatched` / `running` / `waiting_*` / `review` / `merge_ready`）的 scope：与本任务相交就拒绝，并列出相交的那一对路径。确要同一处并行（各改一个不相邻的函数），加 `--allow-overlap` 显式放行，合并冲突自己收。

### 谁去干

`task new --agent` 或 `dispatch --agent` 指定，不指定就用主管会话的 agent。选法按"要读多少上下文"分：跨模块、要读多处既有实现、要判断设计取舍的，给能力强的 agent；机械替换、改文案、补测试这类，给便宜快的。

### 重派阶梯

同一个任务最多派三次（`max_attempts`），每次的要求不同：

| 第几次 | 主管必须先做什么 | 机械判据 |
|---|---|---|
| 1 | 写清范围与验收命令 | 无验收命令不许派 |
| 2 | 至少改一样东西：缩 scope、补背景（`--notes`）、换 agent、换验收口径 | 不带 `--retry-note` 直接拒绝 |
| 3 | 同上，并准备拆任务 | 同上；再失败进 `blocked` 交人 |

`--retry-note` 会写进派工单的「重派说明」节，worker 看得到上一次为什么失败、这次改了什么。不写说明的重派，等于让它重跑一遍同样的错误。

三次用完仍失败，任务进 `blocked`。这时该问的是"任务是不是拆大了"，而不是"再派一次会不会好"。

### 不派工的场合

见 `orchestration-doctrine.md`。一句话：改一行文案、需求还没想清、需要全程做品味判断、跨仓库改动，直接做更快。

## 角色与权限

- **主管** = 人类直接对话的会话（每仓唯一；并行的是任务，不是主管）。可跑控制面命令（task / dispatch / events / reply / ask / review / merge / report）。
- **worker** = `dispatch` 拉起的子 agent（收到 `[FOREMAN:*]` 消息或读到 `running` 的派工单）。只实现、只上报；发 DONE 之前必须先自审（范围 / 验收 / 无跳过 / 无密钥四项，见派工单「自审」节）并修完，DONE 正文带这四项结论。Orca 模式下跑控制面命令会被代码拒绝（hcom 模式没有这层判定，靠派工单红线约束）。
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

`review` 六项：`scope`（越界）、`secrets`、`no-skip`（跳测试/`--no-verify` 痕迹）、`non-empty`、`acceptance`（复跑）、`docs-decisions`（决策记录）。前五项可自动判定，第六项是治理门槛。

自动全绿只证明"没有明显犯规"，不证明"对且值这个复杂度"。主管按五轴读 diff：

| 轴 | 要回答的问题 |
|---|---|
| 正确性 | 边界（空值/0/超长/并发）、错误路径、测试测的是行为还是实现细节 |
| 可读性 | 命名说得出意图？控制流绕不绕？有没有为"以后可能用"加的抽象、注释掉的死代码 |
| 架构 | 跟着既有模式？特性逻辑有没有塞进共用模块？有没有近重复的既有工具？改完读者要记的概念变少了吗 |
| 安全 | 外部输入在边界校验？密钥不进代码与日志？SQL 参数化 |
| 性能 | 没有 N+1、无界循环、热路径新建大对象 |

**必须做一次反证**：取反本次新增的一个条件（或删一条断言），确认对应测试转红再还原。全绿说明缺测试，补测试前不许 merge——这是"测试真的测到本次改动"的唯一证据。

结论逐条标严重度：`阻断`（不许合并）/ `必须改` / `建议` / `备查`。不带严重度的评论视为审查未完成，worker 无法判断哪条必改；指出结构问题时一并给改法（换掉条件链、抽 helper、复用既有权威函数），只说"这里复杂"等于没审。

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
