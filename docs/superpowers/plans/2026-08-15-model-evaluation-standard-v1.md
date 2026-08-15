# TP-Voyager v1.0.7 — Model Evaluation Standard v1 修正版升级计划

> **基线版本：** TP-Voyager v1.0.7  
> **当前版本：** TP-Voyager v1.0.7
> **版本策略：** 本计划不预先提升产品版本号；Model Evaluation Standard v1 先在 v1.0.7 基线上实施与验证，是否产生新的产品版本号在全部实现、数据刷新与回归通过后另行决定。  
> **计划日期：** 2026-08-15  
> **版本口径修正：** 当前仍为 TP-Voyager v1.0.7。本计划是 v1.0.7 基线上的功能改造计划，不提前声明下一版本。
> **目标：** 将现有“Benchmark 快照 + operator 手填 L0~L3”升级为可复现、可校验、可迁移、可由不同用户按同一规则自行维护的 Model Evaluation Standard v1；标准实现完成后，立即按新标准重新采集当前 CodeBuddy / Qoder 可见模型的最新独立 Benchmark 与厂商官方数据，保留现有历史分数，不覆盖、不丢失。

## 0. 本次修订解决的评审问题

### 0.1 实施前最终补充决定（2026-08-15）

- `qoder:auto` 明确退休并从 bundled baseline 删除；不保留兼容 alias。Qoder dynamic tier 只保留 Ultimate / Performance / Efficient / Lite 共 4 个。
- v1→v2 迁移的未校准过渡态：fixed model 使用 `capability_tier=UNCLASSIFIED`、`tier_authority=standard_v1_uncalibrated`、`scorecard=null`，迁移后必须能立即 reload。
- `capability_tier == scorecard.tier` 只在 rules 已 calibrated 且 persisted scorecard 非 null 时强制；未校准态使用上面的 UNCLASSIFIED 规则。
- Scorecard 明确定义为 **persisted maintenance snapshot**，不是 `load()` 时在线重算。Runtime 只校验快照一致性。
- Primary provenance 增加 `primary_approved_by`、`primary_approved_at`、`approval_basis_url`，用于独立复核人工/研究者签收。


本计划是在上一版方案基础上的修正版，必须先解决以下三个硬缺口再进入实现：

1. **operator 已 materialize 的 `model_routing_profiles.json` v1 兼容问题**
   - v2 loader 必须同时接受 v1 与 v2；
   - v1 不得因为 schema 升级直接 `schema unsupported`；
   - v1 只做**内存归一化**，禁止读取时隐式改写；
   - 提供显式 `model-routing-migrate` 命令完成 v1 → v2 持久化迁移；
   - operator 现有字段、自定义推荐、风险边界和历史 Benchmark 数值必须语义等价保留。

2. **`capability_tier` 与 Tier Gate 的唯一权威关系**
   - fixed model 的正式 Tier **只允许由 Standard v1 Scorecard + calibrated tier rules 计算产生**；
   - v1 的静态 `capability_tier` 迁移后仅作为 `legacy_capability_tier` 历史记录，不再拥有决策权；
   - v2 文件中的 `capability_tier` 仅作为**派生缓存/投影值**，必须与 Scorecard 计算结果一致，否则校验失败；
   - Qoder `Ultimate / Performance / Efficient / Lite` 为 dynamic tier，不挂 fixed-model Scorecard，统一投影 `capability_tier = DYNAMIC`，并单独保留 `provider_tier_label`。

3. **Charter 第 6 节 scope-creep gate 论证**
   - 本次工作必须明确服务于 **Captain efficiency + Execution safety + Official backend compatibility**，而不是为了“数据结构更漂亮”而扩张；
   - 真实触发证据是：当前账号可见模型已经从上一基线的 26 route / 15 fixed canonical model 变化为 **27 backend-visible route / 16 fixed canonical model / 4 dynamic Qoder tier**，并新增 GLM-5.3、多个模型跨 CodeBuddy/Qoder 重复出现；
   - 现有自由格式 `capability_tier + benchmark_evidence` 无法可靠表达 benchmark version、model/agent/harness subject、dynamic tier 与 fixed model 的区别，已经不足以稳定支撑跨用户、跨时间的 Captain 模型判断；
   - 新标准不新增 Captain tool、不新增状态机、不联网自动抓榜、不自动选模，只提升现有模型决策证据的**一致性、可追溯性和安全性**。

---

# 1. 当前 v1.0.7（2026-08-15）模型基线

以下模型集合以当前账号实际可见结果为本轮刷新输入事实；**这里只冻结显示名称，不猜内部 route ID**。实际 backend route ID 必须在实施 Task 9 通过 live catalog 重新固化。

## 1.1 CodeBuddy 当前可见模型：12

1. Hy3
2. GLM-5.3
3. GLM-5.2
4. GLM-5.1
5. GLM-5v-Turbo
6. MiniMax-M3
7. MiniMax-M2.7
8. Kimi-K3
9. Kimi-K2.7-Code
10. Kimi-K2.6
11. DeepSeek-V4-Pro
12. DeepSeek-V4-Flash

## 1.2 Qoder 当前可见模型：15

### Dynamic tier：4

1. Ultimate
2. Performance
3. Efficient
4. Lite

### Fixed model：11

