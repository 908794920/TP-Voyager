# TP-Voyager Captain Skill

Captain Skill 是上层 AI 使用 TP-Voyager 的操作规范。

它只教 Captain **怎么查事实、怎么选择、怎么显式派遣、怎么验收**；不保存任务状态、不直接绕过 Runtime 调供应商 CLI，也不把 TP-Voyager 变成第二个规划 AI。

```text
Captain Skill 1.0.6
```

## Captain 只需要记住 6 个工具

```text
voyager_overview
crew_catalog
crew_health
crew_recommend
task_dispatch
task_result
```

默认 MCP Surface 也只暴露这 6 个工具。低层 Task / Context / Planner / Artifact API 只属于显式 diagnostic surface。

## 标准流程

```text
Passenger 目标
   ↓
voyager_overview
   ↓
crew_catalog(include_models=true)
   ↓
必要时 crew_health / crew_recommend
   ↓
Captain 选择 Crew / Model / Effort
   ↓
task_dispatch
   ↓
task_result + Verification / Evidence
   ↓
Captain 接受 / 拒绝 / 决定下一步
```

## v1.0.6 怎么选模型

不要把模型名称写死在 Skill 里，也不要凭记忆猜“哪个更强”。

`crew_catalog(include_models=true)` 会把四类信息合在 route 上：

```text
Provider live facts           # availability/context/effort/reference multiplier
operator dispatch policy      # allow / deny
operator routing profile      # tier/tasks/risks/suggested effort
Runtime Evidence              # success/duration/usage
```

Captain 重点看：

```text
route_id
available
allowlist_status
routable / routability_status
reference_multiplier
capability_profile
reasoning
history
usage
sources
```

解释规则：

- `routable=true`：policy 允许且 Provider 明确可用；
- `routable=false`：Crew route 未 dispatch-ready、policy 拒绝、Provider disabled 或 policy invalid；
- `routable=null`：允许但实时 availability 未确认；
- `capability_profile` 是 operator 维护的建议资料，不是 Runtime 模型评分；
- `reference_multiplier` 只用于相对消耗比较，`calculation_allowed=false`；
- `usage` 才是任务实际返回的 Usage Evidence；
- `suggested_effort` 只是模型级建议；只有当前 Provider/Backend route 明确支持该 effort 时才传入 `task_dispatch`。当前 CodeBuddy 受控 SDK route 不接受 `reasoning_effort`，不要仅凭 profile 强行下发。

真正下发必须继续写清：

```text
crew=...
model=...
reasoning_effort=...
```

TP-Voyager 不会替换失败模型，也不会自动 fallback。

## `crew_recommend` 的边界

`crew_recommend` 只帮助判断**哪个 Crew 的受控执行路线**与 task kind / capability 匹配，并结合有限历史健康事实排序。

它不是模型自动路由器；模型选择仍看 `crew_catalog` 后由 Captain 决定。

## 任务要保持有界

委派时至少明确：

```text
GOAL
SCOPE / read_scope
CONSTRAINTS
VALIDATION
DELIVERABLE
TIMEOUT
```

Patch 还必须明确：

- allowed / forbidden paths；
- verification command；
- 文件数 / diff 预算；
- Runtime-owned isolated worktree。

不要为了“让任务成功”自动扩大边界。

## 统一 Read Scope

推荐：

```json
{
  "files": ["README.md"],
  "directories": ["src/parser"],
  "globs": ["tests/parser/**/*.py"],
  "max_files": 128,
  "max_bytes": 4194304
}
```

TP-Voyager 会将同一逻辑范围映射到各 Crew 的受控读取机制，不允许供应商差异扩大权限。

## 超时预设

| 预设 | 典型任务 | timeout_seconds |
|---|---|---:|
| quick | 小范围查询 / 快速检查 | 180 |
| investigation | 调研 / 代码理解 | 600 |
| review | Code Review / 故障分析 | 600 |
| patch | 受控 small_patch | 900 |
| verify | 有界验证 | 300 |

超时不是自动重试信号。Captain 看已消耗预算和 Evidence 后决定是否重新派遣。

## Usage / Billing

只相信 Provider 实际返回并被 Runtime 持久化的 `tp-voyager.usage/v1`。

不要：

```text
reference multiplier × token = bill
公开 API 单价 × token = TP-Voyager 账单
```

Provider 没返回的消耗字段保持 unknown。

## repository_research

该 task kind 只做受控公共 GitHub 静态源码研究：Runtime 预检并 shallow clone，Crew 在明确 read scope 中只读，报告由 Runtime 作为 Artifact 返回。

禁止把它当下载器、构建器、依赖安装器、任意 Web crawler 或递归 Agent 调度器。

## 安装

把本目录中的 `SKILL.md` 加载到能够访问 TP-Voyager MCP Server 的上层 AI 环境即可。

本项目不绑定单一 Captain 宿主；只要宿主能使用 TP-Voyager MCP 六工具即可。

## 文件

```text
tp-voyager-captain/
├── SKILL.md
├── tp-voyager.manifest.json
├── worker-profiles/
└── README.md
```

更详细的模型目录配置见仓库 `docs/MODEL_ROUTING.md`。
