"""Self-contained MCP Apps card for TP-Voyager runtime configuration."""

from __future__ import annotations


VOYAGER_RUNTIME_PROFILE_URI = "ui://tp-voyager/runtime-profile/v1.html"
VOYAGER_RUNTIME_PROFILE_MIME_TYPE = "text/html;profile=mcp-app"


def render_voyager_runtime_profile_html() -> str:
    """Return the read-only Runtime Profile MCP Apps resource."""
    return r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>TP-Voyager 运行与账户</title>
<style>
  :root {
    color-scheme: dark;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --bg-page: #080b10;
    --bg-panel: #0d1117;
    --bg-surface: #111720;
    --bg-surface-raised: #151c26;
    --bg-surface-hover: #19222e;
    --text-primary: #eef3f8;
    --text-secondary: #aab6c4;
    --text-muted: #748191;
    --border-subtle: #26303b;
    --border-strong: #344252;
    --green: #38c976;
    --green-soft: rgba(56, 201, 118, .10);
    --blue: #5b8ff9;
    --purple: #9b7cf8;
    --red: #ff6b6b;
    --amber: #e7b348;
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --shadow-panel: 0 16px 40px rgba(0, 0, 0, .24);
  }
  * { box-sizing: border-box; }
  html, body { min-width: 0; max-width: 100%; }
  body { margin: 0; padding: 8px; background: var(--bg-page); color: var(--text-primary); }
  .profile-panel { overflow: hidden; border: 1px solid var(--border-subtle); border-radius: var(--radius-lg); background: var(--bg-panel); box-shadow: var(--shadow-panel); }
  .profile-panel[data-sync-state="error"] { border-color: rgba(255, 107, 107, .42); }
  .profile-header { display: flex; align-items: center; gap: 14px; padding: 14px 18px; border-bottom: 1px solid var(--border-subtle); }
  .profile-identity { min-width: 0; flex: 1; }
  .profile-title-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .profile-status-dot { width: 9px; height: 9px; flex: 0 0 9px; border-radius: 999px; background: var(--green); }
  .profile-panel[data-sync-state="syncing"] .profile-status-dot { background: var(--blue); animation: pulse 1.45s ease-in-out infinite; }
  .profile-panel[data-sync-state="error"] .profile-status-dot { background: var(--red); }
  @keyframes pulse { 0%, 100% { opacity: .55; } 50% { opacity: 1; } }
  .profile-title { font-size: 15px; font-weight: 760; letter-spacing: -.01em; }
  .profile-badge { min-height: 22px; padding: 2px 8px; border: 1px solid rgba(56, 201, 118, .42); border-radius: 999px; background: var(--green-soft); color: var(--green); font-size: 10.5px; font-weight: 700; }
  .profile-meta { margin-top: 5px; color: var(--text-muted); font-size: 10.5px; overflow-wrap: anywhere; }
  button { appearance: none; border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); background: var(--bg-surface); color: var(--text-primary); font: inherit; cursor: pointer; }
  button:disabled { opacity: .45; cursor: default; }
  .profile-refresh { flex: 0 0 auto; padding: 6px 10px; color: var(--text-secondary); font-size: 11px; }
  .profile-refresh:hover { border-color: var(--border-strong); background: var(--bg-surface-hover); color: var(--text-primary); }
  .profile-tabs { display: flex; gap: 2px; padding: 0 18px; border-bottom: 1px solid var(--border-subtle); overflow-x: auto; }
  .profile-tab { position: relative; border: 0; border-radius: 0; background: transparent; padding: 11px 12px 10px; color: var(--text-muted); font-size: 11px; white-space: nowrap; }
  .profile-tab:hover { color: var(--text-primary); background: transparent; }
  .profile-tab.is-active { color: var(--text-primary); font-weight: 700; }
  .profile-tab.is-active::after { content: ""; position: absolute; right: 10px; bottom: -1px; left: 10px; height: 2px; border-radius: 2px; background: var(--green); }
  .profile-content { min-height: 280px; padding: 14px 18px 18px; }
  .profile-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
  .profile-card, .profile-section { min-width: 0; border: 1px solid var(--border-subtle); border-radius: var(--radius-md); background: var(--bg-surface); }
  .profile-card { padding: 12px; }
  .profile-card-label, .profile-section-title { color: var(--text-muted); font-size: 10.5px; }
  .profile-card-value { margin-top: 6px; color: var(--text-primary); font-size: 18px; font-weight: 750; overflow-wrap: anywhere; }
  .profile-card-note { margin-top: 4px; color: var(--text-secondary); font-size: 10.5px; line-height: 1.45; }
  .profile-sections { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
  .profile-section { padding: 12px; }
  .profile-section + .profile-section { margin-top: 0; }
  .advanced-config { grid-column: 1 / -1; overflow: hidden; border: 1px solid var(--border-subtle); border-radius: var(--radius-md); background: var(--bg-surface); }
  .advanced-config-summary { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 11px 12px; color: var(--text-secondary); font-size: 10.5px; font-weight: 700; cursor: pointer; user-select: none; }
  .advanced-config-summary::-webkit-details-marker { display: none; }
  .advanced-config-summary::after { content: "+"; color: var(--text-muted); font-size: 15px; font-weight: 500; }
  .advanced-config[open] .advanced-config-summary::after { content: "−"; }
  .advanced-config-content { padding: 0 12px 12px; border-top: 1px solid var(--border-subtle); }
  .advanced-config-content .profile-sections { margin-top: 12px; }
  .profile-section-title { margin-bottom: 9px; color: var(--text-secondary); font-weight: 700; }
  .profile-row { display: grid; grid-template-columns: minmax(86px, .72fr) minmax(0, 1.28fr); gap: 10px; padding: 7px 0; border-top: 1px solid rgba(38, 48, 59, .72); font-size: 10.5px; }
  .profile-row:first-of-type { border-top: 0; padding-top: 0; }
  .profile-key { color: var(--text-muted); overflow-wrap: anywhere; }
  .profile-value { color: var(--text-primary); font-weight: 600; overflow-wrap: anywhere; }
  .chip-list { display: flex; flex-wrap: wrap; gap: 5px; }
  .chip { display: inline-flex; max-width: 100%; padding: 3px 6px; border: 1px solid var(--border-subtle); border-radius: 999px; background: var(--bg-surface-raised); color: var(--text-secondary); font-size: 10px; overflow-wrap: anywhere; }
  .model-list, .account-list { display: grid; gap: 10px; }
  .provider-block { overflow: hidden; border: 1px solid var(--border-subtle); border-radius: var(--radius-md); }
  .provider-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px 12px; background: var(--bg-surface-raised); color: var(--text-primary); font-size: 11px; font-weight: 700; }
  .provider-note { color: var(--text-muted); font-size: 10px; font-weight: 500; }
  .model-row { display: grid; grid-template-columns: minmax(130px, 1.2fr) minmax(70px, .65fr) minmax(80px, .75fr) minmax(0, 1fr); gap: 10px; align-items: center; padding: 10px 12px; border-top: 1px solid var(--border-subtle); font-size: 10.5px; }
  .model-name { min-width: 0; color: var(--text-primary); font-weight: 700; overflow-wrap: anywhere; }
  .model-id { margin-top: 3px; color: var(--text-muted); font-size: 10px; overflow-wrap: anywhere; }
  .status { display: inline-flex; align-items: center; gap: 5px; color: var(--text-secondary); }
  .status::before { content: ""; width: 7px; height: 7px; flex: 0 0 7px; border-radius: 99px; background: var(--text-muted); }
  .status.is-available::before, .status.is-routable::before { background: var(--green); }
  .status.is-unavailable::before { background: var(--red); }
  .status.is-unknown::before { background: var(--amber); }
  .account-card { padding: 12px; border: 1px solid var(--border-subtle); border-radius: var(--radius-md); background: var(--bg-surface); }
  .account-title { color: var(--text-primary); font-size: 12px; font-weight: 750; }
  .account-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 10px; }
  .account-fact { min-width: 0; padding: 8px; border-radius: var(--radius-sm); background: var(--bg-surface-raised); }
  .account-fact-label { color: var(--text-muted); font-size: 9.5px; }
  .account-fact-value { margin-top: 4px; color: var(--text-primary); font-size: 10.5px; font-weight: 700; overflow-wrap: anywhere; }
  .empty-state { display: grid; min-height: 230px; place-items: center; color: var(--text-muted); font-size: 11px; text-align: center; }
  .sync-error { margin-bottom: 10px; padding: 8px 10px; border: 1px solid rgba(255, 107, 107, .35); border-radius: var(--radius-sm); background: rgba(255, 107, 107, .08); color: #ffb2b2; font-size: 10.5px; }
  @media (max-width: 639px) {
    .profile-grid, .profile-sections, .account-grid { grid-template-columns: minmax(0, 1fr); }
    .model-row { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  }
</style>
</head>
<body>
<section id="profile" class="profile-panel" data-sync-state="syncing" data-active-tab="overview">
  <header class="profile-header">
    <div class="profile-identity">
      <div class="profile-title-row"><span class="profile-status-dot" aria-hidden="true"></span><span class="profile-title">TP-Voyager 运行与账户</span><span id="profile-badge" class="profile-badge">正在同步</span></div>
      <div id="profile-meta" class="profile-meta">读取当前生效配置与 Crew 状态…</div>
    </div>
    <button id="profile-refresh" class="profile-refresh" type="button">刷新</button>
  </header>
  <nav class="profile-tabs" role="tablist" aria-label="运行与账户导航">
    <button class="profile-tab is-active" type="button" data-tab="overview" role="tab" aria-selected="true">概览</button>
    <button class="profile-tab" type="button" data-tab="models" role="tab" aria-selected="false">模型</button>
    <button class="profile-tab" type="button" data-tab="accounts" role="tab" aria-selected="false">账户</button>
  </nav>
  <main id="profile-content" class="profile-content"><div class="empty-state">正在读取最近可信配置…</div></main>
</section>
<script>
(() => {
  const REQUEST_TIMEOUT_MS = 30000;
  let nextRequestId = 1;
  const pendingRequests = new Map();
  let profileData = null;
  let activeTab = "overview";
  let syncError = "";
  const panel = document.getElementById("profile");
  const content = document.getElementById("profile-content");
  const meta = document.getElementById("profile-meta");
  const badge = document.getElementById("profile-badge");
  const refreshButton = document.getElementById("profile-refresh");
  const tabs = Array.from(document.querySelectorAll(".profile-tab"));

  function request(method, params, timeoutMs = REQUEST_TIMEOUT_MS) {
    const id = nextRequestId++;
    return new Promise((resolve, reject) => {
      const timeoutId = setTimeout(() => {
        if (!pendingRequests.has(id)) return;
        pendingRequests.delete(id);
        reject(new Error(`MCP bridge request timed out: ${method}`));
      }, Math.max(250, Number(timeoutMs) || REQUEST_TIMEOUT_MS));
      pendingRequests.set(id, { resolve, reject, timeoutId });
      window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    });
  }

  function notify(method, params) {
    window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
  }

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }

  function valueText(value) {
    if (value === null || value === undefined || value === "") return "暂无数据";
    if (value === true) return "是";
    if (value === false) return "否";
    return String(value);
  }

  function setSync(state, message = "") {
    panel.dataset.syncState = state;
    refreshButton.disabled = state === "syncing";
    badge.textContent = state === "syncing" ? "正在同步" : (state === "error" ? "同步失败" : "已同步");
    if (message) meta.textContent = message;
  }

  function chipList(values) {
    const list = node("div", "chip-list");
    const items = Array.isArray(values) ? values : [];
    if (!items.length) list.appendChild(node("span", "chip", "暂无数据"));
    for (const value of items) list.appendChild(node("span", "chip", valueText(value)));
    return list;
  }

  function appendField(parent, label, value) {
    const row = node("div", "profile-row");
    row.appendChild(node("div", "profile-key", label));
    const valueNode = node("div", "profile-value");
    if (Array.isArray(value)) valueNode.appendChild(chipList(value));
    else if (value && typeof value === "object") valueNode.textContent = Object.keys(value).length ? "已配置" : "暂无数据";
    else valueNode.textContent = valueText(value);
    row.appendChild(valueNode);
    parent.appendChild(row);
  }

  function configSection(title, entries) {
    const section = node("section", "profile-section");
    section.appendChild(node("div", "profile-section-title", title));
    for (const [label, value] of entries) appendField(section, label, value);
    return section;
  }

  function overview() {
    const fragment = document.createDocumentFragment();
    const accounts = Array.isArray(profileData?.accounts) ? profileData.accounts : [];
    const models = Array.isArray(profileData?.models) ? profileData.models : [];
    const routable = models.filter((item) => item.routable === true).length;
    const grid = node("div", "profile-grid");
    const cards = [
      ["生效配置", profileData?.config?.schema || "暂无数据", profileData?.config?.config_path || ""],
      ["当前模型", `${routable} / ${models.length}`, "可路由 / 已发现"],
      ["账户状态", `${accounts.filter((item) => item.availability === "available").length} / ${accounts.length}`, "Crew 可用 / 已配置"],
    ];
    for (const [label, value, note] of cards) {
      const card = node("section", "profile-card");
      card.appendChild(node("div", "profile-card-label", label));
      card.appendChild(node("div", "profile-card-value", value));
      card.appendChild(node("div", "profile-card-note", note));
      grid.appendChild(card);
    }
    fragment.appendChild(grid);
    const config = profileData?.config || {};
    const sections = node("div", "profile-sections");
    const crew = config.crew || {};
    sections.appendChild(configSection("Crew", [
      ["Qoder", crew.qoder?.enabled ? "已启用" : "已禁用"],
      ["Qoder CLI", crew.qoder?.cli_path],
      ["Qoder 并发", crew.qoder?.max_concurrent_tasks],
      ["CodeBuddy", crew.codebuddy?.enabled ? "已启用" : "已禁用"],
      ["CodeBuddy CLI", crew.codebuddy?.cli_path || "自动从系统 PATH 发现"],
      ["网络环境", crew.codebuddy?.internet_environment],
      ["CodeBuddy 并发", crew.codebuddy?.max_concurrent_tasks],
    ]));
    const dispatch = config.dispatch || {};
    sections.appendChild(configSection("路由策略", [
      ["允许模型", dispatch.allowed_models],
      ["配置主目录", config.home],
      ["配置文件", config.config_path],
    ]));
    fragment.appendChild(sections);

    const advanced = node("details", "advanced-config");
    advanced.appendChild(node("summary", "advanced-config-summary", "高级配置（可选）"));
    const advancedContent = node("div", "advanced-config-content");
    const advancedSections = node("div", "profile-sections");
    const trustedRoots = config.trusted_roots || {};
    const rootValues = (values, emptyText) => {
      const configured = Object.entries(values || {}).map(([alias, path]) => `${alias}: ${valueText(path)}`);
      return configured.length ? configured : emptyText;
    };
    const taskKinds = Object.keys(dispatch.task_kind_allowed_models || {});
    advancedSections.appendChild(configSection("调度细则", [
      ["优先模型", dispatch.preferred_models?.length ? dispatch.preferred_models : "未配置（不参与自动选模）"],
      ["按任务类型限制", taskKinds.length ? taskKinds : "未配置（所有任务沿用允许模型）"],
    ]));
    advancedSections.appendChild(configSection("可信根目录", [
      ["模型资料根目录", rootValues(trustedRoots.model_evidence, "未配置")],
      ["受信任指令根目录", rootValues(trustedRoots.instructions, "未配置")],
    ]));
    const resources = config.resources || {};
    advancedSections.appendChild(configSection("工作资源", [
      ["Worker Profile", resources.worker_profiles_root || "使用插件内置默认 Profile"],
      ["外部 Worker Skill", resources.worker_skills_root || "未配置（不启用外部 Worker Skill）"],
    ]));
    advancedContent.appendChild(advancedSections);
    advanced.appendChild(advancedContent);
    fragment.appendChild(advanced);
    return fragment;
  }

  function modelStatus(label, className) {
    return node("span", `status ${className}`, label);
  }

  function referenceMultiplierLabel(value) {
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "参考倍率未声明";
    return `参考倍率 x${value}`;
  }

  function localizedAvailability(value) {
    return ({ available: "可用", unavailable: "不可用", unknown: "暂时无法确认" })[value] || "暂时无法确认";
  }

  function localizedAuthStatus(value) {
    return ({ verified: "已验证", not_probed: "未单独验证", unknown: "暂时无法验证" })[value] || "暂时无法验证";
  }

  function localizedCatalogStatus(value) {
    return ({ complete: "模型列表：已完整获取", incomplete_suspected: "模型列表：结果可能不完整", unavailable: "模型列表：暂时无法获取" })[value] || "模型列表：暂时无法确认";
  }

  function localizedQuota(account) {
    if (account.quota_summary) return account.quota_summary;
    return ({ not_observed: "未观测", unknown: "暂时无法读取账户额度", not_supported: "官方 CLI/ACP 未提供余额接口" })[account.quota_status] || "未观测";
  }

  function models() {
    const result = node("div", "model-list");
    const grouped = new Map();
    for (const item of Array.isArray(profileData?.models) ? profileData.models : []) {
      const values = grouped.get(item.backend) || [];
      values.push(item);
      grouped.set(item.backend, values);
    }
    if (!grouped.size) return node("div", "empty-state", "尚未获得模型目录；点击刷新读取 Provider 当前模型。");
    for (const [backend, rows] of grouped) {
      const block = node("section", "provider-block");
      const heading = node("div", "provider-heading");
      heading.appendChild(node("span", "", backend));
      heading.appendChild(node("span", "provider-note", `${rows.length} 个模型`));
      block.appendChild(heading);
      for (const item of rows) {
        const row = node("div", "model-row");
        const identity = node("div", "");
        identity.appendChild(node("div", "model-name", item.display_name || item.model_id));
        identity.appendChild(node("div", "model-id", item.model_id));
        row.appendChild(identity);
        row.appendChild(modelStatus(item.available === true ? "当前可用" : (item.available === false ? "不可用" : "待确认"), item.available === true ? "is-available" : (item.available === false ? "is-unavailable" : "is-unknown")));
        row.appendChild(modelStatus(item.routable === true ? "可路由" : (item.routable === false ? "策略拒绝" : "待确认"), item.routable === true ? "is-routable" : (item.routable === false ? "is-unavailable" : "is-unknown")));
        const detail = node("div", "profile-value");
        detail.appendChild(chipList([referenceMultiplierLabel(item.reference_multiplier), ...(item.supported_efforts || [])]));
        row.appendChild(detail);
        block.appendChild(row);
      }
      result.appendChild(block);
    }
    return result;
  }

  function accounts() {
    const result = node("div", "account-list");
    const accounts = Array.isArray(profileData?.accounts) ? profileData.accounts : [];
    if (!accounts.length) return node("div", "empty-state", "尚未获得 Crew 账户状态；点击刷新重试。");
    for (const account of accounts) {
      const card = node("section", "account-card");
      card.appendChild(node("div", "account-title", account.display_name || account.backend));
      const facts = node("div", "account-grid");
      const values = [
        ["可用性", localizedAvailability(account.availability)],
        ["CLI / SDK 版本", account.version],
        ["认证状态", localizedAuthStatus(account.auth_status)],
        ["模型目录", localizedCatalogStatus(account.model_catalog_status)],
        ["最近成功模型", account.last_successful_model],
        ["账户额度", localizedQuota(account)],
      ];
      for (const [label, value] of values) {
        const fact = node("div", "account-fact");
        fact.appendChild(node("div", "account-fact-label", label));
        fact.appendChild(node("div", "account-fact-value", valueText(value)));
        facts.appendChild(fact);
      }
      card.appendChild(facts);
      result.appendChild(card);
    }
    return result;
  }

  function render() {
    panel.dataset.activeTab = activeTab;
    for (const tab of tabs) {
      const selected = tab.dataset.tab === activeTab;
      tab.classList.toggle("is-active", selected);
      tab.setAttribute("aria-selected", selected ? "true" : "false");
    }
    content.replaceChildren();
    if (syncError) content.appendChild(node("div", "sync-error", syncError));
    if (!profileData) {
      content.appendChild(node("div", "empty-state", "暂无运行配置快照。"));
      return;
    }
    if (activeTab === "models") content.appendChild(models());
    else if (activeTab === "accounts") content.appendChild(accounts());
    else content.appendChild(overview());
  }

  function applyProfile(nextProfile) {
    profileData = nextProfile;
    if (profileData?.refresh_mode === "live") {
      setSync("idle", `已同步 · ${valueText(profileData.observed_at)}`);
      render();
      return;
    }
    setSync("idle", "已加载生效配置，正在读取实时模型与账户状态…");
    render();
    void loadProfile(true);
  }

  async function loadProfile(refreshProfile) {
    setSync("syncing", refreshProfile ? "正在刷新 Provider 模型与账户状态…" : "正在读取当前生效配置…");
    syncError = "";
    render();
    try {
      const result = await request("tools/call", {
        name: "voyager_overview",
        arguments: { limit: 5, include_profile: true, refresh_profile: Boolean(refreshProfile) },
      });
      const payload = result?.structuredContent || result || {};
      if (!payload.runtime_profile) throw new Error("MCP 未返回运行配置快照");
      applyProfile(payload.runtime_profile);
    } catch (error) {
      syncError = `同步失败，保留最近状态：${error?.message || "未知错误"}`;
      setSync("error", "同步失败，保留最近状态");
      render();
    }
  }

  for (const tab of tabs) tab.addEventListener("click", () => { activeTab = tab.dataset.tab || "overview"; render(); });
  refreshButton.addEventListener("click", () => { void loadProfile(true); });

  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;
    const message = event.data || {};
    if (message.id && pendingRequests.has(message.id)) {
      const pending = pendingRequests.get(message.id);
      pendingRequests.delete(message.id);
      clearTimeout(pending.timeoutId);
      if (message.error) pending.reject(message.error); else pending.resolve(message.result);
      return;
    }
    if (message.method === "ui/notifications/tool-result") {
      const payload = message.params?.structuredContent || message.params || {};
      if (payload.runtime_profile) {
        applyProfile(payload.runtime_profile);
      } else {
        void loadProfile(false);
      }
    }
  }, { passive: true });

  request("ui/initialize", {
    appInfo: { name: "tp-voyager-runtime-profile", version: "1.0.9" },
    appCapabilities: {}, protocolVersion: "2026-01-26",
  }, 2500).then(() => notify("ui/notifications/initialized", {})).catch(() => {});
})();
</script>
</body>
</html>'''