1. Cantus
2. Qwen3.8-Max
3. Qwen3.7-Max
4. Qwen3.7-Plus
5. Kimi-K3
6. Kimi-K2.7-Code
7. GLM-5.3
8. GLM-5.2
9. DeepSeek-V4-Pro
10. DeepSeek-V4-Flash
11. MiniMax-M3

## 1.3 本轮 canonical cohort

两个 Backend 合并去重后：

- **backend-visible routes：27**
- **fixed canonical models：16**
- **dynamic routes：4**
- **跨两个 Backend 共同出现的 fixed model：7**

### 16 个 fixed canonical model

1. `hy3`
2. `glm-5.3`
3. `glm-5.2`
4. `glm-5.1`
5. `glm-5v-turbo`
6. `minimax-m3`
7. `minimax-m2.7`
8. `kimi-k3`
9. `kimi-k2.7-code`
10. `kimi-k2.6`
11. `deepseek-v4-pro`
12. `deepseek-v4-flash`
13. `cantus`
14. `qwen3.8-max`
15. `qwen3.7-max`
16. `qwen3.7-plus`

### 跨 Backend 共享 canonical model

- GLM-5.3
- GLM-5.2
- MiniMax-M3
- Kimi-K3
- Kimi-K2.7-Code
- DeepSeek-V4-Pro
- DeepSeek-V4-Flash

**原则：** model-only Evidence 只维护一份 canonical record；只有 agent/harness/backend execution context 不同的 route-specific Evidence 才单独保存。

---

# 2. 当前产品版本与版本边界

## 2.1 当前产品版本

当前正式版本保持：

```text
TP-Voyager v1.0.7
```

本计划只定义在 v1.0.7 基线上进行的 Model Evaluation Standard v1 改造，不在计划阶段提前决定下一产品版本号。

只有在以下事项全部完成后，才讨论是否升版：

- 标准代码实现完成；
- 当前模型数据重新采集完成；
- Tier Rules 完成 calibration；
- 全量回归通过；
- 增量包 + Patch 可从 v1.0.7 基线完整复现。

因此当前所有实现、测试和交付都以 **v1.0.7 作为 source baseline**；若最终需要发布新版本，再单独进行版本收口。

## 2.2 明确不做

本轮不实现：

- 自动联网抓取排行榜；
- 定时模型分数更新；
- 自动模型选择；
- 自动 fallback；
- 单一“TP-Voyager 综合总分”；
- 厂商 claim 自动提升 Tier；
- 新 SQLite 表；
- 新 Captain MCP tool；
- Runtime 根据 Benchmark 自动 dispatch。

数据采集阶段可以由本次执行 AI 联网研究并写入标准化 Evidence；**TP-Voyager Runtime 本身仍然没有联网更新器**。

---

# 3. Model Evaluation Standard v1 总体架构

```text
Provider Live Catalog
        │
        ├── 当前 route / model identity
        │
Independent Benchmark Research
        │
Provider Official Evidence
        │
Legacy Benchmark Evidence
        │
Operator / TP-Voyager Runtime Evidence
        ↓
Source Registry
        ↓
Standard Evidence Records
        ↓
Canonical Model Scorecard
        ↓
Versioned Tier Rules
        ↓
Authoritative Tier
        ↓
model_routing_profiles v2
        ↓
crew_catalog projection
        ↓
Captain 显式选择 Crew + Model + supported effort
```

四层核心：

1. **Source Registry**
2. **Standard Evidence Record**
3. **Scorecard**
4. **Tier Gate**

---

# 4. Schema 与兼容策略

## 4.1 Source Registry

新增：

```text
agent_runtime/application/crew/model_evaluation_sources.baseline.json
```

Schema：

```text
tp-voyager.model_evaluation_sources/v1
```

每个 source 至少描述：

```json
{
  "terminal_bench": {
    "status": "active",
    "role": "primary",
    "source_type": "independent",
    "dimensions": ["terminal_agentic"],
    "requires": [
      "benchmark_version",
      "exact_model_identity",
      "agent",
      "harness",
      "reasoning_effort",
      "attempts_per_task",
      "provenance_url",
      "observed_at"
    ],
    "composite_of": [],
    "freshness_policy_days": 120
  }
}
```

状态：

- `active`
- `supplemental`
- `legacy`
- `archived`
- `experimental`

角色：

- `primary`
- `supplemental`
- `provider`
- `preference`
- `historical`

**Source Registry 负责定义“什么上下文缺失时，这个来源不能进入 Primary”。**

---

## 4.2 Standard Evidence Record

Schema：

```text
tp-voyager.model_evidence/v1
```

示例：

```json
{
  "evidence_schema": "tp-voyager.model_evidence/v1",
  "evidence_id": "aa-terminal-bench-2.1-glm53-opencode-2026-08",
  "source_id": "artificial_analysis",
  "source_type": "independent",
  "subject_type": "model_agent",
  "model": {
    "tested_model": "GLM-5.3",
    "canonical_family": "glm-5.3",
    "model_match": "exact"
  },
  "benchmark": {
    "id": "terminal-bench",
    "version": "2.1",
    "task_count": 89
  },
  "execution": {
    "agent": "opencode",
    "agent_version": null,
    "harness": "harbor",
    "harness_version": null,
    "reasoning_effort": "max",
    "attempts_per_task": 3
  },
  "result": {
    "metric": "pass@1",
    "value": 59.6,
    "scale": "percent"
  },
  "provenance": {
    "observed_at": "2026-08-15",
    "published_at": null,
    "url": "https://source.example/result",
    "methodology_url": "https://source.example/methodology"
  }
}
```

