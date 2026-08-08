# Qoder CLI Backend

## 官方资料

- https://docs.qoder.com/en/cli/model
- https://docs.qoder.com/en/cli/acp
- https://docs.qoder.com/en/cli/sdk/python/quick-start
- https://docs.qoder.com/en/cli/sdk/python/tools
- https://docs.qoder.com/en/cli/sdk/permissions

## TP-Voyager 当前受控路线

```text
acp_read_only
acp_patch
```

两条当前生产路线都使用官方 ACP，且不使用：

```text
--yolo
```

## `acp_read_only`

- 不向 Worker 开放写文件能力；
- 不开放 Terminal 写操作；
- 权限升级请求 fail-closed；
- 只用于受控分析与检索。

## `acp_patch`

- 在 Runtime-owned Git worktree 内执行；
- Host callback 强制 allowed/forbidden path；
- Terminal 命令必须精确匹配 Captain-approved argv/cwd；
- Runtime 负责捕获 Patch、Verification 与 Evidence。


## 实测预算与模型基线

真实使用中，同一类长调查任务在 180 秒预算下可能触发 `max_task_duration`，而 600 秒预算可以正常完成。因此 Captain Skill 对 `investigation/review` 默认使用 600 秒，但**不自动重试**。

本轮真实环境已经成功使用过显式模型 `Lite`。该事实属于本地运行证据；模型是否仍可用必须以当前：

```text
qodercli --list-models
```

结果为准。若 `Lite` 不再可用，Captain 不得静默替换模型。

## Model Catalog

当前动态模型目录来源：

```text
qodercli --list-models
```

## Legacy Route

旧 `acp` / `print` 路线不属于当前生产表面，也不是自动 Fallback。

TP-Voyager 当前只对 Captain 暴露经过受控验收的 `acp_read_only` 与 `acp_patch`。
