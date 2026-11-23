# vibe-base

给 AI 编程 Agent 用的协作规范。装进项目的是两层东西：治理骨架让每个会话一开始就有记忆、决策和文档的规矩，`init` 之后自动生效；执行编排把规划和实施分开，需要并行时 worker 在独立 worktree 里干活，主管只审结果。两层都落进项目，靠 `AGENTS.md` 驱动，技能删掉也照旧可用。

## 一、结构

| 层 | 常驻载体 | 细则载体 | 生效方式 |
| --- | --- | --- | --- |
| 治理骨架 | `AGENTS.md`（≤100 行且总 ≤10000 字符） | `docs/governance/` 八份细则 | 每次会话自动加载 |
| 执行编排 | `AGENTS.md` §7 一行指针 | `docs/governance/orchestration.md` | 人类说「派 worker」时启用 |

两层都是"常驻层只放红线与目录，细则层放操作条文"。`docs/` 分工：`decisions/` 放决策、`memory/` 放记忆、`progress/` 放竣工单、`specs/` 放规格、`design/` 放设计与调研、`governance/` 放细则。过程态在 `.vibe/`（本地忽略，不入库）。

## 二、开始使用

```bash
mkdir -p ~/.pi/agent/skills          # pi
cp -r skills/vibe-base ~/.pi/agent/skills/

mkdir -p ~/.codex/skills             # codex
cp -r skills/vibe-base ~/.codex/skills/
```

装好之后，新项目里说一句「初始化项目治理」或「init AGENTS」。产出 17 个文件（1 份 `AGENTS.md`、15 份 docs、1 个 `scripts/foreman.py`），外加 `.vibe/` 骨架和忽略规则。幂等，不覆盖已有文件；占位符不追问，agent 在后续开发中自己回填。初始化之后技能可以删掉。

`init` 之后先做三件事：填掉 `AGENTS.md` 里测试、lint、类型检查三条命令，给第一个任务写一条真能跑的验收命令，跑一次 `doctor` 修红项。

忽略规则写进 `.git/info/exclude`，不随仓库分发，所以每个克隆各自跑一次 `init`，否则 `.vibe/` 的状态文件会进提交。

决策数到 20 条时 `doctor` 会提示跑复盘，正本是 `docs/governance/governance-review.md`。

## 三、治理骨架要点

- 三层记忆按寿命分开：L1 `AGENTS.md` 常驻只放目录与红线；L2 `docs/decisions/` 一条决策一个文件；L3 `docs/memory/` 的 `context.md` 记当前状态、`handoff.md` 给下一个会话交接。会话开始读 context、handoff 与决策索引最近三条，收尾必须写 handoff。
- 并行会话写记忆：先读最新版，精确片段替换，禁止整文件覆盖与原地追加。`docs/memory/` 由主管独占写入。
- 双预算：`AGENTS.md` ≤100 行且总 ≤10000 字符，`context.md` ≤40 行且总 ≤3000 字符，`handoff.md` ≤60 行且总 ≤4000 字符；超了 `doctor` 的 `governance-budget` 报 FAIL。
- 决策记录四节：决定什么、选择理由（解决什么问题 / 收益 / 约束 / 删掉后多久能重建）、被否决的方案、影响范围与后续约束。建条即登记索引，`doctor` 的 `decisions-index` 双向核对。
- Ask-first 清单只在 `AGENTS.md` §4 维护：引入依赖、装系统级依赖、改 CI 或部署、删数据、force push。其他文档只引用不复制。
- 编码纪律：`AGENTS.md` §3 是红线，操作条文在 `docs/governance/coding-discipline.md`（三段顺序、单会话最低审查口径：验收先行 / 反证一次 / 独立上下文读 diff、从约束推导、不硬接旧设计、两次失败后攻击前提、非法状态写不出来、幂等、共享状态单写者、测行为不测实现、先造工具、守卫的生命周期）。

## 四、执行编排要点

