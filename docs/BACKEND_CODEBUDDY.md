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

## Health / Model Registry 语义

`crew_health(codebuddy)` 将以下事实分开表达：

- CLI / SDK 是否可用；
- 认证是否真的执行过 probe；
- 最近成功的显式模型；
- 模型目录来源与当前账号 entitlement 是否已知。

v1.0.3 的 CodeBuddy 模型目录来自：

```text
codebuddy --help
  -> --model <model>
  -> Currently supported: (...)
```

目录项标记：

```text
source=cli_declared
available=null
entitlement_status=unknown
```

它只证明**当前安装的 CLI 声明支持这些 model id**，不证明当前账号实时拥有每个模型。解析失败时保持 unknown/空目录，不硬编码、不拿历史成功模型冒充当前目录。

CodeBuddy 官方 Credits 文档说明不同模型/任务可能消耗不同 Credits，但 TP-Voyager 当前没有可靠的逐模型固定费率真相源。因此 Registry 中 billing 保持 unknown；只有任务 SDK 实际回传的 token/credit/provider-reported cost 才进入 Usage Evidence。

## `repository_research`

CodeBuddy 可作为受控外部源码研究 Crew，但仍走 `sdk_context_read_only`：Runtime 先完成明确 GitHub URL 的大小预检和浅克隆，CodeBuddy 只收到 `source/` 中经过 read scope 限制的 immutable Context，不开放原生工具。最终研究报告由 Runtime 根据 Crew final answer 写入 `reports/` Artifact，下载源码本身不得被修改或执行。
