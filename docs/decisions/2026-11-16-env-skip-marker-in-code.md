# 环境前置跳过用代码内标记，不用 config 清单

- 日期：2026-11-16
- 状态：已生效（取代 `2026-11-16-skip-allowlist-in-config.md`）
- 相关模块：`skills/vibe-base/assets/scripts/foreman.py`（`ENV_SKIP_MARKER`、`scan_added_lines`、`cmd_review`）、`docs/governance/orchestration.md`

## 决策内容

审查门 `no-skip` 的放行改成代码内标记：跳过行带 `env-skip` 即放行（`pytest.skip("需要 node 18")  # env-skip`），其余跳过与 `--no-verify` 照拦。放行项照报，原文进 `task["review"]["allowed_skips"]`；`secrets` 不参与放行。

## 选择理由

上一条决策把放行清单放进 `.vibe/config.json`，理由是「项目约定不写进会被同步覆盖的脚本」。做实现时验了一下前提，发现 `.vibe/` 按设计不入库（`init` 把它写进 `.git/info/exclude`，`doctor` 的 `state-ignored` 还专门检查这一条）。于是 config 清单只活在一台机器上：新克隆、新 worker 都拿不到，同一条惯例又要在每台机器上重新登记一次。这是把同一个病换个地方，不是修。

约束重排后只剩一种位置满足全部四条：放行规则必须随仓库分发、不被工具同步覆盖、可判定、在 diff 里看得见。代码内标记同时满足：它在被审的那份代码里（分发 + 可见），它是工具定义的协议 token（同步覆盖不到，与 `[FOREMAN:ASK]` 同类），它是字符串匹配（可判定）。

代价是每个声明环境前置的跳过点要加标记，一次性机械改动（storyboard-maker 12 处）。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 放行清单进 `.vibe/config.json` | `.vibe/` 不入库，规则不随仓库分发；新克隆要重新登记，等于把同一个病换个地方 |
| 放行清单进仓库根的 `foreman.json` | 能满足分发，但多一个配置文件、多一套「哪份配置优先」的合并规则，而收益只是省掉 12 行标记 |
| 保留脚本里的硬编码前缀常量 | 就是这次丢功能的成因；换项目还得改代码 |
| 让 `secrets` 也走放行 | 凭据没有正当豁免理由，密钥门会形同虚设 |

## 影响范围

- 涉及文件/模块：`foreman.py`（`ENV_SKIP_MARKER`、`scan_added_lines` 三返回值、review 的 no-skip 详情与证据）；`orchestration.md` 审查节；`README.md` §2 第 4 条
- 兼容性影响：`scan_added_lines` 返回三元组；不带标记的跳过从此一律拦，已有项目需给声明环境前置的跳过补标记
- 后续约束：新增放行口径一律走代码内标记，不引入配置文件；标记必须能在 diff 里被审到
