# TP-Voyager

> **AI 远航指挥系统（AI Voyage Command System）**
>
> 让一个上层 **Captain AI（船长）** 可靠地调度多个专业 **Crew（船员）** 完成真实研发任务。

```text
乘客（人）
   ↓ 目标 / 约束 / 验收标准
Captain AI（船长）
   ↓ 任务拆解 / 船员选择 / 最终判断
TP-Voyager
   ↓ 持久化 / 调度 / 恢复 / 权限 / 验证 / 证据
Crew
├── CodeBuddy CLI
└── Qoder CLI
```

## 项目定位

TP-Voyager 不是第二个“规划 AI”，也不是新的 Agent 社交网络。

它是一层位于 Captain AI 与执行型 AI CLI 之间的**可靠执行与控制层**：

- Captain 负责理解目标、拆解任务、选择 Crew、判断风险、验收结果；
- TP-Voyager 负责可靠执行、任务状态、恢复、隔离、权限控制、验证、证据和结果投影；
- Crew 只负责执行边界明确的具体任务。

当前正式支持的 Crew：

- **CodeBuddy CLI**
- **Qoder CLI**

WorkBuddy 已从当前生产执行路径移除，仅保留必要的历史数据兼容。

## 当前状态

**TP-Voyager v1.0.2 — stable**

`v1.0.1` 是已完成真实环境 Live 验收的稳定基线；`v1.0.2` 不改变 Captain / Voyager / Crew 三层、Runtime 核心状态机、Task/Result/Artifact 边界或 MCP 执行模型，只增强 Captain 可见的互操作契约与真实 Usage 事实记录。`v1.0.2` 已在真实 Windows + 正式 `mcp` + CodeBuddy/Qoder CLI 主机通过 Live Gate（docs/TESTING.md Captain Cognition Live Matrix 5 项全 PASS）。

`v1.0.2` 新增：

- 标准 Captain Skill YAML frontmatter 与 `tp-voyager.manifest/v1`；
- `doctor --json` 安装/Runtime/MCP/Crew 只读诊断；
- `tp-voyager.usage/v1` Usage Evidence（只记录 provider/CLI 实际返回）；
- Passenger/Captain `model_policy.allowed_models`，不自动选模、不 fallback；
- 受 SHA-256 约束的 `worker_profile_ref`；
- CodeBuddy/Qoder 共用的 Captain-facing `read_scope`；
- 外部任务只关联、不接管生命周期的 `correlation_id`。

当前状态：

```text
T0 目标架构收口           ACCEPTED
T1 Crew Registry          ACCEPTED
T2 Captain Boundary       ACCEPTED
T3 受控只读 Worker        ACCEPTED
T4 Patch Worker           ACCEPTED (v1.0.1)
v1.0.2 Captain cognition  LIVE-GATE PASSED / stable
默认 Captain MCP Surface  6 tools
```

`v1.0.1` 已满足稳定化 Gate：**Smoke 全绿 + CodeBuddy/Qoder 最小 Live Patch 矩阵通过 + 无 `patch-*` worktree 残留**。`v1.0.2` 保持这条稳定执行基线不变，新增能力已通过真实 CLI Live Gate。

因此：

- 只读路线与受控 Patch 路线均可作为当前稳定能力使用；
- `v1.0.0` 的 Patch 竞态已由 `v1.0.1` 修复并正式发布。

## 核心能力

### Captain 控制面

Captain 默认 MCP Surface **只暴露 6 个高层工具**：

```text
crew_catalog
crew_health
crew_recommend
voyager_overview
task_dispatch
task_result
```

旧 Task / Sub-Agent / Context / Planner / Artifact 工具仍保留为诊断/兼容面，但只有显式设置：

```text
TP_VOYAGER_MCP_SURFACE=diagnostic
```

才会注册到 MCP。正常 Captain 不需要理解这些低层工具。

### 受控只读任务

适合：

- 代码理解
- 技术调研
- Code Review
- 测试失败分析
- 验证性分析

CodeBuddy 与 Qoder 都必须经过 TP-Voyager 的受控边界，不允许绕过 Runtime 直接执行。

### 受控 Patch 任务

Patch 模式采用：

```text
干净 Git 工作树
    ↓
Runtime 创建隔离 worktree
    ↓
allowed / forbidden path policy
    ↓
精确 argv 命令白名单
    ↓
Patch / Hash 捕获
    ↓
Verification / Evidence
    ↓
不自动合并回乘客工作树
```

### 真实使用默认策略

Captain Skill 提供以下默认预算，不自动重试：

```text
quick          180s
investigation  600s
review         600s
patch          900s
verify         300s
```

`task_result` 会返回：

```text
execution_budget.max_task_duration_seconds
execution_budget.elapsed_seconds
execution_budget.timeout_reason
```

因此 Qoder 长调查超时应被解释为“预算不足或任务未在预算内结束”，而不是直接解释为 Backend 不可用。

CodeBuddy 只读任务可直接通过 `task_dispatch(context_files=[...])` 传入最小文件范围；TP-Voyager 会在内部复用现有 Context Manifest 机制，普通使用者不需要手工操作 `context_*` 工具。

CodeBuddy Health 将“CLI/SDK 可用”“认证是否实际探测”“最近成功的显式模型”分开表达。没有官方机器可读模型目录时仍保持 unknown，不伪造模型清单。