`subject_type` 固定：

- `model_only`
- `model_agent`
- `preference`
- `provider_claim`
- `operator_observed`

---

## 4.3 Legacy Evidence

v1.0.7 所有历史 Benchmark 数值原样保留。

迁移后只增加 discriminator：

```json
{
  "evidence_schema": "legacy_v1",
  "source": "livebench",
  "release": "2026-06-25",
  "tested_model": "GLM-5.2",
  "model_match": "exact",
  "metrics": {
    "overall": 73.2,
    "coding": 79.7,
    "agentic_coding": 51.9
  }
}
```

规则：

- 可展示；
- 可用于历史对照；
- 不删除；
- 不静默改写 raw metrics；
- 不自动进入 Standard v1 Scorecard；
- 只有补齐 Standard v1 必需上下文并重新认证后，才新增一条对应 Standard Evidence；
- **重新认证是 append，不是把 legacy 记录原地改成 standard。**

---

# 5. v1 operator 文件 → v2 迁移设计

这是本轮硬门，不允许省略。

## 5.1 Loader 兼容

`routing_profiles.py` 必须支持：

```text
tp-voyager.model_routing_profiles/v1
tp-voyager.model_routing_profiles/v2
```

行为：

### 读取 v2

直接严格校验。

### 读取 v1

执行纯内存转换：

```text
v1 disk file
   ↓
parse + strict v1 validation
   ↓
normalize_to_v2_in_memory()
   ↓
ModelRoutingProfiles normalized view
```

**读取过程中绝不写磁盘。**

## 5.2 v1 → v2 的字段保留

必须保留：

- route id；
- canonical family；
- 原 `capability_tier`；
- profile confidence；
- specialties；
- recommended tasks；
- risk boundaries；
- suggested effort；
- evidence sources；
- benchmark evidence 原始数值；
- evidence refs；
- operator 允许字段中的全部合法自定义内容。

原 `capability_tier` 转为：

```json
{
  "legacy_capability_tier": "L2"
}
```

不再拥有正式 Tier authority。

## 5.3 显式迁移命令

新增：

```bash
tp-voyager model-routing-migrate --dry-run
tp-voyager model-routing-migrate --write
```

### `--dry-run`

输出：

- source schema；
- target schema；
- profiles 数；
- legacy evidence 数；
- 预计变更字段；
- semantic preservation check；
- 是否存在无法转换字段。

不写文件。

### `--write`

流程：

```text
load v1
→ normalize v2
→ validate v2
→ semantic preservation check
→ 写临时文件
→ fsync/close
→ atomic replace
→ 重新 load 校验
```

任何一步失败：

```text
原文件保持不变
```

## 5.4 Migration Gate

迁移测试必须证明：

```text
迁移前与迁移后：
route identity 相同
recommended_tasks 相同
risk_boundaries 相同
suggested_effort 相同
历史 benchmark raw values 相同
evidence_refs 相同
```

允许新增：

- schema discriminator；
- legacy tier 字段；
- evaluation metadata；
- derived scorecard/tier 字段。

---

# 6. Tier 唯一权威设计

## 6.1 Fixed model

v2 中：

```text
Standard Evidence
    ↓
Scorecard
    ↓
calibrated tier_rules/v1
    ↓
computed tier
    ↓
capability_tier
```

正式权威：

```text
Scorecard.tier
```

`model_routing_profiles.v2.capability_tier` 只是派生缓存，Runtime load 时必须断言：

```text
capability_tier == scorecard.tier
```

若不一致：

```text
fail-closed
```

operator 不能通过直接编辑 `capability_tier` 绕过 Tier Rules。

旧值只保留：

```text
legacy_capability_tier
```

用于“升级前 → 升级后”对照。

## 6.2 Dynamic Qoder tier

以下 4 个 route：

- Ultimate
- Performance
- Efficient
- Lite

统一：

```json
{
  "provider_identity": "dynamic_tier",
  "provider_tier_label": "Ultimate",
  "capability_tier": "DYNAMIC",
  "tier_authority": "provider_dynamic",
  "scorecard": null
}
```

禁止：

- 附某个 fixed model 的 Scorecard；
- 把 `Ultimate` 手填成 L3；
- 把 `Lite` 手填成 L0/L1；
- 用当前一次 provider 底模映射永久代表该 dynamic tier。

Captain 可以看到 `provider_tier_label`，但必须理解：

```text
DYNAMIC != fixed model capability tier
```

---

# 7. Scorecard 设计

每个 fixed canonical model：

```json
{
  "schema": "tp-voyager.model_scorecard/v1",
  "rules_version": "tp-voyager.model_tier_rules/v1",
  "evaluated_at": "2026-08-15",
  "dimensions": {
    "repository_engineering": {},
    "terminal_agentic": {},
    "codebase_understanding": {},
    "general_coding": {},
    "multimodal_coding": {}
  },
  "coverage": "high",
  "confidence": "high",
  "tier": "L3"
}
```

