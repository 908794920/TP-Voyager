"""Self-contained MCP Apps UI for TP-Voyager Agent observability.

The resource has no external network dependencies. It renders only safe,
structured server projection data and keeps presentation state inside the
active iframe. UI refresh always calls the existing read-only
``render_voyager_panel`` tool; it never performs a lifecycle mutation.
"""

from __future__ import annotations


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
  #panel[data-sync-state="syncing"] { --state-color: var(--starting); }
  #panel[data-task-state="queued"] { --state-color: var(--idle); }
  #panel[data-task-state="connecting"] { --state-color: var(--starting); }
  #panel[data-task-state="running"] { --state-color: var(--active); }
  #panel[data-task-state="observing"] { --state-color: var(--active); }
  #panel[data-task-state="completed"] { --state-color: var(--ok); }
  #panel[data-task-state="failed"] { --state-color: var(--bad); }
  #panel[data-task-state="cancelled"] { --state-color: var(--idle); }
  #panel[data-task-state="lost"] { --state-color: var(--bad); }
  #panel[data-task-state="orphaned"] { --state-color: var(--bad); }
  #panel[data-task-state="running"], #panel[data-task-state="observing"], #panel[data-sync-state="syncing"] {
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--state-color) 15%, transparent),
                0 0 18px color-mix(in srgb, var(--state-color) 10%, transparent);
  }
  .top { display: flex; gap: 10px; align-items: center; padding: 11px 12px 9px; }
  .status-dot {
    width: 10px; height: 10px; min-width: 10px; border-radius: 50%;
    background: var(--state-color); box-shadow: 0 0 0 3px color-mix(in srgb, var(--state-color) 18%, transparent);
  }
  #panel[data-task-state="running"] .status-dot,
  #panel[data-task-state="observing"] .status-dot,
  #panel[data-sync-state="syncing"] .status-dot { animation: pulse 1.45s ease-in-out infinite; }
  @keyframes pulse { 0%,100% { opacity: .55; transform: scale(.92); } 50% { opacity: 1; transform: scale(1.12); } }
  .identity { min-width: 0; flex: 1; }
  .title-row { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
  .title { font-weight: 720; font-size: 13.5px; letter-spacing: .01em; }
  .state { color: var(--state-color); font-size: 12px; font-weight: 650; }
  .meta { margin-top: 5px; display: flex; gap: 5px 10px; flex-wrap: wrap; font-size: 11px; color: var(--muted); overflow-wrap: anywhere; }
  .fact { display: inline-flex; gap: 4px; min-width: 0; }
  .fact-key { color: color-mix(in srgb, CanvasText 42%, Canvas 58%); }
  .fact-value { color: color-mix(in srgb, CanvasText 78%, Canvas 22%); font-weight: 550; }
  button {
    appearance: none; border: 1px solid var(--line); border-radius: 8px; background: var(--surface);
    color: CanvasText; padding: 6px 9px; font: inherit; font-size: 11.5px; cursor: pointer;
  }
  button:disabled { opacity: .45; cursor: default; }
  .summary { border-top: 1px solid var(--line); padding: 10px 12px; font-size: 12px; line-height: 1.55; }
  .result-part + .result-part { margin-top: 10px; }
  .result-title { font-weight: 700; margin-bottom: 3px; }
  .result-text { color: color-mix(in srgb, CanvasText 88%, Canvas 12%); white-space: pre-wrap; overflow-wrap: anywhere; }
  .result-list { margin: 3px 0 0; padding-left: 18px; }
  .result-list li + li { margin-top: 3px; }
  .summary-detail { color: var(--muted); font-size: 11px; white-space: pre-wrap; }
  .empty { color: var(--muted); }
  .details { border-top: 1px solid var(--line); padding: 0 12px 9px; }
  .details:empty { display: none; }
  details { border-bottom: 1px solid var(--line); padding: 7px 0; }
  details:last-child { border-bottom: 0; }
  summary { cursor: pointer; font-size: 11.5px; font-weight: 650; color: color-mix(in srgb, CanvasText 82%, Canvas 18%); }
  .list { margin-top: 7px; display: grid; gap: 6px; }
  .row { border-left: 2px solid var(--line); padding: 3px 0 3px 8px; font-size: 11.5px; line-height: 1.45; overflow-wrap: anywhere; }
  .row .label { color: var(--muted); font-size: 10.5px; margin-bottom: 2px; }
  .answer.markdown { white-space: pre-wrap; overflow-wrap: anywhere; font-family: inherit; }
  .answer.markdown pre, .answer.markdown code { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  .answer.markdown code { background: color-mix(in srgb, CanvasText 6%, var(--surface)); border-radius: 6px; padding: 1px 5px; }
  .answer.markdown pre { background: color-mix(in srgb, CanvasText 6%, var(--surface)); border-radius: 8px; padding: 8px 10px; overflow-x: auto; margin: 6px 0; }
  .answer.markdown pre code { padding: 0; background: transparent; }
  .answer.markdown blockquote { margin: 6px 0; padding: 2px 0 2px 10px; border-left: 3px solid var(--line); color: var(--muted); }
  .answer.markdown table { border-collapse: collapse; width: 100%; margin: 6px 0; }
  .answer.markdown th, .answer.markdown td { border: 1px solid var(--line); padding: 4px 8px; text-align: left; vertical-align: top; }
  .answer.markdown th { background: color-mix(in srgb, CanvasText 5%, var(--surface)); }
  .answer.markdown a { color: var(--starting); }
  .answer.markdown ul { margin: 3px 0; padding-left: 20px; }
  .answer.markdown em { font-style: italic; }
  .file { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  .kv { display: grid; grid-template-columns: minmax(90px,auto) 1fr; gap: 4px 10px; font-size: 11.5px; }
  .kv > :nth-child(odd) { color: var(--muted); }
  .error { color: var(--bad); }
  .group-summary { border-top: 1px solid var(--line); padding: 10px 12px 4px; font-size: 12px; font-weight: 700; }
  .child-group { border-top: 1px solid var(--line); padding: 8px 12px 10px; display: grid; gap: 8px; }
  .child-card { border: 1px solid var(--line); border-radius: 9px; padding: 9px 10px; background: var(--surface); }
  .child-card[data-task-state="running"], .child-card[data-task-state="observing"] { border-left: 3px solid var(--active); }
  .child-card[data-task-state="completed"] { border-left: 3px solid var(--ok); }
  .child-card[data-task-state="failed"], .child-card[data-task-state="lost"], .child-card[data-task-state="orphaned"] { border-left: 3px solid var(--bad); }
  .child-title { display: flex; gap: 6px 10px; align-items: baseline; flex-wrap: wrap; font-size: 11.5px; font-weight: 700; }
  .child-meta { margin-top: 4px; display: flex; gap: 4px 10px; flex-wrap: wrap; color: var(--muted); font-size: 10.5px; overflow-wrap: anywhere; }
  .child-summary { margin-top: 7px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11.5px; line-height: 1.5; }
  .child-details { margin-top: 7px; }
  .child-details > summary { font-weight: 600; }
  .child-sections { padding-left: 4px; }
  .foot { display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 10px; padding: 0 12px 9px; }
  .workbench { display: flex; gap: 10px; padding: 10px 12px 12px; align-items: stretch; }
  .wb-nav { width: 250px; min-width: 210px; display: grid; gap: 6px; align-content: start; max-height: 340px; overflow-y: auto; padding-right: 2px; }
  .wb-task { appearance: none; text-align: left; border: 1px solid var(--line); border-left-width: 3px; border-radius: 9px; padding: 8px 9px; display: grid; gap: 3px; cursor: pointer; background: var(--surface); color: CanvasText; }
  .wb-task:hover { background: color-mix(in srgb, CanvasText 5%, var(--surface)); }
  .wb-task.active { background: color-mix(in srgb, CanvasText 7%, var(--surface)); box-shadow: inset 0 0 0 1px color-mix(in srgb, CanvasText 18%, transparent); }
  .wb-task[data-task-state="running"], .wb-task[data-task-state="observing"], .wb-task[data-task-state="connecting"], .wb-task[data-task-state="queued"] { border-left-color: var(--active); }
  .wb-task[data-task-state="completed"] { border-left-color: var(--ok); }
  .wb-task[data-task-state="failed"], .wb-task[data-task-state="lost"], .wb-task[data-task-state="orphaned"] { border-left-color: var(--bad); }
  .wb-task-head { display: flex; gap: 6px; align-items: center; }
  .wb-task-state { font-weight: 650; }
  .wb-task-meta { display: flex; gap: 4px 8px; color: var(--muted); font-size: 10.5px; flex-wrap: wrap; }
  .wb-task-id { color: var(--muted); font-size: 10px; overflow-wrap: anywhere; }
  .wb-task-duration { color: var(--muted); font-size: 10px; }
  .wb-task-failure { color: var(--bad); font-size: 10.5px; overflow-wrap: anywhere; }
  .wb-main { flex: 1; min-width: 0; display: grid; grid-template-rows: auto minmax(0, 1fr); gap: 8px; }
  .wb-tabs { display: flex; gap: 4px; flex-wrap: wrap; }
  .wb-tab { appearance: none; border-radius: 999px; padding: 4px 10px; font-size: 11px; }
  .wb-tab.active { background: color-mix(in srgb, CanvasText 10%, var(--surface)); border-color: color-mix(in srgb, CanvasText 30%, transparent); }
  .wb-body { border: 1px solid var(--line); border-radius: 9px; padding: 10px; min-height: 140px; max-height: 340px; overflow-y: auto; display: grid; gap: 8px; align-content: start; }
  .usage-strip { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
  .usage-pill { border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; font-size: 12px; }
  .usage-derived { font-size: 11px; opacity: .72; margin-left: 4px; }
  .wb-summary-text { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.5; }
  @media (max-width: 520px) {
    .workbench { flex-direction: column; }
    .wb-nav { width: auto; min-width: 0; max-height: 150px; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); }
    .wb-main { grid-template-rows: auto minmax(0, 1fr); }
  }
</style>
</head>
<body>
  <section id="panel" data-task-state="unknown" data-sync-state="syncing" aria-live="polite">
    <div class="top">
      <span class="status-dot" aria-hidden="true"></span>
      <div class="identity">
        <div class="title-row"><span class="title">TP-Voyager 任务</span><span class="state" id="state">正在同步</span></div>
        <div class="meta" id="meta">等待任务数据…</div>
      </div>
      <button id="refresh" type="button">刷新</button>
    </div>
    <div class="summary empty" id="summary">正在同步最新任务状态…</div>
    <div class="details" id="details"></div>
    <div class="foot"><span id="stamp"></span><span>当前会话面板</span></div>
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
  let latestGroupItems = [];
  let refreshTimer = null;
  // v1.0.9.3: iframe-memory presentation state only. Never persisted.
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
      ["执行单元", task?.crew],
      ["执行模型", task?.model],
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

  function resultPart(title, value) {
    if (value === null || value === undefined || value === "") return;
    const wrapper = node("div", null, "result-part");
    wrapper.appendChild(node("div", title, "result-title"));
    if (Array.isArray(value)) {
      if (!value.length) return;
      const list = node("ul", null, "result-list");
      for (const item of value) list.appendChild(node("li", item));
      wrapper.appendChild(list);
    } else {
      wrapper.appendChild(node("div", value, "result-text"));
    }
    summaryEl.appendChild(wrapper);
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
    if (data.error?.message) {
      summaryEl.className = "summary error";
      resultPart("结论", "任务执行失败。");
      const details = [];
      if (data.error.stage) details.push(`阶段：${phaseLabel(data.error.stage)}`);
      details.push(`原因：${data.error.message}`);
      resultPart("风险", details);
      return;
    }
    const card = data?.result_card && typeof data.result_card === "object" ? data.result_card : null;
    if (card) {
      resultPart("结论", card.conclusion || "任务已结束；完整结论见回答。");
      resultPart("关键依据", Array.isArray(card.key_evidence) ? card.key_evidence : []);
      resultPart("风险", Array.isArray(card.risks) ? card.risks : []);
      resultPart("下一步", Array.isArray(card.next_steps) ? card.next_steps : []);
      return;
    }
    if (task?.active) {
      resultPart("结论", "任务正在执行。");
      const activity = latestActivity(data);
      if (activity) resultPart("当前安全活动", activity);
      return;
    }
    if (state === "completed") {
      resultPart("结论", "任务已完成。完整回答可在下方展开查看。");
      return;
    }
    resultPart("结论", "暂无可展示的结构化结果。");
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
    return (items || []).map((item) => {
      const row = node("div", null, "row");
      const count = Number(item.count || 1);
      const parts = [formatTime(item.timestamp), toolLabel(item.tool), actionLabel(item.action), item.path, item.phase ? phaseLabel(item.phase) : null, stateLabel(item.status || item.kind)].filter(Boolean);
      if (count > 1) parts.push(`×${count}`);
      row.appendChild(node("div", parts.join(" · ") || "执行活动"));
      if (item.reason || item.summary) row.appendChild(node("div", item.reason || item.summary, "label"));
      return row;
    });
  }

  function fileRows(items) {
    return (items || []).map((item) => {
      const row = node("div", null, "row file");
      row.appendChild(node("div", `${actionLabel(item.action) || stateLabel(item.kind) || "变更"}  ${item.path || ""}`));
      if (item.capture_state) row.appendChild(node("div", item.capture_state, "label"));
      return row;
    });
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
      original_credits: "原始 Credit",
      billable: "Billable",
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
    const { evidence: source, values } = usagePayload(evidence);
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

    const row = node("div", null, "row");
    const grid = node("div", null, "kv");
    for (const [key, value] of entries) {
      const label = node("div", usageLabel(key));
      if (derived.has(key) || (key === "cache_hit_rate" && value !== null)) {
        label.appendChild(node("span", "推导", "usage-derived"));
      }
      grid.appendChild(label);
      grid.appendChild(node("div", usageValue(value)));
    }
    row.appendChild(grid);
    return [row];
  }

  function usageSummaryValues(evidence) {
    const { values } = usagePayload(evidence);
    return {
      credits: values.credits ?? values.credits_used ?? null,
      totalTokens: values.total_tokens ?? null,
    };
  }

  function renderUsageStrip(container, data) {
    const old = container.querySelector(".usage-strip");
    if (old) old.remove();
    const strip = node("div", null, "usage-strip");
    strip.dataset.role = "usage-summary";
    if (data?.mode === "group") {
      const groupUsage = data?.usage && typeof data.usage === "object" ? data.usage : {};
      const selectedId = String(panel.dataset.selectedTaskId || "");
      const selected = (Array.isArray(data?.tasks) ? data.tasks : []).find((item) => String(item?.task?.task_id || "") === selectedId) || data?.tasks?.[0];
      const selectedUsage = usageSummaryValues(selected?.usage);
      strip.appendChild(node("span", `子任务 Tokens：${usageValue(groupUsage.total_tokens)}`, "usage-pill"));
      strip.appendChild(node("span", `子任务 Credits：${usageValue(groupUsage.credits)}`, "usage-pill"));
      strip.appendChild(node("span", `当前 Tokens：${usageValue(selectedUsage.totalTokens)}`, "usage-pill"));
      strip.appendChild(node("span", `当前 Credits：${usageValue(selectedUsage.credits)}`, "usage-pill"));
    } else {
      const selectedUsage = usageSummaryValues(data?.usage);
      strip.appendChild(node("span", `Tokens：${usageValue(selectedUsage.totalTokens)}`, "usage-pill"));
      strip.appendChild(node("span", `Credits：${usageValue(selectedUsage.credits)}`, "usage-pill"));
    }
    container.appendChild(strip);
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

  // v1.0.9.3: persist the current presentation state (tab/details/scroll)
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

  // v1.0.9.3: concurrent workbench — left task navigation + right detail workspace.
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

  function appendSummaryList(body, title, value) {
    if (!Array.isArray(value) || !value.length) return;
    const part = node("div", null, "result-part");
    part.appendChild(node("div", title, "result-title"));
    const list = node("ul", null, "result-list");
    for (const item of value) list.appendChild(node("li", item));
    part.appendChild(list);
    body.appendChild(part);
  }

  function renderTabBody(item, tab) {
    const body = node("div", null, "wb-body");
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
        appendSummaryList(body, "关键依据", card?.key_evidence);
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
      if (task.crew) meta.appendChild(node("span", task.crew));
      if (task.model) meta.appendChild(node("span", task.model));
      entry.appendChild(meta);
      if (taskId) entry.appendChild(node("div", `任务 ${taskId}`, "wb-task-id"));
      const duration = formatDuration(task.duration_seconds);
      if (duration) entry.appendChild(node("div", `耗时 ${duration}`, "wb-task-duration"));
      const failure = String(item?.error?.message || task.error_message || "").trim();
      if (failure) entry.appendChild(node("div", failure, "wb-task-failure"));
      entry.addEventListener("click", () => {
        panel.dataset.selectedTaskId = taskId;
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
    resultPart("结论", items.length ? `并发任务组包含 ${items.length} 个明确子任务；从左侧选择任务查看详情。` : "未找到该并发组中的任务。");
    const completed = items.filter((item) => item?.task?.state === "completed").length;
    const active = items.filter((item) => item?.task?.active).length;
    const failed = items.filter((item) => ["failed", "lost", "orphaned"].includes(item?.task?.state)).length;
    resultPart("关键依据", [`已完成 ${completed} 个`, `执行中 ${active} 个`, `异常 ${failed} 个`]);
    renderUsageStrip(summaryEl, latestData);

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
    appInfo: { name: "tp-voyager-agent-panel", version: "1.0.9.3" },
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
