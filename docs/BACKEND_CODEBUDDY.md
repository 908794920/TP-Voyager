# CodeBuddy CLI Backend

## 官方资料

- https://www.codebuddy.ai/docs/cli/sdk
- https://www.codebuddy.ai/docs/cli/sdk-python
- https://www.codebuddy.ai/docs/cli/sdk-permissions
- https://www.codebuddy.ai/docs/cli/tools-reference

## TP-Voyager 当前受控路线

```text
sdk_context_read_only
sdk_patch
sdk_verify
```

## 中国区环境

```text
CODEBUDDY_INTERNET_ENVIRONMENT=internal
```

Captain 路径使用官方 Python SDK。

### `sdk_context_read_only`

v1.0.8 保留同一路由名，但明确分成两个 delivery mode：

1. **Workspace Native Read-only (`context_delivery=vendor_workspace`)**
   - `context_id` 缺省时直接使用真实 repository `cwd`；
   - SDK `permission_mode=plan`；
   - native tool 集合只配置 `Read` / `Glob` / `Grep`；其余 Built-in Tool 显式列入 `disallowed_tools`；
   - `can_use_tool` 代码对 workspace 越界与 `.git` / `.codebuddy` / `.qoder` 路径 fail-closed，并拒绝 Edit/Write/Bash/Web/Task/未知 future tool；
   - `mcp_servers={}`、`setting_sources=[]`，不把 repository agent config 当成 trusted execution config。

2. **Explicit Frozen Context (`context_delivery=runtime_snapshot`)**
   - 显式 `context_id` 继续走已有 Context Manifest verify/render；
   - Snapshot 仍受现有 256 KiB render 上限；
   - 所有 CodeBuddy native tools 继续关闭。

两种模式都保留 v1.0.3 的只读终态归属规则：`changed_files=[]`，不把 Passenger Workspace 既有 diff 投影成本次任务产物。

重要：当前 CodeBuddy SDK 文档把 `canUseTool` 描述为工具需要权限确认时的 callback，同时 trusted allow rules 可能直接放行。因而 TP-Voyager 的 callback 单元测试不能替代真实 Vendor session 证明。v1.0.8 在 Windows Live Matrix 通过前，`controlled_capabilities` **不宣称** `read_files` / `search_code` 已验收。

### `sdk_patch`

- 在 Runtime-owned Git worktree 中运行；
- 使用 SDK Host Permission Callback 做路径/命令约束；
- 命令必须匹配 Captain 显式批准的 argv/cwd Policy；Bash 文本使用 shell-safe literal quoting 序列化，避免 `$VAR`、`*`、`$(...)` 等把 literal argv 改解释为 shell program；
- 不使用 Permission Bypass。

## Captain 只读入口

普通本地 repository research / review 默认使用 workspace-native 模式，不提供 `read_scope` 或 `context_id`：

```text
task_dispatch(
  crew="codebuddy",
  task_kind="research",
  access_mode="read_only",
  cwd=<repo>,
  timeout_seconds=600
)
```

只有显式 frozen/bounded corpus 才使用 `context_id` / Context Manifest。`context_files` 仍作为 v1.0.1 兼容入口，并继续映射为经过 Hash/漂移校验和有界 render 的 frozen Context；它不是普通大仓 workspace 模式的默认入口。

## Model Catalog / Billing 语义

v1.0.6 不再把 CodeBuddy model catalog 描述成固定的 `--help` 清单。当前顺序是：

```text
catalog-only ACP: initialize → session/new → close
        ↓ 成功
source=codebuddy_acp_account_live
available=true
reference multiplier (when provider returns it)
        ↓ ACP 不可用
codebuddy --help declaration fallback
source=cli_declared
available=null
entitlement_status=unknown
```

ACP 目录只做控制面读取：不发 Prompt、不开放 tool/terminal/permission 回调。任何异常 callback 都 fail-closed。

Provider 返回的 Credits 倍率投影为：

```text
reference_multiplier
calculation_allowed=false
```

它可以帮助 Captain 比较相对消耗，但**不能**用于计算真实账单。真实任务消耗仍只来自 `tp-voyager.usage/v1` Evidence。

`crew_catalog(include_models=true)` 还会把该实时目录与 operator policy、`model_routing_profiles.json`、Runtime history/Usage 合并成 routable route；CodeBuddy adapter 本身不理解 L0/L1/L2/L3，也不替 Captain 选模型。

当前 CodeBuddy 受控 SDK Backend 声明 `supports_reasoning_effort=true`，并通过官方 SDK `CodeBuddyAgentOptions.effort` 支持 `low`、`medium`、`high`、`xhigh`。Captain 必须经 `task_dispatch(model_parameters={"reasoning_effort": ...})` 显式下发；结果中的 `reasoning_effort_applied` 表示 Runtime 已将该值交给 SDK。SDK 未公开受控的 per-session context-window 参数，因此 `context_window_tokens` 会在创建任务前拒绝，绝不静默忽略。

## `repository_research`

CodeBuddy 可作为受控外部源码研究 Crew，但仍走 `sdk_context_read_only`：Runtime 先完成明确 GitHub URL 的大小预检和浅克隆，CodeBuddy 只收到 `source/` 中经过 read scope 限制的 immutable Context，不开放原生工具。最终研究报告由 Runtime 根据 Crew final answer 写入 `reports/` Artifact，下载源码本身不得被修改或执行。
