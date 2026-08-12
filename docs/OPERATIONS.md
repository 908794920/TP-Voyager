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

### v1.0.6 operator 模型配置

Runtime Home 同时承载两类模型配置：

```text
dispatch_model_policy.json
    → 硬约束：哪些 backend-qualified model route 可以下发

model_routing_profiles.json
    → 建议资料：能力档位、推荐任务、风险边界、suggested effort
```

第二个文件不是授权策略。示例见：

```text
docs/examples/model_routing_profiles.example.json
```

模型认知变化时只更新 operator JSON，不需要修改 Runtime Python。完整语义见 `docs/MODEL_ROUTING.md`。

## CodeBuddy 中国区

中国区账号使用：

```text
CODEBUDDY_INTERNET_ENVIRONMENT=internal
```

不要把登录缓存、Token、Cookie、`.env` 或本地 Credential 提交到 Git。

## Captain 日常操作

默认启动时 MCP 只暴露：

```text
voyager_overview
crew_catalog
crew_health
crew_recommend
task_dispatch
task_result
```

Vendor-specific、`subagent_*`、`context_*`、Planner/Artifact 等历史兼容工具不会注册到默认 MCP Surface。

维护者确实需要低层诊断时，启动前显式设置：

```bat
set TP_VOYAGER_MCP_SURFACE=diagnostic
start_runtime.cmd
```

PowerShell：

```powershell
$env:TP_VOYAGER_MCP_SURFACE = "diagnostic"
.\start_runtime.cmd
```

诊断 Surface 只改变工具可见性，不创建第二套 Runtime 或状态机。

### 推荐超时预算

Captain Skill 当前预设：

```text
quick          180s
investigation  600s
review         600s
patch          900s
verify         300s
```

超时后不自动重试。`task_result` 成功结果会返回 `execution_budget`；超时失败可通过状态中的 timeout 信息判断。

## 本地诊断

推荐：

```powershell
python -m agent_runtime.cli doctor --json
```

`doctor --json` 保持只读：不发送 Prompt、不调用模型、不读取任务正文、不返回 Credential 或 Usage。模型目录控制面同样不发送模型 Prompt：CodeBuddy 优先使用 catalog-only ACP 获取账号态目录/参考倍率，ACP 不可用才回退 `--help` declaration；Qoder 优先通过官方 Python SDK `get_available_models()` 获取实时目录，SDK 不可用时回退 `--list-models`。目录 incomplete/unknown 必须原样保留，不能触发自动 fallback。

`agent_runtime.cli` 继续提供 Runtime DB、任务和 Artifact 的只读诊断能力。

历史 WorkBuddy Home 变量/路径仅用于旧数据迁移输入，不属于当前配置指南。


## repository_research 运维边界

该 Contract 只接受公开 `https://github.com/<owner>/<repo>` URL。Captain 必须提供全新绝对目标目录且其父目录已存在；Runtime 不覆盖已有目录。获取阶段只执行固定 GitHub metadata 请求和 shallow clone，随后移除 clone 的 `origin`。Crew 只拿到 `source/` 下 read scope 允许的静态内容，报告由 Runtime 写入 `reports/`。

运维验收应确认：

```text
source commit 与 task_result 一致
source/.git/config 不再存在 origin
source 内容 hash 在研究前后不变
reports/ 中只出现 Runtime 生成的报告
changed_files=[]
无 workspace.patch
```

不要把 repository_research 用作下载器、构建器、依赖安装器或任意网络研究器。
