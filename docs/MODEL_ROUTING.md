# Data-Driven Routable Model Catalog

TP-Voyager v1.0.7 继续将 `crew_catalog(include_models=true)` 作为 Captain 的**可路由模型目录**。

它不替 Captain 选模型。Runtime 只把实时供应商事实、operator 授权、operator 模型认知和 Runtime Evidence 合并成一个结构化 route projection。

## 1. 事实所有权

```text
Provider live catalog
  owns: model_id / display_name / availability / context / provider effort metadata / reference multiplier

operator ~/.tp-voyager/config.json / dispatch
  owns: authorization / allowlist / policy_sha256

operator model_routing_profiles.json
  owns: capability tier / benchmark snapshot / recommended work / risk boundaries / suggested effort

operator ~/.tp-voyager/config.json / trusted_roots.model_evidence
  owns: trusted local evidence root aliases only

Runtime durable Evidence
  owns: observed success / duration / tokens / credits / provider-reported cost

Captain
  owns: final Crew / model / effort choice
```

TP-Voyager 只做：

```text
load → validate → verify provenance → join → project
```

明确不做：

```text
auto scoring
auto model selection
auto fallback
public-price cost estimation
online benchmark scraping
```

## 2. 默认只读 baseline + 可选 operator materialize

v1.0.7 继续随包发布一份经过审阅的机器可读 baseline。它覆盖当前 MCP 真实目录中的：

```text
CodeBuddy 11 routes
Qoder     15 routes
Total     26 routes
```

当 `${TP_VOYAGER_HOME}/model_routing_profiles.json` 不存在时，Runtime 会直接**只读加载 bundled baseline**：

```text
status = bundled_baseline
source = bundled_model_routing_baseline
selection_performed = false
dispatch_performed = false
```

这一步不写 Runtime Home，因此升级后 MCP 可以立即返回 capability profile，同时不会偷偷创建 operator 配置。

如果需要长期自行维护这些资料，再显式执行：

```powershell
python -m agent_runtime.cli model-routing-init
```

该命令会把同一 baseline **显式复制**到：

```text
${TP_VOYAGER_HOME}/model_routing_profiles.json
```

默认 Runtime Home：

```text
~/.tp-voyager
```

如果文件已经存在，命令会拒绝覆盖。一旦 operator 文件存在，Runtime 优先加载它并停止使用 bundled fallback。TP-Voyager 不在启动 Runtime 时偷偷改 operator 配置。

初始化完成后可检查：

```powershell
python -m agent_runtime.cli doctor --json
```

`doctor` 会投影：

```text
model_routing_profiles.status
model_routing_profiles.sha256
model_routing_profiles.profile_count
model_routing_profiles.evidence_profile_counts
```

## 3. 授权配置与模型资料不要混用

### `config.json / dispatch`

这是**硬约束**，位于 `~/.tp-voyager/config.json`。Captain 仍必须显式选择模型；配置只允许或进一步限制，不自动选择。

例如：

```json
{
  "dispatch": {
    "allowed_models": [
      "codebuddy:hy3",
      "codebuddy:deepseek-v4-flash",
      "qoder:Lite",
      "qoder:qmodel_38max"
    ],
    "preferred_models": [],
    "task_kind_allowed_models": {}
  }
}
```

完整 `config.json` 还包含 Crew 路径、trusted roots、worker resources 和运行时并发设置；见 `docs/OPERATIONS.md`。

### `model_routing_profiles.json`

这是**operator 认知资料**。它可以告诉 Captain 哪个模型适合什么工作，但不能授权模型、不能覆盖 Provider availability、不能触发 dispatch。

四条核心 route 的当前 baseline：

