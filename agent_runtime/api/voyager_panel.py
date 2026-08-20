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
  #panel[data-state="syncing"] { --state-color: var(--starting); }
  #panel[data-state="queued"] { --state-color: var(--idle); }
  #panel[data-state="connecting"] { --state-color: var(--starting); }
  #panel[data-state="running"] { --state-color: var(--active); }
  #panel[data-state="observing"] { --state-color: var(--active); }
  #panel[data-state="completed"] { --state-color: var(--ok); }
  #panel[data-state="failed"] { --state-color: var(--bad); }
  #panel[data-state="cancelled"] { --state-color: var(--idle); }
  #panel[data-state="lost"] { --state-color: var(--bad); }
  #panel[data-state="orphaned"] { --state-color: var(--bad); }
  #panel[data-state="running"], #panel[data-state="observing"], #panel[data-state="syncing"] {
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--state-color) 15%, transparent),
                0 0 18px color-mix(in srgb, var(--state-color) 10%, transparent);
  }
  .top { display: flex; gap: 10px; align-items: center; padding: 11px 12px 9px; }
  .status-dot {
    width: 10px; height: 10px; min-width: 10px; border-radius: 50%;
    background: var(--state-color); box-shadow: 0 0 0 3px color-mix(in srgb, var(--state-color) 18%, transparent);
  }
  #panel[data-state="running"] .status-dot,
  #panel[data-state="observing"] .status-dot,
  #panel[data-state="syncing"] .status-dot { animation: pulse 1.45s ease-in-out infinite; }
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
  .file { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  .kv { display: grid; grid-template-columns: minmax(90px,auto) 1fr; gap: 4px 10px; font-size: 11.5px; }
  .kv > :nth-child(odd) { color: var(--muted); }
  .error { color: var(--bad); }
  .group-summary { border-top: 1px solid var(--line); padding: 10px 12px 4px; font-size: 12px; font-weight: 700; }
  .child-group { border-top: 1px solid var(--line); padding: 8px 12px 10px; display: grid; gap: 8px; }
  .child-card { border: 1px solid var(--line); border-radius: 9px; padding: 9px 10px; background: var(--surface); }
  .child-card[data-state="running"], .child-card[data-state="observing"] { border-left: 3px solid var(--active); }
  .child-card[data-state="completed"] { border-left: 3px solid var(--ok); }
  .child-card[data-state="failed"], .child-card[data-state="lost"], .child-card[data-state="orphaned"] { border-left: 3px solid var(--bad); }
  .child-title { display: flex; gap: 6px 10px; align-items: baseline; flex-wrap: wrap; font-size: 11.5px; font-weight: 700; }
  .child-meta { margin-top: 4px; display: flex; gap: 4px 10px; flex-wrap: wrap; color: var(--muted); font-size: 10.5px; overflow-wrap: anywhere; }
  .child-summary { margin-top: 7px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 11.5px; line-height: 1.5; }
  .child-details { margin-top: 7px; }
  .child-details > summary { font-weight: 600; }
  .child-sections { padding-left: 4px; }
  .foot { display: flex; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 10px; padding: 0 12px 9px; }
</style>
</head>
<body>
  <section id="panel" data-state="syncing" aria-live="polite">
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
      syncing: "正在同步", queued: "排队中", connecting: "正在连接", starting: "启动中",
      running: "执行中", observing: "执行中", pending: "等待中", requested: "已请求",
      completed: "已完成", passed: "已通过", failed: "失败", cancelled: "已取消",
      lost: "连接丢失", orphaned: "连接孤立", unknown: "未知", unavailable: "不可用",
      tool_activity: "工具活动", file_change: "文件变更", assistant_message: "回答", reasoning_summary: "摘要"
    })[value] || value || "未知";
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

  function answerRows(data) {
    const rows = [];
    const full = typeof data?.full_answer === "string" ? data.full_answer : "";
    const conversation = Array.isArray(data?.conversation) ? data.conversation : [];
    if (full) {
      const row = node("div", null, "row");
      const content = node("div", full);
      content.className = "answer markdown";
      row.appendChild(content);
      rows.push(row);
      return rows;
    }
    for (const item of conversation) {
      if (!item?.content) continue;
      const row = node("div", null, "row");
      const content = node("div", item.content);
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
      input_tokens: "输入 Token",
      output_tokens: "输出 Token",
      credits_used: "积分用量",
      reported_cost: "报告成本",
      duration_ms: "执行毫秒",
      turns: "轮次",
    })[key] || key.replaceAll("_", " ");
  }

  function usageRows(usage) {
    if (!usage || typeof usage !== "object") return [];
    const values = usage.usage && typeof usage.usage === "object" ? usage.usage : usage;
    const entries = Object.entries(values).filter(([, value]) => value !== null && value !== undefined && value !== "");
    if (!entries.length) return [];
    const row = node("div", null, "row");
    const grid = node("div", null, "kv");
    for (const [key, value] of entries) {
      grid.appendChild(node("div", usageLabel(key)));
      grid.appendChild(node("div", value));
    }
    row.appendChild(grid);
    return [row];
  }

  function appendSection(title, rows, open) {
    if (!rows.length) return;
    const wrapper = document.createElement("details");
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
    return taskId ? { task_id: taskId } : null;
  }

  function currentSelector() {
    if (latestData?.mode === "group") {
      const groupId = String(latestData?.presentation_group_id || "").trim();
      if (groupId) return { presentation_group_id: groupId };
      const taskIds = Array.isArray(latestData?.task_ids) ? latestData.task_ids.map(String).filter(Boolean) : [];
      if (taskIds.length) return { task_ids: taskIds };
    }
    const currentTask = pickTask(latestData);
    if (currentTask?.task_id) return { task_id: String(currentTask.task_id) };
    return selectorFromInput(latestToolInput);
  }

  function renderSingle(data) {
    latestData = data && typeof data === "object" ? data : {};
    const task = pickTask(latestData);
    const state = task?.state || (latestData.ok === false ? "failed" : "queued");
    panel.dataset.state = state;
    stateEl.textContent = stateLabel(state);
    document.querySelector(".title").textContent = "TP-Voyager 任务";
    renderIdentity(task);
    renderResultSummary(task, state, latestData);

    detailsEl.replaceChildren();
    appendSection("完整回答", answerRows(latestData), false);
    appendSection("执行活动", timelineRows(latestData.timeline), false);
    appendSection("文件变更", fileRows(latestData.files), false);
    appendSection("用量", usageRows(latestData.usage), false);
    stampEl.textContent = task?.updated_at ? `更新于 ${formatTime(task.updated_at)}` : "";
    scheduleRefresh(state);
  }

  function appendChildSection(parent, title, rows) {
    if (!rows.length) return;
    const wrapper = document.createElement("details");
    wrapper.open = false;
    wrapper.appendChild(node("summary", `${title} (${rows.length})`));
    const list = node("div", null, "list");
    for (const row of rows) list.appendChild(row);
    wrapper.appendChild(list);
    parent.appendChild(wrapper);
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

  function childConclusion(item) {
    const card = item?.result_card && typeof item.result_card === "object" ? item.result_card : null;
    if (card?.conclusion) return card.conclusion;
    const activity = latestActivity(item);
    if (activity) return activity;
    const state = item?.task?.state || "";
    if (state === "completed") return "任务已完成。";
    if (item?.task?.active) return "任务正在执行。";
    return "暂无可展示的安全摘要。";
  }

  function childCard(item) {
    const task = item?.task || {};
    const state = task.state || (item?.ok === false ? "failed" : "queued");
    const card = node("article", null, "child-card");
    card.dataset.state = state;
    const title = node("div", null, "child-title");
    title.appendChild(node("span", stateLabel(state)));
    if (task.crew) title.appendChild(node("span", task.crew));
    if (task.model) title.appendChild(node("span", task.model));
    card.appendChild(title);

    const meta = node("div", null, "child-meta");
    if (task.task_id) meta.appendChild(node("span", `任务 ${task.task_id}`));
    const duration = formatDuration(task.duration_seconds);
    if (duration) meta.appendChild(node("span", `耗时 ${duration}`));
    card.appendChild(meta);
    card.appendChild(node("div", childConclusion(item), "child-summary"));

    const answer = answerRows(item);
    const activity = timelineRows(item?.timeline);
    const files = fileRows(item?.files);
    const usage = usageRows(item?.usage);
    if (answer.length || activity.length || files.length || usage.length) {
      const detail = document.createElement("details");
      detail.className = "child-details";
      detail.open = false;
      detail.appendChild(node("summary", "查看子任务详情"));
      const sections = node("div", null, "child-sections");
      appendChildSection(sections, "完整回答", answer);
      appendChildSection(sections, "执行活动", activity);
      appendChildSection(sections, "文件变更", files);
      appendChildSection(sections, "用量", usage);
      detail.appendChild(sections);
      card.appendChild(detail);
    }
    return card;
  }

  function renderGroup(data) {
    latestData = data && typeof data === "object" ? data : {};
    const items = Array.isArray(latestData.tasks) ? latestData.tasks : [];
    const state = latestData.ok === false ? "failed" : groupState(items);
    panel.dataset.state = state;
    stateEl.textContent = stateLabel(state);
    document.querySelector(".title").textContent = "TP-Voyager 并发任务组";
    renderGroupIdentity(latestData, state);

    summaryEl.replaceChildren();
    summaryEl.className = "summary";
    resultPart("结论", items.length ? `并发任务组包含 ${items.length} 个明确子任务；可分别查看结果与过程。` : "未找到该并发组中的任务。");
    const completed = items.filter((item) => item?.task?.state === "completed").length;
    const active = items.filter((item) => item?.task?.active).length;
    const failed = items.filter((item) => ["failed", "lost", "orphaned"].includes(item?.task?.state)).length;
    resultPart("关键依据", [`已完成 ${completed} 个`, `执行中 ${active} 个`, `异常 ${failed} 个`]);

    detailsEl.replaceChildren();
    const group = node("div", null, "child-group");
    for (const item of items) group.appendChild(childCard(item));
    if (items.length) detailsEl.appendChild(group);
    const updated = items.map((item) => Number(item?.task?.updated_at || 0)).filter(Boolean);
    stampEl.textContent = updated.length ? `更新于 ${formatTime(Math.max(...updated))}` : "";
    scheduleRefresh(state);
  }

  function renderData(data) {
    if (data?.mode === "group") renderGroup(data);
    else renderSingle(data);
  }

  function renderSyncing(selector, hintTask = null) {
    if (!selector) return;
    if (refreshTimer) { clearTimeout(refreshTimer); refreshTimer = null; }
    panel.dataset.state = "syncing";
    stateEl.textContent = stateLabel("syncing");
    const groupId = String(selector.presentation_group_id || "").trim();
    const taskIds = Array.isArray(selector.task_ids) ? selector.task_ids : [];
    if (groupId || taskIds.length) {
      document.querySelector(".title").textContent = "TP-Voyager 并发任务组";
      renderGroupIdentity({ presentation_group_id: groupId, task_ids: taskIds }, "syncing");
    } else {
      document.querySelector(".title").textContent = "TP-Voyager 任务";
      const taskId = selector.task_id || hintTask?.task_id || "";
      renderIdentity({ ...(hintTask || {}), task_id: taskId }, "syncing");
    }
    summaryEl.replaceChildren();
    summaryEl.className = "summary empty";
    summaryEl.appendChild(node("div", "正在同步最新任务状态…"));
    detailsEl.replaceChildren();
    stampEl.textContent = "";
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
    refreshButton.disabled = true;
    try {
      await bridgeReady;
      const next = await request("tools/call", {
        name: "render_voyager_panel",
        arguments: { ...selector, limit: 200 },
      });
      if (next?.structuredContent) renderData(next.structuredContent);
    } catch (_) {
      // Host policy may reject UI-originated calls. Keep the last verified
      // server snapshot; no lifecycle mutation is attempted as fallback.
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
      const selector = groupId ? { presentation_group_id: groupId } : { task_id: String(snapshot.task_id) };
      latestToolInput = selector;
      if (snapshot.ok === false) {
        renderSingle({
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
    const selector = currentSelector();
    if (!selector) return;
    renderSyncing(selector, pickTask(latestData));
    void refresh();
  }

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") syncOnResume();
  }, { passive: true });
  window.addEventListener("pageshow", syncOnResume, { passive: true });
  window.addEventListener("focus", syncOnResume, { passive: true });

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
      latestToolInput = selectorFromInput(message.params || {});
      return;
    }
    if (message.method === "ui/notifications/tool-result") {
      handleToolResult(message.params?.structuredContent || message.params || {});
    }
  }, { passive: true });

  const bridgeReady = request("ui/initialize", {
    appInfo: { name: "tp-voyager-agent-panel", version: "1.0.9.2" },
    appCapabilities: {},
    protocolVersion: "2026-01-26",
  }).then(() => {
    notify("ui/notifications/initialized", {});
  }).catch(() => {
    // A host may render the resource before the bridge is ready. A later
    // notification/refresh still fetches the current read-only projection.
  });
})();
</script>
</body>
</html>'''.strip()
