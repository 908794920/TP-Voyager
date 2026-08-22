"""Self-contained MCP Apps UI for TP-Voyager Agent observability.

The resource has no external network dependencies. It renders only safe,
structured server projection data and keeps presentation state inside the
active iframe. UI refresh always calls the existing read-only
``render_voyager_panel`` tool; it never performs a lifecycle mutation.
"""

from __future__ import annotations

from agent_runtime.api.runtime_profile import (
    VOYAGER_RUNTIME_PROFILE_MIME_TYPE,
    VOYAGER_RUNTIME_PROFILE_URI,
    render_voyager_runtime_profile_html,
)


VOYAGER_PANEL_URI = "ui://tp-voyager/agent-panel/v1.html"
VOYAGER_PANEL_MIME_TYPE = "text/html;profile=mcp-app"


def render_voyager_panel_html() -> str:
    return r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>TP-Voyager 任务</title>
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
    --space-1: 8px;
    --space-2: 16px;
    --space-3: 24px;
    --space-4: 32px;
    --radius-sm: 8px;
    --radius-md: 12px;
    --radius-lg: 16px;
    --shadow-panel: 0 16px 40px rgba(0, 0, 0, .24);
  }
  * { box-sizing: border-box; }
  html, body { min-width: 0; max-width: 100%; }
  body {
    margin: 0;
    padding: var(--space-1);
    background: var(--bg-page);
    color: var(--text-primary);
  }
  .panel-shell {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 0;
    width: 100%;
    min-width: 0;
  }
  #panel.panel-main {
    --state-color: var(--text-muted);
    min-width: 0;
    overflow: hidden;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-lg);
    background: var(--bg-panel);
    box-shadow: var(--shadow-panel);
    transition: border-color .18s ease;
  }
  #panel[data-sync-state="syncing"] { --state-color: var(--blue); }
  #panel[data-task-state="queued"] { --state-color: var(--text-muted); }
  #panel[data-task-state="connecting"] { --state-color: var(--blue); }
  #panel[data-task-state="running"], #panel[data-task-state="observing"] { --state-color: var(--green); }
  #panel[data-task-state="completed"] { --state-color: var(--green); }
  #panel[data-task-state="failed"], #panel[data-task-state="lost"], #panel[data-task-state="orphaned"] { --state-color: var(--red); }
  #panel[data-task-state="cancelled"] { --state-color: var(--text-muted); }
  #panel[data-task-state="running"],
  #panel[data-task-state="observing"],
  #panel[data-task-state="completed"] { border-color: rgba(56, 201, 118, .28); }
  #panel[data-task-state="failed"],
  #panel[data-task-state="lost"],
  #panel[data-task-state="orphaned"] { border-color: rgba(255, 107, 107, .34); }
  .panel-header {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 14px 18px;
    border-bottom: 1px solid var(--border-subtle);
  }
  .header-main {
    min-width: 0;
    flex: 1;
    display: flex;
    align-items: flex-start;
    gap: 10px;
  }
  .status-dot {
    width: 9px;
    height: 9px;
    min-width: 9px;
    margin-top: 6px;
    border-radius: 999px;
    background: var(--state-color);
  }
  #panel[data-task-state="running"] > .panel-header .status-dot,
  #panel[data-task-state="observing"] > .panel-header .status-dot,
  #panel[data-sync-state="syncing"] > .panel-header .status-dot { animation: pulse 1.45s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: .55; } 50% { opacity: 1; } }
  .identity { min-width: 0; flex: 1; }
  .header-title-row {
    min-width: 0;
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
  }
  .title { font-weight: 760; font-size: 15px; letter-spacing: -.01em; }
  .status-badge {
    display: inline-flex;
    align-items: center;
    min-height: 22px;
    padding: 2px 8px;
    border: 1px solid color-mix(in srgb, var(--state-color) 42%, transparent);
    border-radius: 999px;
    background: color-mix(in srgb, var(--state-color) 9%, transparent);
    color: var(--state-color);
    font-size: 10.5px;
    font-weight: 700;
  }
  .header-meta {
    margin-top: 6px;
    display: flex;
    gap: 4px 12px;
    flex-wrap: wrap;
    color: var(--text-muted);
    font-size: 10.5px;
    overflow-wrap: anywhere;
  }
  .fact { min-width: 0; display: inline-flex; gap: 5px; }
  .fact-key { color: var(--text-muted); }
  .fact-value { color: var(--text-secondary); font-weight: 600; overflow-wrap: anywhere; }
  button {
    appearance: none;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    background: var(--bg-surface);
    color: var(--text-primary);
    font: inherit;
    cursor: pointer;
  }
  button:disabled { opacity: .45; cursor: default; }
  .refresh-button {
    flex: 0 0 auto;
    padding: 6px 10px;
    color: var(--text-secondary);
    font-size: 11px;
  }
  .refresh-button:hover { border-color: var(--border-strong); background: var(--bg-surface-hover); color: var(--text-primary); }
  .summary {
    padding: 14px 18px 16px;
    font-size: 11.5px;
    line-height: 1.55;
  }
  .summary-grid {
    display: grid;
    grid-template-columns: minmax(190px, 220px) minmax(0, 1fr);
    gap: 10px;
  }
  .result-part {
    min-width: 0;
    padding: 10px 12px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    background: var(--bg-surface);
  }
  .summary-support { grid-column: 1 / -1; }
  .result-title { margin-bottom: 4px; color: var(--text-secondary); font-size: 10.5px; font-weight: 700; }
  .result-text { color: var(--text-primary); white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
  .result-list { margin: 4px 0 0; padding-left: 17px; color: var(--text-secondary); }
  .result-list li + li { margin-top: 3px; }
  .evidence-list { list-style: none; padding-left: 0; display: grid; gap: 5px; }
  .evidence-item {
    min-width: 0;
    display: grid;
    grid-template-columns: 8px auto minmax(0, 1fr);
    gap: 6px;
    align-items: start;
    color: var(--text-secondary);
  }
  .evidence-status-dot { width: 7px; height: 7px; margin-top: 5px; border-radius: 999px; background: var(--text-muted); }
  .evidence-status-text { color: var(--text-muted); font-size: 9.5px; font-weight: 700; white-space: nowrap; }
  .evidence-text { min-width: 0; overflow-wrap: anywhere; word-break: break-word; }
  .evidence-status-completed .evidence-status-dot { background: var(--green); }
  .evidence-status-running .evidence-status-dot { background: var(--blue); }
  .evidence-status-failed .evidence-status-dot { background: var(--red); }
  .evidence-status-unknown .evidence-status-dot { background: var(--text-muted); }
  .evidence-status-completed .evidence-status-text { color: var(--green); }
  .evidence-status-running .evidence-status-text { color: var(--blue); }
  .evidence-status-failed .evidence-status-text { color: var(--red); }
  .empty { color: var(--text-muted); }
  .error { color: var(--red); }
  .usage-overview {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: var(--space-1);
    margin-top: 10px;
  }
  .summary-grid > .usage-overview { margin-top: 0; }
  .usage-card {
    min-width: 0;
    padding: 9px 10px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    background: var(--bg-surface-raised);
  }
  .usage-card-label { min-width: 0; color: var(--text-muted); font-size: 9.5px; font-weight: 650; overflow-wrap: anywhere; }
  .usage-card-value { min-width: 0; margin-top: 3px; color: var(--text-primary); font-size: 13px; font-weight: 740; overflow-wrap: normal; word-break: normal; white-space: nowrap; }
  .usage-card-token .usage-card-value { color: var(--blue); }
  .usage-card-credit .usage-card-value { color: var(--purple); }
  .usage-card-unknown .usage-card-value { color: var(--text-muted); }
  .details { min-width: 0; border-top: 1px solid var(--border-subtle); }
  .details:empty { display: none; }
  .workbench { display: flex; gap: var(--space-2); align-items: stretch; min-width: 0; padding: 14px 18px 16px; }
  @supports (display: grid) {
    .workbench {
      display: grid;
      grid-template-columns: minmax(220px, 270px) minmax(0, 1fr);
    }
    .wb-nav { width: auto; flex-basis: auto; }
  }
  .wb-nav {
    width: 250px;
    min-width: 0;
    flex: 0 0 250px;
    max-height: 390px;
    overflow-y: auto;
    display: grid;
    gap: var(--space-1);
    align-content: start;
    padding-right: 2px;
  }
  .wb-task {
    --task-color: var(--text-muted);
    min-width: 0;
    display: grid;
    gap: 5px;
    padding: 9px 10px;
    text-align: left;
    border: 1px solid var(--border-subtle);
    border-left: 2px solid var(--task-color);
    border-radius: var(--radius-md);
    background: var(--bg-surface);
    color: var(--text-primary);
  }
  .wb-task:hover { background: var(--bg-surface-hover); border-color: var(--border-strong); border-left-color: var(--task-color); }
  .wb-task.active { background: var(--green-soft); border-color: rgba(56, 201, 118, .36); border-left-color: var(--green); border-left-width: 3px; }
  .wb-task[data-task-state="running"], .wb-task[data-task-state="observing"] { --task-color: var(--green); }
  .wb-task[data-task-state="connecting"] { --task-color: var(--blue); }
  .wb-task[data-task-state="queued"] { --task-color: #697789; }
  .wb-task[data-task-state="completed"] { --task-color: var(--green); }
  .wb-task[data-task-state="failed"], .wb-task[data-task-state="lost"], .wb-task[data-task-state="orphaned"] { --task-color: var(--red); }
  .wb-task[data-task-state="cancelled"] { --task-color: var(--text-muted); }
  .wb-task .status-dot { width: 7px; height: 7px; min-width: 7px; margin-top: 0; background: var(--task-color); }
  .wb-task-head { min-width: 0; display: flex; gap: 7px; align-items: center; }
  .wb-task-state { color: var(--text-secondary); font-size: 10.5px; font-weight: 700; }
  .wb-task-meta { min-width: 0; color: var(--text-muted); font-size: 10px; }
  .wb-task-identity { color: var(--text-primary); font-size: 11px; font-weight: 650; overflow-wrap: anywhere; word-break: break-word; }
  .wb-task-id, .wb-task-duration { color: var(--text-muted); font-size: 9.5px; overflow-wrap: anywhere; word-break: break-word; }
  .wb-task-failure { color: var(--red); font-size: 10px; overflow-wrap: anywhere; word-break: break-word; }
  .wb-main {
    min-width: 0;
    flex: 1;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    gap: 9px;
  }
  .wb-tabs {
    min-width: 0;
    display: flex;
    gap: 2px;
    flex-wrap: wrap;
    border-bottom: 1px solid var(--border-subtle);
  }
  .wb-tab {
    padding: 7px 9px 6px;
    border: 0;
    border-bottom: 2px solid transparent;
    border-radius: 6px 6px 0 0;
    background: transparent;
    color: var(--text-muted);
    font-size: 10.5px;
  }
  .wb-tab:hover { background: var(--bg-surface-hover); color: var(--text-secondary); }
  .wb-tab.active { border-bottom-color: var(--green); color: var(--text-primary); background: var(--green-soft); }
  .wb-body {
    min-width: 0;
    min-height: 150px;
    max-height: 390px;
    overflow-y: auto;
    overflow-x: hidden;
    font-size: 11.5px;
    line-height: 1.55;
    display: grid;
    gap: var(--space-1);
    align-content: start;
    padding: 11px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    background: var(--bg-surface);
  }
  .row {
    min-width: 0;
    padding: 8px 9px;
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-sm);
    background: var(--bg-panel);
    font-size: 11px;
    line-height: 1.5;
    overflow-wrap: anywhere;
    word-break: break-word;
  }
  .row .label { color: var(--text-muted); font-size: 10px; }
  .wb-body-summary .result-text {
    max-width: 100%;
    max-height: calc(4 * 1.55em);
    overflow: hidden;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 4;
  }
  .wb-body-summary .evidence-text,
  .wb-body-summary .result-list > li:not(.evidence-item) {
    max-width: 100%;
    max-height: calc(3 * 1.55em);
    overflow: hidden;
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
    overflow-wrap: anywhere;
    word-break: break-word;
  }
  .answer.markdown { min-width: 0; max-width: 100%; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; font-family: inherit; }
  .answer.markdown pre, .answer.markdown code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  .answer.markdown code { max-width: 100%; background: var(--bg-surface-raised); border-radius: 5px; padding: 1px 4px; overflow-wrap: anywhere; }
  .answer.markdown pre { max-width: 100%; margin: 6px 0; padding: 8px 10px; overflow-x: auto; border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); background: #0a0f15; white-space: pre; }
  .answer.markdown pre code { padding: 0; background: transparent; white-space: pre; overflow-wrap: normal; word-break: normal; }
  .answer.markdown blockquote { margin: 6px 0; padding: 2px 0 2px 10px; border-left: 2px solid var(--border-strong); color: var(--text-secondary); }
  .answer.markdown table { display: block; width: 100%; max-width: 100%; overflow-x: auto; border-collapse: collapse; margin: 6px 0; }
  .answer.markdown th, .answer.markdown td { border: 1px solid var(--border-subtle); padding: 4px 7px; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
  .answer.markdown th { background: var(--bg-surface-raised); }
  .answer.markdown a { color: var(--blue); overflow-wrap: anywhere; word-break: break-all; }
  .answer.markdown ul { margin: 3px 0; padding-left: 19px; }
  .answer.markdown em { font-style: italic; }
  .activity-timeline { min-width: 0; display: grid; }
  .activity-item { position: relative; min-width: 0; display: grid; grid-template-columns: 14px minmax(0, 1fr); gap: 9px; padding: 0 0 11px; }
  .activity-item:last-child { padding-bottom: 0; }
  .activity-marker { position: relative; width: 8px; height: 8px; margin-top: 5px; border: 2px solid var(--green); border-radius: 999px; background: var(--bg-surface); }
  .activity-marker::after { content: ""; position: absolute; top: 8px; left: 2px; width: 1px; height: calc(100% + 13px); background: var(--border-strong); }
  .activity-item:last-child .activity-marker::after { display: none; }
  .activity-content { min-width: 0; }
  .activity-head { min-width: 0; display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
  .activity-time { color: var(--text-muted); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 9.5px; }
  .activity-title { color: var(--text-primary); font-size: 10.5px; font-weight: 700; }
  .activity-description { margin-top: 2px; color: var(--text-secondary); font-size: 10.5px; overflow-wrap: anywhere; word-break: break-word; }
  .activity-meta { margin-top: 3px; color: var(--text-muted); font-size: 9.5px; overflow-wrap: anywhere; word-break: break-word; }
  .file-change-list { min-width: 0; display: grid; gap: 7px; }
  .file-change-item { min-width: 0; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 5px 9px; padding: 8px 9px; border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); background: var(--bg-panel); }
  .file-change-kind { color: var(--green); font-size: 9.5px; font-weight: 700; }
  .file-change-path { min-width: 0; color: var(--text-primary); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 10.5px; overflow-wrap: anywhere; word-break: break-all; }
  .file-change-summary { grid-column: 1 / -1; color: var(--text-muted); font-size: 9.5px; overflow-wrap: anywhere; word-break: break-word; }
  .usage-metrics { min-width: 0; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 7px; }
  .usage-metric { min-width: 0; padding: 8px 9px; border: 1px solid var(--border-subtle); border-radius: var(--radius-sm); background: var(--bg-panel); }
  .usage-metric-label { color: var(--text-muted); font-size: 9.5px; font-weight: 650; }
  .usage-metric-value { margin-top: 3px; color: var(--text-primary); font-size: 11.5px; font-weight: 700; overflow-wrap: anywhere; }
  .usage-derived { margin-left: 4px; color: var(--amber); font-size: 8.5px; font-weight: 700; }
  details { border-bottom: 1px solid var(--border-subtle); padding: 7px 0; }
  details:last-child { border-bottom: 0; }
  summary { cursor: pointer; color: var(--text-secondary); font-size: 11px; font-weight: 650; }
  .list { margin-top: 7px; display: grid; gap: 6px; }
  .foot {
    display: flex;
    justify-content: space-between;
    gap: var(--space-1);
    padding: 9px 18px 11px;
    border-top: 1px solid var(--border-subtle);
    color: var(--text-muted);
    font-size: 9.5px;
  }
  @media (max-width: 639px) {
    body { padding: 0; overflow-x: hidden; }
    .panel-shell { grid-template-columns: minmax(0, 1fr); gap: 0; }
    #panel.panel-main { border-radius: 0; }
    .summary-grid { grid-template-columns: minmax(0, 1fr); }
    .summary-support { grid-column: auto; }
    .usage-overview { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .workbench { flex-direction: column; grid-template-columns: minmax(0, 1fr); }
    .wb-nav { width: auto; flex-basis: auto; max-height: 190px; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); }
  }
  @media (max-width: 519px) {
    .usage-overview, .usage-metrics { grid-template-columns: minmax(0, 1fr); }
  }
  @media (max-width: 460px) {
    .panel-header, .summary, .workbench { padding-left: 12px; padding-right: 12px; }
    .panel-header { align-items: flex-start; }
    .refresh-button { padding: 5px 8px; }
  }
</style>
</head>
<body>
  <div class="panel-shell">
    <section id="panel" class="panel-main" data-task-state="unknown" data-sync-state="syncing" aria-live="polite">
      <header class="panel-header top">
        <div class="header-main">
          <span class="status-dot" aria-hidden="true"></span>
          <div class="identity">
            <div class="header-title-row title-row"><span class="title">TP-Voyager 任务</span><span class="status-badge state" id="state">正在同步</span></div>
            <div class="header-meta meta" id="meta">等待任务数据…</div>
          </div>
        </div>
        <button class="refresh-button" id="refresh" type="button">刷新</button>
      </header>
      <div class="summary empty" id="summary">正在同步最新任务状态…</div>
      <div class="details" id="details"></div>
      <footer class="foot"><span id="stamp"></span><span>当前会话面板</span></footer>
    </section>
  </div>
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
  let latestGroupItems = [];
  let refreshTimer = null;
  // v1.0.9: iframe-memory presentation state only. Never persisted.
  const PanelUIStateStore = new Map();
  let refreshing = false;
  let taskState = "unknown";
  let syncState = "syncing";
  let lastSyncAt = 0;
  let hasVerifiedSnapshot = false;
  let lastRenderedRevision = "";
  let lastRenderedUsageRevision = "";
  const REQUEST_TIMEOUT_MS = 5000;
  const BRIDGE_INIT_TIMEOUT_MS = 2500;
  const RESUME_SYNC_TTL_MS = 5000;
  const WORKBENCH_BOTTOM_THRESHOLD_PX = 48;

  const terminalStates = new Set(["completed", "failed", "cancelled", "lost", "orphaned"]);

  function request(method, params, timeoutMs = REQUEST_TIMEOUT_MS) {
    const id = nextRequestId++;
    return new Promise((resolve, reject) => {
      const boundedTimeout = Math.max(250, Number(timeoutMs) || REQUEST_TIMEOUT_MS);
      const timeoutId = setTimeout(() => {
        if (!pendingRequests.has(id)) return;
        pendingRequests.delete(id);
        reject(new Error(`MCP bridge request timed out: ${method}`));
      }, boundedTimeout);
      pendingRequests.set(id, { resolve, reject, timeoutId });
      window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
    });
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
      syncing: "正在同步", queued: "排队中", connecting: "正在连接", starting: "启动中",
      running: "执行中", observing: "执行中", pending: "等待中", requested: "已请求",
      completed: "已完成", passed: "已通过", failed: "失败", cancelled: "已取消",
      lost: "连接丢失", orphaned: "连接孤立", unknown: "未知", unavailable: "不可用",
      tool_activity: "工具活动", file_change: "文件变更", assistant_message: "回答", reasoning_summary: "摘要"
    })[value] || value || "未知";
  }

  function setTaskState(value) {
    taskState = String(value || "unknown");
    panel.dataset.taskState = taskState;
    stateEl.textContent = stateLabel(taskState);
    return taskState;
  }

  function setSyncState(value) {
    syncState = String(value || "idle");
    panel.dataset.syncState = syncState;
    return syncState;
  }

  function isRenderableSnapshot(data) {
    if (!data || typeof data !== "object") return false;
    if (data.mode === "group" && Array.isArray(data.tasks)) return true;
    return !!(data.task && typeof data.task === "object" && String(data.task.task_id || "").trim());
  }

  function actionLabel(value) {
    const labels = {
      read: "读取", search: "搜索", glob: "匹配", create: "创建", write: "写入",
      modify: "修改", update: "更新", delete: "删除", remove: "删除", add: "新增"
    };
    const raw = String(value || "").trim();
    return labels[raw.toLowerCase()] || raw;
  }

  function toolLabel(value) {
    const labels = {
      read: "读取", search: "搜索", glob: "匹配", grep: "检索", edit: "编辑", write: "写入",
      bash: "命令", shell: "命令", tool: "工具"
    };
    const raw = String(value || "").trim();
    return labels[raw.toLowerCase()] || raw;
  }

  function phaseLabel(value) {
    const labels = {
      workspace_snapshot: "工作区快照",
      dispatch: "任务派发",
      execution: "执行",
      verification: "验证",
      result: "结果整理",
    };
    const raw = String(value || "").trim();
    return labels[raw] || raw.replaceAll("_", " ");
  }

  function formatTime(value) {
    if (!value) return "";
    try { return new Date(Number(value) * 1000).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
    catch (_) { return ""; }
  }

  function formatDuration(value) {
    const seconds = Number(value);
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    if (seconds < 60) return `${Math.max(1, Math.round(seconds))} 秒`;
    const minutes = Math.floor(seconds / 60);
    const remain = Math.round(seconds % 60);
    return remain ? `${minutes} 分 ${remain} 秒` : `${minutes} 分`;
  }

  function renderIdentity(task, stateOverride = "") {
    metaEl.replaceChildren();
    const state = stateOverride || task?.state || "";
    const facts = [
      ["状态", state ? stateLabel(state) : null],
      ["任务", task?.task_id],
      ["耗时", formatDuration(task?.duration_seconds)],
    ];
    for (const [key, value] of facts) {
      if (!value) continue;
      const fact = node("span", null, "fact");
      fact.appendChild(node("span", key, "fact-key"));
      fact.appendChild(node("span", value, "fact-value"));
      metaEl.appendChild(fact);
    }
    if (!metaEl.childNodes.length) metaEl.appendChild(node("span", "等待任务数据…"));
  }

  function evidenceSemanticStatus(value) {
    const state = String(value || "").toLowerCase();
    if (["completed", "passed"].includes(state)) return "completed";
    if (["running", "observing", "connecting", "starting"].includes(state)) return "running";
    if (["failed", "lost", "orphaned"].includes(state)) return "failed";
    return "unknown";
  }

  function evidenceStatusLabel(value) {
    return ({ completed: "已完成", running: "执行中", failed: "异常", unknown: "未知" })[value] || "未知";
  }

  function evidenceList(values, statusHint = "unknown") {
    const list = node("ul", null, "result-list evidence-list");
    for (let index = 0; index < values.length; index += 1) {
      const semantic = evidenceSemanticStatus(Array.isArray(statusHint) ? statusHint[index] : statusHint);
      const label = evidenceStatusLabel(semantic);
      const item = node("li", null, `evidence-item evidence-status-${semantic}`);
      item.dataset.evidenceStatus = semantic;
      item.setAttribute("data-evidence-status", semantic);
      item.setAttribute("aria-label", semantic);
      const dot = node("span", null, "evidence-status-dot");
      dot.setAttribute("aria-label", label);
      item.appendChild(dot);
      item.appendChild(node("span", `${label} `, "evidence-status-text"));
      item.appendChild(node("span", values[index], "evidence-text"));
      list.appendChild(item);
    }
    return list;
  }

  function resultPart(title, value, container = summaryEl, evidenceStatus = "unknown") {
    if (value === null || value === undefined || value === "") return;
    if (Array.isArray(value) && !value.length) return;
    const region = title === "结论"
      ? "summary-conclusion"
      : (title === "关键依据" ? "summary-evidence" : "summary-support");
    const wrapper = node("div", null, `result-part ${region}`);
    wrapper.appendChild(node("div", title, "result-title"));
    if (Array.isArray(value)) {
      if (title === "关键依据") wrapper.appendChild(evidenceList(value, evidenceStatus));
      else {
        const list = node("ul", null, "result-list");
        for (const item of value) list.appendChild(node("li", item));
        wrapper.appendChild(list);
      }
    } else {
      wrapper.appendChild(node("div", value, "result-text"));
    }
    container.appendChild(wrapper);
  }

  function latestActivity(data) {
    const item = data?.latest_activity || (Array.isArray(data?.timeline) && data.timeline.length ? data.timeline[data.timeline.length - 1] : null);
    if (!item) return "";
    const count = Number(item.count || 1);
    const parts = [toolLabel(item.tool), actionLabel(item.action), item.path, item.phase ? phaseLabel(item.phase) : null, stateLabel(item.status || item.kind)].filter(Boolean);
    if (count > 1) parts.push(`重复 ${count} 次`);
    return parts.join(" · ");
  }

  function renderResultSummary(task, state, data) {
    summaryEl.replaceChildren();
    summaryEl.className = "summary";
    if (state === "syncing") {
      summaryEl.className = "summary empty";
      summaryEl.appendChild(node("div", "正在同步最新任务状态…"));
      return;
    }
    const grid = node("div", null, "summary-grid");
    summaryEl.appendChild(grid);
    if (data.error?.message) {
      summaryEl.className = "summary error";
      resultPart("结论", "任务执行失败。", grid);
      const details = [];
      if (data.error.stage) details.push(`阶段：${phaseLabel(data.error.stage)}`);
      details.push(`原因：${data.error.message}`);
      resultPart("风险", details, grid);
      return;
    }
    const card = data?.result_card && typeof data.result_card === "object" ? data.result_card : null;
    if (card) {
      resultPart("结论", card.conclusion || "任务已结束；完整结论见回答。", grid);
      resultPart("关键依据", Array.isArray(card.key_evidence) ? card.key_evidence : [], grid, state);
      resultPart("风险", Array.isArray(card.risks) ? card.risks : [], grid);
      resultPart("下一步", Array.isArray(card.next_steps) ? card.next_steps : [], grid);
      return;
    }
    if (task?.active) {
      resultPart("结论", "任务正在执行。", grid);
      const activity = latestActivity(data);
      if (activity) resultPart("当前安全活动", activity, grid);
      return;
    }
    if (state === "completed") {
      resultPart("结论", "任务已完成。完整回答可在下方展开查看。", grid);
      return;
    }
    resultPart("结论", "暂无可展示的结构化结果。", grid);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  // Only http:, https: and mailto: survive as link destinations.  Everything
  // else is rendered as plain label text, so active URLs are dropped.
  function safeLinkHref(href) {
    const raw = String(href || "").trim();
    if (/^(https?:|mailto:)/i.test(raw)) return raw;
    return "";
  }

  // Safe minimal markdown renderer. It only emits known tags and escapes all
  // input first, so injected active markup can never survive as executable.
  function renderSafeMarkdown(value) {
    let text = escapeHtml(value);
    // Fenced code blocks are rendered verbatim before other inline rules.
    text = text.replace(/```([\s\S]*?)```/g, (_, code) => `<pre><code>${code.trim()}</code></pre>`);
    // Headings.
    text = text.replace(/^### (.*)$/gm, "<h3>$1</h3>");
    text = text.replace(/^## (.*)$/gm, "<h2>$1</h2>");
    text = text.replace(/^# (.*)$/gm, "<h1>$1</h1>");
    // Blockquote: ">" was already escaped by escapeHtml to "&gt;".
    text = text.replace(/^&gt; (.*)$/gm, "<blockquote>$1</blockquote>");
    // Tables: consecutive "| a | b |" lines become one <table>.
    text = text.replace(/((?:^\|.*\|[ \t]*$[\r\n]*)+)/gm, (block) => {
      const lines = block.trim().split(/\r?\n/).filter((line) => line.trim() !== "");
      const isSeparator = (line) => /^\|[\s:|-]+\|$/.test(line.trim());
      const cellize = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
      const buildRow = (cells) => `<tr>${cells.map((cell) => `<td>${cell}</td>`).join("")}</tr>`;
      const header = lines[0];
      const body = lines.slice(1).filter((line) => !isSeparator(line));
      let out = `<table><thead>${buildRow(cellize(header))}</thead>`;
      if (body.length) out += `<tbody>${body.map((line) => buildRow(cellize(line))).join("")}</tbody>`;
      return out + "</table>";
    });
    // Inline: bold before italic so "*" is not consumed twice; then safe links
    // and inline code.
    text = text.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    text = text.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
    text = text.replace(/\[([^\]]+)\]\(([^)]*)\)/g, (_, label, href) => {
      const safe = safeLinkHref(href);
      return safe ? `<a href="${safe}" rel="noopener noreferrer">${label}</a>` : label;
    });
    text = text.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    // Unordered lists.
    text = text.replace(/^[-*] (.*)$/gm, "<li>$1</li>");
    text = text.replace(/(<li>[\s\S]*?<\/li>)/g, "<ul>$1</ul>");
    return text.replace(/\n/g, "<br>");
  }

  // Render markdown to nodes without ever assigning innerHTML: DOMParser does
  // not execute scripts, and renderSafeMarkdown already dropped dangerous tags.
  function markdownNodes(markdown) {
    const parsed = new DOMParser().parseFromString(renderSafeMarkdown(markdown), "text/html");
    return Array.from(parsed.body.childNodes);
  }

  function answerRows(data) {
    const rows = [];
    const full = typeof data?.full_answer === "string" ? data.full_answer : "";
    const conversation = Array.isArray(data?.conversation) ? data.conversation : [];
    if (full) {
      const row = node("div", null, "row");
      const content = document.createElement("div");
      content.append(...markdownNodes(full));
      content.className = "answer markdown";
      row.appendChild(content);
      rows.push(row);
      return rows;
    }
    for (const item of conversation) {
      if (!item?.content) continue;
      const row = node("div", null, "row");
      const content = document.createElement("div");
      content.append(...markdownNodes(String(item.content || "")));
      content.className = "answer markdown";
      row.appendChild(content);
      rows.push(row);
    }
    return rows;
  }

  function timelineRows(items) {
    const events = Array.isArray(items) ? items : [];
    if (!events.length) return [];
    const timeline = node("div", null, "activity-timeline");
    for (const item of events) {
      const row = node("div", null, "activity-item");
      row.appendChild(node("span", null, "activity-marker"));
      const content = node("div", null, "activity-content");
      const head = node("div", null, "activity-head");
      const time = formatTime(item.timestamp);
      if (time) head.appendChild(node("span", time, "activity-time"));
      const titleParts = [toolLabel(item.tool), actionLabel(item.action)].filter(Boolean);
      const fallbackTitle = item.phase
        ? phaseLabel(item.phase)
        : stateLabel(item.kind || item.status || "tool_activity");
      head.appendChild(node("span", titleParts.join(" · ") || fallbackTitle || "执行活动", "activity-title"));
      content.appendChild(head);
      const description = String(item.reason || item.summary || "").trim();
      if (description) content.appendChild(node("div", description, "activity-description"));
      const count = Number(item.count || 1);
      const meta = [
        item.path,
        item.phase ? phaseLabel(item.phase) : null,
        stateLabel(item.status || item.kind),
        count > 1 ? `×${count}` : null,
      ].filter(Boolean);
      if (meta.length) content.appendChild(node("div", meta.join(" · "), "activity-meta"));
      row.appendChild(content);
      timeline.appendChild(row);
    }
    return [timeline];
  }

  function fileRows(items) {
    const files = Array.isArray(items) ? items : [];
    if (!files.length) return [];
    const list = node("div", null, "file-change-list");
    for (const item of files) {
      const row = node("div", null, "file-change-item");
      row.appendChild(node("div", actionLabel(item.action) || stateLabel(item.kind) || "变更", "file-change-kind"));
      row.appendChild(node("div", item.path || "", "file-change-path"));
      const summary = String(item.summary || item.capture_state || "").trim();
      if (summary) row.appendChild(node("div", summary, "file-change-summary"));
      list.appendChild(row);
    }
    return [list];
  }

  function usageLabel(key) {
    return ({
      total_tokens: "总 Token",
      input_tokens: "输入 Token",
      cache_read_tokens: "缓存命中",
      cache_miss_tokens: "缓存未命中",
      cache_write_tokens: "缓存写入",
      output_tokens: "输出 Token",
      cache_hit_rate: "缓存命中率",
      credits: "本轮 Credit",
      session_credits: "会话累计 Credit",
    })[key] || key.replaceAll("_", " ");
  }

  function usageValue(value) {
    if (value === null || value === undefined || value === "") return "暂无数据";
    if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString() : String(Math.round(value * 10000) / 10000);
    if (typeof value === "boolean") return value ? "是" : "否";
    return String(value);
  }

  function usagePayload(evidence) {
    const source = evidence && typeof evidence === "object" ? evidence : {};
    const values = source.usage && typeof source.usage === "object" ? source.usage : source;
    return { evidence: source, values };
  }

  function usageRows(evidence) {
    const { values } = usagePayload(evidence);
    const derived = new Set(Array.isArray(values.derived_fields) ? values.derived_fields.map(String) : []);
    const entries = [
      ["total_tokens", values.total_tokens],
      ["input_tokens", values.input_tokens],
      ["cache_read_tokens", values.cache_read_tokens],
      ["cache_miss_tokens", values.cache_miss_tokens],
      ["cache_write_tokens", values.cache_write_tokens],
      ["output_tokens", values.output_tokens],
    ];
    const cacheRead = values.cache_read_tokens;
    const cacheMiss = values.cache_miss_tokens;
    const cacheWrite = values.cache_write_tokens;
    const completeCacheBreakdown = [cacheRead, cacheMiss, cacheWrite]
      .every((value) => typeof value === "number" && Number.isFinite(value) && value >= 0);
    const cacheInput = completeCacheBreakdown ? cacheRead + cacheMiss + cacheWrite : null;
    const hitRate = typeof cacheInput === "number" && cacheInput > 0
      ? `${Math.round((cacheRead / cacheInput) * 10000) / 100}%`
      : null;
    entries.push(["cache_hit_rate", hitRate]);
    entries.push(["credits", values.credits ?? values.credits_used]);
    entries.push(["session_credits", values.session_credits]);

    const metrics = node("div", null, "usage-metrics");
    for (const [key, value] of entries) {
      const metric = node("div", null, "usage-metric");
      const label = node("div", usageLabel(key), "usage-metric-label");
      if (derived.has(key) || (key === "cache_hit_rate" && value !== null)) {
        label.appendChild(node("span", "推导", "usage-derived"));
      }
      metric.appendChild(label);
      metric.appendChild(node("div", usageValue(value), "usage-metric-value"));
      metrics.appendChild(metric);
    }
    return [metrics];
  }

  function usageSummaryValues(evidence) {
    const { values } = usagePayload(evidence);
    return {
      credits: values.credits ?? values.credits_used ?? null,
      totalTokens: values.total_tokens ?? null,
    };
  }

  function usageOverviewCard(label, value, kind) {
    const unknown = value === null || value === undefined || value === "";
    const card = kind === "token"
      ? node("div", null, "usage-card usage-card-token")
      : node("div", null, "usage-card usage-card-credit");
    if (unknown) card.classList.add("usage-card-unknown");
    card.appendChild(node("div", `${label}：`, "usage-card-label"));
    card.appendChild(node("div", usageValue(value), "usage-card-value"));
    return card;
  }

  function renderUsageStrip(container, data) {
    const host = container.classList?.contains("summary-grid")
      ? container
      : (container.querySelector(".summary-grid") || container);
    const old = host.querySelector(".usage-overview");
    if (old) old.remove();
    const strip = node("div", null, "usage-overview");
    strip.dataset.role = "usage-summary";
    if (data?.mode === "group") {
      const groupUsage = data?.usage && typeof data.usage === "object" ? data.usage : {};
      const selectedId = String(panel.dataset.selectedTaskId || "");
      const selected = (Array.isArray(data?.tasks) ? data.tasks : []).find((item) => String(item?.task?.task_id || "") === selectedId) || data?.tasks?.[0];
      const selectedUsage = usageSummaryValues(selected?.usage);
      strip.appendChild(usageOverviewCard("子任务 Tokens", groupUsage.total_tokens, "token"));
      strip.appendChild(usageOverviewCard("子任务 Credits", groupUsage.credits, "credit"));
      strip.appendChild(usageOverviewCard("当前 Tokens", selectedUsage.totalTokens, "token"));
      strip.appendChild(usageOverviewCard("当前 Credits", selectedUsage.credits, "credit"));
    } else {
      const selectedUsage = usageSummaryValues(data?.usage);
      strip.appendChild(usageOverviewCard("Tokens", selectedUsage.totalTokens, "token"));
      strip.appendChild(usageOverviewCard("Credits", selectedUsage.credits, "credit"));
    }
    host.appendChild(strip);
  }

  function appendSection(title, rows, open) {
    if (!rows.length) return;
    const wrapper = document.createElement("details");
    wrapper.dataset.section = title;
    wrapper.open = !!open;
    wrapper.appendChild(node("summary", `${title} (${rows.length})`));
    const list = node("div", null, "list");
    for (const row of rows) list.appendChild(row);
    wrapper.appendChild(list);
    detailsEl.appendChild(wrapper);
  }

  function pickTask(data) {
    if (data?.task) return data.task;
    return null;
  }

  function selectorFromInput(input) {
    const taskIds = Array.isArray(input?.task_ids) ? input.task_ids.map((value) => String(value || "").trim()).filter(Boolean) : [];
    if (taskIds.length) return { task_ids: taskIds };
    const groupId = String(input?.presentation_group_id || "").trim();
    if (groupId) return { presentation_group_id: groupId };
    const taskId = String(input?.task_id || "").trim();
    return taskId ? { task_ids: [taskId] } : null;
  }

  function panelStateKey(data = latestData) {
    const group = String(data?.presentation_group_id || "").trim();
    const task = String(
      pickTask(data)?.task_id
        || (data?.mode === "group" && Array.isArray(data?.task_ids) ? data.task_ids[0] : "")
        || ""
    ).trim();
    return `${group || "single"}/${task || "none"}/panel`;
  }

  function normalizePanelGroup(data) {
    if (!data || typeof data !== "object" || data.mode === "group") return data || {};
    const task = data.task && typeof data.task === "object" ? data.task : null;
    const taskId = String(task?.task_id || "").trim();
    if (!taskId) return data;
    return {
      ...data,
      mode: "group",
      presentation_group_id: String(data.presentation_group_id || "").trim(),
      task_ids: [String(task.task_id)],
      tasks: [data],
    };
  }

  function collectExpandedDetails() {
    const expanded = [];
    for (const detail of detailsEl.querySelectorAll("details")) {
      if (detail.open) expanded.push(String(detail.querySelector("summary")?.textContent || "").trim());
    }
    return expanded;
  }

  function captureWorkbenchScrollState() {
    const body = detailsEl.querySelector(".wb-body");
    if (!body) return {};
    const scrollTop = Math.max(0, Number(body.scrollTop) || 0);
    const scrollHeight = Math.max(0, Number(body.scrollHeight) || 0);
    const clientHeight = Math.max(0, Number(body.clientHeight) || 0);
    const distanceFromBottom = Math.max(0, scrollHeight - clientHeight - scrollTop);
    return {
      workbenchScrollTop: scrollTop,
      workbenchScrollHeight: scrollHeight,
      workbenchClientHeight: clientHeight,
      workbenchPinnedToBottom: distanceFromBottom <= WORKBENCH_BOTTOM_THRESHOLD_PX,
    };
  }

  function shouldFollowWorkbenchBottom() {
    if (panel.dataset.activeTab !== "执行活动") return false;
    const selectedTaskId = String(panel.dataset.selectedTaskId || "");
    const active = latestGroupItems.find((item) => String(item?.task?.task_id || "") === selectedTaskId);
    return !!active?.task?.active;
  }

  function restoreWorkbenchScrollState(state) {
    const body = detailsEl.querySelector(".wb-body");
    if (!body) return;
    const pinned = state?.workbenchPinnedToBottom === true;
    if (pinned || (!state && shouldFollowWorkbenchBottom())) {
      body.scrollTop = body.scrollHeight;
      return;
    }
    if (Number.isFinite(Number(state?.workbenchScrollTop))) {
      body.scrollTop = Math.max(0, Number(state.workbenchScrollTop));
    }
  }

  function savePanelUIState() {
    const key = panelStateKey();
    PanelUIStateStore.set(key, {
      activeTab: panel.dataset.activeTab || "",
      selectedTaskId: panel.dataset.selectedTaskId || "",
      section: panel.dataset.section || "",
      scrollTop: document.documentElement.scrollTop || document.body.scrollTop || 0,
      ...captureWorkbenchScrollState(),
      expandedDetails: collectExpandedDetails(),
      timestamp: Date.now(),
    });
  }

  // v1.0.9: persist the current presentation state (tab/details/scroll)
  // right before a refresh or teardown. State lives only in iframe memory.
  function beforeRefresh() {
    savePanelUIState();
  }

  function restorePanelUIState() {
    const state = PanelUIStateStore.get(panelStateKey());
    if (!state) {
      restoreWorkbenchScrollState(null);
      return;
    }
    if (state.scrollTop !== undefined) window.scrollTo(0, state.scrollTop);
    if (state.activeTab) panel.dataset.activeTab = state.activeTab;
    if (state.selectedTaskId) panel.dataset.selectedTaskId = state.selectedTaskId;
    if (state.section) panel.dataset.section = state.section;
    if (Array.isArray(state.expandedDetails) && state.expandedDetails.length) {
      const wanted = new Set(state.expandedDetails);
      for (const detail of detailsEl.querySelectorAll("details")) {
        const label = String(detail.querySelector("summary")?.textContent || "").trim();
        if (wanted.has(label)) detail.open = true;
      }
    }
    restoreWorkbenchScrollState(state);
  }

  function snapshotRevision(data) {
    const items = data?.mode === "group" && Array.isArray(data?.tasks) ? data.tasks : [data];
    const signatures = items.map((item) => {
      const task = item?.task || {};
      const timeline = Array.isArray(item?.timeline) ? item.timeline : [];
      const conversation = Array.isArray(item?.conversation) ? item.conversation : [];
      const files = Array.isArray(item?.files) ? item.files : [];
      const lastTimeline = timeline[timeline.length - 1] || null;
      const tailEntry = conversation[conversation.length - 1] || null;
      const answer = String(item?.full_answer || "");
      const tailText = String(tailEntry?.content || "");
      return [
        String(task.task_id || ""), String(task.state || ""), !!task.active,
        Number(task.updated_at || 0), !!task.result_available,
        `${answer.length}:${answer.slice(-64)}`,
        conversation.length, Number(tailEntry?.timestamp || 0),
        `${tailText.length}:${tailText.slice(-64)}`,
        timeline.length, JSON.stringify(lastTimeline),
        files.length, JSON.stringify(files[files.length - 1] || null),
        JSON.stringify(item?.result_card || null),
        String(item?.error?.message || ""),
      ];
    });
    return JSON.stringify([
      String(data?.presentation_group_id || ""),
      Array.isArray(data?.task_ids) ? data.task_ids : [],
      signatures,
    ]);
  }

  function usageRevision(data) {
    if (data?.mode === "group") {
      return JSON.stringify([
        data?.usage || {},
        ...(Array.isArray(data?.tasks) ? data.tasks.map((item) => [String(item?.task?.task_id || ""), item?.usage || {}]) : []),
      ]);
    }
    return JSON.stringify(data?.usage || {});
  }

  function updateUsageOnly(data) {
    renderUsageStrip(summaryEl, data);
    if (data?.mode === "group") {
      latestGroupItems = Array.isArray(data?.tasks) ? data.tasks : [];
      if (panel.dataset.activeTab === "用量") {
        const selectedId = String(panel.dataset.selectedTaskId || "");
        const active = latestGroupItems.find((item) => String(item?.task?.task_id || "") === selectedId) || latestGroupItems[0];
        const body = detailsEl.querySelector(".wb-body");
        if (body) {
          const top = body.scrollTop;
          body.replaceChildren(...usageRows(active?.usage));
          body.scrollTop = top;
        }
      }
    } else {
      const section = detailsEl.querySelector('details[data-section="用量"] .list');
      if (section) section.replaceChildren(...usageRows(data?.usage));
    }
  }

  function currentSelector() {
    if (latestData?.mode === "group") {
      const groupId = String(latestData?.presentation_group_id || "").trim();
      if (groupId) return { presentation_group_id: groupId };
      const taskIds = Array.isArray(latestData?.task_ids) ? latestData.task_ids.map(String).filter(Boolean) : [];
      if (taskIds.length) return { task_ids: taskIds };
    }
    const currentTask = pickTask(latestData);
    if (currentTask?.task_id) return { task_ids: [String(currentTask.task_id)] };
    return selectorFromInput(latestToolInput);
  }

  function renderSuccessfulSyncStamp(data) {
    let updatedAt = 0;
    if (data?.mode === "group") {
      const items = Array.isArray(data?.tasks) ? data.tasks : [];
      const updated = items
        .map((item) => Number(item?.task?.updated_at || 0))
        .filter((value) => Number.isFinite(value) && value > 0);
      updatedAt = updated.length ? Math.max(...updated) : 0;
    } else {
      updatedAt = Number(pickTask(data)?.updated_at || 0);
      if (!Number.isFinite(updatedAt) || updatedAt <= 0) updatedAt = 0;
    }
    stampEl.textContent = updatedAt ? `更新于 ${formatTime(updatedAt)}` : "";
  }

  function renderSingle(data) {
    latestData = data && typeof data === "object" ? data : {};
    const task = pickTask(latestData);
    const state = task?.state || (latestData.ok === false ? "failed" : "queued");
    setTaskState(state);
    document.querySelector(".title").textContent = "TP-Voyager 任务";
    renderIdentity(task);
    renderResultSummary(task, state, latestData);
    renderUsageStrip(summaryEl, latestData);

    detailsEl.replaceChildren();
    appendSection("完整回答", answerRows(latestData), false);
    appendSection("执行活动", timelineRows(latestData.timeline), false);
    appendSection("文件变更", fileRows(latestData.files), false);
    appendSection("用量", usageRows(latestData.usage), false);
    renderSuccessfulSyncStamp(latestData);
    scheduleRefresh(state);
  }

  function groupState(items) {
    const states = items.map((item) => item?.task?.state).filter(Boolean);
    if (states.some((value) => value === "running" || value === "observing" || value === "connecting" || value === "queued")) return "running";
    if (states.some((value) => value === "failed" || value === "lost" || value === "orphaned")) return "failed";
    if (states.length && states.every((value) => value === "completed")) return "completed";
    if (states.length && states.every((value) => value === "cancelled")) return "cancelled";
    return states[0] || "queued";
  }

  function renderGroupIdentity(data, state) {
    metaEl.replaceChildren();
    const groupId = String(data?.presentation_group_id || "").trim();
    const ids = Array.isArray(data?.task_ids) ? data.task_ids : [];
    const facts = [
      ["状态", stateLabel(state)],
      ["并发组", groupId || "显式任务列表"],
      ["子任务", `${ids.length} 个`],
    ];
    for (const [key, value] of facts) {
      const fact = node("span", null, "fact");
      fact.appendChild(node("span", key, "fact-key"));
      fact.appendChild(node("span", value, "fact-value"));
      metaEl.appendChild(fact);
    }
  }

  // v1.0.9: concurrent workbench — left task navigation + right detail workspace.
  const GROUP_TABS = ["摘要", "完整回答", "执行活动", "文件变更", "用量"];

  function defaultTabForState(state) {
    const value = String(state || "");
    if (value === "running" || value === "observing" || value === "connecting" || value === "queued") return "执行活动";
    return "摘要";
  }

  function appendSummaryText(body, title, value) {
    if (value === null || value === undefined || value === "") return;
    const part = node("div", null, "result-part");
    part.appendChild(node("div", title, "result-title"));
    part.appendChild(node("div", value, "result-text"));
    body.appendChild(part);
  }

  function appendSummaryList(body, title, value, evidenceStatus = "unknown") {
    if (!Array.isArray(value) || !value.length) return;
    const part = node("div", null, "result-part");
    part.appendChild(node("div", title, "result-title"));
    if (title === "关键依据") part.appendChild(evidenceList(value, evidenceStatus));
    else {
      const list = node("ul", null, "result-list");
      for (const item of value) list.appendChild(node("li", item));
      part.appendChild(list);
    }
    body.appendChild(part);
  }

  function renderTabBody(item, tab) {
    const body = node("div", null, "wb-body");
    if (tab === "摘要") body.classList.add("wb-body-summary");
    const task = item?.task || {};
    const state = task.state || (item?.ok === false ? "failed" : "queued");
    if (tab === "摘要") {
      if (item?.error?.message) {
        appendSummaryText(body, "结论", "任务执行失败。");
        const details = [];
        if (item?.error?.stage) details.push(`阶段：${phaseLabel(item.error.stage)}`);
        details.push(`原因：${item.error.message}`);
        appendSummaryList(body, "风险", details);
      } else {
        const card = item?.result_card && typeof item.result_card === "object" ? item.result_card : null;
        const conclusion = card?.conclusion
          || (task.active ? "任务正在执行。"
            : (state === "completed" ? "任务已完成。" : "暂无可展示的结构化结果。"));
        appendSummaryText(body, "结论", conclusion);
        appendSummaryList(body, "关键依据", card?.key_evidence, state);
        appendSummaryList(body, "风险", card?.risks);
        appendSummaryList(body, "下一步", card?.next_steps);
        if (!card && task.active) {
          const activity = latestActivity(item);
          if (activity) appendSummaryText(body, "当前安全活动", activity);
        }
      }
      return body;
    }
    let rows = [];
    if (tab === "完整回答") rows = answerRows(item);
    else if (tab === "执行活动") rows = timelineRows(item?.timeline);
    else if (tab === "文件变更") rows = fileRows(item?.files);
    else if (tab === "用量") rows = usageRows(item?.usage);
    for (const row of rows) body.appendChild(row);
    if (!rows.length) body.appendChild(node("div", "暂无内容。", "empty"));
    return body;
  }

  function renderTaskNav(items, activeId) {
    const nav = node("nav", null, "wb-nav");
    for (const item of items) {
      const task = item?.task || {};
      const state = task.state || (item?.ok === false ? "failed" : "queued");
      const taskId = String(task.task_id || "");
      const entry = node("button", null, "wb-task");
      entry.type = "button";
      entry.dataset.taskState = state;
      if (taskId === activeId) entry.classList.add("active");
      const head = node("div", null, "wb-task-head");
      const dot = node("span", null, "status-dot");
      dot.setAttribute("aria-hidden", "true");
      head.appendChild(dot);
      head.appendChild(node("span", stateLabel(state), "wb-task-state"));
      entry.appendChild(head);
      const meta = node("div", null, "wb-task-meta");
      const taskIdentity = [task.crew, task.model].filter(Boolean).join(" / ");
      if (taskIdentity) meta.appendChild(node("span", taskIdentity, "wb-task-identity"));
      entry.appendChild(meta);
      if (taskId) entry.appendChild(node("div", `任务 ${taskId}`, "wb-task-id"));
      const duration = formatDuration(task.duration_seconds);
      if (duration) entry.appendChild(node("div", `耗时 ${duration}`, "wb-task-duration"));
      const failure = String(item?.error?.message || task.error_message || "").trim();
      if (failure) entry.appendChild(node("div", failure, "wb-task-failure"));
      entry.addEventListener("click", () => {
        panel.dataset.selectedTaskId = taskId;
        renderUsageStrip(summaryEl, latestData);
        if (!GROUP_TABS.includes(panel.dataset.activeTab)) {
          panel.dataset.activeTab = defaultTabForState(state);
        }
        renderGroupBody(true);
      });
      nav.appendChild(entry);
    }
    return nav;
  }

  function renderTaskDetail(item, activeTab) {
    const main = node("div", null, "wb-main");
    const tabbar = node("div", null, "wb-tabs");
    for (const label of GROUP_TABS) {
      const tab = node("button", null, "wb-tab");
      tab.type = "button";
      tab.dataset.tab = label;
      if (label === activeTab) tab.classList.add("active");
      tab.appendChild(node("span", label));
      tab.addEventListener("click", () => {
        panel.dataset.activeTab = label;
        renderGroupBody(true);
      });
      tabbar.appendChild(tab);
    }
    main.appendChild(tabbar);
    main.appendChild(renderTabBody(item, activeTab));
    return main;
  }

  function renderGroupBody(followLatest = false) {
    const items = latestGroupItems;
    const selectedTaskId = String(panel.dataset.selectedTaskId || "");
    let active = items.find((item) => String(item?.task?.task_id || "") === selectedTaskId);
    if (!active) active = items[0];
    const activeId = String(active?.task?.task_id || "");
    panel.dataset.selectedTaskId = activeId;
    let activeTab = panel.dataset.activeTab;
    if (!GROUP_TABS.includes(activeTab)) activeTab = defaultTabForState(active?.task?.state);
    panel.dataset.activeTab = activeTab;
    detailsEl.replaceChildren();
    const workbench = node("div", null, "workbench");
    workbench.appendChild(renderTaskNav(items, activeId));
    workbench.appendChild(renderTaskDetail(active, activeTab));
    detailsEl.appendChild(workbench);
    if (followLatest) restoreWorkbenchScrollState(null);
  }

  function renderGroup(data) {
    latestData = data && typeof data === "object" ? data : {};
    latestGroupItems = Array.isArray(latestData.tasks) ? latestData.tasks : [];
    const items = latestGroupItems;
    const state = latestData.ok === false ? "failed" : groupState(items);
    setTaskState(state);
    document.querySelector(".title").textContent = "TP-Voyager 并发任务组";
    renderGroupIdentity(latestData, state);

    summaryEl.replaceChildren();
    summaryEl.className = "summary";
    const grid = node("div", null, "summary-grid");
    summaryEl.appendChild(grid);
    const completed = items.filter((item) => item?.task?.state === "completed").length;
    const active = items.filter((item) => item?.task?.active).length;
    const failed = items.filter((item) => ["failed", "lost", "orphaned"].includes(item?.task?.state)).length;
    resultPart("关键依据", [`${completed} 个`, `${active} 个`, `${failed} 个`], grid, ["completed", "running", "failed"]);
    renderUsageStrip(grid, latestData);

    renderGroupBody();
    renderSuccessfulSyncStamp(latestData);
    scheduleRefresh(state);
  }

  function renderData(data) {
    if (!isRenderableSnapshot(data)) return false;
    beforeRefresh();
    const normalized = normalizePanelGroup(data);
    const revision = snapshotRevision(normalized);
    const nextUsageRevision = usageRevision(normalized);
    if (revision === lastRenderedRevision) {
      latestData = normalized;
      latestGroupItems = Array.isArray(normalized?.tasks) ? normalized.tasks : [];
      if (nextUsageRevision !== lastRenderedUsageRevision) updateUsageOnly(normalized);
      lastRenderedUsageRevision = nextUsageRevision;
      const state = normalized?.mode === "group"
        ? (normalized.ok === false ? "failed" : groupState(latestGroupItems))
        : (pickTask(normalized)?.state || (normalized?.ok === false ? "failed" : "queued"));
      setTaskState(state);
      setSyncState("idle");
      renderSuccessfulSyncStamp(normalized);
      hasVerifiedSnapshot = true;
      lastSyncAt = Date.now();
      scheduleRefresh(state);
      return true;
    }
    if (normalized?.mode === "group") renderGroup(normalized);
    else renderSingle(normalized);
    lastRenderedRevision = revision;
    lastRenderedUsageRevision = nextUsageRevision;
    setSyncState("idle");
    restorePanelUIState();
    hasVerifiedSnapshot = true;
    lastSyncAt = Date.now();
    return true;
  }

  function renderSyncing(selector, hintTask = null) {
    if (!selector) return;
    if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
    setSyncState("syncing");
    stampEl.textContent = "正在同步最新状态…";
    // Keep the last verified group content visible while the read-only
    // projection refreshes. Clearing details here causes the visible flash.
  }

  function scheduleRefresh(state) {
    if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
    if (!terminalStates.has(state) && currentSelector()) {
      refreshTimer = setTimeout(refresh, 2200);
    }
  }

  async function refresh() {
    if (refreshing) return;
    const selector = currentSelector();
    if (!selector) return;
    refreshing = true;
    lastSyncAt = Date.now();
    refreshButton.disabled = true;
    try {
      await bridgeReady;
      const next = await request("tools/call", {
        name: "render_voyager_panel",
        arguments: { ...selector, limit: 200 },
      });
      if (isRenderableSnapshot(next?.structuredContent)) {
        renderData(next.structuredContent);
      } else {
        setSyncState("idle");
        stampEl.textContent = "未收到有效状态，保留最近状态";
        scheduleRefresh(taskState);
      }
    } catch (_) {
      setSyncState("error");
      stampEl.textContent = "同步失败，保留最近状态";
      scheduleRefresh(taskState);
    } finally {
      refreshing = false;
      refreshButton.disabled = false;
    }
  }

  function syncAndRefresh() {
    const selector = currentSelector();
    if (!selector) return;
    renderSyncing(selector, pickTask(latestData));
    void refresh();
  }

  function handleToolResult(snapshot) {
    if (snapshot?.task_id && !snapshot?.task) {
      const groupId = String(snapshot.presentation_group_id || "").trim();
      const selector = groupId
        ? { presentation_group_id: groupId }
        : { task_ids: [String(snapshot.task_id)] };
      latestToolInput = selector;
      if (snapshot.ok === false) {
        renderData({
          ok: false,
          schema: "tp-voyager.agent_panel/v1",
          mode: "dispatch",
          task: { task_id: String(snapshot.task_id), crew: snapshot.crew || null, model: snapshot.model || null, state: "failed", active: false },
          conversation: [], timeline: [], files: [], usage: {},
          error: { message: snapshot.detail || snapshot.reason_code || "派发失败" },
        });
        return;
      }
      renderSyncing(selector, { task_id: String(snapshot.task_id), crew: snapshot.crew || null, model: snapshot.model || null });
      void refresh();
      return;
    }
    const explicit = selectorFromInput(snapshot);
    if (explicit) latestToolInput = explicit;
    else if (snapshot?.task?.task_id) latestToolInput = { task_id: String(snapshot.task.task_id) };
    renderData(snapshot || {});
  }

  refreshButton.addEventListener("click", syncAndRefresh);

  function syncOnResume() {
    if (document.visibilityState && document.visibilityState !== "visible") return;
    if (!hasVerifiedSnapshot) return;
    if (refreshing || Date.now() - lastSyncAt < RESUME_SYNC_TTL_MS) return;
    const selector = currentSelector();
    if (!selector) return;
    renderSyncing(selector, pickTask(latestData));
    void refresh();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") syncOnResume();
    else savePanelUIState();
  }, { passive: true });
  window.addEventListener("pageshow", syncOnResume, { passive: true });
  window.addEventListener("focus", syncOnResume, { passive: true });
  window.addEventListener("pagehide", savePanelUIState, { passive: true });

  window.addEventListener("message", (event) => {
    if (event.source !== window.parent) return;
    const message = event.data;
    if (!message || message.jsonrpc !== "2.0") return;
    if (message.id !== undefined && pendingRequests.has(message.id)) {
      const pending = pendingRequests.get(message.id);
      pendingRequests.delete(message.id);
      clearTimeout(pending.timeoutId);
      if (message.error) pending.reject(message.error);
      else pending.resolve(message.result);
      return;
    }
    if (message.method === "ui/notifications/tool-input") {
      latestToolInput = selectorFromInput(message.params || {});
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
  }, BRIDGE_INIT_TIMEOUT_MS).then(() => {
    notify("ui/notifications/initialized", {});
  }).catch(() => {
    // A host may render the resource before the bridge is ready. A later
    // notification/refresh still fetches the current read-only projection.
  });
})();
</script>
</body>
</html>'''.strip()
