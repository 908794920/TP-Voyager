# TP-Voyager 运行与配置

## 启动

推荐先创建仓库内虚拟环境：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

启动：

```bat
scripts\start_runtime.cmd
```

也可以直接：

```bat
python -m agent_runtime.server
```

## Python 选择规则

`scripts\start_runtime.cmd` / `scripts\run_tests.cmd` 按以下顺序选择 Python：

```text
1. TP_VOYAGER_PYTHON
2. 仓库根目录 .venv\Scripts\python.exe
3. PATH 中的 python.exe
```

不再绑定开发者机器上的固定绝对路径。

## 用户目录与统一配置

当前标准启动变量：

```text
TP_VOYAGER_PYTHON
TP_VOYAGER_HOME
TP_VOYAGER_DB
```

默认 Home 是 `~/.tp-voyager`，默认数据库是 `~/.tp-voyager/runtime/tp_voyager.db`。v1.0.7 是 clean break：不读取 `.agent-runtime`、`AGENT_RUNTIME_HOME`、`AGENT_RUNTIME_DB` 或 WorkBuddy Home。

首次使用：

```powershell
python -m agent_runtime.cli init
```

它会创建：

```text
~/.tp-voyager/
├── config.json
├── model_routing_profiles.json
└── runtime/
    ├── artifacts/
    ├── workspaces/
    └── logs/
```

`config.json` 是机器级/用户策略的唯一普通配置事实源，包含：

```text
crew.qoder.enabled / cli_path
crew.codebuddy.enabled / cli_path / internet_environment
dispatch.allowed_models / preferred_models / task_kind_allowed_models
trusted_roots.model_evidence / instructions
resources.worker_profiles_root / worker_skills_root
runtime.max_concurrent_tasks
```

Crew CLI 临时覆盖优先级：

```text
QODER_CLI_PATH / CODEBUDDY_CODE_PATH / CODEBUDDY_INTERNET_ENVIRONMENT
        ↓
~/.tp-voyager/config.json
        ↓
PATH 自动发现（CLI 路径）
```

正常长期配置应写入 `config.json`；临时环境变量适合调试/CI。Token、Cookie、登录缓存、API Key 等 Credential 不得写入 `config.json`，继续交给供应商登录缓存或 Secret 环境变量。

模型能力资料继续放在独立的 `model_routing_profiles.json`；授权则只属于 `config.json.dispatch`。模型认知变化不需要修改 Runtime Python。完整语义见 `docs/MODEL_ROUTING.md`。

CodeBuddy 中国区默认 `internet_environment` 为 `internal`；需要 IOA 或 public 时可在 `config.json` 设置 `ioa` / `public`，或用 `CODEBUDDY_INTERNET_ENVIRONMENT` 临时覆盖。

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
scripts\start_runtime.cmd
```

PowerShell：

```powershell
$env:TP_VOYAGER_MCP_SURFACE = "diagnostic"
.\scripts\start_runtime.cmd
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


## Model Evaluation Standard v1 operations

TP-Voyager v1.0.7 supports v1/v2 routing-profile files. Loading v1 is read-only and compatible; persistent upgrade is explicit:

```powershell
tp-voyager model-routing-migrate --dry-run
tp-voyager model-routing-migrate --write
```

`--dry-run` reports source/target schema, profile counts, legacy evidence preservation and convertibility without writing. `--write` validates v2 first, writes via a temporary file + atomic replace, reloads the result, and leaves the original intact on failure. Re-running migration on v2 is idempotent.

Validate the current evaluation baseline with:

```powershell
tp-voyager model-evaluation-validate
```

The validator is read-only: it does not access the network, does not query Provider live catalogs, and does not change dispatch policy. A non-zero exit is used for schema/evidence/tier-authority failures. Archived/historical evidence and research-status gaps are reporting concerns, not authorization changes.

The evidence-writing procedure is documented in `docs/MODEL_EVALUATION_STANDARD.md`.
