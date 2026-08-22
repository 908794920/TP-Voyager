from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest

from agent_runtime.api.voyager_panel import render_voyager_panel_html


def _run_panel_runtime(scenario: str) -> dict:
    """Run the shipped panel script in a small Node/browser-DOM harness."""

    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node.js is required for panel runtime tests")

    html = render_voyager_panel_html()
    script = html.split("<script>\n", 1)[1].split("\n</script>", 1)[0]
    node_program = f"""
const vm = require("vm");
const panelScript = {json.dumps(script)};
const scenario = {json.dumps(scenario)};

class DomNode {{
  constructor(tagName, text = "") {{
    this.tagName = String(tagName || "#text").toUpperCase();
    this.nodeName = this.tagName;
    this.childNodes = [];
    this.parentNode = null;
    this._text = String(text || "");
    this.attributes = {{}};
    this.dataset = {{}};
    this.className = "";
    this.disabled = false;
    this.open = false;
    this.type = "";
    this.scrollTop = 0;
    this._scrollHeight = null;
    this.clientHeight = 100;
    this._listeners = new Map();
    this.classList = {{
      add: (...names) => {{
        const values = new Set(this.className.split(/\\s+/).filter(Boolean));
        for (const name of names) values.add(String(name));
        this.className = Array.from(values).join(" ");
      }},
      contains: (name) => this.className.split(/\\s+/).includes(String(name)),
    }};
  }}

  get textContent() {{
    if (this.tagName === "#TEXT") return this._text;
    return this.childNodes.map((child) => child.textContent).join("");
  }}

  set textContent(value) {{
    this.childNodes = [];
    if (value !== null && value !== undefined && String(value) !== "") {{
      this.appendChild(new DomNode("#text", String(value)));
    }}
  }}

  get scrollHeight() {{
    return this._scrollHeight === null ? Math.max(100, this.childNodes.length * 80) : this._scrollHeight;
  }}

  set scrollHeight(value) {{ this._scrollHeight = Number(value); }}

  appendChild(value) {{
    const child = typeof value === "string" ? new DomNode("#text", value) : value;
    if (child === null || child === undefined) return child;
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  }}

  append(...values) {{ for (const value of values) this.appendChild(value); }}
  replaceChildren(...values) {{ this.childNodes = []; this.append(...values); }}
  remove() {{
    if (!this.parentNode) return;
    this.parentNode.childNodes = this.parentNode.childNodes.filter((child) => child !== this);
    this.parentNode = null;
  }}

  setAttribute(name, value) {{
    const key = String(name);
    this.attributes[key] = String(value);
    if (key === "id") this.id = String(value);
    if (key === "class") this.className = String(value);
  }}

  addEventListener(type, handler) {{
    const handlers = this._listeners.get(type) || [];
    handlers.push(handler);
    this._listeners.set(type, handlers);
  }}

  dispatchEvent(event) {{
    for (const handler of this._listeners.get(event.type) || []) handler(event);
  }}

  querySelector(selector) {{ return this.querySelectorAll(selector)[0] || null; }}

  querySelectorAll(selector) {{
    const result = [];
    const matches = (item) => {{
      if (selector.startsWith("#")) return item.id === selector.slice(1);
      if (selector.startsWith(".")) return item.className.split(/\\s+/).includes(selector.slice(1));
      return item.tagName.toLowerCase() === selector.toLowerCase();
    }};
    const walk = (item) => {{
      for (const child of item.childNodes) {{
        if (child.tagName !== "#TEXT") {{
          if (matches(child)) result.push(child);
          walk(child);
        }}
      }}
    }};
    walk(this);
    return result;
  }}
}}

class Document extends DomNode {{
  constructor() {{ super("document"); this.visibilityState = "visible"; }}
  createElement(tagName) {{ return new DomNode(tagName); }}
  getElementById(id) {{ return this.querySelector("#" + id); }}
}}

const document = new Document();
const body = new DomNode("body");
const panel = new DomNode("section");
panel.id = "panel";
panel.dataset.taskState = "unknown";
panel.dataset.syncState = "syncing";
const title = new DomNode("span");
title.className = "title";
title.textContent = "TP-Voyager 任务";
const state = new DomNode("span");
state.id = "state";
state.textContent = "正在同步";
const meta = new DomNode("div"); meta.id = "meta";
const summary = new DomNode("div"); summary.id = "summary";
const details = new DomNode("div"); details.id = "details";
const refresh = new DomNode("button"); refresh.id = "refresh";
const stamp = new DomNode("span"); stamp.id = "stamp";
const top = new DomNode("div"); top.appendChild(title); top.appendChild(state);
panel.appendChild(top); panel.appendChild(meta); panel.appendChild(summary);
panel.appendChild(details); panel.appendChild(refresh); panel.appendChild(stamp);
body.appendChild(panel); document.appendChild(body);
document.body = body; document.documentElement = document;

const outgoing = [];
const parent = {{ postMessage: (message) => outgoing.push(message) }};
const window = {{
  parent,
  _listeners: new Map(),
  addEventListener(type, handler) {{
    const handlers = this._listeners.get(type) || [];
    handlers.push(handler); this._listeners.set(type, handlers);
  }},
  dispatchEvent(event) {{
    for (const handler of this._listeners.get(event.type) || []) handler(event);
  }},
  scrollTo: () => {{}},
}};
const DOMParser = class {{
  parseFromString(value) {{
    const parsedBody = new DomNode("body");
    parsedBody.appendChild(new DomNode("#text", String(value || "")));
    return {{ body: parsedBody }};
  }}
}};

const context = {{
  console, document, window, DOMParser,
  setTimeout, clearTimeout, setImmediate, Date, Number, String,
  Object, Array, Set, Map, Promise, Math, JSON, Intl, RegExp,
}};
vm.runInNewContext(panelScript, context, {{ filename: "voyager_panel.js" }});

const send = (message) => window.dispatchEvent({{
  type: "message", source: parent, data: message,
}});
const flush = async () => {{
  await new Promise((resolve) => setImmediate(resolve));
  await Promise.resolve();
}};
const finish = (value) => process.stdout.write(JSON.stringify(value), () => process.exit(0));
const calls = () => outgoing.filter((item) => item.method === "tools/call");
const latestCall = () => calls().at(-1);
const snapshot = (stateValue, usageEvidence = {{}}) => ({{
  ok: true,
  schema: "tp-voyager.agent_detail/v1",
  task: {{ task_id: "task-1", state: stateValue, active: stateValue === "running", crew: "qoder", model: "lite", updated_at: 10 }},
  conversation: [], full_answer: "verified answer", timeline: [], files: [], usage: usageEvidence,
}});
const groupSnapshot = (rows, credits = null, totalTokens = null) => ({{
  ok: true,
  schema: "tp-voyager.agent_panel/v1",
  mode: "group",
  presentation_group_id: "grp-1",
  task_ids: ["task-1"],
  usage: {{ total_tokens: totalTokens, credits }},
  tasks: [{{
    ...snapshot("running"),
    usage: {{ schema: "tp-voyager.usage/v1", provider: "qoder", model: "lite", usage: {{ total_tokens: totalTokens, credits, session_credits: null, derived_fields: [] }} }},
    timeline: Array.from({{ length: rows }}, (_, index) => ({{
      kind: "tool_activity", tool: "Read", action: "read", status: "completed", timestamp: index + 1,
    }})),
  }}],
}});

(async () => {{
  const init = outgoing.find((item) => item.method === "ui/initialize");
  send({{ jsonrpc: "2.0", id: init.id, result: {{}} }});
  await flush();

  if (scenario === "initial_resume_no_duplicate") {{
    send({{ jsonrpc: "2.0", method: "ui/notifications/tool-input", params: {{ task_id: "task-1" }} }});
    window.dispatchEvent({{ type: "pageshow" }});
    window.dispatchEvent({{ type: "focus" }});
    await flush();
    const callsBeforeResult = calls().length;
    send({{
      jsonrpc: "2.0", method: "ui/notifications/tool-result",
      params: {{ structuredContent: snapshot("completed") }},
    }});
    await flush();
    window.dispatchEvent({{ type: "pageshow" }});
    window.dispatchEvent({{ type: "focus" }});
    await flush();
    finish({{ callsBeforeResult, callsAfterResult: calls().length }});
    return;
  }}

  if (scenario === "group_scroll_restore" || scenario === "group_scroll_follow_bottom") {{
    send({{
      jsonrpc: "2.0", method: "ui/notifications/tool-result",
      params: {{ structuredContent: groupSnapshot(5) }},
    }});
    await flush();
    const initialBody = details.querySelector(".wb-body");
    initialBody.scrollHeight = 400;
    initialBody.clientHeight = 100;
    initialBody.scrollTop = scenario === "group_scroll_follow_bottom" ? 300 : 40;
    refresh.dispatchEvent({{ type: "click" }});
    await flush();
    const call = latestCall();
    send({{ jsonrpc: "2.0", id: call.id, result: {{ structuredContent: groupSnapshot(8) }} }});
    await flush();
    const refreshedBody = details.querySelector(".wb-body");
    finish({{
      scrollTop: refreshedBody.scrollTop,
      scrollHeight: refreshedBody.scrollHeight,
      selectedTaskId: panel.dataset.selectedTaskId,
      activeTab: panel.dataset.activeTab,
    }});
    return;
  }}

  if (scenario === "evidence_status_points") {{
    const grouped = groupSnapshot(1, 0, 0);
    const completed = snapshot("completed");
    completed.task = {{ ...completed.task, task_id: "task-completed", state: "completed", active: false }};
    const running = snapshot("running");
    running.task = {{ ...running.task, task_id: "task-running", state: "running", active: true }};
    const failed = snapshot("failed");
    failed.task = {{ ...failed.task, task_id: "task-failed", state: "failed", active: false }};
    grouped.task_ids = ["task-completed", "task-running", "task-failed"];
    grouped.tasks = [completed, running, failed];
    send({{
      jsonrpc: "2.0", method: "ui/notifications/tool-result",
      params: {{ structuredContent: grouped }},
    }});
    await flush();
    const evidenceItems = summary.querySelectorAll(".evidence-item");
    finish({{
      statuses: evidenceItems.map((item) => item.dataset.evidenceStatus || ""),
      classes: evidenceItems.map((item) => item.className),
      itemLabels: evidenceItems.map((item) => item.attributes["aria-label"] || ""),
      labels: evidenceItems.map((item) => (item.querySelector(".evidence-status-dot") || {{ attributes: {{}} }}).attributes["aria-label"] || ""),
      text: evidenceItems.map((item) => item.textContent),
    }});
    return;
  }}

  if (scenario === "usage_unknown_styling") {{
    send({{
      jsonrpc: "2.0", method: "ui/notifications/tool-result",
      params: {{ structuredContent: groupSnapshot(1, null, null) }},
    }});
    await flush();
    const cards = summary.querySelectorAll(".usage-card");
    finish({{
      classes: cards.map((item) => item.className),
      text: cards.map((item) => item.textContent),
    }});
    return;
  }}

  if (scenario === "single_usage_display") {{
    const usage = {{
      schema: "tp-voyager.usage/v1", provider: "codebuddy", model: "hy3",
      usage: {{
        total_tokens: 120, input_tokens: 100, cache_read_tokens: 40,
        cache_miss_tokens: null, cache_write_tokens: null, output_tokens: 20,
        reasoning_tokens: 6, answer_tokens: 14, credits: 0.75,
        session_credits: null, derived_fields: [],
      }},
    }};
    send({{
      jsonrpc: "2.0", method: "ui/notifications/tool-result",
      params: {{ structuredContent: snapshot("completed", usage) }},
    }});
    await flush();
    const usageTab = details.querySelectorAll(".wb-tab").find((item) => item.textContent === "用量");
    usageTab.dispatchEvent({{ type: "click" }});
    await flush();
    finish({{ summary: summary.textContent, details: details.textContent }});
    return;
  }}

  if (scenario === "ui_structure_boundary") {{
    const grouped = groupSnapshot(2, 0.9, 220);
    grouped.task_ids = ["task-1", "task-2"];
    grouped.tasks[0].task = {{
      ...grouped.tasks[0].task, task_id: "task-1", crew: "qoder", model: "lite",
    }};
    grouped.tasks[0].usage = {{
      schema: "tp-voyager.usage/v1", provider: "qoder", model: "lite",
      usage: {{
        total_tokens: 120, input_tokens: 90, cache_read_tokens: 30,
        cache_miss_tokens: 50, cache_write_tokens: 10, output_tokens: 30,
        reasoning_tokens: 10, answer_tokens: 20, credits: 0.4,
        session_credits: 4.2, derived_fields: ["cache_miss_tokens"],
      }},
    }};
    grouped.tasks[0].files = [{{
      kind: "file_change", action: "modify", path: "src/example.py",
      capture_state: "captured", summary: "updated safely",
    }}];
    const second = snapshot("completed");
    second.task = {{
      ...second.task, task_id: "task-2", state: "completed", active: false,
      crew: "codebuddy", model: "hy3",
    }};
    second.usage = {{
      schema: "tp-voyager.usage/v1", provider: "codebuddy", model: "hy3",
      usage: {{ total_tokens: 100, credits: 0.5, reasoning_tokens: 4, answer_tokens: 12 }},
    }};
    grouped.tasks.push(second);
    send({{
      jsonrpc: "2.0", method: "ui/notifications/tool-result",
      params: {{ structuredContent: grouped }},
    }});
    await flush();
    const tabs = details.querySelectorAll(".wb-tab").map((item) => item.textContent);
    const identities = details.querySelectorAll(".wb-task-identity").map((item) => item.textContent);
    const initialUsageOverview = summary.textContent;
    details.querySelectorAll(".wb-task")[1].dispatchEvent({{ type: "click" }});
    await flush();
    const secondTaskUsageOverview = summary.textContent;
    details.querySelectorAll(".wb-task")[0].dispatchEvent({{ type: "click" }});
    await flush();
    const usageTab = details.querySelectorAll(".wb-tab").find((item) => item.textContent === "用量");
    usageTab.dispatchEvent({{ type: "click" }});
    await flush();
    const usageMetrics = details.querySelector(".usage-metrics");
    const usageLabels = details.querySelectorAll(".usage-metric-label").map((item) => item.textContent);
    const usageText = usageMetrics ? usageMetrics.textContent : "";
    const activityTab = details.querySelectorAll(".wb-tab").find((item) => item.textContent === "执行活动");
    activityTab.dispatchEvent({{ type: "click" }});
    await flush();
    const activityCount = details.querySelectorAll(".activity-item").length;
    const activityMarkerCount = details.querySelectorAll(".activity-marker").length;
    const fileTab = details.querySelectorAll(".wb-tab").find((item) => item.textContent === "文件变更");
    fileTab.dispatchEvent({{ type: "click" }});
    await flush();
    finish({{
      tabs, identities, initialUsageOverview, secondTaskUsageOverview,
      usageLabels, usageText, activityCount, activityMarkerCount,
      fileItemCount: details.querySelectorAll(".file-change-item").length,
      filePathText: (details.querySelector(".file-change-path") || {{ textContent: "" }}).textContent,
    }});
    return;
  }}

  if (scenario === "group_usage_display") {{
    const grouped = groupSnapshot(3, 0.9, 220);
    grouped.tasks[0].usage = {{
      schema: "tp-voyager.usage/v1", provider: "qoder", model: "lite",
      usage: {{
        total_tokens: 120, input_tokens: 90, cache_read_tokens: 30,
        cache_miss_tokens: 50, cache_write_tokens: 10, output_tokens: 30,
        reasoning_tokens: 10, answer_tokens: 20, credits: 0.4,
        session_credits: 4.2, derived_fields: ["cache_miss_tokens"],
      }},
    }};
    send({{
      jsonrpc: "2.0", method: "ui/notifications/tool-result",
      params: {{ structuredContent: grouped }},
    }});
    await flush();
    const usageTab = details.querySelectorAll(".wb-tab").find((item) => item.textContent === "用量");
    usageTab.dispatchEvent({{ type: "click" }});
    await flush();
    finish({{ summary: summary.textContent, details: details.textContent }});
    return;
  }}

  if (scenario === "usage_only_skips_workbench_rebuild") {{
    send({{
      jsonrpc: "2.0", method: "ui/notifications/tool-result",
      params: {{ structuredContent: groupSnapshot(5, 0.4, 100) }},
    }});
    await flush();
    const workbench = details.querySelector(".workbench");
    workbench.marker = "keep-workbench";
    refresh.dispatchEvent({{ type: "click" }});
    await flush();
    const call = latestCall();
    send({{ jsonrpc: "2.0", id: call.id, result: {{ structuredContent: groupSnapshot(5, 0.7, 150) }} }});
    await flush();
    finish({{
      marker: details.querySelector(".workbench").marker || null,
      summary: summary.textContent,
      stamp: stamp.textContent,
      syncState: panel.dataset.syncState,
    }});
    return;
  }}

  if (scenario === "unchanged_group_skips_rebuild") {{
    send({{
      jsonrpc: "2.0", method: "ui/notifications/tool-result",
      params: {{ structuredContent: groupSnapshot(5) }},
    }});
    await flush();
    const initialBody = details.querySelector(".wb-body");
    initialBody.marker = "keep-me";
    refresh.dispatchEvent({{ type: "click" }});
    await flush();
    const call = latestCall();
    send({{ jsonrpc: "2.0", id: call.id, result: {{ structuredContent: groupSnapshot(5) }} }});
    await flush();
    finish({{ marker: details.querySelector(".wb-body").marker || null }});
    return;
  }}

  const initialState = (scenario === "running_error_retry" || scenario === "running_invalid_retry") ? "running" : "completed";
  send({{
    jsonrpc: "2.0", method: "ui/notifications/tool-result",
    params: {{ structuredContent: snapshot(initialState) }},
  }});
  await flush();
  const before = {{
    taskState: panel.dataset.taskState,
    syncState: panel.dataset.syncState,
    stateText: state.textContent,
    details: details.textContent,
    stamp: stamp.textContent,
  }};

  refresh.dispatchEvent({{ type: "click" }});
  const during = {{
    taskState: panel.dataset.taskState,
    syncState: panel.dataset.syncState,
    stateText: state.textContent,
    details: details.textContent,
    stamp: stamp.textContent,
  }};
  await flush();
  const call = latestCall();
  if (scenario === "success" || scenario === "ttl") {{
    send({{ jsonrpc: "2.0", id: call.id, result: {{ structuredContent: snapshot("completed") }} }});
  }} else if (scenario === "empty") {{
    send({{ jsonrpc: "2.0", id: call.id, result: {{ structuredContent: null }} }});
  }} else if (scenario === "invalid" || scenario === "running_invalid_retry") {{
    send({{ jsonrpc: "2.0", id: call.id, result: {{ structuredContent: {{}} }} }});
  }} else {{
    send({{ jsonrpc: "2.0", id: call.id, error: {{ code: -32000, message: "refresh failed" }} }});
  }}
  await flush();
  const after = {{
    taskState: panel.dataset.taskState,
    syncState: panel.dataset.syncState,
    stateText: state.textContent,
    details: details.textContent,
    stamp: stamp.textContent,
    calls: calls().length,
  }};

  if (scenario === "ttl") {{
    window.dispatchEvent({{ type: "pageshow" }});
    await flush();
    finish({{ before, during, after, callsAfterSecondResume: calls().length }});
    return;
  }}
  if (scenario === "running_error_retry" || scenario === "running_invalid_retry") {{
    await new Promise((resolve) => setTimeout(resolve, 2300));
    await flush();
    finish({{ before, during, after, callsAfterRetryWindow: calls().length }});
    return;
  }}
  finish({{ before, during, after, requestedSelector: call.params.arguments }});
}})().catch((error) => {{
  process.stderr.write(String(error && error.stack || error));
  process.exitCode = 1;
}});
"""
    script_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".js", delete=False
        ) as script_file:
            script_file.write(node_program)
            script_path = script_file.name
        completed = subprocess.run(
            [node, script_path],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    finally:
        if script_path:
            os.unlink(script_path)
    if completed.returncode != 0:
        raise AssertionError(f"panel runtime harness failed: {completed.stderr}\n{completed.stdout}")
    return json.loads(completed.stdout)


class VoyagerPanelRuntimeTests(unittest.TestCase):
    def test_refresh_success_keeps_task_state_and_settles_sync_state(self) -> None:
        result = _run_panel_runtime("success")
        self.assertEqual(result["before"]["taskState"], "completed")
        self.assertEqual(result["before"]["syncState"], "idle")
        self.assertEqual(result["during"]["taskState"], "completed")
        self.assertEqual(result["during"]["syncState"], "syncing")
        self.assertEqual(result["during"]["stateText"], "已完成")
        self.assertEqual(result["during"]["details"], result["before"]["details"])
        self.assertEqual(result["after"]["taskState"], "completed")
        self.assertEqual(result["after"]["syncState"], "idle")
        self.assertEqual(result["after"]["stateText"], "已完成")
        self.assertEqual(result["during"]["stamp"], "正在同步最新状态…")
        self.assertNotIn("正在同步最新状态", result["after"]["stamp"])
        self.assertTrue(result["after"]["stamp"] == "" or result["after"]["stamp"].startswith("更新于 "))
        self.assertEqual(result["requestedSelector"], {"task_ids": ["task-1"], "limit": 200})

    def test_empty_response_preserves_verified_content_and_settles_idle(self) -> None:
        result = _run_panel_runtime("empty")
        self.assertEqual(result["after"]["taskState"], "completed")
        self.assertEqual(result["after"]["syncState"], "idle")
        self.assertEqual(result["after"]["details"], result["before"]["details"])

    def test_invalid_projection_preserves_verified_content_and_settles_idle(self) -> None:
        result = _run_panel_runtime("invalid")
        self.assertEqual(result["after"]["taskState"], "completed")
        self.assertEqual(result["after"]["syncState"], "idle")
        self.assertEqual(result["after"]["details"], result["before"]["details"])
        self.assertEqual(result["after"]["stamp"], "未收到有效状态，保留最近状态")

    def test_refresh_error_preserves_task_state_and_content(self) -> None:
        result = _run_panel_runtime("error")
        self.assertEqual(result["before"]["taskState"], "completed")
        self.assertEqual(result["before"]["syncState"], "idle")
        self.assertEqual(result["during"]["taskState"], "completed")
        self.assertEqual(result["during"]["syncState"], "syncing")
        self.assertEqual(result["after"]["taskState"], "completed")
        self.assertEqual(result["after"]["syncState"], "error")
        self.assertEqual(result["after"]["stateText"], "已完成")
        self.assertEqual(result["after"]["details"], result["before"]["details"])
        self.assertEqual(result["after"]["stamp"], "同步失败，保留最近状态")

    def test_resume_refresh_is_throttled_for_five_seconds(self) -> None:
        result = _run_panel_runtime("ttl")
        self.assertEqual(result["after"]["syncState"], "idle")
        self.assertEqual(result["callsAfterSecondResume"], 1)

    def test_running_task_keeps_polling_after_refresh_error(self) -> None:
        result = _run_panel_runtime("running_error_retry")
        self.assertEqual(result["after"]["taskState"], "running")
        self.assertEqual(result["after"]["syncState"], "error")
        self.assertGreaterEqual(result["callsAfterRetryWindow"], 2)

    def test_running_task_keeps_polling_after_invalid_refresh(self) -> None:
        result = _run_panel_runtime("running_invalid_retry")
        self.assertEqual(result["after"]["taskState"], "running")
        self.assertEqual(result["after"]["syncState"], "idle")
        self.assertGreaterEqual(result["callsAfterRetryWindow"], 2)

    def test_initial_resume_events_do_not_duplicate_host_tool_result_fetch(self) -> None:
        result = _run_panel_runtime("initial_resume_no_duplicate")
        self.assertEqual(result["callsBeforeResult"], 0)
        self.assertEqual(result["callsAfterResult"], 0)

    def test_group_refresh_restores_internal_workbench_scroll_position(self) -> None:
        result = _run_panel_runtime("group_scroll_restore")
        self.assertEqual(result["selectedTaskId"], "task-1")
        self.assertEqual(result["activeTab"], "执行活动")
        self.assertEqual(result["scrollTop"], 40)

    def test_group_refresh_follows_bottom_when_activity_view_was_pinned(self) -> None:
        result = _run_panel_runtime("group_scroll_follow_bottom")
        self.assertEqual(result["selectedTaskId"], "task-1")
        self.assertEqual(result["activeTab"], "执行活动")
        self.assertEqual(result["scrollTop"], result["scrollHeight"])

    def test_group_key_evidence_exposes_semantic_status_points(self) -> None:
        result = _run_panel_runtime("evidence_status_points")
        self.assertEqual(result["statuses"], ["completed", "running", "failed"])
        self.assertTrue(all("evidence-status-" in item for item in result["classes"]))
        self.assertEqual(result["itemLabels"], ["completed", "running", "failed"])
        self.assertEqual(result["labels"], ["已完成", "执行中", "异常"])
        self.assertEqual(result["text"], ["已完成 1 个", "执行中 1 个", "异常 1 个"])

    def test_usage_overview_unknown_values_receive_neutral_class(self) -> None:
        result = _run_panel_runtime("usage_unknown_styling")
        self.assertEqual(len(result["classes"]), 4)
        self.assertTrue(all("usage-card-unknown" in item for item in result["classes"]))
        self.assertTrue(all("暂无数据" in item for item in result["text"]))

    def test_single_task_usage_displays_token_credit_and_unknown_incomplete_cache_rate(self) -> None:
        result = _run_panel_runtime("single_usage_display")
        self.assertIn("Tokens：120", result["summary"])
        self.assertIn("Credits：0.75", result["summary"])
        self.assertIn("总 Token120", result["details"])
        self.assertIn("本轮 Credit0.75", result["details"])
        self.assertIn("缓存命中率暂无数据", result["details"])

    def test_group_usage_displays_only_supported_token_credit_fields(self) -> None:
        result = _run_panel_runtime("group_usage_display")
        self.assertIn("子任务 Tokens：220", result["summary"])
        self.assertIn("子任务 Credits：0.9", result["summary"])
        self.assertIn("当前 Tokens：120", result["summary"])
        self.assertIn("当前 Credits：0.4", result["summary"])
        for visible in (
            "总 Token120", "输入 Token90", "缓存命中30",
            "缓存写入10", "输出 Token30", "缓存未命中推导50", "缓存命中率", "本轮 Credit0.4",
            "会话累计 Credit4.2",
        ):
            self.assertIn(visible, result["details"])

    def test_usage_boundary_is_scoped_and_task_identity_remains_compact(self) -> None:
        result = _run_panel_runtime("ui_structure_boundary")
        self.assertEqual(result["tabs"], ["摘要", "完整回答", "执行活动", "文件变更", "用量"])
        self.assertEqual(result["identities"], ["qoder / lite", "codebuddy / hy3"])
        self.assertIn("当前 Tokens：120", result["initialUsageOverview"])
        self.assertIn("当前 Credits：0.4", result["initialUsageOverview"])
        self.assertIn("当前 Tokens：100", result["secondTaskUsageOverview"])
        self.assertIn("当前 Credits：0.5", result["secondTaskUsageOverview"])
        self.assertEqual(
            result["usageLabels"],
            [
                "总 Token", "输入 Token", "缓存命中", "缓存未命中推导",
                "缓存写入", "输出 Token", "缓存命中率推导", "本轮 Credit",
                "会话累计 Credit",
            ],
        )
        for hidden in ("Provider", "Model", "思考过程", "回复内容"):
            self.assertNotIn(hidden, result["usageText"])
        self.assertGreaterEqual(result["activityCount"], 1)
        self.assertEqual(result["activityMarkerCount"], result["activityCount"])
        self.assertEqual(result["fileItemCount"], 1)
        self.assertEqual(result["filePathText"], "src/example.py")

    def test_usage_only_refresh_does_not_rebuild_group_workbench(self) -> None:
        result = _run_panel_runtime("usage_only_skips_workbench_rebuild")
        self.assertEqual(result["marker"], "keep-workbench")
        self.assertIn("子任务 Tokens：150", result["summary"])
        self.assertIn("子任务 Credits：0.7", result["summary"])
        self.assertEqual(result["syncState"], "idle")
        self.assertNotIn("正在同步最新状态", result["stamp"])
        self.assertTrue(result["stamp"] == "" or result["stamp"].startswith("更新于 "))

    def test_unchanged_group_refresh_keeps_existing_workbench_dom(self) -> None:
        result = _run_panel_runtime("unchanged_group_skips_rebuild")
        self.assertEqual(result["marker"], "keep-me")


if __name__ == "__main__":
    unittest.main()
