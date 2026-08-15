# TP-Voyager Model Identity Snapshot — 2026-08

## Capture semantics

The visible-name set below is the operator-supplied current-account snapshot. TP-Voyager must not invent a backend route id from a display name. Qoder documents that the account model-list API / CLI returns the account-specific `id`; because that live credentialed catalog is not available in this build environment, unresolved ids remain `IDENTITY_AMBIGUOUS`.

## CodeBuddy

| Visible model | Materialized route | Canonical family | Resolution |
|---|---|---|---|
| Hy3 | `codebuddy:hy3` | `hy3` | FOUND |
| GLM-5.3 | `codebuddy:glm-5.3` | `glm-5.3` | FOUND |
| GLM-5.2 | `codebuddy:glm-5.2` | `glm-5.2` | FOUND |
| GLM-5.1 | `codebuddy:glm-5.1` | `glm-5.1` | FOUND |
| GLM-5v-Turbo | `codebuddy:glm-5v-turbo` | `glm-5v-turbo` | FOUND |
| MiniMax-M3 | `codebuddy:minimax-m3-pay` | `minimax-m3` | FOUND |
| MiniMax-M2.7 | `codebuddy:minimax-m2.7` | `minimax-m2.7` | FOUND |
| Kimi-K3 | `codebuddy:kimi-k3-2` | `kimi-k3` | FOUND |
| Kimi-K2.7-Code | `codebuddy:kimi-k2.7` | `kimi-k2.7-code` | FOUND |
| Kimi-K2.6 | `codebuddy:kimi-k2.6` | `kimi-k2.6` | FOUND |
| DeepSeek-V4-Pro | `codebuddy:deepseek-v4-pro` | `deepseek-v4-pro` | FOUND |
| DeepSeek-V4-Flash | `codebuddy:deepseek-v4-flash` | `deepseek-v4-flash` | FOUND |

## Qoder

| Visible model | Materialized route | Canonical family / tier | Resolution |
|---|---|---|---|
| Ultimate | `qoder:ultimate` | `qoder-ultimate-tier` | FOUND / DYNAMIC |
| Performance | `qoder:performance` | `qoder-performance-tier` | FOUND / DYNAMIC |
| Efficient | `qoder:efficient` | `qoder-efficient-tier` | FOUND / DYNAMIC |
| Lite | `qoder:Lite` | `qoder-lite-tier` | FOUND / DYNAMIC |
| Cantus | `qoder:cmodel` | `cantus` | FOUND route / public identity ambiguous |
| Qwen3.8-Max | `qoder:qmodel_38max` | `qwen3.8-max` | FOUND route / public exact version ambiguous |
| Qwen3.7-Max | `qoder:qmodel_latest` | `qwen3.7-max` | FOUND |
| Qwen3.7-Plus | `qoder:qmodel` | `qwen3.7-plus` | FOUND |
| Kimi-K3 | `qoder:kmodel_latest` | `kimi-k3` | FOUND |
| Kimi-K2.7-Code | `qoder:kmodel` | `kimi-k2.7-code` | FOUND |
| GLM-5.3 | — | `glm-5.3` | IDENTITY_AMBIGUOUS: account display known, backend id not captured |
| GLM-5.2 | `qoder:gm51model` | `glm-5.2` | FOUND |
| DeepSeek-V4-Pro | `qoder:dmodel` | `deepseek-v4-pro` | FOUND |
| DeepSeek-V4-Flash | `qoder:dfmodel` | `deepseek-v4-flash` | FOUND |
| MiniMax-M3 | `qoder:mmodel` | `minimax-m3` | FOUND |

## Retired route

- `qoder:auto` — intentionally removed; no compatibility alias is retained.

## Materialized versus visible count

- Account-visible entries: **27**.
- Materialized routing profiles: **26**.
- Difference: Qoder GLM-5.3 has a known visible name but no captured account-specific route id. This is deliberate fail-closed behavior, not a missing guessed alias.
