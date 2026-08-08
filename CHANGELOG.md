# Changelog

本项目从 TP-Voyager 正式基线开始记录对外版本。

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
