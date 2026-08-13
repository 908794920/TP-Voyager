# TP-Voyager

**让 Captain AI 可靠调度外部 AI Crew 的本地执行与控制层。**

TP-Voyager 连接上层 Captain 与 CodeBuddy / Qoder 等执行型 AI CLI，负责把一次委派变成**可恢复、受约束、可验证、有证据**的任务；任务怎么拆、选哪个 Crew / 模型、结果是否接受，仍由 Captain 决定。

```text
Passenger (Human)
      ↓ 目标 / 约束 / 验收
Captain AI
      ↓ 显式选择 Crew / Model / Effort
TP-Voyager
      ↓ 持久化 / 权限 / 隔离 / 验证 / Evidence
Crew
├── CodeBuddy CLI
└── Qoder CLI
```

> 当前版本：**v1.0.6 — Routable Model Catalog & Repository Cleanup**

## 为什么需要 TP-Voyager

直接让上层 AI 调 CLI 很容易遇到几个现实问题：任务中断后不知道做到哪、模型或权限被悄悄切换、修改越界、结果只有“我做完了”的自述、长任务把 Captain 上下文拖得很大。

TP-Voyager 把这些问题收在执行层：

- **Durable Task**：Task / Session / Attempt / Event 写入 SQLite，可恢复、可查询；
- **受控执行**：read-only、patch、verification 都有明确边界，不使用隐藏 fallback；
- **隔离修改**：Patch 在 Runtime-owned Git worktree 中完成，Passenger Workspace 不由 Runtime 直接修改；
- **Verification + Evidence**：把测试、Patch、Usage、执行结果变成可追溯事实；
- **小型 Captain Surface**：正常只需要 6 个 MCP 工具，不要求 Captain 理解 Runtime 内部实现。

## v1.0.6：可路由模型目录

`crew_catalog(include_models=true)` 不再只返回零散的模型 ID，而是聚合四类事实：

```text
Provider 实时目录
      +
dispatch_model_policy.json      # 能不能用：硬约束
      +
model_routing_profiles.json     # 适合干什么：operator 认知资料
      +
Runtime Evidence                # 实际历史表现 / Usage
      ↓
Routable Model Catalog
      ↓
Captain 自己选 Crew / Model / Effort
```

每个模型路由会尽量给出：

- `route_id`、`available`、`allowlist_status`；
- `routable` / `routability_status`（支持 true / false / unknown 三态）；
- Provider 返回的 context / reasoning effort；
- **reference-only** 倍率，且固定 `calculation_allowed=false`；
- operator 维护的 `capability_tier`、`recommended_tasks`、`risk_boundaries`、`suggested_effort`；
- Runtime 历史成功率、耗时与 Usage Evidence；
- 每类信息自己的 `sources`。

TP-Voyager **不自动给模型打综合分，也不替 Captain 选模型**：

```text
selection_performed = false
dispatch_performed = false
```

真正执行时仍必须显式下发 Crew / model；`reasoning_effort` 只有当前 Backend route 明确支持时才传：

```text
crew=...
model=...
reasoning_effort=...   # supported route only
```

详细配置见 [模型路由目录](docs/MODEL_ROUTING.md)。

## 支持的 Crew

| Crew | 当前受控能力 | 模型目录 |
|---|---|---|
| CodeBuddy CLI | read-only / patch / verification | 优先账号态 ACP；失败时回退 CLI declaration |
| Qoder CLI | read-only / patch / verification | 优先官方 Agent SDK；失败时回退 `--list-models` |

Provider 返回的模型列表、倍率、上下文和 effort 会随账号与供应商变化；TP-Voyager 只投影当前观察到的事实，不把静态文档冒充实时 entitlement。

## 快速开始

### 1. 安装

需要：

- Python 3.10+
- Git
- 已安装并登录至少一个受支持 Crew CLI

Windows PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 启动 Runtime

```powershell
.\scripts\start_runtime.cmd
```

或：

```powershell
.\.venv\Scripts\python.exe -m agent_runtime.server
```

如需显式指定 Python：

```powershell
$env:AGENT_RUNTIME_PYTHON = "D:\path\to\python.exe"
.\scripts\start_runtime.cmd
```

### 3. 配置 operator 模型资料

Runtime Home 默认是：

```text
~/.agent-runtime
```

也可以通过：

```text
AGENT_RUNTIME_HOME
```

指定其他位置。

两个文件职责不同：

```text
<runtime-home>/dispatch_model_policy.json
    → 模型授权：硬约束

<runtime-home>/model_routing_profiles.json
    → 模型能力资料：只读建议，不参与授权
```

