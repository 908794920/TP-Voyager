# Codex Desktop 安装 / 更新与 MCP 同步

`tp-voyager.manifest.json` 是 Captain Skill 的唯一 MCP 启动**声明**来源。共享仓库中的 manifest 只保存可移植事实：server name、stdio command、Captain tool allow-list，以及需要在本机安装时解析的绑定；仓库不保存开发者机器的绝对路径。

安装器会在用户的 Codex Home 内生成本机 `tp-voyager.bindings.json`，只保存本机解析结果。这个文件不属于仓库，也不会被同步回 Git。

## 一条命令完成安装 / 更新 + MCP 注册

在 TP-Voyager 仓库根目录执行：

```powershell
$env:CODEBUDDY_CODE_PATH = "<path-to-codebuddy-command>"
python .\skills\tp-voyager-captain\install_codex_desktop.py
```

安装器会：

1. 从仓库当前 `skills/tp-voyager-captain/` 读取 manifest；
2. 自动把当前仓库根目录解析为 Runtime `cwd`，但不会把这个绝对路径写回仓库；
3. 把仓库 Skill 的托管文件更新到 `$CODEX_HOME\skills\tp-voyager-captain`（未设置 `CODEX_HOME` 时使用当前用户 `~/.codex`）；
4. 保留目标 Skill 目录里仓库无法归属的额外用户文件；
5. 在已安装 Skill 目录生成本机 `tp-voyager.bindings.json`；
6. 从**已安装目录**运行 `sync_codex_desktop.py`，只维护全局 `config.toml` 中 `mcp_servers.tp_voyager` 自己的字段。

Qoder 的默认 CLI 路径使用 manifest 中的 home-relative literal。任何必须使用绝对路径的本机值都通过 install binding 提供，只存在用户安装目录或用户环境变量中，不写回共享仓库。

重复执行必须幂等：第一次报告 `changed`，内容未变化时第二次报告 `no-op`。

## 只读验收

```powershell
python .\skills\tp-voyager-captain\install_codex_desktop.py --check
```

`--check` 同时验证：

- 仓库 Skill 与已安装 Skill 的托管文件一致；
- 本机 bindings 可解析 manifest；
- 全局 Codex 配置中 `tp_voyager` 的 command / args / cwd / env / enabled_tools 与已安装 manifest 一致。

它不会部署文件、不会改配置、不会启动 TP-Voyager、不会调用 CodeBuddy/Qoder，也不会下发任务。

如只想单独检查已经安装的 MCP 注册，也可以在已安装目录执行：

```powershell
python "$HOME\.codex\skills\tp-voyager-captain\sync_codex_desktop.py" --check
```

## 配置保护边界

同步器只维护：

```text
mcp_servers.tp_voyager
mcp_servers.tp_voyager.env 中 manifest-owned keys
```

其他 MCP、plugin、project trust、普通设置、未知 TP-Voyager 字段和注释都保留。项目目录中的任何 `.codex/config.toml` 都不会被删除；Codex Desktop 全局发现不再依赖项目级配置。

安装 / 同步审计输出包括：目标 Skill 路径、全局 config 路径、changed/no-op、manifest SHA-256、config 修改前后 SHA-256、env key 名称。不会输出环境变量值。

## 配置生效

Codex Desktop 不对既有任务热加载新的 MCP 注册。安装 / 同步完成后必须**完全退出并重新启动 Codex Desktop，或至少创建全新任务 / 会话**，再做真实工具发现验收。

新任务应按 manifest 的 `required_captain_tools` 验证工具集合；随后调用：

```text
crew_catalog(include_models=true)
```

确认继续保持：

```text
selection_performed = false
dispatch_performed = false
```

模型 `reference_multiplier` 仍然只是 reference-only，公共投影固定 `calculation_allowed=false`。安装器和同步器都不会自动选择 Crew / model / effort，不做 fallback，也不会自动 dispatch。
