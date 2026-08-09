# Changelog

本项目从 TP-Voyager 正式基线开始记录对外版本。

## 1.0.3 — 2026-08-09

基于 `v1.0.2 stable` 的 Model Awareness Completion + Controlled Repository Research 版本。默认 Captain MCP Surface 仍严格保持 6 tools；不新增 Planner、模型自动路由、自动 fallback、计费系统或第二套状态机。

P0：

- 修复 Captain `read_only` 任务将下发前既有脏工作树误归属为本次 `changed_files` / `workspace.patch` / Artifact 的问题；只读路线不再观察 Git diff，也拒绝 Worker 声称拥有工作区修改。
- CodeBuddy 新增 CLI 声明模型目录 Adapter：解析 `codebuddy --help` 的 `Currently supported` 列表，来源标记 `cli_declared`，账号 entitlement 保持 unknown。
- Qoder 模型目录对 Windows PIPE 单行疑似截断返回显式标记 `incomplete`，不再把一行伪装成完整目录。
- `crew_catalog(include_models=true)` 统一投影 Model Registry：当前目录 + durable 历史 + Usage Evidence；无新增模型表。
- `read_scope` 增加 `max_files` / `max_bytes`，并阻止任意层级 `.git/.codebuddy/.qoder` 进入上下文。

P1：

- Model Registry 为每个模型提供事实型 history/health（sample、success rate、平均耗时、failure streak）与 Usage Statistics；只聚合 `tp-voyager.usage/v1`，`pricing_estimated=false`。
- `crew_health(backend, model=...)` 在不增加 Captain 工具数量的前提下提供单模型事实查询。
- Qoder 模型目录优先通过官方 Python SDK `get_available_models()` 获取当前账号实时 `isEnabled/isFree/priceFactor/context/thinking/promotion` 元数据；不发送模型 Prompt。SDK 不可用时回退 `qodercli --list-models`，疑似 Windows PIPE 单行截断显式标记 incomplete。
- Qoder SDK 的 `priceFactor` 与官方 tier/明确匹配模型 Credit 倍率都只作为 provider/reference metadata 暴露，`calculation_allowed=false`；Runtime 不据此推算任务账单。CodeBuddy 未发现可靠 per-model 固定费率时保持 unknown。
- `worker_profile_ref.allowed_models` 允许 Profile 声明受控模型集合；只做校验，不自动选模。
- `doctor --json` 增加 CodeBuddy/Qoder model catalog 状态，同时继续声明无模型调用、无凭证/任务内容/Usage 返回。

P2：

- Charter 显式加入 `repository_research` 冻结任务类。
- 新增严格受控的公开 GitHub 静态源码研究 Contract：Captain 明确 URL、最大大小、新目标目录、report path、Crew/model 与 read_scope；Runtime 固定 GitHub API 元数据预检 + `git clone --depth 1 --single-branch --no-tags`，随后移除 origin。
- Crew 继续使用既有 CodeBuddy context-only / Qoder ACP read-only 路线：不提供 source 写入、terminal、依赖安装、build/run 或任意 source 网络工具。
- research report 由 Runtime 将 Crew 最终答案写入 `reports/` 后作为普通 Artifact 捕获；下载源码本身不计为 Crew changed_files。
- `repository_research` 继承 Durable Idempotency：相同 key + 相同 Captain Contract 直接 replay，不重复 clone；同 key 不同 Contract 明确 `IDEMPOTENCY_CONFLICT`。
- acquisition 设置 `GIT_TERMINAL_PROMPT=0` 与 `GIT_LFS_SKIP_SMUDGE=1`，防止静态 clone 因交互式认证或 LFS smudge 扩大网络获取。
- 同步收紧 Patch 失败终态：失败证据捕获后、Durable `failed` 对 Captain 可见前先退休 patch worktree；瞬时 cleanup 失败最多两次有界尝试，消除失败终态/cleanup 竞态。

Live Gate（2026-08-09，真实 Windows + 正式 MCP + CodeBuddy CLI 2.133.0 + Qoder CLI 1.1.17 + 真实 GitHub 网络）全部 PASS：