- 隔离实施：`dispatch` 给每个任务建独立 worktree 与分支，主管拿完整 diff，`merge` 后删树。新树缺主仓的 `.env`/`.venv` 会让第一次验收假红，在 `worktree_links` 里登记要带过去的路径。
- 粒度：一次能审完的改动，diff 三百行上下，口子是能否独立验收，不按文件数切。
- 完成定义先于实现：`--scope` 必须列路径，`--accept` 必须是一条可执行命令。`review` 六项：`scope` / `secrets` / `no-skip` / `non-empty` / `acceptance` / `docs-decisions`，输出存 `evidence/`，`merge` 后主干再跑一次。
- 决策三档：自决（worker）、主管拍板（接口形态 / 测试策略 / 换路 / 重派 / 拆任务 / 扩 scope）、Ask-first（`AGENTS.md` §4 清单，必须落决策记录）。
- 异步回合：回合一做体检、建任务、派工，做完结束回合，不 sleep；被唤醒后进回合二跑 `events`。连续两轮空等后第三次拒绝；每次 `events` 输出末尾重申还在等人拍板的事项。
- 协议三消息：`ASK`（问题 + 选项 + 推荐项）、`DONE`（摘要 + 改动文件 + 自审结论 + 验证输出 + 提交）、`ESCALATE`（已试方案 + 阻塞点）。发 DONE 前自审：机械四项、五轴、反证一次。
- 并行与批次由代码判定：`dispatch` 比对在跑任务的 scope，相交即拒（确要重叠加 `--allow-overlap`）；一串有顺序的子项整批派给一个 worker，每项一个 commit 与一份验收证据；`--depends-on` 只做门槛，不建 DAG。
- 失败阶梯：最多三次。重派必须带 `--retry-note`；第三次失败进 `blocked` 交人，`task unblock --reason` 解锁；主管侧缺陷用 `dispatch --infra "<根因>"`，不占次数。
- 命令都走 `scripts/foreman.py`（纯标准库）：`doctor` `init` `task new` `dispatch` `events` `reply` `ask` `review` `reject` `merge` `report` `reconcile` `task unblock` `task abandon`。

两层的接合点：编排层给可执行命令与证据文件，治理层给审查口径（六项机械检查 + 五轴读 diff + 反证）；编排层给隔离树与 scope 拦截，治理层给记忆归属与术语唯一；编排层给 `ASK` 与阻塞门，治理层给 §4 清单与 `reply --decision-doc` 硬闸。

## 五、依赖

治理骨架不挑 Agent，也不挑 IDE。编排能力有两种跑法，任选其一，`init` 只打印命令，本技能不安装任何依赖。

- 通用模式：终端里用，靠 hcom 命令行工具，任何 CLI 类型的 agent 都能跑，没有 daemon。
- Orca 模式：通信与事件驱动走 Orca 自己的能力，主管会话必须在 Orca 管辖的终端里启动。

可选质量门槛工具，机器级 `~/tools/`，属系统级依赖，首次运行自己建 `.venv`：

```bash
git clone https://github.com/unclebob/crapper ~/tools/crapper
git clone https://github.com/unclebob/dryer   ~/tools/dryer
git clone https://github.com/unclebob/mutator ~/tools/mutator
```

| 工具 | 看什么 | 命令 |
| --- | --- | --- |
| crapper | 复杂度热点与 CRAP 分数 | `~/tools/crapper/crapper <目录>...` |
| dryer | 结构重复候选 | `~/tools/dryer/dryer <目录>...` |
| mutator | 变异点与存活数 | `~/tools/mutator/mutator --scan <文件>` |

不装也能按 `docs/governance/coding-discipline.md` 的人工判据检查，`doctor` 的 `quality-tools` 报 WARN 不报 FAIL。工具一律显式给目录；产物落在 `.metrics/`，要进忽略规则。

控制面脚本 `scripts/foreman.py` 是纯标准库，Python 3.9+ 可跑。

## 六、什么时候用

适合：有可执行的验收命令；任务能拆到一次能审完；需要多任务并行且互相隔离；项目长期维护、决策要能追溯；多个 Agent 或多人交替接手同一仓库；存在不可逆动作需要人把关。

不适合：改一行文案或变量名；需求还在模糊期；需要人在旁边频繁做品味判断；跨仓库改动；一次性脚本；没有测试基础设施的项目（验收命令写不出来，整套审查没有锚点）。
