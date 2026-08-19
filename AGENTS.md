# TP-Voyager — AI 执行规则

## 强制阅读顺序

任何 AI 在进行架构、开发、测试或文档修改前，必须依次阅读：

1. `docs/architecture/CHARTER.md`
2. `docs/architecture/DIRECTORY_BASELINE.md`
3. 本文件 `AGENTS.md`

如果拟议修改与 Charter 或 Directory Baseline 冲突，必须先停止并报告冲突，不得自行“优化后继续”。

## 当前基线

**TP-Voyager v1.0.9 — 开发中**

v1.0.7 在 v1.0.6 stable 基线上统一用户机器配置：默认 Home 改为 `~/.tp-voyager`，新增严格 `config.json`，Crew CLI 路径、模型授权、trusted roots、worker resources 与并发上限归一到同一配置事实源。Runtime 继续保持六工具 Captain Surface、显式 Crew/model/effort、Durable Core、Patch/Receipt/Verification 与 RunControl 边界不变。

```text
Passenger → Captain AI → TP-Voyager → Crew
                              ├─ CodeBuddy CLI
                              └─ Qoder CLI
```

- Captain：理解目标、拆解与顺序、选择 Crew/model、应用被接受的 Patch Artifact 到 Passenger Workspace、风险/验收与最终交付。
- TP-Voyager：可靠执行、持久化、恢复、权限、Patch/Receipt 事实校验、隔离 Verification、Outcome/Evidence/Artifact 与资源预算。
- Crew：执行边界明确的具体工作。

**TP-Voyager 不得演化成第二个 Captain。** RunControl 不是 Workflow；`step_key` 不是 Planner；Passenger Workspace mutation 不属于 Runtime。

## Backend 边界

当前正式 Crew 只有：

```text
CodeBuddy CLI
Qoder CLI
```

任何新集成能力必须以它们的官方公开 CLI / SDK / ACP Contract 为依据。

WorkBuddy 已从当前生产执行路径移除。不得重新引入 WorkBuddy transport、Gateway/ACP 执行、公共工具、当前测试或新抽象。历史 `workbuddy.* /v1` schema 字符串只在持久化兼容确有必要时保留；`.agent-runtime` / `AGENT_RUNTIME_*` / WorkBuddy Home 不再属于当前 Runtime 路径选择或迁移流程。

“厂商支持某能力”不等于“TP-Voyager 已允许 Captain 调度该能力”。只有经过 Voyager 受控边界并验收的能力才可标记为 dispatch-ready。

## 目录边界

生产代码顶层保持：

```text
agent_runtime/
├── api/
├── configuration/
├── application/
├── domain/
├── backends/
├── runtime/
├── persistence/
├── verification/
├── testing/
└── server.py
```

`configuration/` 是 v1.0.7 经明确审查新增的唯一用户机器配置边界，只负责 `~/.tp-voyager/config.json` 的解析/初始化；不得演化成通用 `common/platform` 层。

目标 Backend 槽位：

```text
backends/codebuddy/
backends/qoder/
```

未经明确架构审查：

- 不得新增仓库顶层目录；
- 不得新增 `agent_runtime/` 顶层目录；
- 不得创建 `core/`、`platform/`、`services/`、`managers/`、`engine/` 等模糊层；
- 不得为了“更优雅”大规模移动文件。

## Durable Ownership

- SQLite Durable Row 是 Source of Truth。
- 现有 Task Runtime 是唯一 Task 状态机。
- 现有 Workflow / PlanExecution 是可复用的 Durable Foundation，不继续扩展 Planner 智能。
- Captain Dispatch 不得依赖内部 Planner 才能工作。
- Backend / Model / Fallback / Retry 默认必须显式。
- Prompt 与业务内容默认保持瞬态，除非现有 Contract 明确要求持久化。
- 不得创建第二套 Result、Evidence、Artifact、Session、Retry 或 Task 系统。

## Captain 边界

Captain 默认 MCP Surface 只能暴露：

```text
voyager_overview
crew_catalog
crew_health
crew_recommend
task_dispatch
task_result
```

低层兼容工具只能在显式 `TP_VOYAGER_MCP_SURFACE=diagnostic` 诊断模式注册；不得为了普通使用重新扩大默认 Tool Surface。

不要让 Captain 默认读取：

```text
完整 Worker transcript
完整日志
完整 Patch
Vendor CLI 内部参数
SQLite 内部结构
```

大内容通过 Artifact 按需读取。

### 模型目录事实所有权

模型目录必须继续保持数据驱动：

```text
Provider live catalog           → availability/context/effort/reference multiplier
config.json / dispatch          → 能不能用（硬约束）
model_routing_profiles.json     → canonical identity / persisted Scorecard / work & risk advisory
Model Evaluation Standard v1    → Evidence comparability / Tier authority
Runtime Evidence                → 实际历史/Usage
Captain                         → 最终选择
```

- 能力档位、推荐任务、风险边界不得硬编码进 Python；
- fixed model 的正式 Tier 只能来自 persisted `Scorecard.tier` + calibrated `model_tier_rules/v1`；旧静态 Tier 仅保留为 `legacy_capability_tier`；
- Qoder Ultimate / Performance / Efficient / Lite 必须保持 `DYNAMIC`，不得附 fixed-model Scorecard；`qoder:auto` 在 TP-Voyager 本地策略中已退休；
- Provider claim、preference/Elo、legacy evidence 或不兼容 benchmark version 不得单独提升正式 Tier；
- `model_routing_profiles.json` 只提供建议，不能绕过 `config.json.dispatch`；
- `reference_multiplier` 永远是 reference-only，公共投影固定 `calculation_allowed=false`；
- `crew_catalog` 可合并并投影事实，但必须保持 `selection_performed=false`、`dispatch_performed=false`；
- Provider / policy / profile / history 互相不能篡改对方拥有的事实。

## Crew 安全边界

- Captain 选择 Crew，Runtime 只能推荐，不得偷偷替换。
- 不允许隐藏 Fallback。
- 不允许 Worker 默认递归调度其他 Worker。
- 受控路线不得使用 `--yolo` / permission bypass。
- Patch 必须使用 Runtime-owned isolated worktree。
- Patch Policy 必须 fail-closed。
- Worker 自述不能替代 Verification / Evidence。

## 测试规则

默认：

```text
Smoke + 直接受影响专项
```

只有以下变化才升级到 Regression：

```text
Durable Task 生命周期
Session / Lease / Reconciliation
共享 Backend 抽象
公共 Contract
持久化 Schema
Workflow / Recovery
```

只有真实 Backend 集成变化才需要针对性的 Live 测试。

`stress` / `release` 不得成为日常默认测试。

删除功能时：

```text
生产代码
+
对应当前测试
+
对应当前文档
```

一起删除。不要保留僵尸测试。

## Scope Gate

新增功能至少满足一项：

1. 明确降低 Captain Token / 交互成本；
2. 是可靠执行、安全、恢复、验证或证据所必需；
3. 是 CodeBuddy/Qoder 官方 Contract 兼容所必需。

三项都不满足：

```text
REJECT / PARK
```

## 修改原则

任何执行 AI 必须：

```text
读 Charter
→ 读 Directory Baseline
→ 找现有职责槽位
→ 修改最小表面
→ 跑 Smoke + 受影响测试
→ 报告结果
```

不得自行决定：

> “为了架构更整洁，我顺便重构一下。”

当前阶段是 **Real Voyage**，不是继续造船。
