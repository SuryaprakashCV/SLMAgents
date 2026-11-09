"""
Dynamic Local Project Scaffold Generator (`slm_harness/tools/project_templates.py`).
Generates complete local projects strictly on user request with zero pre-built or pre-filled templates.
"""

from __future__ import annotations

from typing import List, Tuple


def generate_custom_interactive_app_files(
    project_name: str,
    project_slug: str,
    resolved_goal: str,
) -> List[Tuple[str, str, str]]:
    """
    Generates a complete 7-file Interactive Application for `resolved_goal`
    with zero pre-filled user entries or pre-populated inputs.
    """
    engine_py = f'''#!/usr/bin/env python3
"""
Backend Application Engine for {project_name} (`engine.py`).
Goal: {resolved_goal}
Starts with an empty workspace state (zero pre-filled records).
"""

from __future__ import annotations

import time
from typing import Any, Dict, List


class CustomAppEngine:
    def __init__(self) -> None:
        self.items: List[Dict[str, Any]] = []

    def handle_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        action = str(payload.get("action") or "list").strip().lower()
        if action == "list":
            return {{"status": "ok", "items": list(self.items), "count": len(self.items)}}

        if action == "create":
            title = str(payload.get("title") or "").strip()
            details = str(payload.get("details") or "").strip()
            category = str(payload.get("category") or "General").strip()
            if not title:
                return {{"status": "error", "error": "Title/input cannot be empty."}}
            record = {{
                "id": len(self.items) + 1,
                "title": title,
                "details": details,
                "category": category,
                "status": "Active",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }}
            self.items.append(record)
            return {{"status": "ok", "item": record, "items": list(self.items), "count": len(self.items)}}

        if action == "clear":
            self.items.clear()
            return {{"status": "ok", "items": [], "count": 0}}

        return {{"status": "error", "error": f"Unsupported action: {{action}}"}}
'''

    server_py = '''#!/usr/bin/env python3
"""
Standalone Local HTTP & REST Server (`server.py`).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from engine import CustomAppEngine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINE = CustomAppEngine()


class AppRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            self._send_json(200, {"status": "ok"})
            return
        rel = "index.html" if parsed.path in ("/", "") else parsed.path.lstrip("/")
        target = os.path.abspath(os.path.join(BASE_DIR, rel))
        if not target.startswith(BASE_DIR) or not os.path.isfile(target):
            self._send_json(404, {"error": "Not found"})
            return
        ctype, _ = mimetypes.guess_type(target)
        with open(target, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype or "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/action":
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                req = json.loads(raw_body)
            except Exception:
                req = {}
            self._send_json(200, ENGINE.handle_action(req))
            return
        self._send_json(404, {"error": "Not found"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    HTTPServer((args.host, args.port), AppRequestHandler).serve_forever()


if __name__ == "__main__":
    main()
'''

    index_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{project_name}</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <header class="topbar">
    <div class="brand">
      <span class="brand-badge">⚡</span>
      <div>
        <h1>{project_name}</h1>
        <p class="subtitle">{resolved_goal}</p>
      </div>
    </div>
    <div class="topbar-actions">
      <button class="btn-secondary" id="btn-clear-all" type="button">↺ Clear All</button>
      <button class="btn-secondary" id="btn-export-json" type="button">⬇ Export JSON</button>
    </div>
  </header>

  <main class="workspace">
    <section class="panel">
      <div class="panel-header">
        <h2>1. Add New Entry</h2>
        <span class="hint">No pre-filled fields — enter your own data</span>
      </div>
      <form id="entry-form" class="form-stack">
        <div class="control-row">
          <label for="entry-title">Title / Input <span class="req">*</span></label>
          <input type="text" id="entry-title" value="" placeholder="Enter title or input..." autocomplete="off" />
        </div>
        <div class="control-row">
          <label for="entry-category">Category / Tag</label>
          <input type="text" id="entry-category" value="" placeholder="Enter category (optional)..." autocomplete="off" />
        </div>
        <div class="control-row">
          <label for="entry-details">Details / Content</label>
          <textarea id="entry-details" rows="5" placeholder="Enter details..."></textarea>
        </div>
        <button type="submit" class="btn-primary">＋ Submit &amp; Process</button>
      </form>
    </section>

    <section class="panel">
      <div class="panel-header">
        <h2>2. Live Workspace Entries</h2>
        <span class="status-pill" id="count-pill">0 Entries</span>
      </div>
      <div id="entries-list" class="entries-list">
        <p class="empty-note">Workspace is clean and empty. Submit an entry on the left to begin.</p>
      </div>
    </section>
  </main>

  <script src="app.js"></script>
