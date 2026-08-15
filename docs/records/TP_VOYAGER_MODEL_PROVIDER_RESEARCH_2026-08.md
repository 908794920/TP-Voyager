# TP-Voyager Model Provider Research Record — 2026-08

Provider material establishes identity, release status, modality/context/tool support, and vendor claims. It is **not** an independent Tier authority.

| Canonical model | Provider status | Primary official source used | Evaluation treatment |
|---|---|---|---|
| `deepseek-v4-pro` | exact official identity documented | `https://api-docs.deepseek.com/news/news260424/` | Provider evidence only; independent agent benchmark decides Tier |
| `deepseek-v4-flash` | exact official identity documented | `https://api-docs.deepseek.com/news/news260424/` | Provider evidence only |
| `glm-5.2` | exact official identity documented | `https://z.ai/blog/glm-5.2` | Provider evidence only |
| `glm-5.1` | exact official identity documented | `https://docs.z.ai/guides/llm/glm-5.1` | Provider model card + provider benchmark claim kept separate |
| `glm-5v-turbo` | exact official identity documented | `https://docs.z.ai/guides/vlm/glm-5v-turbo` | Provider evidence only |
| `glm-5.3` | backend-visible; exact public provider record not captured | current account snapshot | `IDENTITY_AMBIGUOUS` for external scoring; no inherited GLM-5.2 evidence |
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

Backend visibility is a separate fact from public vendor identity. A model can be selectable in CodeBuddy/Qoder while still lacking enough public exact-version evidence for standardized scoring. This is why GLM-5.3 and Qwen3.8-Max can be routable/visible facts without being assigned an inherited external Tier.
