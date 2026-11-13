# doctor 新增 decisions-index 检查：决策索引双向一致

- 日期：2026-11-13
- 状态：已生效
- 相关模块：`scripts/foreman.py`（`decisions_index_report` + `cmd_doctor`）、`tests/test_foreman.py`、`skills/vibe-base/README.md`

## 决策内容

`doctor` 增加第 14 项检查 `decisions-index`：核对 `docs/decisions/README.md` 索引与决策文件的双向一致——**孤儿**（文件存在但索引里没有任何条目）与**死链**（索引条目指向不存在的文件）。任一出现即 FAIL。同时把 `governance-budget` 里「决策 N 条」的口径从 `glob("*.md")` 改成排除 `README.md`/`_template.md`（此前多报 2 条）。

## 选择理由

- 实测依据：storyboard-maker 首轮治理复盘里，182 份决策文件有 1 份从未登记、索引里另有 1 条指向不存在文件、5 份文件被重复登记。这类缺陷人眼扫不出来，靠「新建即登记」的自觉执行了 182 次就漏了。
- 规则已写在 L1（§6 新建即登记），缺的是判定手段。`l1-maintenance.md` 的可判定性铁律要求每条规则能被 lint/测试/评审验证，这条此前只能靠评审。
- 与 `governance-budget` 同属「把定期自查变成每次体检都看得见的一条」，放在同一个位置、同一档严重度（FAIL），避免出现「有的规则硬、有的规则软」的错觉。

## 判定口径（三条边界）

- 只解析以 `- YYYY-MM-DD` 开头的行：索引头部的格式示例（`- YYYY-MM-DD｜<模块>｜…`）与说明文字不算条目，否则每个项目都会报一条假死链。
- 同一文件被多条索引行引用**合法**：追记行各自带一句改动说明，是索引的增值信息，不是重复登记错误。
- 索引文件本身不存在算 FAIL（`decisions-index` 单独一条），而不是当作「没有决策」放行——索引是决策的唯一可查面，缺了等于决策库不可检索。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 只在复盘时人工核对 | 复盘是低频动作；索引缺陷恰好在两次复盘之间累积，等复盘时已经要补几十条 |
| 做成 WARN（不参与 ok 判定） | `doctor` 只有 ok/FAIL 两档，加第三档要改输出契约与全部消费方；且孤儿/死链都是规则明文违反，与 `governance-budget` 同级合理 |
| 顺带把重复登记也判违规 | 追记行是有意义的索引条目，判违规会逼人删掉改动说明，损失信息 |
| 在 `review` 里检查（只挡 merge） | 索引维护与任务无关，多数任务不碰决策库，挡 merge 只会被绕过 |

## 影响范围

- 涉及文件/模块：`skills/vibe-base/assets/scripts/foreman.py`（新增 `decisions_index_report` + doctor 第 14 项 + 决策计数口径修正）、`tests/test_foreman.py`（`TestDecisionsIndex` 4 例：一致、孤儿+死链、缺索引、doctor 集成）、`skills/vibe-base/README.md`（`doctor` 一行职责）
- 兼容性影响：已初始化项目升级后 `doctor` 可能变红（索引有孤儿/死链），修法是补一行索引或删一条死链，不涉及代码
- 实测：storyboard-maker `188 条索引 / 182 份文件，双向一致`；vibe-base 自身 `7 条索引 / 7 份文件`
