# 镜像改符号链接，骨架删掉影子文档

- 日期：2026-11-13
- 状态：已生效
- 相关模块：`AGENTS.md`、`docs/governance/`、`scripts/foreman.py`（根侧均为链接）、`skills/vibe-base/assets/`、`skills/vibe-base/scripts/init_vibe_project.py`、`tests/test_mirror.py`

## 决策内容

根目录与 `skills/vibe-base/assets/` 的关系从「两份实体文件 + 测试守漂移」改为「一份实体 + 符号链接」。同时删掉骨架里的 `dispatch-template.md`，控制面不再自持版本号，`doctor` 去掉两条硬编码 agent 检查、新增治理预算检查。

## 选择理由

- 双份实体意味着每次改动写两遍，漏一次就是技能发旧版，而且只能靠测试事后发现。链接让「改哪边都是同一份」，漂移从机制上消失。
- `dispatch-template.md` 讲的是 `brief_text()` 的生成物，文档自己写着"以生成物为准"——它是代码的影子。独有的两条判据（范围必须列路径、验收必须可执行）并进 `orchestration.md` 的派工单节即可。
- 两个版本号（`SKILL.md` 1.1.0、脚本 `VERSION` 1.0.0）已经漂移过，而脚本的版本只有冒烟测试在消费。删掉自持版本，版本只在 `SKILL.md` 一处。
- `doctor` 里 `agent:codex` / `agent:pi` 把某台机器的 agent 选择写进通用模板，信息量接近零（缺二进制时 `dispatch` 本来就 fail-visible）。换成一个可执行的复盘触发：AGENTS.md 行数与单行长度按模板契约检查，决策数 ≥20 提示复盘。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 保留双份实体 + 镜像测试（原状） | 每次改动写两遍，测试只能事后发现漂移，且漂移方向恰好是"技能发旧版" |
| 用构建步骤从根生成 assets | 多一条必须记得跑的命令，Agent 比人更容易漏；生成物也不该进 Git |
| 连状态类文件一起链接 | 根是本仓实况、模板是占位，链接过去等于把本仓会话状态发给所有新项目 |
| 保留 `dispatch-template.md`，只标注"以生成物为准" | 一份需要人工同步的影子文档，迟早与生成物不一致 |

## 影响范围

- 涉及文件/模块：根侧 `AGENTS.md`、`docs/governance/*.md`（7 份）、`docs/decisions/_template.md`、`docs/progress/README.md`、`scripts/foreman.py` 改为符号链接；`skills/vibe-base/assets/docs/governance/dispatch-template.md` 删除；骨架 17 → 16 文件
- 兼容性影响：Windows 检出需要 `core.symlinks=true`（本仓库为 Linux/macOS 单人维护）。`foreman.py --version` 移除，冒烟改用 `--help`。已初始化过旧骨架的项目会多一个 `docs/governance/dispatch-template.md`，重跑 init 不会删除它（需人工删）
- 后续约束：新增骨架文件后，根侧同名文件必须做成链接（`tests/test_mirror.py` 会拦住实体副本）；状态类文件保持独立且模板留占位；改治理预算要同时改 `AGENTS.md` §5 与 `foreman.py` 的 `AGENTS_BUDGET_*`
