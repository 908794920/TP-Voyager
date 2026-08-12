> **v1.0.2 发布状态（2026-08-09）**
>
> 本文定义的 Captain Cognition Live Matrix 已在隔离环境 `%TEMP%\tp-voyager-v102-live` 以真实 MCP stdio、CodeBuddy CLI 与 Qoder CLI 执行并通过；对应范围为 doctor、两家 Crew 的 read_scope/Usage、Qoder timeout Usage 保留及两家 bounded patch。
>
> 下文“标记 stable 前”的措辞保留为后续版本复用的准入规则，不表示当前 v1.0.2 仍待发布。

---

# TP-Voyager 测试策略

测试只保护**当前支持 Contract**，不永久保存每个历史实现阶段。

## 日常默认

```text
Smoke + 直接受影响专项
```

直接运行：

```bat
scripts\run_tests.cmd
```

无参数时默认 Smoke。

## 维护 Profile

### `smoke`

快速结构性信心。

### `current`

当前 TP-Voyager Captain / Crew / 受控执行表面。

### `regression`

仅用于跨核心边界的修改，例如：

- Durable Task 生命周期；
- Session / Lease / Reconciliation；
- 公共执行 Contract；
- Persistence Schema；
- Workflow / Recovery；
- 共享 Backend 抽象。

### `stress`

只用于显式 Scheduler / Lease / Race 场景。

### `release`

只用于正式 Release Gate。

## Live 测试

真实 CodeBuddy/Qoder 的以下变化应做小范围 Live 验证：

- 登录/认证；
- CLI/SDK/ACP 调用；
- Streaming；
- Cancel/Resume；
- Model Discovery；
- Permission Bridge；
- Patch Isolation。

不要把真实模型调用伪装成纯单元测试 PASS。


## v1.0.1 Patch 稳定化 Gate

真实使用暴露过一次 Patch 终态/cleanup 竞态，因此当前 Patch Release Gate 必须额外满足：

```text
Task 对外为 completed
AND Verification = PASSED
AND runtime/workspaces/patch-* 无残留
AND Git worktree registration 无残留
```

自动测试中使用同步 Gate 确认：cleanup 尚未完成时，`completed` 不得可见；注入 cleanup failure 时任务必须终止为 failure。

正式恢复 Patch production-ready 声明前，只需要做最小 Live Matrix：CodeBuddy/Qoder 各一个 bounded patch，并检查原始工作树与 Runtime worktree。不要重跑历史多小时套件。

## v1.0.2 Captain Cognition Gate

代码侧必须满足：

```text
Smoke 全绿
+ Current 全绿
+ schema 11 -> 12 迁移保留既有 Evidence/Workflow 数据
+ doctor --json 不调用模型且不返回 Credential/任务内容/Usage
+ Usage Evidence 只保存 provider 实际返回字段
+ Qoder timeout/cancel 前已观察到的 Usage 不因无最终 Result 而丢失
+ model_policy 不做自动选择/fallback
+ read_scope 对 CodeBuddy/Qoder 都 fail-closed
+ worker_profile_ref SHA-256 不匹配必须拒绝
+ 默认 Captain MCP Surface 仍为 6 tools
```

正式把 v1.0.2 标记为 stable 前，在装有正式 MCP、CodeBuddy SDK/CLI、Qoder CLI 的主机执行最小 Live Matrix：

```text
1. doctor --json
2. CodeBuddy read_scope read-only + task_result usage
3. Qoder read_scope read-only + 显式 model_policy/model + task_result usage
4. 一个 Qoder cancel/timeout 样本，确认已返回 Usage 时 Evidence 仍存在
5. CodeBuddy/Qoder 各一个现有 bounded patch，确认 v1.0.1 Patch Gate 未回归
```

不要用公开 Token 单价、Credit 倍率或 Benchmark 推算 Usage 来伪造 Live 结果。


## 防止测试膨胀

删除一个正式功能时，应同时删除：

```text
生产代码
+
当前测试
+
当前文档
```

不要为了历史版本继续保留僵尸测试。

Routine 修改禁止重复运行多小时历史 Audit / Release / Stress。


## v1.0.3 Model Awareness + Repository Research Gate

`v1.0.3` 必须先继承 v1.0.2 stable 的所有 Gate，再额外满足：

```text
P0
- 脏工作树 + read_only：changed_files=[]、无 workspace.patch、无未授权旧 Artifact
- CodeBuddy 模型目录只来自 CLI 声明，available/entitlement 不得伪装为账号已授权
- Qoder 疑似单行 PIPE 目录必须标 incomplete，不得伪装 complete

P1
- crew_catalog(include_models=true) 返回来源、目录完整性、历史/Usage projection
- crew_health(backend, model=...) 只返回事实，不选模、不评分、不估价
- Qoder capability/billing 只允许 sourced reference metadata
- worker_profile_ref.allowed_models 只校验显式 model
- read_scope max_files/max_bytes fail-closed

P2
- repository_research 仅公开 GitHub URL
- Captain 必须给大小上限、全新目标目录、Crew/model/read_scope/report_path
- 大小预检通过后只 shallow clone
- source origin 被移除，Crew 不获得 Terminal/写文件/源码侧 Web 工具
- source 内容研究前后不变
- 报告只由 Runtime 写入 reports/ 并进入 Artifact/Evidence
- 不覆盖已有目录、不自动 fallback、不递归派工
```

