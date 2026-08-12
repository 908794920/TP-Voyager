# Data-Driven Routable Model Catalog

TP-Voyager v1.0.6 把 `crew_catalog(include_models=true)` 变成 Captain 可直接消费的**可路由模型目录**。

它不负责替 Captain 选模型，只把不同来源的事实合并到同一个 route 上。

## 1. 事实所有权

```text
Provider live catalog
  owns: model_id / display_name / availability / context / efforts / reference multiplier

operator dispatch_model_policy.json
  owns: authorization / allowlist / policy_sha256

operator model_routing_profiles.json
  owns: capability tier / recommended work / risk boundaries / suggested effort

Runtime durable Evidence
  owns: observed success / duration / tokens / credits / provider-reported cost

Captain
  owns: final Crew / model / effort choice
```

TP-Voyager 只做：

```text
load → validate → join → project
```

明确不做：

```text
auto scoring
auto model selection
auto fallback
public-price cost estimation
```

## 2. 两个 operator 文件不要混用

Runtime Home 默认：

```text
~/.agent-runtime
```

可用 `AGENT_RUNTIME_HOME` 修改。

### `dispatch_model_policy.json`

这是**硬约束**。现有 dispatch 逻辑继续以它为授权 Source of Truth。

示意：

```json
{
  "require_explicit_model": true,
  "allowed_models": [
    "codebuddy:hy3",
    "codebuddy:deepseek-v4-flash",
    "qoder:lite",
    "qoder:qmodel_38max"
  ]
}
```

模型不在显式 allowlist 时，Catalog 会显示 `allowlist_status=denied`，真正 `task_dispatch` 也会拒绝。

### `model_routing_profiles.json`

这是**建议资料**，没有授权能力。

```json
{
  "schema": "tp-voyager.model_routing_profiles/v1",
  "updated_at": "2026-08-12",
  "profiles": {
    "codebuddy:deepseek-v4-flash": {
      "canonical_family": "deepseek-v4-flash-0731",
      "capability_tier": "L2",
      "recommended_tasks": [
        "repository_investigation",
        "implementation",
        "debugging",
        "multi_file_change"
      ],
      "risk_boundaries": [
        "architecture_requires_captain_review"
      ],
      "suggested_effort": "high",
      "evidence_sources": [
        "https://api-docs.deepseek.com/news/news260424/"
      ]
    }
  }
}
```

仓库提供一个当前路由快照：

```text
docs/examples/model_routing_profiles.example.json
```

把它复制到 Runtime Home 后再按自己的账号、经验和新资料维护。

## 3. 为什么 profile 不写进 Python

模型认知变化远快于 Runtime Contract。

把 `L0/L1/L2/L3`、推荐任务或风险边界硬编码在 Python 会导致：

```text
模型更新
→ 修改程序
→ 跑代码发布
→ 才能改变 Captain 认知
```

v1.0.6 改为：

```text
模型更新
→ 编辑 operator JSON
→ 下次 catalog 查询直接生效
```

Python 不需要知道 `qmodel_38max` “到底有多聪明”；它只验证 route ID、字段格式并把 operator 资料关联到供应商实时 route。

## 4. Route ID 与 canonical family

Provider ID 不保证等于公开模型名称。

例如：

```text
qoder:qmodel_38max
        ↓ operator mapping
qwen3.8-max
```

因此：

- `route_id` 是**真正下发使用的 backend-qualified ID**；
- `canonical_family` 是用于理解/资料维护的模型家族名称；
- dispatch 永远使用实际 Provider `model_id`，不能拿 canonical family 替代。

对于 `auto` / `ultimate` / `performance` / `efficient` / `lite` 这类供应商 tier，也不要反推一个固定底模。示例 profile 只描述这个 tier 的用途，不声明固定模型身份。

## 5. Routable 三态

`routable` 不是简单 boolean，因为 Provider 查询可能暂时拿不到实时 availability。

### `true`

```text
policy permits
+
provider confirms available
```

`routability_status=confirmed`

### `false`

典型原因：

```text
crew_not_dispatch_ready
denied_by_policy
provider_disabled
policy_invalid
```

### `null`

典型情况：

```text
policy permits
+
current provider availability is not confirmed
```

`routability_status=availability_unconfirmed`

这遵守 TP-Voyager 的基本原则：**Unknown 就是 Unknown，不猜。**

## 6. Provider 没返回模型时为什么还要显示

Catalog 的 route 集合来自：

