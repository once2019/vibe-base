# 派发策略：批次任务 + 三道机械门槛

- 日期：2026-11-13
- 状态：已生效
- 相关模块：`scripts/foreman.py`（`build_items` / `acceptance_items` / `run_acceptance_set` / `scope_conflicts` / `unmet_dependencies` / `cmd_dispatch`）、`docs/governance/orchestration.md`（派发策略节）、`tests/test_foreman.py`

## 决策内容

主管派工从「一个任务一条验收命令，逐个派」扩成五件事：拆粒度、**批次**、并行还是串行、选谁干、重派阶梯。后三件由代码兜底：

1. **批次任务**：`task new` 的 `--accept` 可重复，`--item` 给子项标题，两者按顺序配对。`task.json.items[]` 存子项；派工单多「子项」节（按顺序做、每项一个 commit）；`review` / `merge` 逐项跑验收、逐项留证据（`acceptance-<dispatch>-NN.log`），检查项叫 `acceptance:<n> <标题>`；竣工单列子项表。
2. **依赖门槛**：`task new --depends-on T-001,T-002`。`dispatch` 时前置未 `completed` 直接拒绝并报出前置状态。
3. **scope 相交拦截**：`dispatch` 比对在跑任务（`dispatched` / `running` / `waiting_*` / `review` / `merge_ready`）的 scope，相交即拒绝并列出相交的那一对路径；`--allow-overlap` 显式放行。
4. **重派阶梯**：`failed` 状态重派必须带 `--retry-note`，写清这次改了什么（缩 scope / 补背景 / 换 agent / 换验收口径），该说明进派工单的「重派说明」节。

## 选择理由

- 实测场景：意图 → 规格 → 任务拆分之后，得到的是**一串有依赖的子项**。逐个派工，每个子项一轮 `review` + `merge`，主管被整条链锁住；而这条链本来跑不成并行，拆开派换不来并行度。整条链当一个任务派，主管只在整批结束时审一次。
- 逐项验收是这个方案的成立条件。整批只跑一条 `&&` 串起来的命令，失败时不知道第几项挂了，review 的 `acceptance` 也就退化成"整批过没过"。逐项留证让「一次 review」不至于丢掉定位能力。
- 依赖与 scope 相交都是可机械判定的，靠主管记性必然漏：写依赖是纯手工登记，scope 相交要在脑子里比对所有在跑任务。放进 `dispatch` 的入口检查，漏了就被拦。
- 重派不给说明，等于让 worker 重跑一遍同样的错误。要求写一句"这次改了什么"，成本一行字，收益是把重派变成有假设的实验。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 只写文档，不做批次支持 | 一次 review 覆盖 N 个子项时，验收失败无法定位到项，主管只能整批打回，等于把返工成本转嫁给 worker |
| 批次用 `&&` 串命令凑 | 失败信息只有整体 exit code；`evidence/` 里一份日志混着 N 条命令输出，复现时还得自己拆 |
| 给批次加进度上报协议（`[FOREMAN:PROGRESS]`） | 每报一次就唤醒主管一个回合，与"解放主管"的初衷相反；DONE 里列 commit 已足够事后审 |
| 让 `dispatch` 自动按 `depends_on` 串成 DAG 调度 | 调度器要处理并发上限、失败传播、重试策略，是另一个产品；本仓要的是"别在依赖没完成时派工"这道门槛 |
| scope 相交一律禁止并行 | 两个任务各改同一目录下的不同文件是常见且安全的；留 `--allow-overlap` 让主管显式承担合并风险 |
| 重派时强制改 scope | 有些失败是背景不足（worker 没读到关键文档），补 `--notes` 才是对的修法；只要求"写清改了什么"，不指定改哪样 |

## 影响范围

- 涉及文件/模块：`skills/vibe-base/assets/scripts/foreman.py`（新增 4 个纯函数 + `dispatch` 三道门 + `review`/`merge`/`report`/`brief` 逐项化）、`orchestration.md` 新增「派发策略」节、`orchestration-orca.md` 更正「foreman 是线性单任务」的表述（`--depends-on` 是门槛不是调度）
- 兼容性影响：`task new --accept` 由单值改为可重复，单条命令的任务形态与证据文件名不变（`acceptance-<dispatch>.log`）；`task set-acceptance` 会清空 `items`（单条命令替换子项清单）；旧 `task.json` 没有 `items` 字段时按单条验收处理
- 实测：`/tmp/e2e-skill` 用真 Orca 通道跑了一个 2 子项批次——worker 产出两个 commit（`56a19f2`、`93323e0`），`review` 两项各自通过并留证，合并后竣工单列出子项表