能力轴：

1. `repository_engineering`
2. `terminal_agentic`
3. `codebase_understanding`
4. `general_coding`
5. `multimodal_coding`

`multimodal_coding = N/A` 时：

- 不扣普通 coding tier；
- 但不能宣称 multimodal specialty。

禁止：

- 把 Arena Elo 强转 0~100；
- 把 Artificial Analysis general intelligence index 强转 coding score；
- 把厂商 claim 当 deterministic benchmark；
- 把不同 benchmark raw score 直接平均成总分。

---

# 8. Tier Rules v1

Tier：

- `L0`
- `L1`
- `L2`
- `L3`
- `UNCLASSIFIED`
- `DYNAMIC`

## 8.1 硬门

fixed model 正式 Tier 至少要求：

- `model_match = exact`；
- 至少一个 independent evidence family；
- 进入计分的 evidence 未 archived；
- L2/L3 至少一个 software-engineering / agentic Primary signal；
- L3 至少两个相互独立的 Primary evidence family；
- provider_claim 不能独立推动 L2/L3；
- preference/Elo 不能单独决定 L2/L3；
- evidence 冲突时降低 confidence；
- source requirements 未满足时，不能把 evidence 标成 Primary。

## 8.2 不提前拍阈值

实施顺序：

```text
标准结构
→ 新 cohort 数据采集
→ benchmark/version 分布分析
→ threshold/band 校准
→ 冻结 tier_rules/v1
→ 生成正式 Tier
```

禁止提前规定：

```text
Terminal-Bench 55 = L3
```

## 8.3 初期 N/A / UNCLASSIFIED 是合法结果

现有 legacy evidence 与 5 个能力轴并不完全匹配。

因此在数据刷新过程中允许：

- dimension = `N/A`
- coverage = `low`
- confidence = `low`
- tier = `UNCLASSIFIED`

这不是失败。

**猜一个 L1/L2 才是失败。**

最终发布 v1.0.7 时，只要求对 16 个 fixed model 都有明确的：

- research status；
- coverage；
- confidence；
- tier 或 UNCLASSIFIED reason。

---

# 9. Source 与 Evidence 的硬完成标准

## 9.1 Primary Evidence 必须满足

每条 Primary Evidence 必须：

1. `source_id` 在 Source Registry 中为 active；
2. 有可访问 provenance URL；
3. 有 exact tested model；
4. `model_match = exact`；
5. 有 benchmark id；
6. 有 benchmark version/release；
7. 有 metric 名称、value、scale；
8. 有 observed_at；
9. Source Registry 声明需要 agent/harness/effort/attempts 时，这些字段全部存在；
10. composite/component 关系已标记；
11. 能判断是否与另一条 Evidence 重复；
12. 能通过 `model-evaluation-validate`。

缺任一 source-required 字段：

```text
不得标 Primary
```

只能：

- supplemental；
- provider；
- preference；
- historical；
- experimental。

## 9.2 数据采集“完成”的定义

对每个 canonical model、每个 Primary Source 都必须记录一次结果：

```text
FOUND
NOT_LISTED
IDENTITY_AMBIGUOUS
SOURCE_UNAVAILABLE
NOT_APPLICABLE
```

这样“没找到数据”和“忘了查”不会混在一起。

## 9.3 数字真实性检查

代码无法证明网页上的 `59.6` 一定录入正确，因此研究阶段必须做人工 provenance gate：

- 结果 URL 指向包含该模型/榜单成绩的页面或官方数据记录；
- methodology URL 能解释 benchmark/version/agent/harness；
- tested model 名称与 Evidence 一致；
- 若页面只显示 family/别名，不能冒充 exact；
- 厂商官方 Benchmark 必须标 `provider_claim`；
- 同一数值从二手博客转载时，二手博客不能升级为 Primary。

---

# 10. Double Counting / Isolation 规则

## 10.1 Composite

有 component：

```text
component 进入维度计算
composite 只展示
```

无 component：

```text
composite_fallback = true
```

才允许进入。

## 10.2 Harness

以下永远是不同 Evidence：

```text
GLM-5.3 + OpenCode + Terminal-Bench 2.1
GLM-5.3 + Claude Code + Terminal-Bench 2.1
```

不能平均为：

```text
GLM-5.3 = X
```

## 10.3 Benchmark Version

例如：

```text
Terminal-Bench 2.0
Terminal-Bench 2.1
```

默认不可直接合并。

只有 tier rules 明确声明属于同一 calibration domain 时才能比较。

## 10.4 Provider / preference

以下不进入 deterministic Primary dimension：

- provider benchmark claims；
- Arena preference Elo；
- marketing capability labels。

可用于：

- confidence；
- specialty；
- risk boundary；
- conflict detection；
- historical comparison。

---

# 11. 2026-08 Source Registry 初始候选

> 以下是标准实现阶段的初始候选，不是永久写死。Task 10 必须在采集前重新检查活跃状态、methodology、版本与维护状态。

## Primary 候选

- Artificial Analysis Coding Agent components / capability indices
- DeepSWE
- Terminal-Bench 2.x / Harbor
- SWE-Atlas-QnA

## Supplemental 候选