TP-Voyager 随包提供一份经过审阅的 **26-route baseline**。如果 Runtime Home 尚未存在 operator 文件，MCP 会**只读使用 bundled baseline**，因此升级后无需额外复制就能看到能力资料。

如果希望把 baseline materialize 到 Runtime Home 并自行维护，显式执行：

```powershell
.\.venv\Scripts\python.exe -m agent_runtime.cli model-routing-init
```

该命令只在 `model_routing_profiles.json` 不存在时写入 Runtime Home，**绝不覆盖 operator 已维护的文件**。一旦 operator 文件存在，它会覆盖 bundled baseline。

四条核心 route 的精简示例仍保留在：

[docs/examples/model_routing_profiles.example.json](docs/examples/model_routing_profiles.example.json)

独立 benchmark、profile confidence 和受信任本地 Evidence 的配置见 [模型路由目录](docs/MODEL_ROUTING.md)。

## Captain 怎么用

默认 MCP Surface 只有 6 个高层工具：

```text
voyager_overview
crew_catalog
crew_health
crew_recommend
task_dispatch
task_result
```

推荐主路径：

```text
voyager_overview
      ↓
crew_catalog(include_models=true)
      ↓
必要时 crew_health / crew_recommend
      ↓
Captain 显式选择 Crew / Model / Effort
      ↓
task_dispatch
      ↓
task_result + Verification / Evidence
      ↓
Captain 验收
```

`crew_recommend` 只做 **Crew 受控能力/健康度** 的辅助判断，不是模型自动路由器。

完整 Captain 规则见 [Captain Skill](skills/tp-voyager-captain/README.md)。

## 受控执行模式

### Read-only

用于源码理解、技术调研、Code Review、测试失败分析等。读取范围由 `read_scope` 限定；只读任务不归属工作区已有 Git diff。

### Patch

用于边界明确的小型代码修改：

```text
Runtime-owned worktree
→ 路径白名单 / 禁止路径
→ 精确命令授权
→ Patch Artifact
→ Verification
→ Evidence
```

Runtime 不自动把 Patch 合并回 Passenger Workspace。

### Verification

Captain Host 应用 Patch 后，可以把 Apply Receipt + 精确 Verification Subject 交回 Runtime，在一次性隔离 worktree 中执行确定性验证。

## 项目结构

```text
TP-Voyager/
├── agent_runtime/              # Runtime 生产代码
│   ├── api/                    # MCP / public projection
│   ├── application/            # Crew、dispatch、task 等 use cases
│   ├── backends/               # CodeBuddy / Qoder adapters
│   ├── domain/                 # 稳定 contracts
│   ├── persistence/            # SQLite durable truth
│   ├── runtime/                # lease / handles / diagnostics
│   └── verification/           # artifact / verification
├── skills/
│   └── tp-voyager-captain/     # Captain 使用规范
├── tests/
├── docs/                       # 面向人阅读的产品/运维/架构文档
│   └── architecture/           # 治理基线（Charter / Directory Baseline）
├── scripts/                    # 启动与测试脚本
├── AGENTS.md                   # AI 开发约束
└── CHANGELOG.md
```

v1.0.6 **不为了目录好看大搬 Durable Core**。已有大型历史 service 暂时保持兼容位置；新能力优先进入已有职责槽位，后续只有在真实维护成本证明值得时再迁移。

## 文档入口

先看 [docs/README.md](docs/README.md)。常用入口：

- [架构](docs/ARCHITECTURE.md)
- [模型路由](docs/MODEL_ROUTING.md)
- [运行与配置](docs/OPERATIONS.md)
- [测试策略](docs/TESTING.md)
- [CodeBuddy Backend](docs/BACKEND_CODEBUDDY.md)
- [Qoder Backend](docs/BACKEND_QODER.md)
- [CHANGELOG](CHANGELOG.md)

`docs/records/` 只保存历史验收记录，不属于日常阅读主路径。

## 不做什么

TP-Voyager 不是：

- 第二个 Captain / 自动任务规划器；
- 自动模型评分器或自动 fallback 系统；
- 账单估算器；
- Agent 社交网络 / 无限递归派工系统；
- 企业审批平台；
- Vector DB / 自动知识写回平台。

核心原则：

```text
Simple over complete
Explicit over automatic
Reuse over rebuilding
Bounded over unrestricted
Evidence over claims
```

## 测试

日常默认：

```powershell
.\scripts\run_tests.cmd
```

更完整的维护 profile：

```powershell
.\scripts\run_tests.cmd current
.\scripts\run_tests.cmd regression
```

详见 [docs/TESTING.md](docs/TESTING.md)。

## License

[MIT License](LICENSE)
