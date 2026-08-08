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

## Model Catalog

在没有确认官方机器可读模型目录之前，TP-Voyager 不猜测 CodeBuddy 模型清单。

未知字段保持：

```text
unknown
```
