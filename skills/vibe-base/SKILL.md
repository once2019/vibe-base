---
name: vibe-base
version: 1.0.3
description: 初始化项目的 vibe coding 治理体系：当用户要求初始化项目治理、创建 AGENTS.md、新项目接入 vibe 模板、bootstrap 协作规范时，生成治理骨架（AGENTS.md + docs/ 三层记忆与文档体系），幂等不覆盖已有文件。日常的记忆写作与治理复盘细则由项目 AGENTS.md 直接引用本技能 references/ 下的文件，无需经本 description 触发。
---

# vibe-base — 治理骨架初始化

本技能承载两件事：①治理骨架的初始化流程（下文）；②规范与细则的正本分发——assets/ 含 AGENTS.md 模板与全部细则（`docs/governance/` 四份），随 init 一起落入项目，由项目 AGENTS.md 直接引用，不经本文件路由。

## 初始化流程

1. 确认目标目录：默认当前工作区根目录；用户指定了路径则用其值。

2. 运行脚手架（路径相对本技能根目录）：

   ```bash
   python3 scripts/init_vibe_project.py <目标目录>
   ```

   - 幂等：已存在的文件跳过不覆盖；输出三段——新建清单、跳过清单、待填占位符（文件:行）。
   - 目标目录已有 `AGENTS.md` 时脚本会跳过它：先向用户确认采用"人工合并"还是"模板替换（--force）"，未经确认不得覆盖；`--force` 覆盖前必须展示将被覆盖的文件清单并获用户确认。

3. 生成完成后即结束，不向用户追问项目概述、技术栈、命令等信息：占位符 `<...>` 是待回填区，由 Agent 在后续开发会话中获知实际信息时主动替换（AGENTS.md 头部已内置此机制）。`docs/decisions/_template.md`、`docs/decisions/README.md`、`docs/design/README.md`、`docs/progress/README.md` 是格式示例文件，其占位符永不回填。

4. 向用户报告骨架位置与回填机制一句话说明；脚本的占位符清单是回填进度参考，复跑可查看剩余待回填项。

## 红线

- 模板固有规则（三层记忆协议、双预算、Ask-first 机制、验证留证、安全红线、行为与沟通红线）不得裁剪或弱化，只允许替换占位符与增补项目特定条目。
- `AGENTS.md` 唯一事实来源，不得在其他文档复述其规则。

## Tradeoff

占位符回填制牺牲"一次填完的完整性"换取冷启动零打断；存量信息丰富的项目可初始化时主动回填，豁免等待。
