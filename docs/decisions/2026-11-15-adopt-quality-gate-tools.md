# 引入三个质量门槛工具：crapper、dryer、mutator

- 日期：2026-11-15
- 状态：已生效
- 相关模块：`docs/governance/coding-discipline.md`「可选质量门槛」、`scripts/foreman.py` 的 `BUILD_NOISE`、storyboard-maker 的 `.gitignore`

## 决策内容

在机器级 `~/tools/` 装三个工具，不进项目依赖、不进 git，产物落 `.metrics/` 并加进忽略规则：

- `crapper`：复杂度与 CRAP 分数
- `dryer`：结构重复候选
- `mutator`：变异点与存活数

`uml-viewer` 不引入。

## 选择理由

实测数据（storyboard-maker，101 个源文件，2446 个测试）：

- crapper 1.7 秒扫完，直接给出最差函数：`generate_scene_shots` CC=149、`main` CC=103、`run_stage` CC=75。这类热点靠人读代码找不出来。
- dryer 给出具体到行号的重复候选，分数 0.86 到 1.00，例如 `asset_design.py:55-60` 与 `stages/assets.py:122-127`。
- mutator 单文件 `ops_log.py` 扫出 101 个变异点，`--scan` 只列不跑，成本可接受。
- 三个工具都是 Python 实现，首次运行自建 `.venv`，不需要系统级安装，也不需要改项目依赖。
- 与 `AGENTS.md` §3「完整性不靠抽样」和 review 的反证要求同源：复杂度和覆盖率给出可判定的数字，变异存活是"测试没测到"的机械证据。

`uml-viewer` 不引入：Clojure + Quil 的图形界面，要 Clojure 环境，输出是给人看的类图。在无头环境跑不起来，且它消费的 `.metrics/*.edn` 已经由前三个工具产出，需要时另装即可。

## 被否决的替代方案

| 方案 | 否决原因 |
| --- | --- |
| 把三个工具装进项目 `.venv` 或 `pyproject` | 会改项目依赖，且每个项目重复一份；工具与项目代码无关 |
| 默认把 CRAP 接成提交前必过 | 需要 `pytest-cov` 与 lcov 链路，属新增依赖；先作为可选门槛，等覆盖率链路稳定再议 |
| 全量跑变异测试 | 每个变异点跑一次测试，2446 个测试的基线 33 秒，全量要几小时；按改动文件跑 |
| 引入 uml-viewer 看依赖图 | Clojure + 图形界面，无头环境不可用 |

## 影响范围

- 涉及文件：`docs/governance/coding-discipline.md`「可选质量门槛」（改写为命令表与阈值说明）、`scripts/foreman.py` 的 `BUILD_NOISE` 增 `.metrics/`、storyboard-maker `.gitignore` 增 `.metrics/`
- 兼容性影响：不装工具的项目按人工判据执行，条文不失效；`init` 新增的忽略项对老仓库要重跑一次 `init` 才生效
- 后续约束：工具是机器级路径 `~/tools/<name>/<name>`，项目文档里不写死项目内路径；升级工具时删掉其 `.venv` 重建，venv 不能整体移动（shebang 与 editable 链接是绝对路径）