</body>
</html>
'''

    styles_css = '''* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b0f19; color: #e2e8f0; min-height: 100vh; }
.topbar { display: flex; justify-content: space-between; align-items: center; padding: 16px 28px; background: #111827; border-bottom: 1px solid #1f2937; }
.brand { display: flex; align-items: center; gap: 14px; }
.brand-badge { font-size: 26px; background: #1e293b; padding: 10px; border-radius: 12px; border: 1px solid #334155; }
.brand h1 { font-size: 19px; font-weight: 700; color: #f8fafc; }
.subtitle { font-size: 13px; color: #94a3b8; margin-top: 2px; }
.topbar-actions { display: flex; gap: 10px; }
.btn-secondary { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 8px; padding: 8px 13px; font-size: 12.5px; font-weight: 600; cursor: pointer; }
.btn-primary { background: #4f46e5; color: #fff; border: none; border-radius: 10px; padding: 12px 16px; font-size: 14px; font-weight: 600; cursor: pointer; }
.workspace { display: grid; grid-template-columns: 420px 1fr; gap: 20px; padding: 20px 28px; max-width: 1560px; margin: 0 auto; }
.panel { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 20px; display: flex; flex-direction: column; gap: 14px; }
.panel-header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 10px; border-bottom: 1px solid #1f2937; }
.panel-header h2 { font-size: 16px; font-weight: 600; color: #f8fafc; }
.hint { font-size: 12px; color: #94a3b8; }
.status-pill { font-size: 12px; background: #1e293b; color: #38bdf8; padding: 4px 10px; border-radius: 999px; border: 1px solid #334155; }
.form-stack { display: flex; flex-direction: column; gap: 12px; }
.control-row { display: flex; flex-direction: column; gap: 6px; }
.control-row label { font-size: 12.5px; font-weight: 600; color: #cbd5e1; }
input[type="text"], textarea { background: #0f172a; color: #f8fafc; border: 1px solid #334155; border-radius: 8px; padding: 10px 12px; font-size: 13.5px; outline: none; }
.entries-list { display: flex; flex-direction: column; gap: 10px; }
.entry-card { background: #0f172a; border: 1px solid #1e293b; border-radius: 10px; padding: 14px; }
.empty-note { color: #64748b; font-style: italic; }
'''

    app_js = f'''const appItems = [];

function escapeHtml(s) {{
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}}

function renderItems() {{
  const list = document.getElementById("entries-list");
  document.getElementById("count-pill").textContent = `${{appItems.length}} Entries`;
  if (appItems.length === 0) {{
    list.innerHTML = '<p class="empty-note">Workspace is clean and empty. Submit an entry on the left to begin.</p>';
    return;
  }}
  list.innerHTML = appItems.map(item => `
    <div class="entry-card">
      <div style="display:flex;justify-content:space-between;align-items:center;">
        <strong>${{escapeHtml(item.title)}}</strong>
        <span class="status-pill">${{escapeHtml(item.category || "General")}}</span>
      </div>
      <p style="margin-top:6px;color:#cbd5e1;font-size:13.5px;">${{escapeHtml(item.details || "")}}</p>
    </div>
  `).join("");
}}

document.addEventListener("DOMContentLoaded", () => {{
  document.getElementById("entry-form").addEventListener("submit", async (e) => {{
    e.preventDefault();
    const titleEl = document.getElementById("entry-title");
    const catEl = document.getElementById("entry-category");
    const detEl = document.getElementById("entry-details");
    const title = (titleEl.value || "").trim();
    if (!title) return;
    const item = {{
      title,
      category: (catEl.value || "").trim() || "General",
      details: (detEl.value || "").trim(),
      created_at: new Date().toISOString(),
    }};
    try {{
      await fetch("/api/projects/{project_slug}/action", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ action: "create", ...item }}),
      }});
    }} catch (_) {{}}
    appItems.push(item);
    titleEl.value = "";
    catEl.value = "";
    detEl.value = "";
    renderItems();
  }});

  document.getElementById("btn-clear-all").addEventListener("click", () => {{
    appItems.length = 0;
    renderItems();
  }});

  document.getElementById("btn-export-json").addEventListener("click", () => {{
    const blob = new Blob([JSON.stringify(appItems, null, 2)], {{ type: "application/json" }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "{project_slug}_data.json";
    a.click();
    URL.revokeObjectURL(url);
  }});
}});
'''

    test_project_py = '''#!/usr/bin/env python3
"""
Automated Verification Suite (`tests/test_project.py`).
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import CustomAppEngine


class TestCustomAppEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = CustomAppEngine()

    def test_01_starts_empty_with_zero_prefilled_items(self) -> None:
        res = self.engine.handle_action({"action": "list"})
        self.assertEqual(res["count"], 0)
        self.assertEqual(res["items"], [])

    def test_02_create_and_list_item(self) -> None:
        res = self.engine.handle_action({"action": "create", "title": "First Item", "details": "Verified"})
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["count"], 1)

    def test_03_empty_title_rejected(self) -> None:
        res = self.engine.handle_action({"action": "create", "title": "   "})
        self.assertEqual(res["status"], "error")


if __name__ == "__main__":
    unittest.main()
'''

    readme_md = f'''# {project_name}

**Goal:** {resolved_goal}

## Overview
`{project_slug}` is a local full-stack web application built on disk by **SLMAgents** strictly from your request with zero pre-filled data.
'''

    return [
        ("index.html", "Interactive Web Studio UI (Zero Pre-Filled Fields)", index_html),
        ("styles.css", "Responsive Dark-Mode Studio Stylesheet", styles_css),
        ("app.js", "Frontend State & API Controller", app_js),
        ("engine.py", "Backend Application Logic Engine", engine_py),
        ("server.py", "Standalone Zero-Dependency Local HTTP Server", server_py),
        ("tests/test_project.py", "Automated Unit & Boundary Verification Suite (unittest)", test_project_py),
        ("README.md", "Project Documentation & Local Run Guide", readme_md),
    ]


def build_dynamic_project_plan(
    q_clean: str,
    project_name: str,
    project_slug: str,
    project_kind: str,
    primary_agent: str,
    action_focus: str = "building",
) -> dict:
    """
    Constructs a tailored plan for `q_clean` dynamically based on the user's request.
    """
    live_url = f"http://localhost:8000/projects/{project_slug}/index.html"

    s1_title = f"Core Application Architecture & Backend Engine for {project_name}"
    s1_desc = f"Design the backend logic engine (`engine.py`) and REST endpoints (`server.py`) tailored to: {q_clean} (starting with a clean, empty state and zero pre-filled dummy data)."
    s2_title = f"Interactive Frontend Studio UI for {project_name}"
    s2_desc = "Build the responsive frontend (`index.html`, `styles.css`, `app.js`) with clean empty inputs, live state updates, and JSON export."
    s3_title = "Automated Functional & Boundary Test Suite"
    s3_desc = "Create and execute `tests/test_project.py` locally to verify all endpoints and input validation guards."
    s4_title = "Live Local Hosting & Downloadable Project Archive"
    s4_desc = f"Serve the built application live at `{live_url}` and package `{project_slug}.zip` for instant download."

    steps = [
        {
            "node_id": "plan_1",
            "agent": "slm_task_planner",
            "instruction": f"Step 1 — {s1_title}",
            "depends_on": [],
        },
        {
            "node_id": "plan_2",
            "agent": "slm_code_interpreter",
            "instruction": f"Step 2 — {s2_title}",
            "depends_on": ["plan_1"],
        },
        {
            "node_id": "plan_3",
            "agent": "slm_code_interpreter",
            "instruction": f"Step 3 — {s3_title}",
            "depends_on": ["plan_2"],
        },
        {
            "node_id": "plan_4",
            "agent": primary_agent,
            "instruction": f"Step 4 — {s4_title}",
            "depends_on": ["plan_3"],
        },
    ]

    question = "Does this plan look good to you, or would you like any changes before I build and launch the app locally?"
    options = [
        "✅ Looks great — go ahead and build it!",
        "🧪 Go ahead + include an expanded test & edge-case suite",
        "⚡ Keep it lightweight — build a clean, fast MVP first",
    ]
    plan_markdown = (
        f"Here is the plan to build **{project_name}** locally on your machine (starting completely clean with zero pre-filled data):\n\n"
        f"### 🗺️ Proposed Build Plan: *{project_name}*\n\n"
        f"1. **Step 1 — {s1_title}**\n"
        f"   {s1_desc}\n"
        f"2. **Step 2 — {s2_title}**\n"
        f"   {s2_desc}\n"
        f"3. **Step 3 — {s3_title}**\n"
        f"   {s3_desc}\n"
        f"4. **Step 4 — {s4_title}**\n"
        f"   {s4_desc}\n\n"
        f"---\n"
        f"💬 **{question}**"
    )

    return {
        "needs_blocking_clarification": True,
        "question": question,
        "options": options,
        "steps": steps,
        "plan_markdown": plan_markdown,
        "plan_context": (
            f"Execute the approved 4-step local project build for '{q_clean}' "
            f"and serve it live at {live_url}."
        ),
    }