- Arena Coding / Agent / WebDev
- LiveBench
- SWE-bench Pro

## Legacy / Historical

- LiveCodeBench 当前旧 release

## Archived

- BigCodeBench 历史记录，仅保留，不采新分

## Provider Sources

按 16 个 fixed canonical model 重新确认：

- DeepSeek
- Zhipu / GLM
- Moonshot / Kimi
- MiniMax
- Alibaba / Qwen
- Hy3 的真实 provider identity
- Cantus 的真实 provider identity
- CodeBuddy/Qoder live catalog 声明

---

# Implementation Plan

## Task 1 — 锁定 v1.0.7 scope gate 与当前模型 snapshot

**Files**

- Create: `docs/records/TP_VOYAGER_V107_MODEL_SCOPE_2026-08.md`
- Modify: `CHANGELOG.md`（先新增 Unreleased/v1.0.7 scope 条目，不在中途宣称发布）

**RED / Acceptance**

- [ ] 文档明确记录 27 backend-visible routes / 16 fixed / 4 dynamic。
- [ ] 明确当前可见 display names 与实际 route ID 是两层事实。
- [ ] 明确 Charter 第 6 节三个 gate 中，本轮满足：
  - Captain efficiency；
  - Execution safety；
  - Official backend compatibility。
- [ ] 明确“不新增自动选模器/联网 updater”。

---

## Task 2 — 建立 Source Registry 与 Standard Evidence Schema

**Files**

- Create: `agent_runtime/application/crew/model_evaluation.py`
- Create: `agent_runtime/application/crew/model_evaluation_sources.baseline.json`
- Create: `tests/test_model_evaluation_standard.py`

**Interfaces**

- `ModelEvaluationSourceRegistry.load_bundled()`
- `validate_standard_evidence(record)`
- `EvidenceSubjectType`

**RED tests**

- [ ] exact Standard Evidence 通过。
- [ ] source required benchmark version 缺失 → reject。
- [ ] `model_agent` 在 source 要求 agent/harness 时缺字段 → reject。
- [ ] archived source 不能进入 active scoring。
- [ ] duplicate evidence id → reject。
- [ ] unknown field → fail-closed。
- [ ] Primary provenance URL 缺失 → reject。
- [ ] `model_match != exact` 不能成为 Primary。

---

## Task 3 — v1/v2 双 schema Loader 与内存归一化

**Files**

- Modify: `agent_runtime/application/crew/routing_profiles.py`
- Test: `tests/test_v106_model_routing_catalog.py`
- Test: `tests/test_model_evaluation_standard.py`

**Interfaces**

- Accept:
  - `tp-voyager.model_routing_profiles/v1`
  - `tp-voyager.model_routing_profiles/v2`
- Internal view 始终标准化为 v2 semantics。

**RED tests**

- [ ] materialized v1 operator file 能 load，不报 unsupported schema。
- [ ] v1 load 不修改磁盘 mtime/hash。
- [ ] v1 static tier 映射为 legacy tier。
- [ ] v1 Benchmark raw values 原样可读取。
- [ ] v1 operator allowed custom fields 语义保留。
- [ ] v2 继续 strict unknown-field fail-closed。

---

## Task 4 — 显式 `model-routing-migrate`

**Files**

- Modify: `agent_runtime/cli.py`
- Modify: `agent_runtime/application/crew/routing_profiles.py`
- Test: `tests/test_model_evaluation_standard.py`
- Modify: `docs/OPERATIONS.md`

**Commands**

```bash
tp-voyager model-routing-migrate --dry-run
tp-voyager model-routing-migrate --write
```

**RED tests**

- [ ] dry-run 不写文件。
- [ ] write 前先完成 v2 validation。
- [ ] 写入失败时原 v1 文件保持不变。
- [ ] write 使用 atomic replace。
- [ ] write 后重新 load 成功。
- [ ] semantic preservation snapshot 完全一致。
- [ ] 再次执行 migration 幂等。

---

## Task 5 — baseline/profile v2 + Legacy Evidence 保留

**Files**

- Modify: `agent_runtime/application/crew/model_routing_profiles.baseline.json`
- Modify: `docs/examples/model_routing_profiles.example.json`
- Modify: `agent_runtime/application/crew/routing_profiles.py`
- Test: `tests/test_model_evaluation_standard.py`

**Acceptance**

- [ ] 所有现有 Benchmark 数字仍存在。
- [ ] 所有旧 Evidence 标 `legacy_v1`。
- [ ] old metrics 不被 normalize/round。
- [ ] 原 static tier 保存到 `legacy_capability_tier`。
- [ ] v2 `capability_tier` 不允许再作为 operator authoritative input。
- [ ] dynamic route 不允许 fixed scorecard。

---

## Task 6 — Scorecard Builder

**Files**

- Create: `agent_runtime/application/crew/model_scorecard.py`
- Modify: `agent_runtime/application/crew/model_evaluation.py`
- Test: `tests/test_model_evaluation_standard.py`

**Interfaces**

- `build_scorecard(canonical_family, evidence, registry, tier_rules)`

**RED tests**

