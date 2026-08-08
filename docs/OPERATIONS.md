# TP-Voyager 运行与配置

## 启动

推荐先创建仓库内虚拟环境：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动：

```bat
start_runtime.cmd
```

也可以直接：

```bat
python -m agent_runtime.server
```

## Python 选择规则

`start_runtime.cmd` / `run_tests.cmd` 按以下顺序选择 Python：

```text
1. AGENT_RUNTIME_PYTHON
2. 仓库根目录 .venv\Scripts\python.exe
3. PATH 中的 python.exe
```

不再绑定开发者机器上的固定绝对路径。

## Runtime 环境变量

当前标准变量：

```text
AGENT_RUNTIME_PYTHON
AGENT_RUNTIME_HOME
AGENT_RUNTIME_DB
```

建议把 Runtime Home 放在 Git 仓库之外或保持 `.gitignore` 排除。

## CodeBuddy 中国区

中国区账号使用：

```text
CODEBUDDY_INTERNET_ENVIRONMENT=internal
```

不要把登录缓存、Token、Cookie、`.env` 或本地 Credential 提交到 Git。

## Captain 日常操作

优先使用：

```text
voyager_overview
crew_catalog
crew_health
crew_recommend
task_dispatch
task_result
```

Vendor-specific 兼容/诊断工具不是 Captain 的常规控制面。

## 本地诊断

`agent_runtime.cli` 提供只读诊断能力，可检查 Runtime DB、任务和 Artifact 状态。

历史 WorkBuddy Home 变量/路径仅用于旧数据迁移输入，不属于当前配置指南。
