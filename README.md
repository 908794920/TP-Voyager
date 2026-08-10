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

**TP-Voyager v1.0.5 — Full Development Flow Control（release candidate，基于 v1.0.4 stable）**



v1.0.5 在不增加 Captain 默认工具数量、不接入 Planner/Workflow Engine 的前提下新增：`trusted_instruction_refs`、结构化 CrewOutcome、Captain-Host Apply Receipt 校验、精确 Verification Subject、Disposable Verification Workspace、大仓库 Scope 分片与 Runtime-owned snapshot 复用、Durable RunControl 资源预算、以及 `task_result(run_id, step_key)` 跨会话恢复。Passenger Workspace 的真实修改仍由 Captain Host 完成。
v1.0.4 keeps the exact six-tool Captain surface (`voyager_overview`,
`crew_catalog`, `crew_health`, `crew_recommend`, `task_dispatch`,
`task_result`).  Model choice is explicit and policy can only narrow it;
trusted Worker Skills are hash-pinned, while input Artifacts are bounded,
hash-rechecked untrusted data.  Account-live ACP catalog evidence remains a
human-authorized Live Gate and is never inferred from CLI help output.

`v1.0.2` 是已经通过真实 Windows + 正式 MCP + CodeBuddy/Qoder CLI Live Gate 的 stable 基线。`v1.0.3` 继续保持 Captain / Voyager / Crew 三层、Runtime 核心状态机、Task/Result/Artifact/Evidence 真相源和默认 6-tool Captain MCP Surface 不变，集中补齐模型事实查询、只读归属边界和受控外部源码研究 Contract。

`v1.0.3` 新增/修复：

- **read_only artifact attribution 修复**：只读任务不扫描/投影工作区既有 Git diff，不生成 `workspace.patch`，`changed_files=[]`；
- **Model Registry projection**：`crew_catalog(include_models=true)` 返回 CodeBuddy/Qoder 模型目录快照、来源、完整性、能力/计费参考元数据、历史表现与 Usage 汇总；
- **CodeBuddy CLI model catalog**：只解析 `codebuddy --help` 的 CLI 声明模型，来源标记 `cli_declared`，账号授权状态保持 unknown；
- **Qoder account model catalog**：优先通过官方 `qoder-agent-sdk` 的 `get_available_models()` 获取当前账号实时目录与 provider-live entitlement/`priceFactor`/promotion 元数据；SDK 控制面不可用时回退 `qodercli --list-models`，Windows PIPE 疑似单行截断时标记 `incomplete_suspected`；
- **Model facts query**：`crew_health(backend, model=...)` 在原有工具上提供该模型的目录状态、历史成功率/耗时和 Usage 汇总，不新增自动路由工具；
- **Profile ↔ Model constraint**：`worker_profile_ref.allowed_models` 只做显式模型约束，不替 Captain 选模；
- **read_scope budget**：新增 `max_files` / `max_bytes`，并拒绝任何层级的 `.git/.codebuddy/.qoder` 状态目录；
- **repository_research**：Captain 显式给定公开 GitHub URL、大小上限、全新目标目录、Crew/model/read_scope；Runtime 做固定 GitHub 元数据预检和浅克隆，Crew 仅静态只读，最终报告由 Runtime 写入指定 `reports/` Artifact。

明确仍不做：

```text
自动模型选择 / 自动 fallback / 模型评分 / 费用估算
Planner / DAG / Scheduler / 第二任务系统
任意网络研究 / 私有仓库获取 / 运行下载源码 / 安装依赖 / build/start
```

当前状态：

```text
T0 目标架构收口             ACCEPTED
T1 Crew Registry            ACCEPTED
T2 Captain Boundary         ACCEPTED
T3 受控只读 Worker          ACCEPTED
T4 Patch Worker             ACCEPTED (v1.0.2 stable baseline)
v1.0.3 P0/P1/P2 code gate   PASS (Smoke/Current/Regression/Stress)
v1.0.3 real CLI Live Gate   PASSED (2026-08-09)
默认 Captain MCP Surface    6 tools
```

`v1.0.3` 已在真实 Windows + 正式 MCP + CodeBuddy/Qoder CLI/账号环境完成 `docs/TESTING.md` 定义的 v1.0.3 Live Matrix（L1–L8 + 人工体验 UX-1~4），全部 PASS。

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

CodeBuddy Health 将“CLI/SDK 可用”“认证是否实际探测”“最近成功的显式模型”分开表达。v1.0.4 优先通过 catalog-only ACP `initialize → session/new → close` 投影账号态模型与 reference-only 倍率；ACP 不可用时才回退到 `codebuddy --help` 的 `cli_declared` 清单，回退结果不携带实时倍率，也不伪装 entitlement。

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

TP-Voyager 已进入真实航行阶段；`v1.0.3` 在 v1.0.2 stable 基线上补齐 Model Awareness 与受控 repository_research，仍不扩展为 Planner、计费系统或自动模型路由。

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
