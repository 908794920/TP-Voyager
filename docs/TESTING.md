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
