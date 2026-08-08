# Changelog

本项目从 TP-Voyager 正式基线开始记录对外版本。

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