1. A1/A2 安装与 `doctor --json`：version=1.0.3、schema 12、Captain tools 6、safety 全 false（无模型调用/无 Credential/无任务正文/无历史 Usage）、selection_performed=false、pricing_estimated=false；CodeBuddy catalog `cli_declared`、Qoder catalog 经 SDK 获取完整（complete）；
2. B Model Registry：CodeBuddy `cli_declared` 且 available/entitlement 保持 unknown；Qoder `official_dynamic_sdk` 15 模型带 isEnabled/isFree/context/thinking/promotion，priceFactor 仅 reference metadata（calculation_allowed=false），usage/history 为真实聚合；`selection_performed=false`；
3. C1/C2 脏工作树 read_only（CodeBuddy/Qoder，Qoder 显式选择 Lite）：changed_files=[]、无 workspace.patch、无脏文件 Artifact、既有 dirty diff 保留；
4. D Usage Evidence：`tp-voyager.usage/v1`、provider/model 一致、未回传字段保持 null/缺失不估算、reported_cost 原样保存、静态 priceFactor 未补算 usage；
5. E/F repository_research（CodeBuddy/Qoder，octocat/Hello-World）：shallow clone（`git_clone_depth_1`）、origin 移除、`git status --short` 为空、changed_files=[]、报告写入 reports/ 并作为 Artifact 捕获、无运行/安装/修改源码；Qoder ACP 拒绝写文件/Terminal 授权；
6. G 幂等/安全负例：同 key 同 Contract `replayed=true`/`dispatch_performed=false` 不重复 clone；同 key 不同目标 `IDEMPOTENCY_CONFLICT`；已存在目标目录拒绝覆盖；非 GitHub/超大小 fail-closed、无自动 fallback；
7. H bounded patch regression（CodeBuddy/Qoder 最小样本）：verification=PASSED、Passenger 原始 worktree 未被直接修改、Task 进入终态时 patch-* 临时 worktree 已退休无残留；
8. I 人工体验 UX-1~4：模型认知/只读脏工作区/外部源码研究/Captain 决策权全 PASS，Runtime 未自动选模、未自动 fallback、未换 Crew。

已满足 stable gate 全部条件（A~G 全 PASS、UX 全 PASS、H 无回归、无 Credential/任务正文泄露、无自动选模/fallback）。

## 1.0.2 — 2026-08-09

基于 `v1.0.1 stable` 的 Captain 认知与互操作增强版本；不改变 Captain/Voyager/Crew 三层、Runtime 核心状态机、Task/Result/Artifact 边界或默认 6-tool MCP Surface。

新增与收口：

- Captain Skill 标准 YAML frontmatter；
- `tp-voyager.manifest/v1`，声明 Skill、stdio MCP 启动、必需 Captain tools 与 doctor 入口；
- `python -m agent_runtime.cli doctor --json`，只读检查 Runtime/MCP/Captain tools/CodeBuddy/Qoder，不调用模型、不返回 Credential/任务内容/Usage；
- `tp-voyager.usage/v1` Usage Evidence：复用现有 Attempt-bound Evidence，记录 CLI/SDK/ACP 实际返回的 token/credit/provider-reported cost，缺失字段不推算；Qoder timeout/cancel 前已收到的 usage 可在无最终 Result 时保留；
- `model_policy.allowed_models` 作为 Passenger/Captain 模型池约束；Voyager 不自动选择、不 fallback；同时移除 Qoder Adapter 内“未指定模型则取目录首项”的隐式选择；
- `worker_profile_ref` (`name/version/sha256`)：只解析受信 Profile Store，哈希不一致 fail-closed，Profile 内容仅进入瞬时 Crew prompt；
- vendor-neutral `read_scope` (`files/directories/globs`)：统一展开为有界 concrete file set，CodeBuddy 映射到 Context Manifest，Qoder 映射到 ACP host allowed paths；保留 `context_files` 作为 CodeBuddy v1.0.1 兼容入口；
- `correlation_id` 只作为外部任务关联元数据，不建立第二任务系统；
- SQLite schema 11 → 12，仅扩展 Evidence type `usage`，不新增 Usage/计费表。