```text
Provider current models
∪ explicit global policy routes
∪ operator routing profiles
∪ Runtime historical models
```

所以某个白名单模型临时没有出现在 Provider catalog 时，不会凭空消失，而会显示类似：

```json
{
  "route_id": "qoder:qmodel_38max",
  "available": null,
  "allowlist_status": "allowed",
  "routable": null,
  "routability_status": "availability_unconfirmed"
}
```

这比“没查到就当不存在”更适合 Captain 判断故障、权限或目录漂移。

## 7. reference multiplier 不是账单

Provider 实时返回的倍率只用于理解**相对消耗**。

Catalog 会投影：

```text
reference_multiplier
calculation_allowed = false
```

即使上游错误地声称可以计算，TP-Voyager 的 public route projection 也不会把它变成计费输入。

真正的任务消耗仍然来自：

```text
tp-voyager.usage/v1
```

包括 Provider 实际回传的 tokens、credits 或 reported cost。不存在的字段保持未知。

## 8. context 与 effort

Provider 返回的实时字段优先。

Qoder Agent SDK 的 `context_config` 会投影为最大已声明 context token 数；`thinking_config.enabled.efforts` 会归一化为：

```json
"reasoning": {
  "supported_efforts": ["low", "medium", "xhigh"],
  "suggested_effort": "medium",
  "suggested_effort_supported": true
}
```

其中：

- `supported_efforts` 来自 Provider；
- `suggested_effort` 来自 operator profile；
- `suggested_effort_supported` 只是两者的机械匹配；
- 如果 Provider 没提供 effort 目录，保持 unknown，不用公开资料伪装成实时支持状态。

尤其要注意：模型本身公开支持某个 reasoning effort，不等于当前 TP-Voyager Backend route 已能传递该参数。当前 CodeBuddy 受控 SDK route 仍声明 `supports_reasoning_effort=false`，所以 Captain 不能只因为 profile 写了 `suggested_effort=high` 就向 CodeBuddy 下发 `reasoning_effort=high`；只有 Provider/Backend route 明确支持时才传。

## 9. Catalog 输出示意

以下使用 Qoder 的 route 举例，因为当前 Qoder 控制面可以返回 context/thinking 配置；不要把这个示例套用到 Provider 未确认的字段：

```json
{
  "route_id": "qoder:qmodel_38max",
  "model_id": "qmodel_38max",
  "available": true,
  "allowlist_status": "allowed",
  "routable": true,
  "routability_status": "confirmed",
  "reference_multiplier": 0.5,
  "calculation_allowed": false,
  "context_window_tokens": 1000000,
  "capability_profile": {
    "canonical_family": "qwen3.8-max",
    "capability_tier": "L3",
    "recommended_tasks": ["architecture", "complex review", "long-horizon coding"],
    "risk_boundaries": ["avoid for mechanical tasks"],
    "suggested_effort": "medium"
  },
  "reasoning": {
    "supported_efforts": ["low", "medium", "xhigh"],
    "suggested_effort": "medium",
    "suggested_effort_supported": true
  },
  "sources": {
    "availability": "official_dynamic_sdk",
    "authorization": "operator_dispatch_policy",
    "capability_profile": "operator_model_routing_profiles",
    "history": "runtime_task_history",
    "usage": "runtime_evidence"
  }
}
```

## 10. Captain 选择规则

Catalog 给信息，Captain 做决定：

```text
任务是什么？
  ↓
哪些 route 在 policy 内？
  ↓
哪些 route 当前可用？
  ↓
operator profile 推荐什么工作 / 有什么边界？
  ↓
Provider reference multiplier 如何？
  ↓
Runtime 历史结果怎样？
  ↓
Captain 选择
  ↓
task_dispatch(crew=..., model=..., reasoning_effort=...)
```

`reasoning_effort` 只在当前 Backend route 明确支持时传；否则保持空值。`crew_recommend` 仍只作为 Crew 层辅助，不承担自动模型选择。

## 11. 更新 operator profile 的建议

模型资料更新时：

1. 先以当前 Provider model catalog 确认真实 route ID；
2. 优先使用供应商官方文档/模型卡；
3. 无可靠资料时用 `UNCLASSIFIED`，不要猜；
4. `recommended_tasks` 写任务类型，不写营销口号；
5. `risk_boundaries` 明确“什么时候不要用”；
6. `suggested_effort` 只写默认建议，不写成强制；
7. 保留 `evidence_sources`，方便后续复核；
8. 修改 JSON 后不需要改 TP-Voyager Python。
