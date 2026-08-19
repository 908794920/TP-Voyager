# TP-Voyager Captain Skill

Captain Skill 是上层 AI 使用 TP-Voyager 的操作规范。

它只教 Captain **怎么查事实、怎么选择、怎么显式派遣、怎么验收**；不保存任务状态、不直接绕过 Runtime 调供应商 CLI，也不把 TP-Voyager 变成第二个规划 AI。

## 自动触发

无需在每个新会话输入 `$tp-voyager-captain`。对有界仓库调研、Code Review、
故障分析、独立验证和小范围补丁，Captain 会主动判断是否应通过已挂载的 MCP
委派 Crew；普通问答和一步即可完成的修改保持直接处理。自动触发不代表自动选模：
Captain 仍须读取当前目录/策略、显式选择 Crew 与模型，并在失败、重试、fallback
或扩大范围时停下等待人类决定。

```text
Captain Skill 1.0.9
```

## Captain MCP 合约

`tp-voyager.manifest.json` 中的 `mcp.required_captain_tools` 是 Captain 默认工具 allow-list 的唯一配置事实；本文不维护第二份启动命令或工具清单。

默认 MCP Surface 必须与 manifest 一致。低层 Task / Context / Planner / Artifact API 只属于显式 diagnostic surface。

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

## v1.0.7 怎么选模型

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
- `suggested_effort` 只是模型级建议；只有当前 Provider/Backend route 明确支持该 effort 时才传入 `task_dispatch` 的 `model_parameters`。当前 CodeBuddy 受控 SDK route 支持 `low|medium|high|xhigh`；它不支持 `context_window_tokens`，不得强行下发。Qoder 的 `context_window_tokens` 则通过官方 CLI 的每会话启动参数下发，不依赖 ACP config option。

真正下发必须继续写清：

```text
crew=...
model=...
model_parameters={"reasoning_effort":"...","context_window_tokens":200000}
```

TP-Voyager 不会替换失败模型，也不会自动 fallback。

参数请求不是展示提示：Runtime 在创建任务前依据当前 Provider 动态目录检查模型是否支持该 effort，以及 Qoder 是否支持该精确上下文窗口。`task_result.usage` 只显示 Provider 实报的 token、Credit、费用和货币；`status=provider_omitted` 表示 Provider 未返回，绝不可自行估算。

`capability_profile` 还会携带 `profile_confidence`、`benchmark_evidence`、
`recommended_tasks`、`risk_boundaries` 和可选的 `evidence_refs`。这些资料帮助
Captain 理解模型的能力形状，但**不得把 benchmark 分数变成自动路由优先级**。

如果 `profile_evidence_status` 为 `stale` / `unverified` / `rejected`，应把
该能力画像视为需要复核的 operator 资料；它不会改变 dispatch policy 的
allowed/denied。模型授权与能力认知始终是两套不同事实。

本地 Evidence 只按受信 root alias、相对路径和 SHA-256 校验，不自动进入 Crew Prompt。

## `crew_recommend` 的边界

`crew_recommend` 只帮助判断**哪个 Crew 的受控执行路线**与 task kind / capability 匹配，并结合有限历史健康事实排序。

它不是模型自动路由器；模型选择仍看 `crew_catalog` 后由 Captain 决定。

## 任务要保持有界

委派时至少明确：

```text
GOAL
WORKSPACE / SCOPE
CONSTRAINTS
VALIDATION
DELIVERABLE
TIMEOUT
```

普通本地仓库的 **normal workspace read-only**（`research` / `code_review` /
`test_failure_triage`）默认使用真实 `cwd`，**do not provide `read_scope` by
default**。任务的 mission、权限、时间和角色仍然有界，但不要让 Captain 预估
仓库有多少文件或字节来决定能不能 dispatch。

Patch 还必须明确：

- allowed / forbidden paths；
- verification command；
- 文件数 / diff 预算；
- Runtime-owned isolated worktree。

不要为了“让任务成功”自动扩大边界。

## Explicit frozen/bounded corpus

只有当任务明确要求 **explicit frozen/bounded corpus** 时才传 `read_scope`：

```json
{
  "files": ["README.md"],
  "directories": ["src/parser"],
  "globs": ["tests/parser/**/*.py"],
  "max_files": 128,
  "max_bytes": 4194304
}
```

此时现有 `max_files` / `max_bytes` 继续 fail-closed。`repository_research`、
`verification` 和显式 frozen-context review 仍保留 bounded scope，不因普通
workspace read-only 的默认路线变化而放宽。

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

`tp-voyager.manifest.json` 是 Skill 的 MCP 启动事实来源。安装或更新 Skill 后，宿主仍需完成自己的 MCP 注册。

Codex Desktop 使用明确、幂等的全局同步入口：

```powershell
python "$HOME\.codex\skills\tp-voyager-captain\sync_codex_desktop.py"
python "$HOME\.codex\skills\tp-voyager-captain\sync_codex_desktop.py" --check
```

同步器只维护全局 Codex 配置里的 `mcp_servers.tp_voyager`，不会删除项目级 `.codex/config.toml`。配置变化后必须完全重启 Codex Desktop 或新建任务；已有任务不会热加载 MCP。

完整说明和验收命令见 [`CODEX_DESKTOP.md`](CODEX_DESKTOP.md)。其他 Captain 宿主可按同一 manifest 注册自己的 MCP transport。

## 文件

```text
tp-voyager-captain/
├── SKILL.md
├── CODEX_DESKTOP.md
├── sync_codex_desktop.py
├── tp-voyager.manifest.json
├── worker-profiles/
└── README.md
```

更详细的模型目录配置见仓库 `docs/MODEL_ROUTING.md`。
