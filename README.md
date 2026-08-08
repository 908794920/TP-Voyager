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

**TP-Voyager Initial Real-Use Baseline v1.0.0 — FINAL ACCEPTED**

已完成并通过真实环境验收：

```text
T0 目标架构收口           ACCEPTED
T1 Crew Registry          ACCEPTED
T2 Captain Boundary       ACCEPTED
T3 受控只读 Worker        ACCEPTED
T4 受控 Patch Worker      ACCEPTED
Final MCP Discovery       ACCEPTED
```

当前阶段已经从“造船”进入**真实航行（Real Voyage）**。后续功能只应由真实使用问题驱动。

## 核心能力

### Captain 控制面

Captain 日常主要使用：

```text
crew_catalog
crew_health
crew_recommend
voyager_overview
task_dispatch
task_result
```

Runtime 仍提供通用 Task / Sub-Agent 生命周期工具，用于状态、等待、结果、取消和恢复。

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

TP-Voyager v1.0.0 已具备真实使用基线。

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
