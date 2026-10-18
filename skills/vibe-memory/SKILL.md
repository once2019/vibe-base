---
name: vibe-memory
description: 三层记忆的写作细则。当需要写或更新 docs/memory/context.md 状态快照、docs/memory/handoff.md 会话交接文档、创建或修订 docs/decisions/ 下的决策记录及其 README 索引时使用本技能；会话收尾写 handoff 前必读。当用户要求"更新项目记忆、写交接、登记技术决策、记录这个决策"时也触发。
---

# vibe-memory — 三层记忆写作细则

写 L2 决策记录与 L3 会话记忆前加载本技能。红线与双预算常驻 AGENTS.md §5（不受本技能影响）；本技能只承载"怎么写才合格"的判断性细则，全部提炼自模板实战复盘（`docs/design/governance-retrospective.md`）。

## 触发场景与路由

| 场景 | 读哪份细则 |
| --- | --- |
| 更新 `docs/memory/context.md`（阶段性完成 / 收尾 / 状态变化） | `references/memory-writing.md` 上半 |
| 撰写 `docs/memory/handoff.md`（会话收尾必写） | `references/memory-writing.md` 下半 |
| 创建 `docs/decisions/YYYY-MM-DD-标题.md` | `references/decision-writing.md` |
| 修订既有决策（追记 / 另建新条）/ 维护索引 | `references/decision-writing.md` |
| 会话产出重要分析（比较/调研/根因）需落库 | `references/decision-writing.md` 探索性分析路由节 |

## 工作流程

1. 确认场景命中上表之一；场景外的记忆问题（L1 自身维护、治理复盘）不在本技能范围。
2. 读对应 references 细则，再动笔写目标文件；格式骨架（各文件的既有结构）不改动，只按细则填充内容。
3. 写完自查细则中的病态清单：快照是否写成了日志？运行态是否带了失效条件？否决原因是否空泛？索引是否同步？
4. 收尾前复跑 vibe-init 脚手架可查看占位符残留（如该仓库由 vibe-init 初始化）。

## Tradeoff

细则加载与逐项自查会拖慢写作，偏质量而非速度；琐碎的单行状态更新凭判断简化流程。

## 红线（重申，权威在 AGENTS.md §5）

- 双预算（行数 + 单行字符）超出先裁剪再写入，禁止为守行数写超长行。
- 更新即重写；并行会话先读最新、精确片段替换落地，禁止整文件覆盖式写入。
- 会话收尾必写 handoff.md，无例外；运行态条目必须带失效条件，失效即删。
