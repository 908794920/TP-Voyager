# Codex Desktop 安装与 MCP 同步

`tp-voyager.manifest.json` 是 Captain Skill 的唯一 MCP 启动事实来源。Codex Desktop 同步器从 manifest 读取：server name、stdio command/args、cwd、环境变量和 Captain tool allow-list；同步脚本和本文不维护第二份启动参数。

## 同步到全局 Codex 配置

Skill 安装/更新后显式执行：

```powershell
python "$HOME\.codex\skills\tp-voyager-captain\sync_codex_desktop.py"
```

默认目标是 `$CODEX_HOME\config.toml`；未设置 `CODEX_HOME` 时是 `$HOME\.codex\config.toml`。同步器只维护 `mcp_servers.tp_voyager` 自己的 managed fields，并保留其他 MCP、plugin、project trust、普通设置和注释。项目级 `E:\updateProject\.codex\config.toml` 不会被删除或作为 Desktop 全局注册的前提。

重复执行必须返回 `action=no-op`；manifest 的 MCP 内容变化时只更新 TP-Voyager managed fields。

## 只读检查

```powershell
python "$HOME\.codex\skills\tp-voyager-captain\sync_codex_desktop.py" --check
```

检查只读取 manifest 和 Codex 全局配置，不启动 TP-Voyager，不调用 CodeBuddy/Qoder，不下发任务。它验证 `tp_voyager` 的 command/args/cwd/env 和 `enabled_tools` 与 manifest 一致，并返回 manifest SHA-256 与配置 SHA-256。审计输出只显示环境变量名称，不显示环境变量值。

## 配置生效

Codex Desktop 不对已有任务热加载 MCP 配置。同步完成后必须**完全退出并重新启动 Codex Desktop，或至少新建任务/会话**。已有任务即使继续运行，也不能作为 MCP 注册成功的验收依据。

新任务中应以 manifest 的 `required_captain_tools` 为期望集合验证工具发现；随后调用 `crew_catalog(include_models=true)`，确认仍为：

```text
selection_performed = false
dispatch_performed = false
```

模型 reference multiplier 仍是 reference-only，公共投影必须保持 `calculation_allowed=false`。同步器不会自动选择 Crew/model/effort，不做 fallback，也不会自动 dispatch。

## 最小验收

```powershell
$sync = "$HOME\.codex\skills\tp-voyager-captain\sync_codex_desktop.py"
python $sync
python $sync
python $sync --check
```

预期：第一次 `added` 或 `updated`；第二次 `no-op`；`--check` 返回 `check-ok`。然后完整重启 Codex Desktop、新建任务，按 manifest 的 tool allow-list 验证工具可发现。
