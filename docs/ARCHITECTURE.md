# TP-Voyager 架构说明

## 产品模型

```text
Passenger → Captain AI → TP-Voyager → Crew
                                  ├─ CodeBuddy CLI
                                  └─ Qoder CLI
```

乘客只负责给出目的地、约束和验收预期。

Captain AI 负责理解目标、拆解任务、选择 Crew、判断风险、评审结果和最终交付。

TP-Voyager 是可靠执行与控制层，不是第二个 Captain。

## Durable Foundation

现有 `agent_runtime/` 是经过验收的执行核心，继续复用：

```text
Task / Session / Attempt / Event / Idempotency
Lease / Fencing
Cancel / Restart Reconciliation
Workflow / PlanExecution
Context / Knowledge / Tool Runtime
Structured Result / Evidence / Artifact
Verification / Plan Result
```

持久化原则：

```text
SQLite Durable Row = Source of Truth
```

## Captain 主路径

默认 MCP Surface 只注册 6 个 Captain 工具：

```text
crew_catalog(include_models=true) / crew_health(..., model=...) / crew_recommend
                 ↓
        Captain 查看 Crew / Model 事实
                 ↓
          Captain 选择 Crew / Model
                 ↓
            task_dispatch
                 ↓
     CodeBuddy / Qoder Adapter
                 ↓
        Durable Task Lifecycle
                 ↓
        Verification / Evidence
                 ↓
 task_result / voyager_overview
```

低层 Task / Context / Planner / Artifact API 没有被删除，但只属于显式 `diagnostic` Surface。这样仍然只有一个 Runtime、一个 Durable Task 状态机，只是对 Captain 的工具可见面更小。

Crew 推荐、模型目录、能力/计费参考、历史表现与 Usage 汇总都只提供决策依据。TP-Voyager 不把这些事实转化为自动模型评分或自动路由。

禁止：

- 隐藏 Fallback；
- 隐式 Backend/Model 切换；
- 无限重试；
- 自动扩大 Mission；
- Runtime 代替 Captain 做业务规划。

## 受控模式

### `read_only`

用于代码分析、调研、Review、故障分析等。

- CodeBuddy：接收 Runtime 渲染且 Hash 验证的 Context，原生工具关闭；
- Qoder：通过官方 ACP 提供工作区范围内的受控 Read/Search；
- 不允许使用 `--yolo`。

### `patch`

用于小型、边界明确的代码修改：

```text
Runtime-owned Git worktree
→ allowed/forbidden paths
→ exact argv command whitelist
→ patch capture
→ deterministic verification
→ evidence
```

Runtime 不自动把 Patch 合并回乘客工作树。

Patch 的终态顺序固定为：

```text
Crew terminal material
→ Artifact capture
→ Verification
→ isolated worktree cleanup + Git registration check
→ durable completed
```

因此 Captain 一旦观察到 `completed`，Runtime-owned `patch-*` worktree 必须已经退休。Cleanup 失败会把 Task 收敛为明确 failure，而不是先宣布成功再后台清理。

## Model Awareness

v1.0.6 不增加新的 Captain MCP tool，而是把既有模型事实整理成 data-driven Routable Model Catalog：

```text
Provider model catalog
+ dispatch_model_policy.json
+ model_routing_profiles.json
+ Durable Task/Session model history
+ Attempt-bound Usage Evidence
        ↓
crew_catalog(include_models=true)
crew_health(backend, model=...)
```

事实所有权保持分离：Provider 拥有实时 availability/context/effort/reference multiplier；dispatch policy 拥有授权；operator routing profile 拥有能力档位/推荐任务/风险边界；Runtime Evidence 拥有实际历史和 Usage；Captain 拥有最终选择。

`routable` 使用三态语义：明确允许且 Provider 可用为 true；明确拒绝/disabled/policy invalid 为 false；允许但实时 availability 未确认时为 null。Provider 当前目录缺失的 policy/profile/history route 仍可投影，但绝不伪造 availability。

`reference_multiplier` 永远保持 reference-only，公共 route projection 固定 `calculation_allowed=false`。TP-Voyager 不把模型资料转化成自动评分、自动选择、自动 fallback 或账单估算。

完整配置与字段语义见 `docs/MODEL_ROUTING.md`。

## Controlled Repository Research

`repository_research` 是独立只读 Contract：

```text
Captain exact public GitHub URL + size limit + new target + Crew/model + read_scope
        ↓
Runtime fixed GitHub metadata precheck
        ↓
shallow clone -> target/source -> remove origin
        ↓
existing CodeBuddy/Qoder read-only route
        ↓
Runtime-owned target/reports/... Artifact
```

它复用现有 Task/Session/Attempt/Evidence/Artifact，不增加 Planner/Workflow 状态机。禁止执行、安装依赖、build/start、修改下载源码、覆盖已有目录、任意网络爬取、自动 fallback 或递归派工。Provider transport 仍可联网，因此该 Contract 是“源码侧受控静态研究”，不是物理断网沙箱。

## WorkBuddy

WorkBuddy 已从当前生产 Backend 移除。

只允许保留读取/迁移历史数据所必需的 schema/path 兼容。历史 WorkBuddy 记录不属于当前文档 Contract，也不应重新进入生产执行路径。

## 架构约束

正式约束来源：

1. `TP_VOYAGER_CHARTER.md`
2. `TP_VOYAGER_DIRECTORY_BASELINE.md`
3. `AGENTS.md`

任何新需求必须先通过 Charter Gate。
