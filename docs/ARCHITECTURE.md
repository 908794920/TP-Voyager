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
crew_catalog / crew_health / crew_recommend
                 ↓
          Captain 选择 Crew
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

Crew 推荐只提供决策依据。

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

## WorkBuddy

WorkBuddy 已从当前生产 Backend 移除。

只允许保留读取/迁移历史数据所必需的 schema/path 兼容。详见：

`docs/records/legacy-workbuddy/DATA_COMPATIBILITY.md`

## 架构约束

正式约束来源：

1. `TP_VOYAGER_CHARTER.md`
2. `TP_VOYAGER_DIRECTORY_BASELINE.md`
3. `AGENTS.md`

任何新需求必须先通过 Charter Gate。