| Route | Tier | Confidence | 主要定位 |
|---|---|---|---|
| `qoder:Lite` | L0 | medium | 搜索、摘要、机械修改、简单逻辑、快速验证 |
| `codebuddy:hy3` | L1 | medium-high | 常规编码、SQL、文档、中型 repo、明确边界多文件工作 |
| `codebuddy:deepseek-v4-flash` | **L3** | **high** | 已由 operator 确认映射到 **DeepSeek-V4-Flash-0731**；高级 Coding/Agent 主力 |
| `qoder:qmodel_38max` | **L3** | **medium-high** | 已由 operator 确认映射到正式 **Qwen3.8-Max**；复杂长程、多模态、架构级工作 |

注意：

```text
Tier ≠ 路由优先级
```

DeepSeek-0731 和 Qwen3.8-Max 都可以是 L3，但前者更适合高频、成本敏感的 Coding/Agent 执行，后者更偏复杂长程、多模态和高难权衡。Captain 仍应结合 reference multiplier、Provider availability 和自己的任务风险选择。

## 4. 为什么能力资料不能只看厂商宣传

当前 baseline 区分：

```text
Independent benchmarks
Official/provider specifications
Operator local research
Runtime observed Evidence
```

能力 Tier 主要参考独立/统一评测，例如：

```text
LiveBench
Artificial Analysis
Arena
Terminal-Bench（必须绑定 agent / effort / harness）
SWE-bench（只有 exact 同 harness 条目才采用）
```

厂商资料主要用于：

```text
模型身份
上下文长度
模态
支持参数
官方 route/slug
```

`benchmark_evidence` 是 operator 维护的快照，不由 Runtime 在线访问评测网站。

示例：

```json
{
  "source": "artificial_analysis",
  "release": "2026-07-31",
  "tested_model": "DeepSeek V4 Flash 0731 Max",
  "model_match": "exact",
  "effort": "max",
  "metrics": {
    "intelligence_index": 50,
    "gdpval_aa_v2_elo": 1559
  },
  "url": "https://artificialanalysis.ai/"
}
```

`model_match` 只能是：

```text
exact
near_exact
family
predecessor
dynamic_tier
missing
```

这样不会把旧 checkpoint 或同家族分数冒充当前 route 的 exact 实测。

## 5. 本地 Markdown 可以成为可验证 Evidence

URL 不是唯一 provenance。当前版本支持：

```json
{
  "kind": "trusted_file",
  "root_alias": "operator_model_research",
  "path": "Codex外部模型CLI委派参考.md",
  "sha256": "df278a0d4fe6d32316539feabc210742a349fc0f422f0d6553ace7f0601a1b82"
}
```

真实绝对目录不写进 profile，而是单独配置：

```text
~/.tp-voyager/config.json -> trusted_roots.model_evidence
```

例如 Windows：

```json
{
  "trusted_roots": {
    "model_evidence": {
      "operator_model_research": "D:/AI/model-research"
    },
    "instructions": {}
  }
}
```

Runtime 验证流程：

```text
root_alias
  ↓
operator trusted root
  ↓
relative path
  ↓
禁止 ../ / absolute path / symlink escape
  ↓
SHA-256
  ↓
verification status
```

MCP 只返回：

```text
root_alias
relative path
expected SHA-256
actual SHA-256（若读取成功）
verification
byte_size
```

**不会返回 trusted root 的绝对路径，也不会把 Markdown 正文注入 Captain/Crew Prompt。**

这是一条 provenance 机制，不是新的 RAG/Knowledge 系统。

### Evidence 状态

```text
verified      本地 trusted_file 哈希匹配
stale         文件缺失或哈希变化
unverified    trusted root 尚未配置
declared      只有 URL 声明
rejected      路径逃逸、过大或不可安全读取
not_declared  没有 Evidence ref
```

Evidence 过期不会改变 `allowed / denied`。授权仍只属于 `config.json.dispatch`。

## 6. Profile 示例