- [ ] provider_claim 不进入 deterministic Primary dimension。
- [ ] preference 不进入 deterministic Primary dimension。
- [ ] composite/component 不 double count。
- [ ] incompatible versions 不平均。
- [ ] near_exact/family 不进入正式 dimension。
- [ ] stale/legacy 降 coverage/confidence。
- [ ] multimodal N/A 不降低普通 coding tier。
- [ ] evidence 缺失产生 N/A / low coverage，而不是猜分。

---

## Task 7 — Tier Rules v1 Engine 与权威关系

**Files**

- Create: `agent_runtime/application/crew/model_tier_rules.baseline.json`
- Modify: `agent_runtime/application/crew/model_scorecard.py`
- Test: `tests/test_model_evaluation_standard.py`

**RED tests**

- [ ] fixed model 正式 Tier 必须来自 Scorecard。
- [ ] `capability_tier != scorecard.tier` → reject。
- [ ] legacy tier 不能覆盖 computed tier。
- [ ] L3 必须 exact identity。
- [ ] L3 至少两个独立 Primary evidence family。
- [ ] L2/L3 至少一个 agentic/software-engineering Primary。
- [ ] provider claim alone → UNCLASSIFIED。
- [ ] insufficient evidence → UNCLASSIFIED。
- [ ] dynamic route → DYNAMIC。
- [ ] dynamic route scorecard 非 null → reject。

**阶段行为**

在 Task 12 calibration 完成前：

```text
tier_rules.status = uncalibrated
```

不允许产生“正式新 Tier”。

---

## Task 8 — Captain Projection 收敛为唯一 Tier 语义

**Files**

- Modify: `agent_runtime/application/crew/service.py`
- Test: `tests/test_v106_model_routing_catalog.py`
- Test: `tests/test_captain_policy_evidence.py`

**Fixed model projection**

```json
{
  "capability_profile": {
    "capability_tier": "L2",
    "tier_authority": "standard_v1",
    "legacy_capability_tier": "L3",
    "profile_confidence": "high",
    "scorecard": {
      "coverage": "high",
      "dimensions": {}
    }
  }
}
```

**Dynamic route projection**

```json
{
  "capability_profile": {
    "capability_tier": "DYNAMIC",
    "tier_authority": "provider_dynamic",
    "provider_tier_label": "Ultimate",
    "scorecard": null
  }
}
```

**Acceptance**

- [ ] Captain 不再把 legacy static tier 当正式结果。
- [ ] Captain 可看到历史 tier 但不能混淆 authority。
- [ ] 不新增模型自动选择。
- [ ] allowlist/routability/dispatch 逻辑不变化。

---

## Task 9 — 用户标准校验入口

**Files**

- Modify: `agent_runtime/cli.py`
- Test: `tests/test_model_evaluation_standard.py`
- Modify: `docs/OPERATIONS.md`

**Command**

```bash
tp-voyager model-evaluation-validate
```

输出至少包含：

```text
profiles: 27
canonical_fixed_models: 16
dynamic_routes: 4
profile_schema: v1|v2
migration_available: true|false
standard_evidence: N
legacy_evidence: N
invalid_evidence: 0
primary_missing_required_context: N
archived_source_evidence: N
incomparable_groups: N
tier_rules: calibrated|uncalibrated
tier_authority_conflicts: 0
```

**Exit Code**

- valid = 0；
- schema invalid = non-zero；
- tier authority mismatch = non-zero；
- exact identity conflict = non-zero；
- archived historical evidence = warning；
- source NOT_LISTED = informational。

Validator：

- read-only；
- 不联网；
- 不写文件；
- 不重新探测 Provider。

---

## Task 10 — 编写用户可执行的标准文档

**Files**

- Create: `docs/MODEL_EVALUATION_STANDARD.md`
- Modify: `docs/MODEL_ROUTING.md`
- Modify: `docs/README.md`
- Modify: `README.md`

必须包含：

- 如何新增 Evidence；
- Source Registry；
- Primary done 标准；
- exact identity；
- benchmark version；
- agent/harness isolation；
- provider claim；
- preference；
- legacy；
- dynamic tier；
- double counting；
- migration；
- Tier authority；
- UNCLASSIFIED 合法性；
- 一个完整的用户手工更新示例；
- 一个错误示例：“只抄排行榜综合分然后改 L3”。

**验收：**

只读此文档，一个新用户应能新增一条 schema-valid Evidence。

---

# Data Refresh Phase

> Task 1~10 完成并通过针对性测试后才进入。  
> 本阶段由执行 AI 联网研究；Runtime 不获得联网更新能力。

## Task 11 — 冻结 27-route Live Identity Snapshot

**Files**

- Create: `docs/records/TP_VOYAGER_MODEL_IDENTITY_SNAPSHOT_2026-08.md`

**步骤**

- [ ] 分别读取 CodeBuddy/Qoder 当前 live catalog。
- [ ] 用当前账号可见模型表进行交叉核对。
- [ ] 固化实际 backend route ID，不使用计划里的猜测。
- [ ] 将 27 route 分成：
  - fixed exact model；
  - dynamic tier；
  - ambiguous/unknown。
- [ ] 固化 16 fixed canonical family。
- [ ] 对跨 Backend 同模型验证 canonical identity 是否真的相同。
- [ ] 任何 alias/版本日期不确定的 route 不进入 exact Primary scoring。

**Dynamic**

