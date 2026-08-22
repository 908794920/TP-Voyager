# Codex Desktop 安装 / 更新与 MCP 同步

`tp-voyager.manifest.json` 仍是 TP-Voyager Captain 对既有 MCP 启动契约的唯一声明来源。v1.0.9.2 只收敛 Codex 的安装与展示入口，不新增第二个 MCP server、第二套控制面，也不改变 Runtime / Captain 的职责边界。

## v1.0.9.2 目标安装形态

Codex 中只保留一个当前入口：

```text
@TP-Voyager
└─ $tp-voyager:captain
```

仓库中的 `integrations/codex/local-marketplace/plugins/tp-voyager/` 是新的唯一插件源；插件只导出 `skills/captain/SKILL.md` 一个 Captain Skill，并继续复用已有 `tp_voyager` MCP 注册。插件没有 `.mcp.json` / `.app.json`，因此不会启动第二个 Runtime。

仓库根部的 `skills/tp-voyager-captain/SKILL.md` 仅作为 legacy migration shim，方便旧安装识别迁移关系，不再定义第二份派发/观测行为规则。旧 `tp-voyager-observability` 插件源继续保留在仓库中作为迁移证据，但不再由 marketplace 广告为当前插件。

## 一条命令完成安装 / 更新

在 TP-Voyager 仓库根目录执行：

```powershell
python -m agent_runtime.cli init
python .\skills\tp-voyager-captain\install_codex_desktop.py
```

安装器会：

1. 从当前仓库读取 `tp-voyager.manifest.json`，解析本机 `repository_root` 等绑定，但不把绝对路径写回仓库；
2. 继续只维护全局 Codex `config.toml` 中既有 `mcp_servers.tp_voyager` 自己拥有的字段；
3. 将新的 skills-only `tp-voyager` 插件同步到 `$CODEX_HOME\plugins\tp-voyager`；
4. 将 personal marketplace 收敛为只广告当前 `tp-voyager` 插件，并保持 `INSTALLED_BY_DEFAULT`；
5. 若稳定版 Codex CLI 可用，使用官方 `codex plugin add ... --json` / `codex plugin list --json` 验证安装；若检测到当前插件内容已变化且插件已安装，则先 remove 再 add，以刷新 Codex plugin cache；
6. 创建或更新 `$CODEX_HOME\AGENTS.md` 中 TP-Voyager managed block，保留 block 外所有用户内容；
7. 检测旧全局 Skill `$CODEX_HOME\skills\tp-voyager-captain` 和旧插件 `$CODEX_HOME\plugins\tp-voyager-observability`，但在迁移验收前**绝不静默删除**；
8. 返回新插件状态、MCP 状态、legacy 检测结果、显式 cleanup steps、`restart_required` 与 `new_conversation_required`。

重复执行必须幂等：内容未变化时保持 no-op；只有插件内容漂移需要 cache refresh 时才重新安装当前插件。

Crew CLI 路径不再属于 Codex MCP binding；Qoder / CodeBuddy 的长期本机路径继续统一从 `~/.tp-voyager/config.json` 读取，环境变量只作为临时覆盖。

## 只读验收

```powershell
python .\skills\tp-voyager-captain\install_codex_desktop.py --check
```

`--check` 只读验证：

- 当前插件文件和 manifest；
- personal marketplace 只广告 `tp-voyager`；
- `mcp_servers.tp_voyager` 注册仍匹配 manifest；
- 全局 `AGENTS.md` managed block；
- 可用时只调用 `codex plugin list --json` 检查 installed/enabled 状态；
- legacy 入口是否仍存在以及对应 cleanup steps。

它不会部署文件、修改配置、调用 Crew、dispatch/resume/cancel Task，也不会执行 plugin add/remove。

## 面板可观测性边界

新的 `$tp-voyager:captain` Skill 同时包含唯一的 Codex 面板规则：

- `task_dispatch` 返回的 `task_id` 是单 Task 唯一标识；显式并发组使用 Captain 提供的 `presentation_group_id`；
- 刷新只能调用只读 `render_voyager_panel`，并且只能使用精确 `task_id`、精确 `presentation_group_id` 或明确 `task_ids`；
- `task_result` / durable task result 是终态结果真源，面板只是只读 projection；
- 不得为了创建或刷新卡片再次 dispatch、resume、cancel、换 Crew/model、扩大范围或修改 Task；
- 不展示 prompt、system、secret、raw tool output、绝对宿主路径或 hidden/private reasoning；
- `TP_VOYAGER_CREW_OUTCOME_JSON` 等机器协议由投影层解析，不原样显示；
- 完成态优先展示 canonical final answer，Conversation 与 Timeline 独立限长，执行过程默认收起。

