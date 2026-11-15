# 回流派发反思：重派结算、人工解锁、scan 上限、scope 提醒

- 日期：2026-11-15
- 状态：已生效
- 相关模块：`scripts/foreman.py`（dispatch / task unblock / reconcile / 三个 transport）、`docs/governance/orchestration.md`「重派阶梯」

## 决策内容

storyboard-maker 的 master 会话在 2026-11-15 做了「派发反思落地」（提交 `06466e7`、`fde9688`），四处改动回流进技能版，并修正其中的缺陷：

1. 重派前自动结算上一 attempt 的 worker（`stop_prev_worker`）：Orca 走 `worker-stop` + `worker-release`，hcom 走 `kill <实例名>`，都只停人不删树。
2. `task unblock <id> --reason` 把 `blocked` 拉回 `failed`，`--reset-attempts` 重置阶梯计数。状态机 `blocked` 增加 `failed` 出口。
3. `inbox_scan_limit` 可配置（默认 400），reconcile 不再硬编码 100 条。
4. 重派说明里点名 scope 外路径时当场警告，不阻断。

另加一条技能版自己的改动：worker 名字带 attempt（`w-T-001-a2`），重派不复用旧名。

## 选择理由

storyboard 侧的问题都是实测出来的，不是推演：

- 重派不结算旧 worker → 僵尸会话占树，协议消息在新旧会话间混淆（实测 w-T-036）。
- 重派说明点名了 scope 外的路径，worker 一改就撞 scope 门，两次 FAIL 同源（T-030、T-036）。
- `blocked` 是死端：阶梯用尽后即使人工判明根因（验收环境缺陷）也无法重派，只能改状态文件。
- reconcile 的 inbox 上限硬编码 100，单 run 实测 137 条消息，核对结果不完整还只报 warn。

回流时修正了四处：

- `cmd_task_unblock` 原实现把解锁日志记了两遍（`else` 分支一次，`set_status` 后又无条件一次）。
- Orca 的 `stop_prev_worker` 原给 300 秒超时，两条命令最坏让重派卡十分钟；本地 daemon 调用 30 秒足够。
- retry-note 的路径正则硬编码 `tests|pipeline|app|schemas|references|docs|scripts`，只对 storyboard 成立；技能版改成「含斜杠的路径片段」，并用前后断言排除 URL 与 `~/`。
- hcom 的 `stop_prev_worker` 原为空实现，hcom 模式下重派仍留僵尸；补 `hcom kill <实例名>`。

worker 名字带 attempt 是补充：hcom 模式下 `w-{task_id}` 每次重派都同名，旧会话没停干净时新旧 worker 无法区分。名字唯一后，`events` 的发送者反查与 `doctor` 的提示都更准。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 保留 storyboard 的 foreman.py 分叉，只同步文档 | 分叉会继续扩大；这批改动对所有项目都成立，留在项目里等于第二个事实源 |
| 直接覆盖 storyboard 的 foreman.py | 会丢掉 storyboard 的本地修正；实际做法是回流并修正后再整体覆盖，覆盖前逐块比对过 |
| 重派时删掉旧 worktree 再重建 | 累计 diff 与 cherry-pick 需要旧树；只停人不删树 |
| `blocked` 允许 `dispatched` 直接重派 | 绕过人工判因与 `--retry-note`，等于让阶梯形同虚设；必须经 `failed` |
| retry-note 的路径警告改为阻断 | 说明里可能只是描述性提及，阻断会让合理重派失败；警告足够 |

## 影响范围

- 涉及文件：`scripts/foreman.py`（状态机、config 默认、三个 transport、`cmd_dispatch`、`cmd_task_unblock`、`cmd_reconcile`、子命令注册）、`tests/test_foreman.py`（+6 条）、`docs/governance/orchestration.md`、storyboard-maker 的 `scripts/foreman.py` 与 `orchestration.md`
- 兼容性影响：`blocked` 增加 `failed` 出口属状态机放宽，旧任务文件不受影响；worker 名字格式变化只影响新派工，历史派工记录里的 handle 仍是旧格式，反查按记录本身匹配
- 后续约束：项目自持副本要靠人工同步；下次改动 `scripts/foreman.py` 后要重跑 storyboard 的门禁再提交

## 追记 2026-11-15（被淘汰 attempt 的树）

回流后跑 storyboard 的 doctor，`resource-leaks` 报出 T-036 的两棵树：`w-T-036`（D-001）与 `w-T-036-2`（D-002），
都在 `~/orca/workspaces/`。原因是 `merge` 的清理只处理最后一次 dispatch，前几次 attempt 的树没人管；而 Orca
每个 attempt 建一棵新树，等于每失败一次留一份永久副本。

修法：`merge` 在清理当前 dispatch 后遍历其余 dispatch，删掉仍存在的树与分支（`worktree remove --force` 失败先
`prune` 再重试一次），结果记进 `task["cleanup"]["stale_attempts"]`。hcom 模式所有 attempt 共用一棵树，按路径相等跳过。

实际清理了 T-036 的两棵树与 `once2019/w-T-036*` 两个分支（被淘汰 attempt 的产物，D-003 才是合并的那个），
清理前确认过树是干净的，SHA 留在会话记录里。

测试 181 → 182。
