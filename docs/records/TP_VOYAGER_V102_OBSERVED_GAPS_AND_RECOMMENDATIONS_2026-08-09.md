# TP-Voyager v1.0.2 实测问题、证据与候选优化建议

> 日期：2026-08-09  
> 目的：为无法访问本机账号与 CLI 环境的网页开发者提供可核验的事实、复现命令和候选改进项。  
> 决策状态：本文不是需求指令；每一项是否实现由开发者结合产品 Charter 自行决定。

## 1. 当前快照与范围

本轮工作区 `HEAD` 仍为 `76f4d98`（v1.0.1 stable），但工作树包含未提交的 v1.0.2 release-candidate 改动。v1.0.2 已新增：Captain Skill frontmatter、manifest、`doctor --json`、`model_policy`、`read_scope`、`worker_profile_ref`、`correlation_id` 和 Usage Evidence。

本报告只讨论：

- CodeBuddy/Qoder 模型目录与显式选模；
- Provider 实际返回的 Token、积分与费用证据；
- 只读任务的上下文/Artifact 隔离；
- 跨宿主安装与诊断；
- 是否需要扩展外部静态源码研究能力。

不建议以本文为由引入自动选模、自动 fallback、第二任务系统、第二 Result/Evidence 系统或 Planner。

## 2. 已确认事实和建议

### 2.1 CodeBuddy：CLI 声明模型目录未接入 TP-Voyager MCP

#### 本机证据

以下命令在本机 PowerShell 执行，均返回 exit code `0`：

```powershell
codebuddy --version
# 2.133.0

codebuddy config get model
# hy3
# hy3

codebuddy --help
```

`codebuddy --help` 的 `--model` 行原始内容为：

```text
--model <model>  Model for the current session. Please provide the model ID.
Currently supported: (hy3, glm-5.2, glm-5.1, glm-5v-turbo,
minimax-m3-pay, minimax-m2.7, kimi-k3-2, kimi-k2.7, kimi-k2.6,
deepseek-v4-pro, deepseek-v4-flash)
```

可提取的模型 ID：

```text
hy3
glm-5.2
glm-5.1
glm-5v-turbo
minimax-m3-pay
minimax-m2.7
kimi-k3-2
kimi-k2.7
kimi-k2.6
deepseek-v4-pro
deepseek-v4-flash
```

另有一次真实 TP-Voyager 下发证据：

```text
task_id: wb-b575b30afdb9
Crew: CodeBuddy
route: sdk_context_read_only
model: deepseek-v4-flash
result: completed
elapsed_seconds: 9.167
```

这证明 `deepseek-v4-flash` 在当前账号/SDK 路线上至少成功执行过一次。

#### 当前源码行为

`agent_runtime/api/mcp_server.py` 中 CodeBuddy 注册为：

```python
CrewProvider(
    descriptor=codebuddy_crew_descriptor(),
    probe=probe_codebuddy_cli,
    models=None,
)
```

因此 `crew_catalog(include_models=true)` 无法列出 CodeBuddy 模型；`crew_health` 最多只能返回 Runtime 历史中的 `last_successful_model`。

#### 建议（开发者决定）

新增一个很小的 CodeBuddy 模型目录 Adapter：

```text
codebuddy --help
  -> 提取 --model 行的 Currently supported: (...)
  -> list_codebuddy_models()
  -> crew_catalog(include_models=true)
```

约束：

- 使用 `cli_declared` 作为来源标记，而不是 `official_dynamic`；
- `--help` 表示当前 CLI 版本声明支持，不等于逐账号实时授权；
- `codebuddy config get model` 的重复输出必须 trim + deduplicate；
- 查询不发送 Prompt、不读取工作区文件、不启动 Crew 会话；
- 查询/解析失败时返回 unknown 或空列表，不得硬编码模型或把历史成功模型伪装成当前目录。

可选的后续校验：当模型不在 CLI 声明列表中，拒绝为 `MODEL_NOT_DECLARED_BY_CLI`；当实际账号拒绝已声明模型时，如实返回厂商错误，不自动换模型。

### 2.2 Qoder：交互可见完整模型列表，但当前 TP 捕获方式仅收到一个模型

#### 本机证据：直接 PowerShell 调用

以更长等待窗口执行：

```powershell
qodercli --version
# 1.1.17

qodercli --list-models
```

直接 PowerShell 输出在约 `1.795` 秒后显示：

