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

> 当前版本：**v1.0.9 — 开发中**

## 为什么需要 TP-Voyager

直接让上层 AI 调 CLI 很容易遇到几个现实问题：任务中断后不知道做到哪、模型或权限被悄悄切换、修改越界、结果只有“我做完了”的自述、长任务把 Captain 上下文拖得很大。

TP-Voyager 把这些问题收在执行层：

- **Durable Task**：Task / Session / Attempt / Event 写入 SQLite，可恢复、可查询；
- **受控执行**：read-only、patch、verification 都有明确边界，不使用隐藏 fallback；
- **隔离修改**：Patch 在 Runtime-owned Git worktree 中完成，Passenger Workspace 不由 Runtime 直接修改；
- **Verification + Evidence**：把测试、Patch、Usage、执行结果变成可追溯事实；
- **小型 Captain Surface**：正常只需要 6 个 MCP 工具，不要求 Captain 理解 Runtime 内部实现。

## v1.0.7：统一配置与受控执行

TP-Voyager 的机器级配置统一放在用户目录：

```text
~/.tp-voyager/
├── config.json                    # Crew 路径、模型授权、trusted roots、资源根、并发上限
├── model_routing_profiles.json    # 模型能力资料；可选 operator materialize
└── runtime/
    ├── tp_voyager.db
    ├── artifacts/
    ├── workspaces/
    └── logs/
```

首次使用执行：

```powershell
.\.venv\Scripts\python.exe -m agent_runtime.cli init
```

`init` 会创建目录、尝试从 PATH 发现 Qoder / CodeBuddy CLI，并生成严格的 `tp-voyager.config/v2`。重复执行不会覆盖已有 `config.json`。

模型目录继续保持数据驱动：

```text
Provider 实时目录
      +
config.json / dispatch            # 能不能用：硬约束
      +
model_routing_profiles.json       # 适合干什么：operator 认知资料
      +
Runtime Evidence                  # 实际历史表现 / Usage
      ↓
Routable Model Catalog
      ↓
Captain 自己选 Crew / Model / Effort
```

TP-Voyager **不自动给模型打综合分，也不替 Captain 选模型**；真正执行仍必须显式下发 Crew / model。可选执行设置统一放入 `model_parameters`：`reasoning_effort` 只有当前 Backend route 明确支持时才传；Qoder 的 `context_window_tokens` 通过官方 `qodercli --context-window <tokens>` 在 ACP 会话启动前固定下发。详细配置见 [模型路由目录](docs/MODEL_ROUTING.md)。

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
$env:TP_VOYAGER_PYTHON = "D:\path\to\python.exe"
.\scripts\start_runtime.cmd
```

### 3. 初始化并编辑用户配置

默认用户目录：

```text
~/.tp-voyager
```

可通过 `TP_VOYAGER_HOME` 改变整个用户目录，通过 `TP_VOYAGER_DB` 单独覆盖 SQLite 路径。启动脚本的 Python 覆盖变量是 `TP_VOYAGER_PYTHON`。

首次初始化：

```powershell
.\.venv\Scripts\python.exe -m agent_runtime.cli init
```

生成的 `config.json` 结构：

```json
{
  "schema": "tp-voyager.config/v2",
  "crew": {
    "qoder": {
      "enabled": true,
      "cli_path": "",
      "max_concurrent_tasks": 2
    },
    "codebuddy": {
      "enabled": true,
      "cli_path": "",
      "internet_environment": "internal",
      "max_concurrent_tasks": 2
    }
  },
  "dispatch": {
    "allowed_models": [
      "qoder:lite",
      "qoder:qmodel_38max",
      "codebuddy:hy3",
      "codebuddy:deepseek-v4-flash"
    ],
    "preferred_models": [],
    "task_kind_allowed_models": {}
  },
  "trusted_roots": {
    "model_evidence": {},
    "instructions": {}
  },
  "resources": {
    "worker_profiles_root": "",
    "worker_skills_root": ""
  }
}
```

Qoder 与 CodeBuddy 的并发槽位完全独立，默认各为 `2`，不存在额外的 Runtime 总任务上限。需要提高并发时，直接修改 `~/.tp-voyager/config.json` 对应 Crew 的 `max_concurrent_tasks`（合法范围 `1..64`）。`config/v2` 是 clean break；旧 `config/v1` 不会自动迁移或继续接受已移除的 `runtime.max_concurrent_tasks`。

Crew CLI 的解析顺序是“临时环境变量覆盖 → `config.json` → PATH”。当前临时覆盖变量仍是 `QODER_CLI_PATH`、`CODEBUDDY_CODE_PATH` 和 `CODEBUDDY_INTERNET_ENVIRONMENT`；正常长期使用应写入 `config.json`。Token、Cookie、登录缓存等 Credential **不得**写入该文件。

`model_routing_profiles.json` 继续独立存在，因为它是可更新、带 Evidence/provenance 的模型认知资料，而不是普通机器配置。`tp-voyager init` 会在缺失时 materialize 随包的 26-route baseline；当前账号快照有 27 个可见条目，但 Qoder GLM-5.3 的 account-specific route id 未在本构建环境捕获，因此 baseline 不猜测该 alias。也可以单独执行 `python -m agent_runtime.cli model-routing-init`。

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

`task_dispatch` 的可选 `model_parameters` 必须绑定显式 `model`，例如
`{"reasoning_effort":"high"}`，或 Qoder 的
`{"reasoning_effort":"medium","context_window_tokens":200000}`。结果会同时保留请求值和后端实际应用状态；不会自动降级、替换模型或重试。

当 Captain 提供参数时，TP-Voyager 还会用当前 Provider 动态目录在创建任务前验证：思考档必须在该模型声明的 effort 列表中；Qoder 上下文必须是该模型声明的 context-window 值。目录未知、不支持或不兼容均会明确拒绝。`task_result.usage` 只显示 Provider 实报的 `input_tokens`、`output_tokens`、`credits_used`、`reported_cost` 和 `currency`；若没有实报，返回 `{"status":"provider_omitted"}`，绝不按模型倍率估算。

Qoder 的可下发 ID 为小写的 `qoder:lite`（任务中的 `model="lite"`）；`Lite` 只是 Provider 展示名称。不要把显示名称写入 allowlist 或 dispatch 请求。

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
│   ├── configuration/          # 用户级 TP-Voyager 配置
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

v1.0.7 **不为了目录好看大搬 Durable Core**。已有大型历史 service 暂时保持兼容位置；新能力优先进入已有职责槽位，后续只有在真实维护成本证明值得时再迁移。

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


### Model Evaluation Standard v1

The current v1.0.7 baseline standardizes model-evaluation provenance without changing Captain authority. Fixed-model Tier is a persisted Scorecard result; legacy tiers are historical only, Qoder Ultimate/Performance/Efficient/Lite remain `DYNAMIC`, and `qoder:auto` is retired by local policy. Existing v1 operator profile files remain readable and can be explicitly migrated with `tp-voyager model-routing-migrate`; use `tp-voyager model-evaluation-validate` for read-only validation. See `docs/MODEL_EVALUATION_STANDARD.md`.
