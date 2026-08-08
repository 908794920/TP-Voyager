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
run_tests.cmd
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
