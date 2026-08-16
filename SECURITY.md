# 安全说明

如果你发现 TP-Voyager 存在可能导致以下问题的安全缺陷，请不要在公开 Issue 中直接粘贴敏感利用细节：

- 越过 allowed/forbidden path；
- 绕过命令白名单；
- Worker 写入乘客原始工作树；
- 绕过 Verification / Evidence；
- Credential / Token 泄露；
- 未授权读取 Runtime 数据；
- 隐式调用未批准的 Crew / Backend。

建议先通过仓库维护者提供的私有联系方式或 GitHub Security Advisory 报告。

## 提交安全报告时

请提供：

- 受影响版本；
- 最小复现步骤；
- 风险说明；
- 是否需要真实 CodeBuddy/Qoder 环境；
- 可公开到什么程度。

不要附带：

- 真实 Token；
- 登录 Cookie；
- 私钥；
- 真实企业业务数据；
- 未脱敏 Runtime DB。

## 支持范围

当前重点维护：

```text
TP-Voyager 1.x 当前稳定基线
```

历史 WorkBuddy 执行能力已经退出支持范围，仅保留必要的数据兼容逻辑。

## 隔离边界

TP-Voyager 的隔离目标是可信宿主机上的**受控执行边界**，不是恶意二进制安全沙箱。Patch 使用 Runtime-owned Git worktree；Qoder bounded read-only 使用只含 approved `read_scope` 文件的 Runtime snapshot 作为 cwd。Crew CLI/SDK 仍以宿主用户身份运行，因此本项目不宣称能阻止被攻陷或恶意的本机 Crew 进程主动访问宿主机其他绝对路径。
