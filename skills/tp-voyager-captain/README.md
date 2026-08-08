# TP-Voyager Captain Skill

这是 TP-Voyager 面向上层 **Captain AI（船长 AI）** 的操作 Skill。

它不会替代 TP-Voyager Runtime，也不会直接集成某个厂商 CLI。它的作用是让能够访问 TP-Voyager MCP Server 的上层 AI 学会：

- 查询当前 Crew；
- 根据能力与健康状态选择合适的 Worker；
- 通过 TP-Voyager 下发受控只读或 Patch 任务；
- 查看 Voyage / Task 进度；
- 获取结构化结果、Verification 与 Evidence；
- 避免绕过 Runtime 直接调用 CodeBuddy/Qoder；
- 避免隐藏 Fallback、无限重试和任务范围膨胀；
- 避免把完整 Worker 日志和大段输出塞回 Captain 上下文。

## 文件

```text
tp-voyager-captain/
├── SKILL.md   # 给 Captain AI 加载的正式操作规范
└── README.md  # 本说明
```

## 使用方式

把 `SKILL.md` 安装或加载到你的上层 AI 环境，并确保该 AI 能访问 TP-Voyager MCP Server。

不同 AI 工具的 Skill 安装方式可能不同，因此本仓库不把 Skill 绑定到 Codex、Claude、Qoder 或其他单一宿主。

加载后，Captain 的标准操作循环应保持：

```text
乘客目标
  ↓
voyager_overview
  ↓
crew_catalog / crew_recommend
  ↓
Captain 选择 Crew
  ↓
task_dispatch
  ↓
voyager_overview
  ↓
task_result
  ↓
Captain 验收 / 决策
```

## 前置条件

要求：

- TP-Voyager Initial Real-Use Baseline v1.0.0 或兼容版本；
- Captain AI 能访问 TP-Voyager MCP tools；
- 目标 Crew 已在本机安装、登录并通过 TP-Voyager Health/Capability 检查。

## 为什么 `SKILL.md` 使用英文

`SKILL.md` 是直接供不同模型和不同 AI 宿主加载的机器操作规范。为减少跨宿主兼容差异，当前保持英文。

面向开发者和使用者的介绍、架构、测试与运维文档以中文为主。
