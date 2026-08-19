"""Self-contained MCP Apps UI for TP-Voyager Agent observability.

The resource has no external network dependencies.  It renders only structured
server output and keeps presentation state inside the active iframe.  Live
refresh is explicitly scoped to an open panel and calls the read-only
``render_voyager_panel`` tool through the MCP Apps ``tools/call`` bridge.
"""

from __future__ import annotations


VOYAGER_PANEL_URI = "ui://tp-voyager/agent-panel/v1.html"
VOYAGER_PANEL_MIME_TYPE = "text/html;profile=mcp-app"


def render_voyager_panel_html() -> str:
    return r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>TP-Voyager Agent</title>
<style>
  :root {
    color-scheme: dark light;
    font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --bg: color-mix(in srgb, Canvas 96%, transparent);
    --surface: color-mix(in srgb, CanvasText 5%, Canvas 95%);
    --muted: color-mix(in srgb, CanvasText 58%, Canvas 42%);
    --line: color-mix(in srgb, CanvasText 14%, transparent);
    --ok: #2fbf71;
    --active: #37c978;
    --starting: #4f8cff;
    --wait: #e3ae38;
    --bad: #ef5350;
    --idle: #8a9099;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 8px; background: transparent; color: CanvasText; }
  #panel {
    --state-color: var(--idle);
    border: 1px solid color-mix(in srgb, var(--state-color) 68%, transparent);
    border-left-width: 3px;
    border-radius: 12px;
    background: var(--bg);
    overflow: hidden;
    transition: border-color .2s ease, box-shadow .2s ease;
  }
  #panel[data-state="queued"] { --state-color: var(--idle); }
  #panel[data-state="connecting"] { --state-color: var(--starting); }
  #panel[data-state="running"] { --state-color: var(--active); }
  #panel[data-state="observing"] { --state-color: var(--active); }
  #panel[data-state="completed"] { --state-color: var(--ok); }
  #panel[data-state="failed"] { --state-color: var(--bad); }
  #panel[data-state="cancelled"] { --state-color: var(--idle); }
  #panel[data-state="lost"] { --state-color: var(--bad); }
  #panel[data-state="orphaned"] { --state-color: var(--bad); }
  #panel[data-state="running"], #panel[data-state="observing"] {
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--state-color) 15%, transparent),
                0 0 18px color-mix(in srgb, var(--state-color) 10%, transparent);
  }
  .top { display: flex; gap: 10px; align-items: center; padding: 11px 12px 9px; }
  .status-dot {
    width: 10px; height: 10px; min-width: 10px; border-radius: 50%;
    background: var(--state-color); box-shadow: 0 0 0 3px color-mix(in srgb, var(--state-color) 18%, transparent);
  }
  #panel[data-state="running"] .status-dot,
  #panel[data-state="observing"] .status-dot { animation: pulse 1.45s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: .55; transform: scale(.92); } 50% { opacity: 1; transform: scale(1.12); } }
  .identity { min-width: 0; flex: 1; }
  .title-row { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
  .title { font-weight: 720; font-size: 13.5px; letter-spacing: .01em; }
  .state { color: var(--state-color); font-size: 12px; font-weight: 650; text-transform: lowercase; }
  .meta { margin-top: 3px; font-size: 11.5px; color: var(--muted); overflow-wrap: anywhere; }
  button {
    appearance: none; border: 1px solid var(--line); border-radius: 8px; background: var(--surface);
    color: CanvasText; padding: 6px 9px; font: inherit; font-size: 11.5px; cursor: pointer;
  }
  button:disabled { opacity: .45; cursor: default; }
  .summary { border-top: 1px solid var(--line); padding: 9px 12px; font-size: 12px; line-height: 1.45; white-space: pre-wrap; }
  .empty { color: var(--muted); }
  .details { border-top: 1px solid var(--line); padding: 0 12px 9px; }
  details { border-bottom: 1px solid var(--line); padding: 7px 0; }
  details:last-child { border-bottom: 0; }
  summary { cursor: pointer; font-size: 11.5px; font-weight: 650; color: color-mix(in srgb, CanvasText 82%, Canvas 18%); }
  .list { margin-top: 7px; display: grid; gap: 6px; max-height: 250px; overflow: auto; }
  .row { border-left: 2px solid var(--line); padding: 3px 0 3px 8px; font-size: 11.5px; line-height: 1.4; overflow-wrap: anywhere; }
  .row .label { color: var(--muted); font-size: 10.5px; margin-bottom: 2px; }
  .file { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  .kv { display: grid; grid-template-columns: minmax(90px,auto) 1fr; gap: 4px 10px; font-size: 11.5px; }
  .kv > :nth-child(odd) { color: var(--muted); }
  .error { color: var(--bad); }
  .foot { display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 10px; padding: 0 12px 9px; }
</style>
</head>
<body>
  <section id="panel" data-state="queued" aria-live="polite">
    <div class="top">
      <span class="status-dot" aria-hidden="true"></span>
      <div class="identity">
        <div class="title-row"><span class="title">TP-Voyager Agent</span><span class="state" id="state">waiting</span></div>
        <div class="meta" id="meta">Waiting for task data…</div>
      </div>
      <button id="refresh" type="button">Refresh</button>
    </div>
    <div class="summary empty" id="summary">No Agent data yet.</div>
    <div class="details" id="details"></div>
    <div class="foot"><span id="stamp"></span><span>current conversation panel</span></div>
  </section>
<script>
(() => {
  const panel = document.getElementById("panel");
  const stateEl = document.getElementById("state");
  const metaEl = document.getElementById("meta");
  const summaryEl = document.getElementById("summary");
  const detailsEl = document.getElementById("details");
  const refreshButton = document.getElementById("refresh");
  const stampEl = document.getElementById("stamp");
  const pendingRequests = new Map();
  let nextRequestId = 1;
  let latestToolInput = null;
  let latestData = null;
  let refreshTimer = null;
  let refreshing = false;

  const terminalStates = new Set(["completed", "failed", "cancelled", "lost", "orphaned"]);

  function request(method, params) {
    const id = nextRequestId++;
    window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    return new Promise((resolve, reject) => pendingRequests.set(id, { resolve, reject }));
  }

  function notify(method, params) {
    window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
  }

  function node(tag, text, className) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined && text !== null) item.textContent = String(text);
    return item;
  }

  function stateLabel(value) {
    return ({
      queued: "queued", connecting: "starting", running: "running", observing: "running",
      completed: "completed", failed: "failed", cancelled: "cancelled", lost: "lost", orphaned: "orphaned"
    })[value] || value || "unknown";
  }

  function formatTime(value) {
    if (!value) return "";
    try { return new Date(Number(value) * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
    catch (_) { return ""; }
  }

  function section(title, rows, open) {
    const wrapper = document.createElement("details");
    wrapper.open = !!open;
    wrapper.appendChild(node("summary", `${title} (${rows.length})`));
    const list = node("div", null, "list");
    if (!rows.length) list.appendChild(node("div", "No data.", "row empty"));
    for (const row of rows) list.appendChild(row);
    wrapper.appendChild(list);
    return wrapper;
  }

  function conversationRows(items) {
    return (items || []).map((item) => {
      const row = node("div", null, "row");
      row.appendChild(node("div", item.role === "reasoning_summary" ? "Analysis summary" : "Assistant", "label"));
      row.appendChild(node("div", item.content || ""));
      return row;
    });
  }

  function timelineRows(items) {
    return (items || []).map((item) => {
      const row = node("div", null, "row");
      const parts = [formatTime(item.timestamp), item.tool, item.action, item.path, item.status || item.kind].filter(Boolean);
      row.appendChild(node("div", parts.join(" · ") || "activity"));
      if (item.reason || item.summary) row.appendChild(node("div", item.reason || item.summary, "label"));
      return row;
    });
  }

  function fileRows(items) {
    return (items || []).map((item) => {
      const row = node("div", null, "row file");
      row.appendChild(node("div", `${item.action || item.kind || "file"}  ${item.path || ""}`));
      if (item.capture_state) row.appendChild(node("div", item.capture_state, "label"));
      return row;
    });
  }

  function usageRows(usage) {
    if (!usage || typeof usage !== "object") return [];
    const values = usage.usage && typeof usage.usage === "object" ? usage.usage : usage;
    const entries = Object.entries(values).filter(([, value]) => value !== null && value !== undefined && value !== "");
    if (!entries.length) return [];
    const row = node("div", null, "row");
    const grid = node("div", null, "kv");
    for (const [key, value] of entries) {
      grid.appendChild(node("div", key.replaceAll("_", " ")));
      grid.appendChild(node("div", value));
    }
    row.appendChild(grid);
    return [row];
  }

  function pickTask(data) {
    if (data?.task) return data.task;
    if (Array.isArray(data?.tasks) && data.tasks.length) return data.tasks[0];
    return null;
  }

  function render(data) {
    latestData = data && typeof data === "object" ? data : {};
    const task = pickTask(latestData);
    const state = task?.state || (latestData.ok === false ? "failed" : "queued");
    panel.dataset.state = state;
    stateEl.textContent = stateLabel(state);
    const meta = [task?.crew, task?.model, task?.task_id].filter(Boolean).join(" · ");
    metaEl.textContent = meta || "No active Agent selected";

    const conversation = Array.isArray(latestData.conversation) ? latestData.conversation : [];
    const lastMessage = conversation.length ? conversation[conversation.length - 1]?.content : "";
    if (latestData.error?.message) {
      summaryEl.textContent = latestData.error.message;
      summaryEl.className = "summary error";
    } else if (lastMessage) {
      summaryEl.textContent = lastMessage;
      summaryEl.className = "summary";
    } else if (task) {
      summaryEl.textContent = task.active ? "Agent is active. Open details for live execution trace." : "Agent finished. Open details for trace and evidence.";
      summaryEl.className = "summary";
    } else {
      summaryEl.textContent = "No Agent data yet.";
      summaryEl.className = "summary empty";
    }

    detailsEl.replaceChildren();
    detailsEl.appendChild(section("Conversation", conversationRows(conversation), true));
    detailsEl.appendChild(section("Timeline", timelineRows(latestData.timeline), false));
    detailsEl.appendChild(section("Files", fileRows(latestData.files), false));
    detailsEl.appendChild(section("Usage", usageRows(latestData.usage), false));
    stampEl.textContent = task?.updated_at ? `updated ${formatTime(task.updated_at)}` : "";
    scheduleRefresh(state);
  }

  function scheduleRefresh(state) {
    if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
    if (!terminalStates.has(state) && pickTask(latestData)) {
      refreshTimer = setTimeout(refresh, 2200);
    }
  }

  async function refresh() {
    if (refreshing) return;
    const taskId = pickTask(latestData)?.task_id || latestToolInput?.task_id || "";
    refreshing = true;
    refreshButton.disabled = true;
    try {
      await bridgeReady;
      const next = await request("tools/call", {
        name: "render_voyager_panel",
        arguments: { task_id: taskId, limit: 200 },
      });
      if (next?.structuredContent) render(next.structuredContent);
    } catch (_) {
      // A host may not allow UI-originated tool calls.  Keep the last valid
      // server snapshot visible; manual model calls still work as fallback.
    } finally {
      refreshing = false;
      refreshButton.disabled = false;
    }
  }

  function handleToolResult(snapshot) {
    if (snapshot?.task_id && !snapshot?.task) {
      const taskId = String(snapshot.task_id);
      latestToolInput = { task_id: taskId };
      const state = String(snapshot.status || "connecting");
      render({
        ok: snapshot.ok !== false,
        schema: "tp-voyager.agent_panel/v1",
        mode: "dispatch",
        task: {
          task_id: taskId,
          crew: snapshot.crew || null,
          model: snapshot.model || null,
          state,
          active: !terminalStates.has(state),
          updated_at: Date.now() / 1000,
        },
        conversation: [],
        timeline: [{ kind: "agent_started", status: state, timestamp: Date.now() / 1000 }],
        files: [],
        usage: {},
        error: snapshot.ok === false ? { message: snapshot.detail || snapshot.reason_code || "Dispatch failed." } : null,
      });
      if (snapshot.ok !== false) setTimeout(refresh, 80);
      return;
    }
    render(snapshot || {});
  }

  refreshButton.addEventListener("click", refresh);

  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;
    const message = event.data;
    if (!message || message.jsonrpc !== "2.0") return;
    if (message.id !== undefined && pendingRequests.has(message.id)) {
      const pending = pendingRequests.get(message.id);
      pendingRequests.delete(message.id);
      if (message.error) pending.reject(message.error);
      else pending.resolve(message.result);
      return;
    }
    if (message.method === "ui/notifications/tool-input") {
      const input = message.params || {};
      latestToolInput = { task_id: String(input.task_id || "") };
      return;
    }
    if (message.method === "ui/notifications/tool-result") {
      handleToolResult(message.params?.structuredContent || message.params || {});
    }
  }, { passive: true });

  const bridgeReady = request("ui/initialize", {
    appInfo: { name: "tp-voyager-agent-panel", version: "1.0.9" },
    appCapabilities: {},
    protocolVersion: "2026-01-26",
  }).then(() => {
    notify("ui/notifications/initialized", {});
  }).catch(() => {
    // Some hosts may render the resource before exposing the full MCP Apps
    // bridge. The server snapshot/fallback text remain authoritative.
  });
})();
</script>
</body>
</html>'''.strip()
