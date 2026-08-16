# TP-Voyager Model Provider Research Record — 2026-08

Provider material establishes identity, release status, modality/context/tool support, and vendor claims. It is **not** an independent Tier authority.

| Canonical model | Provider status | Identity / release provenance | Evaluation treatment |
|---|---|---|---|
| `deepseek-v4-pro` | exact current release: `DeepSeek-V4-Pro-0813` (2026-08-13) | operator-confirmed backend identity; independent release confirmation: `https://www.reuters.com/world/china/deepseek-releases-official-v4-pro-model-it-steps-up-expansion-2026-08-13/`; DeepSeek stable API alias remains `deepseek-v4-pro` | April-preview / pre-0813 scores are historical only; current 0813 requires fresh exact Primary evidence |
| `deepseek-v4-flash` | exact current release: `DeepSeek-V4-Flash-0731` (2026-07-31) | operator-confirmed backend identity; current Artificial Analysis page explicitly identifies `DeepSeek V4 Flash 0731`; DeepSeek stable API alias remains `deepseek-v4-flash` | current exact supplemental evidence is valid, but no compatible Primary coding-agent evidence is captured |
| `glm-5.2` | exact official identity documented | `https://z.ai/blog/glm-5.2` | Provider evidence only |
| `glm-5.1` | exact official identity documented | `https://docs.z.ai/guides/llm/glm-5.1` | Provider model card + provider benchmark claim kept separate |
| `glm-5v-turbo` | exact official identity documented | `https://docs.z.ai/guides/vlm/glm-5v-turbo` | Provider evidence only |
| `glm-5.3` | exact formal model release announced 2026-08-14; current CodeBuddy backend visibility confirmed | current account snapshot + independent release confirmation: `https://www.reuters.com/technology/chinas-zai-says-new-model-nears-anthropics-mythos-5-cyber-defence-tests-2026-08-14/` | identity is exact; provider-reported CyberGym/ExploitBench results remain supplemental; no inherited GLM-5.2 evidence |
| `hy3` | exact official identity documented | `https://www.tencent.com/tencent-hunyuan-officially-releases-hy3-advancing-agent-capabilities-and-deeper-product-integration/` | Provider evidence only |
| `kimi-k3` | exact official identity documented | `https://www.kimi.com/blog/kimi-k3` | Provider evidence + independent agent benchmark |
| `kimi-k2.7-code` | exact official identity documented | `https://www.kimi.com/resources/kimi-k2-7-code` | Provider evidence only in formal Tier |
| `kimi-k2.6` | exact official identity documented | `https://www.kimi.com/help/kimi-api/api-model-selection` | Provider evidence + independent agent benchmark |
| `minimax-m3` | exact official identity documented | `https://www.minimax.io/blog/minimax-m3` | Provider TBench claim retained as provider claim only |
| `minimax-m2.7` | exact official identity documented | `https://www.minimax.io/models/text/m27` | Provider TBench claim retained as provider claim only |
| `qwen3.7-max` | current Qwen identity documented | `https://qwen.ai/home` | Provider evidence only |
| `qwen3.7-plus` | current Qwen identity documented | `https://qwen.ai/home` | Provider evidence + independent agent benchmark |
| `qwen3.8-max` | exact public version unresolved | `https://qwenlm.github.io/qwen-code-docs/en/blog/updates/weekly-update-2026-07-23/` | Public material says `qwen3.8-max-preview`; do not map to account `Qwen3.8-Max` as exact |
| `cantus` | public provider identity unresolved | — | `IDENTITY_AMBIGUOUS`; no external scores promoted |

## Provider claim isolation

Vendor-published benchmark numbers are stored with `subject_type=provider_claim` and `source_role=provider`. Current retained examples include MiniMax Terminal-Bench claims and a GLM-5.1 SWE-bench Pro claim. They can inform specialties/risk review but cannot independently create L2/L3.

## Backend catalog caveat

Backend visibility, exact model/release identity, backend route-id resolution, and benchmark comparability are separate facts. GLM-5.3 now has an exact formal model identity, but its Qoder account-specific route id is still unresolved and compatible independent Primary benchmark evidence is still pending. Qwen3.8-Max remains a separate exact-version ambiguity because the observed public material names a preview build.

## Fresh-release audit correction — 2026-08-15

- DeepSeek V4 Flash is evaluated as the **0731 official release**, not the April preview. Current Artificial Analysis model-level evidence explicitly labels the 0731 release.
- DeepSeek V4 Pro is evaluated as the **0813 official release**. Pre-0813/April-preview benchmark rows remain historical and cannot determine the 0813 Tier.
- GLM-5.3 is treated as a **formally announced exact model**, not `IDENTITY_AMBIGUOUS`. Lack of current Primary benchmark rows affects Tier coverage, not model identity.