### Durable Runtime

当前 `agent_runtime/` 继续复用已经验收的 Durable Core：

```text
Task / Session / Attempt / Event / Idempotency
Lease / Fencing / Cancel / Restart Reconciliation
Workflow / PlanExecution
Context / Knowledge / Tool Runtime
Structured Result / Evidence / Artifact / Verification / Plan Result
SQLite Durable Row = Source of Truth
```

产品名称是 TP-Voyager，但 Python 包名暂时仍为 `agent_runtime`，避免无价值的 import 与迁移 churn。

## Captain Skill

仓库内置：

```text
skills/tp-voyager-captain/
├── SKILL.md
└── README.md
```

把该 Skill 加载到能访问 TP-Voyager MCP Server 的上层 AI 后，它会获得一套稳定的“船长操作规范”，包括：

- 如何查询 Crew；
- 如何选择并派遣子 Agent；
- 如何查看 Voyage 进度；
- 如何读取结果与证据；
- 如何避免绕过 Runtime；
- 如何控制 Captain 上下文和 Token 消耗。

详细说明见：

[skills/tp-voyager-captain/README.md](skills/tp-voyager-captain/README.md)

## 环境要求

- Python **3.10+**
- Git
- MCP Python package
- 使用 CodeBuddy 时：已安装并登录 CodeBuddy CLI
- 使用 Qoder 时：已安装并登录 Qoder CLI

依赖声明见：

```text
requirements.txt
pyproject.toml
```

## 快速开始

### 1. 创建虚拟环境

Windows PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. 启动 Runtime

如果仓库根目录存在 `.venv`，`start_runtime.cmd` 会优先使用：

```text
.venv\Scripts\python.exe
```

也可以显式指定 Python：

```powershell
$env:AGENT_RUNTIME_PYTHON = "D:\path\to\python.exe"
.\start_runtime.cmd
```

或者：

```powershell
.\.venv\Scripts\python.exe -m agent_runtime.server
```

### 3. 中国区 CodeBuddy

如果使用中国区账号：

```powershell
$env:CODEBUDDY_INTERNET_ENVIRONMENT = "internal"
```

不要把 Token、登录缓存或本地 `.env` 提交到 Git。

## 测试原则

日常开发默认只运行：

```text
Smoke + 直接受影响的专项测试
```

运行 Smoke：

```powershell
.\run_tests.cmd
```

显式运行其他维护 Profile：

```powershell
.\run_tests.cmd current
.\run_tests.cmd regression
```

`stress` / `release` 仅在对应核心边界变化或正式发布时运行，禁止把历史多小时测试重新变成日常门槛。

更多说明：

[docs/TESTING.md](docs/TESTING.md)

## 项目结构

```text
TP-Voyager/
├── agent_runtime/                  # Durable Runtime 与应用层
│   ├── api/
│   ├── application/
│   ├── domain/
│   ├── backends/
│   │   ├── codebuddy/
│   │   └── qoder/
│   ├── persistence/
│   ├── runtime/
│   ├── verification/
│   └── testing/
├── skills/
│   └── tp-voyager-captain/
├── tests/
├── docs/
├── TP_VOYAGER_CHARTER.md
├── TP_VOYAGER_DIRECTORY_BASELINE.md
└── AGENTS.md
```

## 架构与开发约束

任何架构、需求、测试或目录变更都必须先阅读：

1. [`TP_VOYAGER_CHARTER.md`](TP_VOYAGER_CHARTER.md)
2. [`TP_VOYAGER_DIRECTORY_BASELINE.md`](TP_VOYAGER_DIRECTORY_BASELINE.md)
3. [`AGENTS.md`](AGENTS.md)

两份基线规范是项目的最高约束。发布整理阶段不会因为“更漂亮”而重写它们的核心语义。

当前核心原则：

```text
简单 > 完整
显式 > 自动
复用 > 重建
边界明确 > 无限制
证据 > 声明
真实需求 > 预设未来
```

## 文档

- [架构说明](docs/ARCHITECTURE.md)
- [运行与配置](docs/OPERATIONS.md)
- [测试策略](docs/TESTING.md)
- [CodeBuddy Backend](docs/BACKEND_CODEBUDDY.md)
- [Qoder Backend](docs/BACKEND_QODER.md)
- [Captain Skill](skills/tp-voyager-captain/README.md)

历史验收记录位于 `docs/records/`，它们用于追溯，不属于当前产品 Contract。

## 开源协议

本项目采用 **MIT License**。

MIT 是非常宽松、友好的开源协议，允许个人和商业场景自由使用、复制、修改、合并、发布、分发、再授权和销售，只需保留版权与许可证声明。

法律文本以仓库根目录 [`LICENSE`](LICENSE) 中的官方英文原文为准。

## 当前开发策略

TP-Voyager 已进入真实航行阶段；`v1.0.2` 只在 v1.0.1 stable 基线上增强 Captain 认知与互操作契约，不扩展为 Planner、计费系统或自动模型路由。

当前不主动扩展：

- Agent 社交网络
- 自动无限重试
- 隐式模型切换
- 自动任务膨胀
- Vector DB / 自动知识写回
- Web UI
- 复杂 DAG 平台
- 企业审批系统

下一阶段的正确方式是：

```text
真实使用
  ↓
发现重复出现的问题
  ↓
通过 Charter Gate
  ↓
做最小增强
```
