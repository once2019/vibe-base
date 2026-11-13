# 会话交接文档（handoff.md）

> 会话收尾必写本文件（供新会话快速恢复工作；旧"上下文 70% 触发"条款不可测量，已废）。
> 新会话第一件事=读 `docs/memory/context.md` + 本文件，再查 `docs/decisions/README.md` 索引最近 3 条。
> 规则与预算见 AGENTS.md §5（交接知识/运行态分区｜≤60 行且单行 ≤150 字符｜失效运行态条目更新时清除）。

- 撰写时间：2026-11-13
- 交接原因：foreman 并入 vibe-base 技能完成（单技能交付），代码与文档已落，剩人工安装与真实 E2E

## 运行态（失效即删；每条必须带失效条件）

- 验证用临时仓库 `/tmp/vb2`（失效条件：/tmp 清理或本会话结束后即可删）
- 源技能仓库 `/home/sun/vibe-foreman` 仍保留原样、未改动（失效条件：确认不再需要独立 foreman 后归档）

## 已完成工作摘要

- 技能包：`skills/vibe-base` 1.0.3→1.1.0，新增 `assets/scripts/foreman.py`（1879 行，纯标准库）、
  `assets/docs/governance/` 编排四份（orchestration + doctrine + orca + dispatch-template）、
  `tests/`（79 项）
- 规则重写（AGENTS.md 93/100 行）：§4 评审权下移到「人类或主管 Agent + Ask-first 清单为人类强制项」、
  §5 记忆归属（docs/memory 主管独占）、§3 决策档位三词、§6 纳入 `.vibe/`
- 过程态重设计：`.foreman/`→`.vibe/`；`task.json.docs.progress` 显式登记落点；`report` 写 `docs/progress/`；
  `reply --decision-doc` 为 Ask-first 硬门槛；`review` 增 `docs-decisions` 检查项
- 脚手架：新增文件 13→18（拆解后 17）；占位符扫描排除 `scripts/` 与三份参考文档；不再复制 `__pycache__`
- 追补：未 init 时的 traceback 改为可读错误（`Repo.config()` 一处兜住）；`.vibe/` 空骨架改由 init 一并建好，
  「默认休眠」四条条文同步改写；测试 79→83 项
- 追补二：hcom 名字撞线防护（默认名带仓库名 + doctor 两条检查 + 撞名自动加哈希 + tag 带项目前缀），
  审查后修掉前缀比较误判、tag 未随名字加哈希、模板漂移静默跳过、工具缓存被复制进项目；
  第二轮整体审查：版本声明改 3.9+、SKILL.md 数字对齐、镜像不变量加测试、merge 后验收失败分支补测、删死代码；
  另加 worker 自审与主管复核统一到 code-review 五轴 + 反证 + 严重度；
  技能设计反思后精简：镜像改符号链接、删影子文档与死配置、doctor 换掉硬编码 agent 检查，测试 114 项
- 收口：编排附属 3 份从 design/ 挪进 governance/；`governance-retrospective.md` 拆解——8 问与再复盘信号并入
  `governance-review.md`，证据链只留本仓库 design/，assets/docs/design/ 只剩占位 README
- 验证：79 测试全绿（含变异实验）、双跑幂等、md5 镜像一致（详见 progress 文件）

## 未完成工作清单

- [x] `doctor` 新增 `decisions-index`（2026-11-13）：孤儿/死链即 FAIL，测试 117→121

- [x] **真实通道 E2E 已跑通**（Orca，`/tmp/e2e-skill`，2026-11-13）：两轮全链路
      dispatch → worker(pi) → DONE → review → merge → report；暴露并修掉 4 个问题（见 progress）
- [x] **storyboard-maker 已升级**（`6a36a74`，2026-11-13）：控制面同步到 `24d2756`（E2E 修复版），
      骨架其余 14 份逐字节一致、AGENTS.md 保持项目版；四门禁绿（pytest 1981）。该仓仍未 push（ahead 266）
- [x] `~/storyboard-maker` 接入完成并提交（`4db5885`）：控制面先过一遍标准 lint/类型
      （ruff 14→0、mypy 20→6→0），故无需在项目侧加 overrides

## 关键决策引用

- [单技能融合执行编排](../decisions/2026-11-13-单技能融合执行编排.md)｜改编排块或 AGENTS.md 相关条文前必读
- [权威文档登记表与核实门槛](../decisions/2026-10-24-权威文档登记表与核实门槛.md)｜新建权威文档前必读

## 新会话必须遵守的约束

- 编排块的条文正本是 `docs/governance/orchestration.md`（与 assets 成对）；不得另建第二份编排说明
- 正本只有 `skills/vibe-base/assets/`；根侧同名文件是**符号链接**（一份实体）。例外是 5 个状态类文件
  （memory/context、memory/handoff、decisions/README、specs/README、design/README）：根=实况、模板=占位。
  `docs/design/governance-retrospective.md` 只在根（维护记录，不随 init 分发）
- 状态变更只经 `scripts/foreman.py`，不手改 `.vibe/` 下的 task.json 与 evidence
- 改 `foreman.py` 后必须跑仓库根 `python3 -m unittest discover -s tests`（`tests/` 属本仓库，不随技能分发）
