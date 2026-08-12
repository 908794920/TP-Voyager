# CodeBuddy CLI Backend

## 官方资料

- https://www.workbuddy.ai/docs/cli/
- https://www.workbuddy.ai/docs/cli/reference
- https://www.workbuddy.ai/docs/cli/iam
- https://www.workbuddy.ai/docs/cli/sdk-python

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

- Runtime 先验证 Context Manifest / `read_scope`；
- 只把受控、Hash 验证后的上下文提供给 CodeBuddy；
- CodeBuddy 原生工具关闭；
- 不允许扩大文件读取边界；
- v1.0.3 起只读终态不扫描工作区 Git diff，`changed_files=[]`，不生成 `workspace.patch`。

### `sdk_patch`

- 在 Runtime-owned Git worktree 中运行；
- 使用 SDK Host Permission Callback 做路径/命令约束；
- 命令必须匹配 Captain 显式批准的 Policy；
- 不使用 Permission Bypass。

## Captain 只读入口

新任务优先使用：

```text
task_dispatch(
  crew="codebuddy",
  task_kind="research",
  access_mode="read_only",
  cwd=<repo>,
  read_scope={...},
  timeout_seconds=600
)
```

`context_files` 仍作为 v1.0.1 兼容入口。TP-Voyager 会把统一 read scope 映射为 Context Manifest，再做 Hash/漂移校验和有界 Snapshot 渲染。

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

当前 CodeBuddy 受控 SDK Backend 声明 `supports_reasoning_effort=false`。因此 operator profile 可以保留模型级 `suggested_effort` 作为认知资料，但 Captain 不能把它当成当前 CodeBuddy route 已支持的实时参数；只有 Backend/Provider 明确支持后才可下发 `reasoning_effort`。

## `repository_research`

CodeBuddy 可作为受控外部源码研究 Crew，但仍走 `sdk_context_read_only`：Runtime 先完成明确 GitHub URL 的大小预检和浅克隆，CodeBuddy 只收到 `source/` 中经过 read scope 限制的 immutable Context，不开放原生工具。最终研究报告由 Runtime 根据 Crew final answer 写入 `reports/` Artifact，下载源码本身不得被修改或执行。