```json
{
  "canonical_family": "deepseek-v4-flash-0731",
  "provider_identity": "operator_confirmed",
  "capability_tier": "L3",
  "profile_confidence": "high",
  "specialties": [
    "coding_agent",
    "cost_efficient_frontier_execution"
  ],
  "recommended_tasks": [
    "complex implementation",
    "repository investigation",
    "multi-file change",
    "difficult debugging",
    "test-fix loop"
  ],
  "risk_boundaries": [
    "architecture/final technical decisions require Captain review",
    "text-only model"
  ],
  "suggested_effort": "high",
  "benchmark_evidence": [],
  "evidence_refs": []
}
```

完整 26-route baseline 位于：

```text
agent_runtime/application/crew/model_routing_profiles.baseline.json
```

供人阅读/修改的四核心示例位于：

```text
docs/examples/model_routing_profiles.example.json
```

## 7. Provider、Profile 和 Backend effort 必须分开

`model_routing_profiles.json` 中：

```text
suggested_effort
```

只是 operator 建议。

真正能否下发 effort 还取决于当前 Backend/Provider route。

例如当前 CodeBuddy controlled SDK route 本身：

```text
supports_reasoning_effort = false
```

所以即使：

```text
DeepSeek-V4-Flash-0731 suggested_effort = high
```

Catalog 也应该显示：

```text
suggested_effort = high
suggested_effort_supported = false
```

Captain 不能仅凭 profile 强行传 `reasoning_effort=high`。

Qoder 则继续以实时 SDK `thinking_config` 为准。

## 8. `crew_catalog(include_models=true)` 的 route

典型结果：

```json
{
  "route_id": "codebuddy:deepseek-v4-flash",
  "available": true,
  "allowlist_status": "allowed",
  "routable": true,
  "routability_status": "confirmed",
  "reference_multiplier": 0.05,
  "calculation_allowed": false,
  "capability_profile": {
    "canonical_family": "deepseek-v4-flash-0731",
    "provider_identity": "operator_confirmed",
    "capability_tier": "L3",
    "profile_confidence": "high",
    "evidence_status": "verified"
  },
  "reasoning": {
    "supported_efforts": [],
    "suggested_effort": "high",
    "suggested_effort_supported": false
  },
  "history": {},
  "usage": {},
  "sources": {
    "availability": "codebuddy_acp_account_live",
    "authorization": "operator_dispatch_policy",
    "capability_profile": "operator_model_routing_profiles",
    "usage": "runtime_evidence"
  }
}
```

解释规则：

- `routable=true`：policy 允许 + Crew route dispatch-ready + Provider 明确可用；
- `routable=false`：policy 拒绝、Provider disabled、policy invalid 或 Crew route 未就绪；
- `routable=null`：policy 允许，但实时 availability 未确认；
- `reference_multiplier` 永远是相对参考，`calculation_allowed=false`；
- `usage` 才是任务实际 Usage Evidence；
- `capability_profile` 只提供 Captain 决策资料。

## 9. 更新模型认知不需要改 Python

以后新 benchmark 出现时：

```text
更新 Runtime Home/model_routing_profiles.json
→ 重新调用 crew_catalog
```

不需要修改：

```text
CrewRegistryService
CodeBuddy adapter
Qoder adapter
MCP schema
```

只有 profile **格式/安全边界**需要变化时，才应升级 TP-Voyager 程序。

## 10. `crew_recommend` 仍只是 Crew 级辅助

`crew_recommend` 继续回答：

> 哪个 Crew 的受控执行路线满足这个 task kind / capability？

它不回答：

> 应该自动选择哪个底模？

最终流程保持：

```text
crew_catalog(include_models=true)
        ↓
Captain 根据 route facts 自己判断
        ↓
task_dispatch(
    crew=...,
    model=...,
    reasoning_effort=... only when supported
)
```

## 11. Benchmark snapshot record

本次 26-route 独立评测归纳的可追溯记录位于：

```text
docs/records/TP_VOYAGER_V106_MODEL_ROUTING_BENCHMARK_BASELINE_2026-08-13.md
```

它用于说明 operator baseline 如何得出当前 Tier / Confidence；运行时不读取该 Markdown 来自动评分。