```text
MODEL
Auto
Ultimate
Performance
Efficient
Lite
Cantus
Qwen3.8-Max
Qwen3.7-Max
Qwen3.7-Plus
Kimi-K3
Kimi-K2.7-Code
GLM-5.2
DeepSeek-V4-Pro
DeepSeek-V4-Flash
MiniMax-M3
```

退出码为 `0`，标准错误为空。因此，`Lite` 是当前交互终端可见的模型之一。

#### 本机证据：TP-Voyager 同等 Python 捕获方式

当前 `agent_runtime/backends/qoder/model_catalog.py` 使用：

```python
subprocess.run(
    [cli, "--list-models"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    timeout=15,
    text=True,
    encoding="utf-8",
)
```

用相同 `subprocess.run(..., stdout=PIPE)` 实测的原始输出是：

```python
'MODEL\nQwen3.8-Max\n'
```

随后调用当前 Adapter：

```python
list_qoder_models()
```

只得到：

```json
[
  {
    "model_id": "Qwen3.8-Max",
    "source": "official_dynamic",
    "available": true
  }
]
```

#### 结论

这不是单纯的文本 Parser 漏掉了 14 行：在 TP-Voyager 当前采用的 PIPE 捕获方式中，原始 `stdout` 本身只有一行模型。它是当前 Windows/Qoder CLI 的终端输出/捕获兼容性问题。

#### 建议（开发者决定）

在改 Parser 前先在真实 Windows + Qoder CLI 环境验证可靠的官方兼容采集方式：

```text
交互控制台
PowerShell 重定向
Python PIPE
Windows PTY/ConPTY（如需要）
Qoder 官方 SDK/ACP 模型目录 API（如有）
```

实现目标：

- 能稳定区分完整目录与不完整目录；
- 若仅收到疑似部分结果，返回 `incomplete/unknown`，不得将 `Qwen3.8-Max` 伪装为完整目录；
- 保持 Captain 显式选模，不按列表自动选模型；
- 增加以上 15 项真实输出的回归夹具。

现有 `parse_list_models_output()` 对单列文本 `MODEL\nQwen3.8-Max` 已能正确解析；问题重点在输入采集，而非该 Parser 的基本 split 逻辑。

### 2.3 Usage Evidence：已能留存厂商实际返回的 Token/积分/费用事实

#### 本机真实 CodeBuddy 证据

任务 `wb-b575b30afdb9` 的 `task_result.usage` 返回：

```json
{
  "schema": "tp-voyager.usage/v1",
  "provider": "codebuddy",
  "model": "deepseek-v4-flash",
  "source": "codebuddy_sdk_result",
  "usage": {
    "input_tokens": null,
    "output_tokens": null,
    "credits_used": null,
    "reported_cost": 0.0,
    "currency": "USD"
  },
  "provider_usage": {}
}
```

可确认：本次 SDK 回传费用为 `0.0 USD`；没有回传 Token 或积分。TP-Voyager 正确保留 `null`，没有推算。

#### 当前实现

`BackendUsage` 可持久化：

```text
input_tokens
output_tokens
credits_used
reported_cost
currency
provider_usage
```

Usage 以现有 Attempt-bound Evidence 保存，schema 由 11 升至 12，仅增加 `evidence_type = usage`，不新建计费表。Qoder ACP 即使在 timeout/cancel 前已报告 usage，也会尽力写入当前 Attempt。

#### 当前边界

以下能力尚未实现，也不应被误称为已实现：

```text
账户积分余额
按模型/日期/任务的汇总报表
预算拦截
根据公开单价估算成本
自动降级模型
```

#### 建议（开发者决定）

先完成最小 Live Matrix，再决定是否需要只读汇总 API：

```text
CodeBuddy: hy3、deepseek-v4-flash 各一个最小只读任务
Qoder: Lite、一个非免费模型（需人类明确同意消耗积分）
异常: Qoder timeout/cancel 后 Usage Evidence 是否仍可读
```

若增加汇总，建议只汇总已有 `tp-voyager.usage/v1` 证据，不查询余额、不估价、不新建独立计费真相源。

### 2.4 已确认 BUG：只读任务错误捕获并暴露既有工作区改动

#### 复现条件

工作区在下发前已存在未提交的 v1.0.2 改动。下发任务：

```text
Crew: CodeBuddy
route: sdk_context_read_only
access_mode: read_only
read_scope.files: [README.md]
模型: deepseek-v4-flash
```

#### 实际结果

尽管该任务只读、只授权 `README.md`，`task_result` 仍返回：

