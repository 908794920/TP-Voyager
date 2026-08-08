# TP-Voyager Captain Skill

这是 TP-Voyager 面向上层 **Captain AI（船长 AI）** 的唯一入口 Skill。

它不保存任务状态、不直接调用厂商 CLI，也不代替 Runtime 的验证、Evidence、隔离 worktree 或恢复机制。它只负责把“调查、审查、修复”等自然语言目标编排成 TP-Voyager 已支持的高层调用。

## 当前版本

```text
Captain Skill 1.0.2
```

本轮 v1.0.2 新增：

- 标准 YAML frontmatter 与宿主无关 Skill 标识；
- `tp-voyager.manifest.json` 声明 MCP 启动、Captain tools 与 doctor 入口；
- 新只读任务优先使用统一 `read_scope`，CodeBuddy/Qoder 由 Runtime 各自映射；
- `model_policy.allowed_models` 只做 Passenger/Captain 模型池约束，不自动选模或 fallback；
- `worker_profile_ref` 通过 `name/version/sha256` 解析可信 Worker Profile；
- `correlation_id` 只关联外部任务，不接管外部生命周期；
- `task_result.usage` 返回真实 provider Usage Evidence；缺失 Token/Credit/Cost 不推算；
- 保留 `context_files` 作为 CodeBuddy v1.0.1 兼容入口。

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

## 统一 Read Scope

新任务优先通过 `task_dispatch(read_scope=...)` 描述只读范围：

```json
{
  "files": ["README.md"],
  "directories": ["src/parser"],
  "globs": ["tests/parser/**/*.py"]
}
```

TP-Voyager 将同一 Captain Contract 解析为有界具体文件集合：

```text
read_scope
  ├─ CodeBuddy → Context Manifest → immutable snapshot
  └─ Qoder     → ACP host allowed_paths
```

转换必须 fail-closed，不得因为某个 Crew 的内部机制不同而扩大读取权限。
`context_files` 仍保留给旧 CodeBuddy 调用兼容。

## Model / Profile / Correlation

- `model_policy.allowed_models`：Passenger/Captain 允许池；选择仍由 Captain 显式完成。
- `worker_profile_ref`：可信 Profile 的 `name/version/sha256`，内容只进入瞬时 Prompt，不写入 Session metadata。
- `correlation_id`：仅外部关联键，不建立第二任务系统。

## Usage Evidence

TP-Voyager 只记录 CLI/SDK/ACP 实际返回的使用事实，例如 input/output tokens、credits 或 provider-reported cost。不存在的字段保持未知，不按公开价格、倍率或 Token 公式推算。Qoder 在失败/超时前已经返回的 Usage 也会尽量绑定到当前 Attempt。

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
├── SKILL.md                   # Captain AI 正式操作规范
├── tp-voyager.manifest.json  # TP-Voyager 自有发现/启动契约
├── worker-profiles/           # 可选：受哈希约束的 operator-owned Profile store
└── README.md                  # 面向中文使用者的说明
```