- Ultimate
- Performance
- Efficient
- Lite

不进入 fixed cohort benchmark aggregation。

---

## Task 12 — 按新 Source Registry 重新采集独立 Benchmark

**Research order**

1. Artificial Analysis Coding Agent components / capability indices；
2. DeepSWE；
3. Terminal-Bench 当前正式版本 / Harbor；
4. SWE-Atlas-QnA；
5. Arena Coding / Agent / WebDev；
6. LiveBench；
7. SWE-bench Pro；
8. LiveCodeBench（historical only）；
9. archived source 只验证归档状态，不采 current score。

### 对 16 个 fixed model 逐个建立 Research Matrix

每个 source/model combination 必须记录：

- `FOUND`
- `NOT_LISTED`
- `IDENTITY_AMBIGUOUS`
- `SOURCE_UNAVAILABLE`
- `NOT_APPLICABLE`

`FOUND` 时必须记录：

- exact tested model；
- benchmark id/version；
- metric/value/scale；
- task count/subset；
- attempts/scoring method；
- agent/harness/version（source 要求时）；
- reasoning effort（source 要求时）；
- result URL；
- methodology URL；
- observed_at/published_at；
- source role；
- composite relationship；
- duplicate relationship。

### Primary Done Gate

只有满足 Source Registry 全部 requires 的 `FOUND` Evidence 才能进入 Primary。

否则降级，不得“补猜”。

---

## Task 13 — 重新采集厂商官方数据

按 16 canonical model 分组：

### DeepSeek

- DeepSeek-V4-Pro
- DeepSeek-V4-Flash

### GLM

- GLM-5.3
- GLM-5.2
- GLM-5.1
- GLM-5v-Turbo

### Kimi

- Kimi-K3
- Kimi-K2.7-Code
- Kimi-K2.6

### MiniMax

- MiniMax-M3
- MiniMax-M2.7

### Qwen

- Qwen3.8-Max
- Qwen3.7-Max
- Qwen3.7-Plus

### Other identity

- Hy3
- Cantus

每个厂商来源确认：

- exact official name；
- release/version；
- deprecated/superseded 状态；
- context window；
- modality；
- tool/function calling；
- reasoning/thinking configuration；
- 官方 Benchmark claim；
- 当前维护状态。

厂商 Benchmark：

```text
subject_type = provider_claim
```

永远不能单独推动 L2/L3。

对 Hy3 / Cantus：

- 必须优先解决真实 provider/model identity；
- identity 无法 exact 时，不得强行绑定独立 Benchmark。

---

## Task 14 — Cohort 分布分析与 Tier Rules v1 Calibration

**Files**

- Modify: `agent_runtime/application/crew/model_tier_rules.baseline.json`
- Create: `docs/records/TP_VOYAGER_MODEL_TIER_CALIBRATION_2026-08.md`

**步骤**

- [ ] 对 16 fixed model 建证据矩阵。
- [ ] 按 benchmark/version 分组，不跨版本平均。
- [ ] 每个 dimension 建 benchmark-specific band。
- [ ] 记录 calibration dataset/version/date。
- [ ] 记录 source coverage。
- [ ] 记录模型缺失矩阵。
- [ ] leave-one-source-out 检查：
  - 移除任一 Supplemental 不应让大量模型 Tier 翻转。
- [ ] 对 Tier 边界模型做显式人工 review。
- [ ] 每个 override/review 都写 reason。
- [ ] 冻结 `tp-voyager.model_tier_rules/v1`。
- [ ] 将 rules status 改为 `calibrated`。

**禁止：**

- 为了让旧 Tier 看起来合理而移动阈值；
- 强制形成固定比例的 L0/L1/L2/L3；
- 用 provider claim 填补 Primary 缺失；
- 让 dynamic tier 参与 fixed cohort calibration。

---

## Task 15 — 生成 v1.0.7 Current Model Evaluation Baseline

**Files**

- Modify: `agent_runtime/application/crew/model_routing_profiles.baseline.json`
- Create: `docs/records/TP_VOYAGER_MODEL_EVALUATION_BASELINE_2026-08.md`

对 16 fixed model：

- [ ] append Standard Evidence；
- [ ] legacy Evidence 全保留；
- [ ] 生成 dimensions；
- [ ] 生成 coverage；
- [ ] 生成 confidence；
- [ ] 生成 authoritative Tier 或 UNCLASSIFIED；
- [ ] 更新 specialties；
- [ ] 更新 recommended tasks；
- [ ] 更新 risk boundaries；
- [ ] 记录升级前 legacy tier；
- [ ] 记录升级后 standard tier；
- [ ] 每个 Tier 变化有 evidence-based explanation。

对 4 dynamic route：

- [ ] `capability_tier = DYNAMIC`；
- [ ] `provider_tier_label` 保留；
- [ ] `scorecard = null`；
- [ ] 不绑定某个 fixed canonical benchmark。

---

## Task 16 — 全量验证与交付

**Version policy**

- 当前产品版本保持 `1.0.7`；
- 本任务不修改 `pyproject.toml`、README、AGENTS、Captain Skill、manifest 中的产品版本号；
- CHANGELOG 只记录本次尚未发布的 Model Evaluation Standard 改造说明，不提前宣称 v1.0.7；
- 若最终决定升版，另做独立版本收口步骤。

