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

- Runtime 先验证 Context Manifest；
- 只把受控、Hash 验证后的上下文提供给 CodeBuddy；
- CodeBuddy 原生工具关闭；
- 不允许扩大文件读取边界。

### `sdk_patch`

- 在 Runtime-owned Git worktree 中运行；
- 使用 SDK Host Permission Callback 做路径/命令约束；
- 命令必须匹配 Captain 显式批准的 Policy；
- 不使用 Permission Bypass。


## Captain 只读入口

普通 Captain 不需要直接调用 `context_register/context_verify`。

可以直接使用：

```text
task_dispatch(
  crew="codebuddy",
  task_kind="research",
  access_mode="read_only",
  cwd=<repo>,
  context_files=["README.md", "src/..."],
  timeout_seconds=600
)
```

TP-Voyager 会把 `context_files` 转成既有 Context Manifest，再做 Hash/漂移校验和有界 Snapshot 渲染。

## Health 语义

`crew_health(codebuddy)` 将以下事实分开表达：

- CLI 是否可用；
- SDK 是否可用；
- 认证是否真的执行过 probe；
- 官方机器可读 Model Catalog 是否已确认；
- 从现有 Durable Task/Session 历史观察到的最近成功显式模型。

`auth_status=not_probed` 不能解释成“未登录”。如果历史中已有显式 `hy3` 成功任务，`last_successful_model=hy3` 只是本地运行证据，不会被伪装成官方模型目录。

## Model Catalog

在没有确认官方机器可读模型目录之前，TP-Voyager 不猜测 CodeBuddy 模型清单。

未知字段保持：

```text
unknown
```
