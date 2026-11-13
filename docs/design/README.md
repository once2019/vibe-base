# design/ — 设计文档

存放架构设计、模块设计、接口设计。每个文档须在头部标注：

```text
- 变更日期：YYYY-MM-DD
- 变更原因：<本次修改解决了什么问题>
```

同步规则：功能迭代或设计理念变化时必须更新对应文档并刷新上述标注；文档与代码不一致视为任务未完成。

## 权威文档索引

一行一份：`文件名｜一句话职责`，新建即登记，更新前先查（规则见 AGENTS.md §6 权威文档唯一）。

- governance-retrospective.md｜治理复盘方法论与反模式正本
- orchestration-doctrine.md｜执行编排理念：为什么是隔离/事件/状态三层、什么时候不该用
- orchestration-orca.md｜Orca 原生路径的机制坑与人工介入（仅排障时读）
- dispatch-template.md｜派工单各节写法与修改规则