代码 Gate：核心/迁移/诊断测试通过，Smoke 18/18、Current 10/10；Stress 各组在 MCP import stub 条件下通过。

Live Gate（2026-08-09，真实 Windows + 正式 `mcp` + CodeBuddy/Qoder CLI）5 项全 PASS：

1. `doctor --json` 不调用模型、不返回 Credential/任务内容/Usage；
2. CodeBuddy `read_scope` read-only + `task_result` usage；
3. Qoder `read_scope` read-only + 显式 `model_policy`/model + `task_result` usage；
4. Qoder timeout 样本：`max_task_duration` 触发 BackendTimeoutError，usage 字段结构在无最终 Result 时仍保留；
5. CodeBuddy/Qoder bounded patch 各 1：completed、verification PASSED、Passenger worktree 不变、completed 时刻无 `patch-*` worktree 残留（v1.0.1 Patch Gate 无回归）。

辅助验证：默认 Captain MCP Surface 保持 6 tools；无隐藏 fallback（workbuddy → CREW_NOT_SUPPORTED）；`model_policy` 越界 → MODEL_NOT_ALLOWED；`worker_profile_ref` 无效 → WORKER_PROFILE_INVALID，均 dispatch_performed=false。

## 1.0.1 — 2026-08-08

`v1.0.1-rc1` 稳定化正式发布。已在真实 Windows + 真实 MCP + CodeBuddy CLI / Qoder CLI 环境完成 Live 验收（10 项 PASS）：

- Real MCP 默认 6-tool Captain Surface；
- Diagnostic Surface 显式 opt-in；
- Crew Health 语义（CLI/SDK 可用性、auth_status、model 观测分开表达，不伪造模型清单）；
- CodeBuddy hy3 只读 context（context_auto_created、marker 不泄露、文件未变、预算 600s）；
- Qoder Lite 600s 长调查（hash 不变、无新文件）；
- CodeBuddy / Qoder bounded patch（clean Git fixture + patch_policy，verification PASSED，Passenger worktree 不变）；
- Patch `completed` 对外可见时不存在对应 `patch-*` worktree（竞态修复在真实环境成立）；
- 无隐藏 fallback（workbuddy 派发 fail-closed，reason_code=CREW_NOT_SUPPORTED）。

## 1.0.1-rc1 — 2026-08-08

真实使用反馈驱动的稳定化候选版本，不新增第二套 Runtime/Planner/状态机。

修复与收口：

- 修复 Patch `completed` 与隔离 worktree 清理的竞态：只有 worktree 已确认清理后才能持久化成功；清理失败会明确终止为 failure；
- 默认 MCP Surface 收敛为 6 个 Captain 高层工具；完整历史工具仅在 `TP_VOYAGER_MCP_SURFACE=diagnostic` 时暴露；
- `task_dispatch(context_files=[...])` 可为 CodeBuddy 只读路线自动创建最小 Context Manifest；
- `crew_health` 区分认证“未探测”与“不可用”，并从现有 Durable Task/Session 历史投影最近一次成功的显式模型；
- `task_result` 增加执行预算与实际耗时投影；
- Captain Skill 增加预检、任务模板和超时预设：quick=180s、investigation/review=600s、patch=900s、verify=300s；不自动重试；
- README 不再把 `v1.0.0` Patch 描述为当前 production-ready，正式恢复声明需通过本 RC Live Patch Gate。

## 1.0.0 — 2026-08-08

首个 TP-Voyager 真实使用基线。后续真实使用发现 Patch 终态/cleanup 竞态，因此 Patch 能力由 `v1.0.1-rc1` 重新稳定化；只读路线和整体架构验收结论不受该问题影响。

主要能力：

- Captain-facing MCP 控制面；
- Crew Registry / Health / Recommendation；
- CodeBuddy CLI 受控只读与 Patch；
- Qoder CLI 受控只读与 Patch；
- Durable Task / Session / Attempt / Event；
- Lease / Fencing / Cancel / Reconciliation；
- Context / Knowledge / Artifact / Evidence / Verification；
- Runtime-owned isolated Git worktree；
- Captain Skill；
- WorkBuddy 当前生产执行路径退出；
- 最终真实 MCP Discovery 验收通过。
