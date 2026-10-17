---
name: vibe-init
description: 初始化项目的 vibe coding 治理骨架——生成 AGENTS.md 协作规范与 docs/ 三层记忆体系（decisions 决策记录、memory 会话记忆、specs/design/progress）。当用户要求初始化项目治理、创建 AGENTS.md、新项目接入 vibe 模板、新项目冷启动或 bootstrap Agent 协作规范时使用本技能。
---

# vibe-init — 治理骨架初始化

将仓库初始化为遵循 vibe coding 治理体系的协作仓库：根目录 `AGENTS.md`（L1 长期记忆，唯一起源）+ `docs/`（L2 决策记忆、L3 会话记忆、specs/design/progress）。适用于空仓库冷启动，也适用于尚无治理体系的存量仓库。

## 工作流程

1. 确认目标目录：默认当前工作区根目录；用户指定了路径则用其值。

2. 运行脚手架（路径相对本技能根目录）：

   ```bash
   python3 scripts/init_vibe_project.py <目标目录>
   ```

   - 幂等：已存在的文件跳过不覆盖；输出三段——新建清单、跳过清单、待填占位符（文件:行）。
   - 目标目录已有 `AGENTS.md` 时脚本会跳过它：先向用户确认采用"人工合并"还是"模板替换（--force）"，未经确认不得覆盖。
   - `--force` 覆盖前必须展示将被覆盖的文件清单并获用户确认。

3. 生成完成后即结束，不向用户追问项目概述、技术栈、命令等信息：占位符 `<...>` 是待回填区，由 Agent 在后续开发会话中获知实际信息时主动替换（AGENTS.md 头部已内置此机制）。`docs/decisions/_template.md`、`docs/decisions/README.md`、`docs/design/README.md`、`docs/progress/README.md` 是格式示例文件，其占位符永不回填。

4. 向用户报告骨架位置与回填机制一句话说明即可；脚本的占位符清单是回填进度参考，复跑脚本可查看剩余待回填项。

## Tradeoff

占位符回填制牺牲"一次填完的完整性"换取冷启动零打断——AGENTS.md 在回填完成前信息不完整；存量信息丰富的项目可在初始化时主动回填占位符，豁免等待。

## 红线

- 初始化阶段禁止追问占位符内容：回填是开发过程中的持续行为，不是初始化任务。

- 模板固有规则（三层记忆协议、双预算、Ask-first 机制、验证留证、安全红线）是骨架核心，不得裁剪或弱化；只允许替换 `<...>` 占位符与增补项目特定条目。
- `AGENTS.md` 唯一事实来源：不得在其他文档复述其已有规则；新增规则写入唯一对应章节。

## 后续

- 首次会话起按 `AGENTS.md` §7 标准工作流程运转：读 context.md / handoff.md → 查决策索引最近 3 条 → 动手。
- 记忆与决策的写作细则由 vibe-memory 技能承载（如已安装）；未安装时按 `AGENTS.md` §5 执行。
