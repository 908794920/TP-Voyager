# v1.0.7 Release Documentation and Canonical Lite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align the v1.0.7 source and release material with the live `qoder:lite` dispatch ID and record verified parameter/Usage behavior.

**Architecture:** The configuration parser retains strict exact route IDs; therefore the source baseline, fixtures, and routing-profile data use one canonical lowercase Qoder model ID. Integration tests set an isolated `TP_VOYAGER_HOME`, while documentation separates dispatch IDs, display labels, and provider-observed Usage.

**Tech Stack:** Python 3, unittest, JSON baselines, Markdown.

## Global Constraints

- Do not rewrite an existing user configuration.
- Do not add token or cost estimation.
- Do not commit, tag, push, or alter remote state.

---

### Task 1: Canonical Qoder Lite defaults

**Files:**
- Modify: `agent_runtime/configuration/user_config.py`
- Modify: `agent_runtime/application/crew/model_routing_profiles.baseline.json`
- Test: `tests/test_v107_user_config.py`
- Test: `tests/test_model_evaluation_standard.py`

- [ ] Use the canonical lowercase `qoder:lite` route in the default configuration and bundled profile.
- [ ] Update assertions to expect the exact lower-case dispatch ID.
- [ ] Run the two focused test modules.

### Task 2: Hermetic server integration tests

**Files:**
- Modify: `tests/test_qoder_backend.py`
- Modify: `tests/test_codebuddy_backend.py`

- [ ] Give Qoder server integration tests a temporary `TP_VOYAGER_HOME` with a
  default config and use `model="lite"`.
- [ ] Preserve the probe test's process home variables while clearing only
  application-specific environment variables.
- [ ] Run the affected test modules.

### Task 3: v1.0.7 release material

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/MODEL_ROUTING.md`
- Modify: `docs/TESTING.md`

- [ ] State the canonical dispatch ID and separate it from the display label.
- [ ] Record parameter preflight, Qoder context-window startup application, and
  provider-observed Usage status without a cost estimate.
- [ ] Add a v1.0.7 release gate with the observed MCP evidence and remaining
  full-suite requirement.
