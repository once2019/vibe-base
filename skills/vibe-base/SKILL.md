---
name: vibe-base
version: 1.2.0
description: 初始化项目的 vibe coding 治理体系，并把「规划与实施分离」的执行编排能力一并装进项目：当用户要求初始化项目治理、创建 AGENTS.md、新项目接入 vibe 模板、bootstrap 协作规范时生成治理骨架（AGENTS.md + docs/ 三层记忆与文档体系 + 执行编排块），幂等不覆盖已有文件。已初始化的项目里用户说「用规划与实施分离把 X 落地」「派 worker」「并行推进这几件事」时，按项目内 docs/governance/orchestration.md 执行，不需要重新安装本技能。
---

# vibe-base：治理骨架 + 执行编排分发

本技能承载两件事：①治理骨架的初始化流程（下文）；②规范与细则的正本分发。assets/ 含 AGENTS.md 模板、`docs/governance/` 下八份细则（五份规范类：记忆、决策、L1 维护、复盘、编码纪律；三份编排类：条文、理念、Orca 排障）与 `scripts/foreman.py`，随 init 一起落入项目，由项目 AGENTS.md 直接引用，不经本文件路由。

## 初始化流程

1. 确认目标目录：默认当前工作区根目录；用户指定了路径则用其值。

2. 运行脚手架（路径相对本技能根目录）：

   ```bash
   python3 scripts/init_vibe_project.py <目标目录>
   ```

   - 幂等：已存在的文件跳过不覆盖；输出三段：新建清单、跳过清单、待填占位符（文件:行）。
   - 产出 17 个文件（`AGENTS.md`、15 份 docs、`scripts/foreman.py`），外加 `.vibe/` 骨架与忽略规则；忽略规则写进 `.git/info/exclude`，不随仓库分发。
   - 目标目录已有 `AGENTS.md` 时脚本会跳过它：先向用户确认采用"人工合并"还是"模板替换（--force）"，未经确认不得覆盖；`--force` 覆盖前必须展示将被覆盖的文件清单并获用户确认。

3. 生成完成后即结束，不向用户追问项目概述、技术栈、命令等信息：占位符 `<...>` 是待回填区，由 Agent 在后续开发会话中获知实际信息时主动替换（AGENTS.md 头部已内置此机制）。`docs/decisions/_template.md`、`docs/decisions/README.md`、`docs/design/README.md`、`docs/progress/README.md` 是格式示例文件，其占位符永不回填；`scripts/*.py` 是程序不是模板，不参与占位符扫描。

4. 向用户报告骨架位置与回填机制一句话说明；脚本的占位符清单是回填进度参考，复跑可查看剩余待回填项。

## 执行编排块（规划与实施分离）

init 会把编排能力一起装进项目，形态是「文档 + 控制面脚本 + `.vibe/` 骨架（`config.json` 与忽略规则）」。能力常备、默认不派工：判断是否启用看有没有在跑的任务，没有就不探测编排通道、不派生 agent。

- 触发：人类在会话里说「用规划与实施分离把 X 落地」「派 worker 去做」「并行推进这几件事」。
- 入口：项目内 `docs/governance/orchestration.md`（条文正本，含命令序列、落点分工、决策档位）。
- 执行：`doctor` 体检（18 项）→ `init` → `task new` → `dispatch` → 结束回合；被唤醒后跑 `events` 收协议消息，其输出末尾会重申还在等人拍板的事项。
- 前置：编排通道（hcom / Orca）属项目 AGENTS.md §4 的 Ask-first 事项，只提示安装，不自动装。
- 可选质量门槛：`docs/governance/coding-discipline.md` 用 crapper / dryer / mutator 出机械证据，装在机器级 `~/tools/`，同属系统级依赖。不装按人工判据走，`doctor` 的 `quality-tools` 报 WARN 不报 FAIL。
- 边界：本技能不参与运行期。项目初始化后删掉本技能，这套能力照旧可用，因为它是项目自持的文档与脚本。

## 安装

```bash
mkdir -p ~/.pi/agent/skills && cp -r skills/vibe-base ~/.pi/agent/skills/     # pi
mkdir -p ~/.codex/skills   && cp -r skills/vibe-base ~/.codex/skills/        # codex
```

无第三方依赖（Python 3.9+ 标准库）。依赖与可选项见 README「四、依赖」，产出清单见「六、开始使用」。回归测试不在技能包内，由维护本技能的仓库跑 `python3 -m unittest discover -s tests`。

## 红线

- 模板固有规则（三层记忆协议、双预算、Ask-first 机制、验证留证、安全红线、行为与沟通红线）不得裁剪或弱化，只允许替换占位符与增补项目特定条目。
- `AGENTS.md` 唯一事实来源，不得在其他文档复述其规则。
- 编排块默认不派工：init 只备骨架（`.vibe/` 目录、`config.json`、忽略规则），不装通道、不跑 doctor；判据看有没有在跑的任务，没有就不探测通道。
- 编排块一旦启用，状态变更只经 `scripts/foreman.py`，不手工编辑 `.vibe/` 下的状态与证据。

## Tradeoff

占位符回填制牺牲"一次填完的完整性"换取冷启动零打断；存量信息丰富的项目可初始化时主动回填，豁免等待。其余代价（自持副本不随技能升级、多一个可能不用的脚本）见 README「三、理论支撑与取舍」的对照表。