单任务卡继续兼容 `task_id`；并发卡只根据显式组关系或明确 task IDs 展示成员，不能根据“最近任务”、全局任务或模糊 correlation 自动挑选。

## 更新后必须新建 Codex 会话

Codex 对已打开会话不会可靠热加载新的插件 Skill / MCP 注入。完成安装或更新后，完全重启 Codex Desktop（如安装器提示），然后创建 **new conversation** / 新任务，再验证 `$tp-voyager:captain` 和既有 `tp_voyager` MCP。

新会话至少验证：

```text
crew_catalog(include_models=true)
```

仍保持 `selection_performed=false`、`dispatch_performed=false`，并确认 Captain 默认工具数量仍为 7。安装器不会自动选择 Crew/model/effort，也不会自动 dispatch。

## Legacy 迁移与显式清理

迁移阶段先保留旧全局 Skill 与旧 Observability 插件，直到新入口完成真实 Codex 验证。保留 legacy 的含义是“不静默删除”；因此清理前插件页可能暂时同时存在旧插件和新插件，不能把“只剩一个插件”作为清理前条件。

### 清理前验证

在 **new conversation** 中先验证：

1. 当前 `TP-Voyager` / `tp-voyager` 插件已安装并启用，`$tp-voyager:captain` 可用；
2. 既有 `tp_voyager` MCP 的 7 个 Captain 工具可正常发现；
3. `task_dispatch` 后可以显示并只读刷新面板；
4. 单任务终态仍以 `task_result` 为准；
5. 显式 `presentation_group_id` 的并发组能够在一张卡中看到全部明确子任务；
6. 安装器报告 legacy 检测结果与 cleanup steps，但没有自动删除旧目录或旧注册。

上述验证通过后，再由用户显式清理旧入口。安装器只给步骤，不自动删除：

```text
Remove/uninstall legacy plugin tp-voyager-observability only after validation.
Remove/delete legacy standalone Skill tp-voyager-captain only after validation.
```

如果旧插件是通过 Codex CLI 注册的，可按当前机器的 marketplace 名称执行对应 `codex plugin remove tp-voyager-observability@<marketplace> --json`；若只是遗留目录，则先确认 Codex 已不再引用，再手动删除 `$CODEX_HOME\plugins\tp-voyager-observability`。

旧全局 Skill 同理：确认新插件 Captain 已生效后，才手动删除 `$CODEX_HOME\skills\tp-voyager-captain`。不要在验证前静默删除任何旧目录。

### 清理后最终验收

完成显式 cleanup 后，完全重启 Codex Desktop 并再次创建 **new conversation**，最终确认：

1. Codex 插件页只有一个当前 `TP-Voyager` 插件；
2. 它只导出 `$tp-voyager:captain` 一个 Captain Skill；
3. 既有 `tp_voyager` MCP 仍可用且默认 Captain 工具数仍为 7；
4. 单任务 panel / `task_result` 与显式并发组 panel 均继续通过；
5. 不再存在两个独立 Skill 对同一派发/观测行为分别定义当前规则。

## 配置保护边界

同步器只维护：

```text
mcp_servers.tp_voyager
mcp_servers.tp_voyager.env 中 manifest-owned keys
```

其他 MCP、plugin、project trust、普通设置、未知 TP-Voyager 字段和注释都保留。项目目录中的 `.codex/config.toml` 不会被删除；Crew CLI 路径继续来自 `~/.tp-voyager/config.json`（环境变量只作临时覆盖）。

安装 / 同步审计输出只包含目标路径、changed/no-op、manifest/config hash、env key 名称等安全信息，不输出环境变量值。

## 全局 AGENTS managed block

安装器只更新 `$CODEX_HOME\AGENTS.md` 中以下标记区间：

```text
<!-- >>> TP-Voyager managed guidance >>> -->
...
<!-- <<< TP-Voyager managed guidance <<< -->
```

用户规则保持原样。若 `$CODEX_HOME\AGENTS.override.md` 非空，安装器只报告 `agents_guidance_effective=false` / `agents_override_present=true`，不会覆盖用户 override。