**Targeted tests**

```bash
python -m pytest tests/test_model_evaluation_standard.py -q
python -m pytest tests/test_v106_model_routing_catalog.py -q
python -m pytest tests/test_captain_policy_evidence.py -q
```

**Full regression**

```bash
python -m pytest -q
python -m compileall -q agent_runtime tests
```

**Migration verification**

- [ ] v1 materialized file load 成功；
- [ ] v1 dry-run 不写；
- [ ] v1 migrate 后 semantic snapshot 一致；
- [ ] v2 二次 migrate 幂等；
- [ ] 迁移失败保持原文件。

**Evaluation static gates**

- [ ] 0 unknown source id；
- [ ] 0 duplicate evidence id；
- [ ] 0 Primary evidence missing required context；
- [ ] 0 fixed scorecard on dynamic tier；
- [ ] 0 tier authority mismatch；
- [ ] 0 active Tier based only on provider_claim；
- [ ] 0 composite/component double count；
- [ ] 0 exact-identity violation；
- [ ] 0 archived source used as current Primary；
- [ ] legacy evidence count >= v1.0.7；
- [ ] 16 fixed model 都有 research status；
- [ ] 4 dynamic route 都是 DYNAMIC；
- [ ] 27 live routes 全部有 identity resolution 状态。

---

# 12. 最终交付

仍按 TP-Voyager 当前交付约定：

只提供：

1. `TP_Voyager_v1.0.7_Model_Evaluation_Standard_v1_delta.zip`
2. `TP_Voyager_v1.0.7_Model_Evaluation_Standard_v1.patch`

## 增量包包含

- 本轮新增/修改文件；
- `DELTA_MANIFEST.json`；
- `DELETED_FILES.txt`；
- identity snapshot；
- model evaluation research record；
- provider research record；
- tier calibration record；
- final evaluation baseline record。

> 注意：交付文件名中的 `v1.0.7` 表示其 **source/current baseline**，不是宣称产生了新的产品 release。

## Patch 必须通过硬复现

```text
原始 v1.0.7 baseline
        +
v1.0.7 patch
        ↓
final tree
        ↓
逐文件 SHA-256 compare
        ↓
missing = 0
extra   = 0
changed = 0
```

缓存文件不进入交付：

- `__pycache__`
- `*.pyc`
- `.pytest_cache`

---

# 13. 最终验收标准

本次 v1.0.7 Model Evaluation Standard 改造只有在以下全部成立后才算完成：

1. materialized v1 operator profile 不会因 v2 升级崩溃。
2. v1 → v2 有显式、幂等、原子、语义保留的迁移路径。
3. 旧 `capability_tier` 仅为历史记录，不再拥有 Tier authority。
4. fixed model 的正式 Tier 唯一来自 Scorecard + calibrated tier_rules/v1。
5. dynamic Qoder tier 永远是 `DYNAMIC`，不挂 fixed Scorecard。
6. 用户仅阅读 `MODEL_EVALUATION_STANDARD.md` 即可创建 schema-valid Evidence。
7. 两个用户采集同一 Benchmark，在 model/version/agent/harness/effort 相同时能得到结构与语义一致的 Evidence。
8. 每条 Primary Evidence 都满足 Source Registry 的 required context 与 provenance gate。
9. “未找到数据”有 `NOT_LISTED/IDENTITY_AMBIGUOUS/...` 记录，不等同于漏查。
10. v1.0.7 历史 Benchmark raw values 全部保留。
11. 不可比 benchmark/version 不会被自动平均。
12. model + 不同 agent/harness 不会被误归并成纯模型成绩。
13. composite/component 不 double count。
14. provider claim 与 Arena preference 不能单独提升正式 Tier。
15. 16 个 fixed model 全部完成 current research matrix。
16. 证据不足的模型允许 `UNCLASSIFIED`，禁止猜 Tier。
17. 4 个 dynamic route 全部保持 `DYNAMIC`。
18. Captain 仍然显式选择 Crew/model/effort；TP-Voyager 不自动选模。
19. 本次升级通过 Charter scope-creep gate 的书面论证保留在 records。
20. 最终交付只有增量包 + Patch，并可从 v1.0.7 字节级复现 final tree。

---

# 14. 实施时需要重新验证的外部研究源

以下仅作为本计划的 Source Registry 候选。进入 Task 12/13 时必须重新访问并确认当日：

- 是否仍维护；
- 当前 benchmark/version；
- methodology 是否变化；
- leaderboard 是否换 harness；
- 是否 archive/deprecate；
- 是否新增更适合 repository engineering / terminal agentic 的可靠来源。

候选：

- Artificial Analysis Coding Agent
- DeepSWE
- Terminal-Bench / Harbor
- SWE-Atlas
- Arena Coding / Agent / WebDev
- LiveBench
- SWE-bench Pro
- LiveCodeBench
- BigCodeBench（历史归档检查）
- 各模型厂商官方 Model Card / 文档 / release notes
- CodeBuddy / Qoder 当前 live model catalog

**实施原则：**

```text
先确认 source 当前状态
→ 再采 current data
→ 再做 cohort calibration
→ 最后生成 Tier
```

不得把本计划编写时的 source 状态当作永久事实。
