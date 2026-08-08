# TP-Voyager Captain Skill

这是 TP-Voyager 面向上层 **Captain AI（船长 AI）** 的唯一入口 Skill。

它不保存任务状态、不直接调用厂商 CLI，也不代替 Runtime 的验证、Evidence、隔离 worktree 或恢复机制。它只负责把“调查、审查、修复”等自然语言目标编排成 TP-Voyager 已支持的高层调用。

## 当前版本

```text
Captain Skill 1.0.1
```

本轮真实使用反馈后，Skill 新增：

- 首次派遣预检；
- `quick / investigation / review / patch / verify` 超时预设；
- CodeBuddy 只读任务通过 `task_dispatch(context_files=...)` 自动建立最小 Context Manifest；
- Qoder 在需要显式模型时优先验证当前是否仍提供已实测成功的 `Lite`，不静默替换模型；
- Patch 前要求明确/确认写入路径、验证命令和改动预算；
- 默认只使用 6 个 Captain MCP 工具；
- 读取 `task_result.execution_budget` 判断实际耗时和预算，而不是把超时直接解释为 Crew 不可用。

## 默认 Captain 工具

正常使用只需要理解：

```text
crew_catalog
crew_health
crew_recommend
voyager_overview
task_dispatch
task_result
```

TP-Voyager 默认 MCP Surface 也只暴露这 6 个工具。

历史 Task / Context / Planner / Artifact 等低层工具仍可通过显式诊断模式提供给维护者，但 Captain Skill 在常规航行中不得依赖它们。

## 标准流程

```text
乘客目标
  ↓
voyager_overview
  ↓
crew_catalog / crew_health / crew_recommend
  ↓
Captain 明确选择 Crew
  ↓
task_dispatch
  ↓
voyager_overview
  ↓
task_result
  ↓
Captain 根据 Verification / Evidence / Budget 做决定
```

## 超时预设

| 预设 | 典型任务 | timeout_seconds |
|---|---|---:|
| quick | 小范围查询 / 快速检查 | 180 |
| investigation | 调研 / 代码理解 | 600 |
| review | Code Review / 故障分析 | 600 |
| patch | 受控 small_patch | 900 |
| verify | 有界验证 | 300 |

超时预设不是自动重试策略。任务超时后由 Captain 决定是否以更大的显式预算重新派遣。

## CodeBuddy 只读

CodeBuddy 受控只读路线不允许原生文件系统工具自由读取仓库。

Captain 应把最小相关文件列表直接传给：

```text
task_dispatch(..., context_files=[...])
```

TP-Voyager 会复用现有 Context Manifest 机制完成：

```text
文件列表
  → SHA-256 Manifest
  → 漂移校验
  → 有界 Context Snapshot
  → CodeBuddy SDK
```

使用者不需要再手工调用 `context_register/context_verify`。

## Qoder 只读

Qoder 继续走受控 ACP Read-only 路线。

本项目真实使用已经验证过 `Lite` 可用于长调查任务；但模型可用性可能变化，因此需要显式模型时应以当前 Qoder 动态目录为准。若 `Lite` 不再可用，Captain 不得静默切换其他模型。

## Patch

Patch 仍由 Runtime 强制执行：

```text
隔离 Git worktree
+ allowed / forbidden paths
+ 精确 argv 命令白名单
+ Verification
+ Evidence
```

在派遣 Patch 前，Captain 必须已经从乘客目标中得到明确授权，或主动确认：

- 可以修改哪些路径；
- 使用哪些验证命令；
- 最多修改多少文件 / diff；
- 本次超时预算。

Skill 不得为了“让任务成功”自动扩大这些边界。

## 安装

把本目录中的 `SKILL.md` 加载到能够访问 TP-Voyager MCP Server 的上层 AI 环境即可。

不同 AI 宿主的 Skill 安装方式不同，本项目不把 Captain Skill 绑定到 Codex、Claude、Qoder 或其他单一产品。

## 文件

```text
tp-voyager-captain/
├── SKILL.md   # Captain AI 正式操作规范
└── README.md  # 面向中文使用者的说明
```