自动化最小 Gate：

```powershell
.\scripts\run_tests.cmd smoke
.\scripts\run_tests.cmd current
python -m agent_runtime.testing.runner regression
python -m agent_runtime.testing.runner stress
```

### v1.0.3 Windows Real Live Matrix

在真实 MCP + CodeBuddy/Qoder CLI/账号环境中，建议按顺序执行，避免重复消耗模型额度：

```text
L1 doctor --json
   - version=1.0.3
   - CodeBuddy model catalog source=cli_declared
   - CodeBuddy entitlement 不得被声明为已确认
   - Qoder catalog 若仅捕获单行则必须 incomplete；若完整则记录 complete
   - selection_performed=false / pricing_estimated=false

L2 dirty-worktree read_only regression（CodeBuddy）
   - 下发前人为制造一个与 read_scope 无关的未提交文件改动
   - Crew 只读 README.md
   - task_result.changed_files=[]
   - 无 workspace.patch / 无脏文件 Artifact

L3 dirty-worktree read_only regression（Qoder）
   - 同 L2
   - 同样不得归属既有 diff

L4 model facts
   - crew_catalog(include_models=true)
   - 选择一个 CodeBuddy CLI 声明模型和一个 Qoder 当前可见模型
   - crew_health(..., model=...)
   - 检查 history/usage 为真实历史聚合；无历史时保持 0/null
   - billing reference 不得变成 estimated task cost

L5 Usage regression
   - CodeBuddy/Qoder 各一个最小 read_only（尽量复用 L2/L3）
   - Provider 没返回字段必须 null，不得估算
   - 如 Qoder 返回 Usage，再做一次显式短 timeout，确认 partial Usage 仍可读

L6 repository_research / CodeBuddy
   - 使用一个小型公开 GitHub 仓库
   - 全新目标目录 + 明确 max_size_bytes + read_scope
   - 成功后 source/ 不被修改、origin 已移除、changed_files=[]、无 patch
   - reports/repository-research.md 存在且 Artifact hash/size 可读

L7 repository_research / Qoder
   - 同 L6，Crew 改为 Qoder
   - ACP 不应出现写文件/Terminal 授权成功

L8 bounded patch regression（可复用 v1.0.2 小样本）
   - CodeBuddy/Qoder 各一个最小 patch
   - 确认 v1.0.3 read-only/research 改动未破坏隔离 worktree + Verification
```

v1.0.3 Windows Real Live Matrix 状态：**PASSED（2026-08-09）**。

真实 Windows + 正式 MCP（`python -m agent_runtime.server` stdio）+ CodeBuddy CLI 2.133.0 + Qoder CLI 1.1.17 + 真实 GitHub 网络。隔离运行时 `%TEMP%\tp-voyager-v103-live`，隔离 git fixture 验证脏工作树归属。结果：

- L1 doctor：version=1.0.3、schema 12、Captain tools 6、safety 全 false、selection/pricing=false、CodeBuddy `cli_declared`、Qoder catalog SDK 完整（complete）；
- L2/L3 脏工作树 read_only（CodeBuddy/Qoder，Qoder 显式选择 Lite）：changed_files=[]、无 workspace.patch、无脏文件 Artifact、既有 dirty diff 保留；
- L4 model facts：crew_catalog(include_models=true) + crew_health(backend, model=...) 返回目录来源/完整性/历史/Usage 聚合；Qoder priceFactor 仅 reference（calculation_allowed=false）；
- L5 Usage regression：`tp-voyager.usage/v1`、provider/model 一致、未回传字段保持 null/缺失、reported_cost 原样保存、静态 priceFactor 未补算；
- L6/L7 repository_research（CodeBuddy/Qoder，octocat/Hello-World）：shallow clone（`git_clone_depth_1`）、origin 移除、git status 为空、changed_files=[]、报告写入 reports/ 并捕获 Artifact、无运行/安装/修改源码；Qoder ACP 拒绝写文件/Terminal 授权；
- L8 bounded patch（CodeBuddy/Qoder 最小样本）：verification=PASSED、Passenger worktree 未被直接修改、Task 终态时 patch-* worktree 无残留；
- 人工体验 UX-1~4 全 PASS。

另有幂等/安全负例（G1~G4）：同 key 同 Contract `replayed=true` 不重复 clone、同 key 不同目标 `IDEMPOTENCY_CONFLICT`、已存在目标目录拒绝覆盖、非 GitHub/超大小 fail-closed。

