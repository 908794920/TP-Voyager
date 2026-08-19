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

v1.0.8 把普通 workspace 与显式 frozen/bounded corpus 分成两个清晰模式，但两者都保持只读：

- **Normal workspace read-only**：Captain 默认不提供 `read_scope`；Qoder ACP 直接以真实 repository `cwd` 启动，不做全仓 admission scan，也不预估 `max_files` / `max_bytes`；Vendor Built-in Tool 可见集合限制为 `Read` / `Grep` / `Glob`，并使用同一集合的 allow rule。
- **Explicit bounded read-only**：当 Captain 明确提供 `read_scope` 时，仍由 Runtime materialize 一次性 snapshot，并以 snapshot 作为 Qoder cwd；`routing_metadata.read_scope` 继续是唯一 scope 真源。
- 两种模式都不向 Worker 开放文件写入、Terminal、Web、Agent/Subagent 或 MCP；ACP host 的 file-write / terminal / permission escalation 保持 fail-closed。
- `forbidden_paths` 的 Runtime host policy 继续包含 `.git` / `.qoder` / `.codebuddy`；但 Vendor-native `Read/Grep/Glob` 是否在所有真实会话中都经过该 host callback，不能由单元测试推断。敏感路径必须以 Windows account-live acceptance 为准。
- v1.0.3 起只读终态不扫描/归属源工作区既有 Git diff。
- bounded snapshot 提供 **workspace-exposure isolation**，不是 OS sandbox；normal workspace 模式更明确依赖 Vendor 原生只读能力与真实 Live Gate。

当前实现已通过 ACP command/host-policy 单元回归，但 v1.0.8 的 Windows Qoder Live Matrix 在本次 Linux sandbox 中无法执行，因此本文件不把该 Live Gate 写成 PASS。

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

TP-Voyager 明确区分**观测行**和**完整目录**。真实 Windows 环境曾观察到交互终端能显示完整列表，而 Python `stdout=PIPE` 只得到 `MODEL + 单行`。因此若捕获形态为疑似单行截断：

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

v1.0.6 进一步从 SDK `context_config` 与 `thinking_config` 机械归一化 context / supported effort，并与 operator `model_routing_profiles.json`、dispatch policy、Runtime Evidence 合并。Operator 的 suggested effort 只做建议；Provider 未返回的实时能力保持 unknown。

## Model Parameters

Captain 可在显式 `model` 的任务上提供：

```json
{"reasoning_effort":"medium","context_window_tokens":200000}
```

`reasoning_effort` 只在 Qoder ACP 对当前会话声明 `thought_level` 选项时，才通过 `session/set_config_option` 下发。`context_window_tokens` 不是 Qoder ACP `configOptions`；Runtime 在启动 ACP 前把它传为官方 CLI 参数 `qodercli --acp --context-window <tokens>`。因此它是每个会话的启动参数，不会被当作显示字段或静默忽略。任务结果保留 requested/applied 状态；任何协议或启动失败均为显式失败，不会换模型、降级或重试。

Captain 请求参数时，Runtime 先读取当前 Provider 模型目录：所选 effort 必须在 `thinking_config` 中声明；上下文值必须精确出现在 Qoder `context_config`。目录没有对应事实、或值不被该模型支持时，任务不会创建。`task_result.usage` 仅投影 Provider 实报的 token、Credit、费用和货币；未实报时状态为 `provider_omitted`，不会使用目录倍率估算。

完整 route 字段见 `docs/MODEL_ROUTING.md`。

## 实测预算与模型基线

长调查任务在较短预算下可能触发 `max_task_duration`，因此 Captain Skill 对 `investigation/review` 默认使用 600 秒，但**不自动重试**。模型是否当前可用，以 Registry 的最新目录状态为事实；目录 incomplete 时应由 Captain 保守处理，不能静默替换模型。

## `repository_research`

Qoder 可作为受控外部源码研究 Crew，但仍走 `acp_read_only`：Runtime 负责 GitHub metadata 预检/浅克隆并把 read scope 映射成 ACP allowed paths；Qoder 无 Terminal/写文件能力。Provider 通信仍需要网络，但源码侧不开放 Web/Terminal 工具，最终报告由 Runtime 写到指定 `reports/` Artifact。

## Legacy Route

旧 `acp` / `print` 路线不属于当前生产表面，也不是自动 Fallback。TP-Voyager 当前只对 Captain 暴露经过受控验收的 `acp_read_only` 与 `acp_patch`。
