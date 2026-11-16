# no-skip 放行改成 config 登记：项目约定不写进会被覆盖的脚本

- 日期：2026-11-16
- 状态：已被 `2026-11-16-env-skip-marker-in-code.md` 取代
- 相关模块：`skills/vibe-base/assets/scripts/foreman.py`（`scan_added_lines`、`compile_skip_allowlist`、`cmd_review`）、`docs/governance/orchestration.md`

## 决策内容

审查门 `no-skip` 增加放行清单，落在 `.vibe/config.json` 的 `skip_allowlist`（正则列表，默认空）。命中的行放行但照报，原文进 `task["review"]["allowed_skips"]`；`secrets` 不参与放行。同时修掉一处计数缺陷：同一行会被嵌套的 marker 子串重复计数（`pytest.skip` 里含 `test.skip(` 与 `t.skip(`），`[:5]` 截断后看不到真正的第二处违规。

## 选择理由

- 解决什么问题：storyboard-maker 的 `no-skip` 原本有一段窄放行，用于仓库惯例「声明环境依赖的跳过」（`pytest.skip("需要 node …")`）。2026-11-13 有决策、实现也进过库，但一次「同步资源回收修补」的整文件覆盖把它连同条件一起删了。此后任何人按惯例写 node 冒烟守卫都会被误判不过，T-015 修过一次，T-047 又踩。
- 根因不是「同步前没看目标文件近期加了什么窄规则」，而是**项目约定存在了会被覆盖的文件里**。同步脚本是工具副本的搬运工，它不知道副本里混了项目知识；靠人记得看，第二次就失效了。
- 由此推出形状：放行清单必须离开脚本，进 config（项目状态，不被同步触碰）。这与 `worktree_links` 同一个模式：因项目而异的清单登记，代码只提供机制，默认空，不猜。
- 约束条件：放行必须可见。放行项静默消失等于审查失明，所以单独返回并写进证据。密钥不参与放行：凭据没有「环境依赖」这种正当理由。
- 删掉后多久能重建：随时可重建，但重建出来的又是脚本里的硬编码，下一次同步再丢一遍。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 照搬 storyboard 的 `SKIP_ALLOW_PREFIX` 硬编码常量 | 项目约定进脚本，就是这次丢功能的成因；换项目还得改代码 |
| 放行项直接丢弃、不出现在输出里 | 放行静默等于审查失明，主管无法判断放过去的是什么 |
| 让 `secrets` 也走同一套放行 | 凭据没有正当豁免理由；一旦能放行，密钥门就形同虚设 |
| 不做放行，改成每次人工判定 `no-skip` FAIL | 把机械门降级成人工门，六项检查里可自动判定的项从五项降到四项 |

## 影响范围

- 涉及文件/模块：`foreman.py`（config 默认值、`compile_skip_allowlist`、`scan_added_lines` 三返回值、`cmd_review` 的 no-skip 检查与证据）；`orchestration.md` 审查节；`README.md` §2 第 4 条
- 兼容性影响：`scan_added_lines` 从二元组变三元组，仓内调用点与测试同步改；已初始化项目默认 `skip_allowlist` 为空，行为与之前一致（任何跳过都拦）
- 后续约束：新增放行模式一律进 config，不进代码；正则写错当场报错，不允许静默退化成「什么都没放行」