```text
changed_files: 包含 AGENTS.md、CHANGELOG.md、mcp_server.py 等既有工作区改动
Artifact: workspace.patch
workspace.patch size_bytes: 100244
Artifact: 多个不在 read_scope 内的文件
```

这些内容是下发前的工作区既有改动，不是本次 Crew 写入。

#### 风险

```text
上层 Captain 可能误判 Crew 修改了文件；
无关的本地改动可能被持久化并在 task_result 中暴露；
read_only 的证据和最小上下文边界失真。
```

#### 建议修复

```text
read_only：
- changed_files 必须为空；
- 禁止生成 patch Artifact；
- 只保留已授权 Context Manifest/read_scope 的必要元数据；
- 不扫描或投影工作区 diff。

patch：
- dispatch 前记录 source baseline；
- 仅允许归属隔离 worktree 内、baseline 之后的变更；
- 原始 source worktree 的脏改动绝不归属于 Crew。
```

建议新增回归测试：准备脏工作树 -> 下发只读单文件任务 -> `changed_files=[]`、无 patch Artifact、无未授权文件 Artifact。

### 2.5 Skill/manifest/doctor 已补齐基础契约，但尚未达到“AI 一句话安装完成”

v1.0.2 已有：

```text
skills/tp-voyager-captain/SKILL.md 的 YAML frontmatter
tp-voyager.manifest/v1
python -m agent_runtime.cli doctor --json
```

本机 `doctor --json` 实测没有模型调用，也未返回凭证、任务正文或 Usage；但它发现当前旧 Runtime DB：

```text
database: C:\Users\tangpeng\.workbuddy\runtime\workbuddy_runtime.db
schema_version: 11
supported_schema_version: 12
installation_ready: false
```

这不是 doctor 故障，而是有效的迁移前状态提示。

尚缺：

```text
tp-voyager setup --host <host> --json
```

它才可以把 manifest 转换为具体宿主配置、进行受控迁移、注册 MCP、加载 Skill 并执行 doctor。实现前应先在各目标宿主真实验证：配置写入、刷新/重启、MCP 工具发现、doctor 与六工具调用。

### 2.6 外部源码静态研究：当前尚不是正式 TP-Voyager 路线

`tp-github` 类型任务需要：

```text
GitHub API 大小检查
-> git clone --depth 1
-> 禁止运行下载源码
-> 静态精读
-> 写指定报告目录
```

当前 TP-Voyager 只有 `read_only` 与 `patch` 路线：前者不能写/执行终端，后者要求既有 Git 仓、隔离 worktree、Patch Policy 并在成功后清理。因此不能以 `small_patch` 冒充这类任务。

这不是已确认 BUG，而是产品范围决策：

- 若定位仅为已有工作区内的受控开发 Crew：保持不支持；
- 若定位包含通用外部研究 Skill：另行设计严格受限的 `static_source_research` Contract。

在 Charter 审查和真实 CodeBuddy/Qoder 权限验证前，不建议开发该能力。

## 3. 尚无足够现场证据的候选项

以下项目尚无重复真实痛点样本，不建议立即开发：

```text
全局并发上限 / 每 Crew 并发上限
concurrency_key 工作区冲突串行化
depends_on_task_ids 依赖门禁
受 hard_cap 约束的 progress_extend 超时策略
Usage 按时间/模型/关联号的汇总投影
外部源码静态研究路线
```

若未来出现真实任务积压、Patch 冲突、长任务持续有效进展或费用治理需求，应先保留 Task ID、超时/冲突事实和 Captain 手工成本，再按 Charter Scope Gate 评估。

## 4. 建议的决策顺序

```text
P0  修复 read_only 任务错误捕获既有工作区 diff
P0  完成 v1.0.2 CodeBuddy/Qoder 最小 Live Gate
P1  解决 Qoder 完整模型目录的 Windows 捕获兼容性
P1  接入 CodeBuddy CLI 声明模型目录
P1  评估 setup --host 自动接入
P2  仅在真实需求出现后评估 Usage 汇总、并发/依赖、静态源码研究
```

## 5. 给开发者的验收原则

```text
模型目录：不完整即 unknown/incomplete，不猜测；
模型选择：Captain 显式选择，不自动 fallback；
Usage：只记录厂商实际返回，不按价格表推算；
只读任务：不产生 patch、不归属既有 diff、不暴露未授权文件；
安装：doctor 通过才标 ready；
所有新增能力：复用现有 Task/Attempt/Evidence/Artifact 真相源。
```

