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

两条当前生产路线都使用官方 ACP，且不使用 `--yolo`。

## `acp_read_only`

- 不向 Worker 开放写文件能力；
- 不开放 Terminal；
- 权限升级请求 fail-closed；
- 读取必须落在 Runtime 解析后的 concrete `read_scope`；
- v1.0.3 起只读终态不扫描/归属源工作区既有 Git diff。

## `acp_patch`

- 在 Runtime-owned Git worktree 内执行；
- Host callback 强制 allowed/forbidden path；
- Terminal 命令必须精确匹配 Captain-approved argv/cwd；
- Runtime 负责捕获 Patch、Verification 与 Evidence。

## Model Catalog

动态目录仍来自：

```text
Qoder Agent SDK: QoderSDKClient.get_available_models()  # preferred
qodercli --list-models                               # compatibility fallback
```

但 v1.0.3 明确区分**观测行**和**完整目录**。真实 Windows 环境曾观察到交互终端能显示完整列表，而 Python `stdout=PIPE` 只得到 `MODEL + 单行`。因此若捕获形态为疑似单行截断：

```text
catalog_status=incomplete
model.metadata.catalog_status=incomplete_suspected
```

TP-Voyager 会保留实际观测到的模型，但不会把它伪装成完整账号目录，也不会据此自动选模或 fallback。

官方 Qoder 模型页面能够明确对应的 tier intent、能力描述标签和 Credit rate 只作为：

```text
official reference metadata
```

它们不会形成模型评分，也不会用于推算某个 Task 的费用。任务真实 Token/Credit/Cost 仍以 `tp-voyager.usage/v1` Evidence 为准。

## 实测预算与模型基线

长调查任务在较短预算下可能触发 `max_task_duration`，因此 Captain Skill 对 `investigation/review` 默认使用 600 秒，但**不自动重试**。模型是否当前可用，以 Registry 的最新目录状态为事实；目录 incomplete 时应由 Captain 保守处理，不能静默替换模型。

## `repository_research`

Qoder 可作为受控外部源码研究 Crew，但仍走 `acp_read_only`：Runtime 负责 GitHub metadata 预检/浅克隆并把 read scope 映射成 ACP allowed paths；Qoder 无 Terminal/写文件能力。Provider 通信仍需要网络，但源码侧不开放 Web/Terminal 工具，最终报告由 Runtime 写到指定 `reports/` Artifact。

## Legacy Route

旧 `acp` / `print` 路线不属于当前生产表面，也不是自动 Fallback。TP-Voyager 当前只对 Captain 暴露经过受控验收的 `acp_read_only` 与 `acp_patch`。