### 人工体验验收

让真实 Captain/用户完成三种自然语言体验：

1. “列出当前 CodeBuddy/Qoder 可见模型，并告诉我哪些信息是账号事实、哪些只是官方参考。”
2. “只读分析这个已有脏工作区里的 README，不要把我原来的改动算成你的修改。”
3. “研究这个公开 GitHub 仓库，只读源码并把报告写到指定目录；不要运行或安装它。”

验收重点不是回答文风，而是 Captain 是否能在**不理解 CodeBuddy/Qoder CLI 差异**的情况下正确使用六个高层工具、是否会尊重 incomplete/unknown、是否仍由 Captain 显式选择 Crew/model。

## v1.0.5 Full Development Flow Control Gate

v1.0.5 inherits the v1.0.4 stable gates and adds the following code-side requirements:

```text
- schema 12 -> 13 preserves existing durable data and creates only the resource-ledger RunControl additions
- trusted_instruction_refs are alias/path/SHA-256 pinned and content is transient
- CrewOutcome is explicit tp-voyager.crew_outcome/v1; prose is never inferred
- Captain Host remains the only Passenger Workspace mutation owner
- Apply Receipt is content-addressed and verification reconstructs base + exact Patch Artifact
- verification runs only in a disposable worktree; Passenger Workspace is unchanged by Runtime verification
- CodeBuddy typed Usage is normalized without serializing arbitrary provider objects
- Qoder Usage provenance distinguishes observed/omitted/unrecognized and file access Evidence contains no file content
- large research uses ContextManifest + deterministic scope segments; single-task 256-file/8MiB limits are not widened
- repository_snapshot_ref reuses a clean Runtime-owned snapshot without re-cloning
- RunControl is a resource ledger only; ceilings may stay equal/narrow and admission is atomic/idempotent
- run_id + step_key can recover the same durable Task through existing task_result
- default Captain MCP Surface remains exactly six tools
```

Automated release-candidate gate:

```powershell
.\scripts\run_tests.cmd smoke
.\scripts\run_tests.cmd current
python -m agent_runtime.testing.runner regression
python -m agent_runtime.testing.runner stress
```

### v1.0.5 Real Captain Host Stable Gate

Stable requires a real ChatGPT/Codex Captain Host, real TP-Voyager MCP stdio, and real CodeBuddy/Qoder accounts. At minimum verify:

```text
H1 Host loads the v1.0.5 Captain Skill and sees exactly the six Captain tools.
H2 A real run_control + step_key sequence can be recovered after MCP restart/new Captain session via task_result(run_id, step_key).
H3 CodeBuddy and Qoder each execute one bounded task with structured CrewOutcome behavior observed; malformed/missing Outcome stays unavailable.
H4 A real small_patch produces a Patch Artifact; Captain Host applies it to Passenger Workspace and supplies tp-voyager.apply_receipt/v1.
H5 verify_only/access_mode=verification reconstructs the exact base + Patch Artifact, matches receipt result_tree_hash, runs authorized verification, and does not mutate Passenger Workspace beyond the Captain-applied change.
H6 A deliberately mismatched receipt/tree fails with APPLY_RECEIPT_SUBJECT_MISMATCH before verification commands run.
H7 RunControl max_dispatches cannot be pierced by concurrent dispatch; idempotency replay is not charged twice; widening an existing ceiling is rejected.
H8 If strict Credits/tokens are requested but provider-level enforcement is unavailable, dispatch fails BUDGET_NOT_ENFORCEABLE rather than estimating.
H9 A >256-file repository_research is processed through deterministic segments; later segments reuse repository_snapshot_ref and do not clone again.
H10 CodeBuddy typed Usage and Qoder Usage/access provenance are visible only as provider-observed bounded Evidence.
H11 Complete one real flow: research -> analysis/design -> patch -> Captain Host apply -> independent verification -> NEEDS_FIX targeted rework (or an injected sample) -> re-verification -> delivery decision.
H12 Final Passenger Workspace contains only Captain-accepted changes, and the external project/task ledger records the Captain's acceptance/verification facts.
```

A Python MCP client alone does not satisfy H1/H2/H11/H12. Those items must be exercised from the actual Captain Host used in production.

### v1.0.5 Real Captain Host Stable Gate 状态：PASSED（2026-08-10）

真实 Codex Desktop Captain Host + 真实 MCP stdio + CodeBuddy CLI 2.133.1 + Qoder CLI 1.1.17。A2~I 矩阵 11 项全 PASS，`task_result(run_id, step_key)` 在 MCP/Captain 重启后恢复同一 Durable Task 成立，`git diff --check` 通过。

v1.0.5 已满足 stable gate 条件，`v1.0.5-rc` 提升为 `v1.0.5 stable`。历史额度失败保留为 superseded 证据，不构成当前阻断。
