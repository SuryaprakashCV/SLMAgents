"""
Dynamic Zero-Pre-Fill Project Generators (`project_templates.py`).

Generates complete, multi-file local applications (`index.html`, `styles.css`, `app.js`,
`engine.py`, `server.py`, `tests/test_project.py`, `README.md`) with ZERO pre-filled
user data or pre-populated form values:
1. `generate_interviewer_app_files`: Interactive AI Interviewer Studio (chat-driven technical &
   behavioral interviewer, dynamic question progression, live answer evaluation & rubric scorecard).
2. `generate_resume_builder_app_files`: Interactive Conversational Resume Builder Studio (step-by-step
   chat interview that collects user details from scratch and renders a live downloadable resume).
3. `generate_custom_interactive_app_files`: Clean, interactive full-stack web studio for any custom
   user application goal with zero pre-filled dummy entries.
"""

from __future__ import annotations

from typing import List, Tuple


def generate_interviewer_app_files(
    project_name: str,
    project_slug: str,
    resolved_goal: str,
) -> List[Tuple[str, str, str]]:
    """
    Generates a complete 7-file Interactive AI Interviewer Application with zero pre-filled fields.
    """
    engine_py = '''#!/usr/bin/env python3
"""
Interactive AI Interviewer Engine (`engine.py`).

Starts with a 100% clean state (no pre-filled candidate details or answers).
Conducts a structured, adaptive technical & behavioral interview based on the role
and skills entered by the user at runtime, evaluates each candidate response on
Clarity, Technical Depth, Concrete Examples, and Structure, and compiles a live scorecard.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class AnswerEvaluation:
    question_index: int
    question_text: str
    category: str
    candidate_answer: str
    score: float
    clarity_score: float
    depth_score: float
    examples_score: float
    feedback: str
    strengths: List[str] = field(default_factory=list)
    improvements: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class InterviewEngine:
    """
    Pure-CPU Interactive Interview & Answer Evaluation Engine.
    """

    def build_question_bank(self, role: str, skills: List[str], experience_level: str = "") -> List[Dict[str, str]]:
        clean_role = (role or "").strip() or "Target Role"
        clean_skills = [s.strip() for s in (skills or []) if s and s.strip()]
        primary_skill = clean_skills[0] if clean_skills else clean_role
        secondary_skill = clean_skills[1] if len(clean_skills) > 1 else "system design and debugging"
        third_skill = clean_skills[2] if len(clean_skills) > 2 else "scalability and reliability"
        level_tag = f" ({experience_level.strip()})" if (experience_level or "").strip() else ""

        return [
            {
                "category": "Introduction & Background",
                "question": f"Welcome to your {clean_role}{level_tag} interview! To start, walk me through the most complex project you have built or worked on recently and your specific engineering responsibilities.",
                "focus": "ownership, architecture context, and communication clarity",
            },
            {
                "category": f"Core Technical Depth ({primary_skill})",
                "question": f"Focusing on {primary_skill}: what are the most critical design trade-offs, performance bottlenecks, or edge cases you watch out for in production, and how do you handle them?",
                "focus": f"deep practical mastery of {primary_skill} and trade-off analysis",
            },
            {
                "category": f"Problem Solving & Debugging ({secondary_skill})",
                "question": f"Suppose a critical workflow involving {secondary_skill} suddenly shows elevated latency or unexpected failures under load. How would you isolate the root cause step-by-step and verify your fix?",
                "focus": "systematic debugging methodology, observability, and verification",
            },
            {
                "category": f"Architecture & Scale ({third_skill})",
                "question": f"How would you design an end-to-end module for {clean_role} that needs to handle 10x traffic growth while maintaining strict reliability around {third_skill}?",
                "focus": "system design, modularity, failure recovery, and scaling strategy",
            },
            {
                "category": "Behavioral & Engineering Judgment",
                "question": f"Tell me about a time you had to make a difficult technical decision with incomplete requirements or tight deadlines. How did you align stakeholders and measure the outcome?",
                "focus": "STAR method (Situation, Task, Action, Result), trade-offs, and impact",
            },
        ]

    def evaluate_answer(
        self,
        question_index: int,
        question_text: str,
        category: str,
        candidate_answer: str,
        role: str = "",
        skills: Optional[List[str]] = None,
    ) -> AnswerEvaluation:
        ans = (candidate_answer or "").strip()
        if not ans:
            return AnswerEvaluation(
                question_index=question_index,
                question_text=question_text,
                category=category,
                candidate_answer="",
                score=0.0,
                clarity_score=0.0,
                depth_score=0.0,
                examples_score=0.0,
                feedback="No answer provided. Please share a concrete explanation with examples.",
                strengths=[],
                improvements=["Provide a complete answer covering approach, trade-offs, and measurable results."],
            )

        words = re.findall(r"\\S+", ans)
        word_count = len(words)
        sentences = [s.strip() for s in re.split(r"[.!?]+", ans) if s.strip()]

        # 1. Clarity & Structure score (0-100)
        if word_count >= 45:
            clarity = 92.0
        elif word_count >= 25:
            clarity = 82.0
        elif word_count >= 12:
            clarity = 68.0
        else:
            clarity = max(35.0, word_count * 4.5)

        if len(sentences) >= 2:
            clarity = min(98.0, clarity + 4.0)

        # 2. Technical Depth & Reasoning signals (0-100)
        tech_signals = re.findall(
            r"\\b(?:architecture|latency|throughput|cache|database|index|async|thread|memory|complexity|"
            r"trade-off|tradeoff|bottleneck|test|edge case|metric|monitor|log|profile|scale|api|schema|"
            r"security|validation|retry|timeout|queue|pipeline|model|algorithm|design|deploy|root cause)\\b",
            ans,
            flags=re.IGNORECASE,
        )
        skill_hits = 0
        for sk in (skills or []):
            if sk and sk.strip() and re.search(re.escape(sk.strip()), ans, re.IGNORECASE):
                skill_hits += 1

        depth = min(98.0, 55.0 + len(set(t.lower() for t in tech_signals)) * 7.5 + skill_hits * 8.0 + min(20.0, word_count * 0.35))
        if word_count < 8:
            depth = min(depth, 45.0)

        # 3. Concrete Examples & Impact signals (0-100)
        has_numbers = bool(re.search(r"\\b\\d+(?:\\.\\d+)?(?:%|ms|s|x|k|m|gb|mb|req|users|days|hours)?\\b", ans, re.IGNORECASE))
        has_example_markers = bool(
            re.search(r"\\b(?:for example|for instance|specifically|implemented|built|reduced|improved|measured|resulted|when i|in my)\\b", ans, re.IGNORECASE)
        )
        examples = 58.0
        if has_numbers:
            examples += 20.0
        if has_example_markers:
            examples += 16.0
        if word_count >= 35:
            examples += 4.0
        examples = min(98.0, examples)
        if word_count < 8:
            examples = min(examples, 40.0)

        overall = round(clarity * 0.30 + depth * 0.40 + examples * 0.30, 1)

        strengths: List[str] = []
        improvements: List[str] = []

        if clarity >= 80:
            strengths.append("Clear, well-structured explanation that is easy to follow.")
        else:
            improvements.append("Expand your answer into 3-4 structured sentences (Context -> Approach -> Outcome).")

        if depth >= 80:
            strengths.append("Strong technical depth and explicit discussion of engineering trade-offs.")
        else:
            improvements.append("Mention specific architectural trade-offs, edge cases, or failure modes you considered.")

        if examples >= 78:
            strengths.append("Backed by concrete implementation details or measurable impact.")
        else:
            improvements.append("Include a concrete example or quantifiable metric (e.g., latency reduction, test coverage, throughput).")

        if overall >= 85:
            verdict = "Strong hire-level answer."
        elif overall >= 70:
            verdict = "Solid response with good fundamentals."
        else:
            verdict = "Brief response — adding concrete technical details and metrics will strengthen it."

        feedback = f"{verdict} Scored {overall}/100 (Clarity: {clarity:.0f}, Depth: {depth:.0f}, Examples: {examples:.0f})."

        return AnswerEvaluation(
            question_index=question_index,
            question_text=question_text,
            category=category,
            candidate_answer=ans,
            score=overall,
            clarity_score=round(clarity, 1),
            depth_score=round(depth, 1),
            examples_score=round(examples, 1),
            feedback=feedback,
            strengths=strengths,
            improvements=improvements,
        )

    def handle_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        action = str(payload.get("action") or "start").strip().lower()
        role = str(payload.get("role") or "").strip()
        raw_skills = payload.get("skills") or []
        if isinstance(raw_skills, str):
            skills = [s.strip() for s in raw_skills.split(",") if s.strip()]
        else:
            skills = [str(s).strip() for s in raw_skills if str(s).strip()]
        experience_level = str(payload.get("experience_level") or "").strip()

        if action == "start":
            if not role:
                return {
                    "status": "error",
                    "error": "Please enter a target role before starting the interview.",
                }
            questions = self.build_question_bank(role=role, skills=skills, experience_level=experience_level)
            return {
                "status": "ok",
                "role": role,
                "skills": skills,
                "experience_level": experience_level,
                "questions": questions,
                "current_index": 0,
                "first_question": questions[0],
            }

        if action == "answer":
            q_idx = int(payload.get("question_index") or 0)
            q_text = str(payload.get("question_text") or "")
            cat = str(payload.get("category") or "Technical")
            ans = str(payload.get("answer") or "")
            ev = self.evaluate_answer(
                question_index=q_idx,
                question_text=q_text,
                category=cat,
                candidate_answer=ans,
                role=role,
                skills=skills,
            )
            questions = self.build_question_bank(role=role or "Software Engineer", skills=skills, experience_level=experience_level)
            next_idx = q_idx + 1
            next_q = questions[next_idx] if next_idx < len(questions) else None
            return {
                "status": "ok",
                "evaluation": ev.to_dict(),
                "next_index": next_idx if next_q else None,
                "next_question": next_q,
                "completed": next_q is None,
            }

        return {"status": "error", "error": f"Unknown action: {action}"}
'''

    server_py = '''#!/usr/bin/env python3
"""
Standalone Local Web Server for AI Interviewer Studio (`server.py`).

Run locally:
    python3 server.py --port 5050
Then open:
    http://127.0.0.1:5050
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from engine import InterviewEngine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINE = InterviewEngine()


class InterviewRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._send_json(200, {"status": "ok", "service": "AI Interviewer Studio"})
            return

        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.abspath(os.path.join(BASE_DIR, rel))
        if not target.startswith(BASE_DIR) or not os.path.isfile(target):
            self._send_json(404, {"error": f"Not found: {path}"})
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
        if parsed.path in ("/api/action", "/api/interview"):
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                req = json.loads(raw_body)
            except Exception:
                req = {}
            res = ENGINE.handle_action(req)
            self._send_json(200, res)
            return
        self._send_json(404, {"error": f"Unknown endpoint: {parsed.path}"})


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Interviewer Studio Local Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    httpd = HTTPServer((args.host, args.port), InterviewRequestHandler)
    print(f"AI Interviewer Studio running locally at http://{args.host}:{args.port}")
    httpd.serve_forever()


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
      <span class="brand-badge">🎤</span>
      <div>
        <h1>{project_name}</h1>
        <p class="subtitle">Interactive Technical &amp; Behavioral Interviewer with Real-Time Answer Evaluation &amp; Rubric Scoring</p>
      </div>
    </div>
    <div class="topbar-actions">
      <button class="btn-secondary" id="btn-reset-session" type="button">↺ Reset Session</button>
      <button class="btn-secondary" id="btn-export-transcript" type="button">⬇ Export Transcript &amp; Scorecard</button>
    </div>
  </header>

  <main class="workspace">
    <!-- Left Column: Interview Setup (Starts 100% Blank — Zero Pre-Filled Data) -->
    <section class="panel setup-panel">
      <div class="panel-header">
        <h2>1. Interview Configuration</h2>
        <span class="hint">Enter your details to begin</span>
      </div>

      <div class="control-row">
        <label for="candidate-name">Your Name (Optional)</label>
        <input type="text" id="candidate-name" value="" placeholder="Enter your name..." autocomplete="off" />
      </div>

      <div class="control-row">
        <label for="target-role">Target Role <span class="req">*</span></label>
        <input type="text" id="target-role" value="" placeholder="Enter role (e.g., Backend Engineer, Product Manager)..." autocomplete="off" />
      </div>

      <div class="control-row">
        <label for="experience-level">Experience Level (Optional)</label>
        <input type="text" id="experience-level" value="" placeholder="Enter experience level (e.g., Junior, Senior, Lead)..." autocomplete="off" />
      </div>

      <div class="control-row">
        <label for="focus-skills">Key Skills / Topics to Test</label>
        <input type="text" id="focus-skills" value="" placeholder="Comma-separated topics you want to be interviewed on..." autocomplete="off" />
      </div>

      <button class="btn-primary" id="btn-start-interview" type="button">▶ Start Live Interview</button>

      <div class="scorecard-box" id="scorecard-box">
        <h3>📊 Live Interview Scorecard</h3>
        <div class="score-grid">
          <div class="metric-card">
            <span class="metric-label">Overall Score</span>
            <span class="metric-val" id="score-overall">—</span>
          </div>
          <div class="metric-card">
            <span class="metric-label">Questions Answered</span>
            <span class="metric-val" id="score-progress">0 / 0</span>
          </div>
          <div class="metric-card">
            <span class="metric-label">Clarity Avg</span>
            <span class="metric-val" id="score-clarity">—</span>
          </div>
          <div class="metric-card">
            <span class="metric-label">Technical Depth</span>
            <span class="metric-val" id="score-depth">—</span>
          </div>
        </div>
        <div id="latest-feedback-box" class="feedback-list">
          <p class="empty-note">No answers evaluated yet. Configure your role above or type directly in the chat to begin.</p>
        </div>
      </div>
    </section>

    <!-- Right Column: Interactive Interview Chat -->
    <section class="panel chat-panel">
      <div class="panel-header">
        <h2>2. Live Interview Room</h2>
        <span class="status-pill" id="interview-status-pill">Waiting to Start</span>
      </div>

      <div class="chat-transcript" id="chat-transcript">
        <div class="msg assistant">
          <div class="msg-role">AI Interviewer</div>
          <div class="msg-bubble">
            Hello! Nothing is pre-filled — please enter the <strong>Target Role</strong> and <strong>Skills</strong> on the left and click <strong>Start Live Interview</strong> (or simply type the role you want to interview for right here in the chat) to begin Question 1.
          </div>
        </div>
      </div>

      <form class="chat-composer" id="chat-form">
        <textarea id="chat-input" rows="3" placeholder="Type your answer (or enter your target role to begin)..."></textarea>
        <div class="composer-actions">
          <span class="composer-tip">Press <strong>Ctrl+Enter</strong> or click <strong>Send Answer</strong></span>
          <button type="submit" class="btn-send" id="btn-send-answer">Send Answer ➤</button>
        </div>
      </form>
    </section>
  </main>

  <script src="app.js"></script>
</body>
</html>
'''

    styles_css = '''* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif;
  background: #0b0f19;
  color: #e2e8f0;
  min-height: 100vh;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 28px;
  background: #111827;
  border-bottom: 1px solid #1f2937;
}

.brand {
  display: flex;
  align-items: center;
  gap: 14px;
}

.brand-badge {
  font-size: 28px;
  background: #1e293b;
  padding: 10px;
  border-radius: 12px;
  border: 1px solid #334155;
}

.brand h1 {
  font-size: 19px;
  font-weight: 700;
  color: #f8fafc;
}

.subtitle {
  font-size: 13px;
  color: #94a3b8;
  margin-top: 2px;
}

.topbar-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.workspace {
  display: grid;
  grid-template-columns: 390px 1fr;
  gap: 20px;
  padding: 20px 28px;
  max-width: 1560px;
  margin: 0 auto;
  height: calc(100vh - 82px);
}

@media (max-width: 1024px) {
  .workspace {
    grid-template-columns: 1fr;
    height: auto;
  }
}

.panel {
  background: #111827;
  border: 1px solid #1f2937;
  border-radius: 14px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
}

.panel-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-bottom: 10px;
  border-bottom: 1px solid #1f2937;
}

.panel-header h2 {
  font-size: 16px;
  font-weight: 600;
  color: #f8fafc;
}

.hint {
  font-size: 12px;
  color: #94a3b8;
}

.req {
  color: #f87171;
}

.control-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.control-row label {
  font-size: 12.5px;
  font-weight: 600;
  color: #cbd5e1;
}

input[type="text"],
textarea {
  background: #0f172a;
  color: #f8fafc;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 13.5px;
  outline: none;
}

input[type="text"]:focus,
textarea:focus {
  border-color: #6366f1;
}

.btn-primary {
  background: linear-gradient(135deg, #4f46e5, #6366f1);
  color: #ffffff;
  border: none;
  border-radius: 10px;
  padding: 12px 16px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.btn-primary:hover {
  opacity: 0.95;
}

.btn-secondary {
  background: #1e293b;
  color: #e2e8f0;
  border: 1px solid #334155;
  border-radius: 8px;
  padding: 8px 13px;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
}

.scorecard-box {
  margin-top: 6px;
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 10px;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.scorecard-box h3 {
  font-size: 14px;
  color: #f8fafc;
}

.score-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}

.metric-card {
  background: #111827;
  border: 1px solid #1f2937;
  border-radius: 8px;
  padding: 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.metric-label {
  font-size: 11px;
  color: #94a3b8;
  text-transform: uppercase;
}

.metric-val {
  font-size: 18px;
  font-weight: 700;
  color: #f8fafc;
}

.feedback-list {
  font-size: 12.5px;
  color: #cbd5e1;
  line-height: 1.5;
}

.empty-note {
  color: #64748b;
  font-style: italic;
}

.status-pill {
  font-size: 12px;
  background: #1e293b;
  color: #38bdf8;
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid #334155;
}

.chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.chat-transcript {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 8px 4px;
}

.msg {
  display: flex;
  flex-direction: column;
  max-width: 86%;
  gap: 4px;
}

.msg.assistant {
  align-self: flex-start;
}

.msg.user {
  align-self: flex-end;
}

.msg-role {
  font-size: 11px;
  font-weight: 600;
  color: #94a3b8;
  padding: 0 4px;
}

.msg.user .msg-role {
  text-align: right;
}

.msg-bubble {
  padding: 12px 15px;
  border-radius: 12px;
  font-size: 13.8px;
  line-height: 1.55;
}

.msg.assistant .msg-bubble {
  background: #1e293b;
  border: 1px solid #334155;
  color: #f1f5f9;
}

.msg.user .msg-bubble {
  background: #4f46e5;
  color: #ffffff;
}

.eval-badge {
  display: inline-block;
  margin-top: 8px;
  padding: 6px 10px;
  border-radius: 8px;
  background: rgba(16, 185, 129, 0.14);
  border: 1px solid rgba(16, 185, 129, 0.4);
  color: #6ee7b7;
  font-size: 12.5px;
}

.chat-composer {
  border-top: 1px solid #1f2937;
  padding-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.composer-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.composer-tip {
  font-size: 12px;
  color: #64748b;
}

.btn-send {
  background: #10b981;
  color: #06281e;
  border: none;
  border-radius: 8px;
  padding: 9px 18px;
  font-size: 13.5px;
  font-weight: 700;
  cursor: pointer;
}
'''

    app_js = f'''/**
 * Frontend Controller for {project_name} (`app.js`).
 * Starts with zero pre-filled information — all inputs and state begin clean.
 */

const interviewState = {{
  started: false,
  candidateName: "",
  role: "",
  experienceLevel: "",
  skills: [],
  questions: [],
  currentIndex: 0,
  evaluations: [],
  transcript: [],
}};

function appendChatMessage(role, htmlContent) {{
  const container = document.getElementById("chat-transcript");
  const wrap = document.createElement("div");
  wrap.className = `msg ${{role}}`;
  const roleLabel = role === "assistant" ? "AI Interviewer" : (interviewState.candidateName || "You");
  wrap.innerHTML = `
    <div class="msg-role">${{roleLabel}}</div>
    <div class="msg-bubble">${{htmlContent}}</div>
  `;
  container.appendChild(wrap);
  container.scrollTop = container.scrollHeight;
  interviewState.transcript.push({{ role, content: htmlContent, timestamp: new Date().toISOString() }});
}}

function escapeHtml(str) {{
  return String(str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}}

async function callInterviewBackend(payload) {{
  const endpoints = ["/api/projects/{project_slug}/action", "/api/action"];
  for (const ep of endpoints) {{
    try {{
      const resp = await fetch(ep, {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(payload),
      }});
      if (resp.ok) {{
        return await resp.json();
      }}
    }} catch (err) {{
      // Fallback to next endpoint or local engine
    }}
  }}
  return localInterviewFallback(payload);
}}

function localInterviewFallback(payload) {{
  const role = (payload.role || "Target Role").trim();
  const skills = Array.isArray(payload.skills) ? payload.skills : String(payload.skills || "").split(",").map(s => s.trim()).filter(Boolean);
  const p1 = skills[0] || role;
  const p2 = skills[1] || "debugging and root-cause analysis";
  const p3 = skills[2] || "system scalability and reliability";
  const questions = [
    {{ category: "Introduction & Background", question: `Welcome to your ${{role}} interview! Walk me through the most complex project you have worked on recently and your core responsibilities.` }},
    {{ category: `Technical Depth (${{p1}})`, question: `Focusing on ${{p1}}: what are the most critical trade-offs or edge cases you handle in production?` }},
    {{ category: `Problem Solving (${{p2}})`, question: `Suppose a workflow involving ${{p2}} shows unexpected failures or high latency. How do you isolate and fix the root cause?` }},
    {{ category: `System Design (${{p3}})`, question: `How would you architect a module for ${{role}} to scale 10x while maintaining reliability around ${{p3}}?` }},
    {{ category: "Behavioral & Engineering Judgment", question: "Tell me about a time you had to make a tough technical decision under a tight deadline. How did you measure the result?" }},
  ];
  if (payload.action === "start") {{
    return {{ status: "ok", role, skills, questions, current_index: 0, first_question: questions[0] }};
  }}
  const ans = String(payload.answer || "").trim();
  const words = ans.split(/\\s+/).filter(Boolean).length;
  const clarity = Math.min(96, Math.max(40, words * 2.2));
  const depth = Math.min(96, 55 + Math.min(40, words * 1.1));
  const examples = /\\d|example|implemented|built|reduced|improved/i.test(ans) ? 88 : 65;
  const score = Math.round((clarity * 0.3 + depth * 0.4 + examples * 0.3) * 10) / 10;
  const qIdx = Number(payload.question_index || 0);
  const nextIdx = qIdx + 1;
  return {{
    status: "ok",
    evaluation: {{
      question_index: qIdx,
      question_text: payload.question_text || "",
      category: payload.category || "Interview",
      candidate_answer: ans,
      score,
      clarity_score: Math.round(clarity),
      depth_score: Math.round(depth),
      examples_score: Math.round(examples),
      feedback: `Scored ${{score}}/100 (Clarity: ${{Math.round(clarity)}}, Depth: ${{Math.round(depth)}}, Examples: ${{Math.round(examples)}}).`,
      strengths: words >= 20 ? ["Clear structure and relevant technical context."] : [],
      improvements: words < 25 ? ["Add concrete metrics and specific architectural trade-offs."] : [],
    }},
    next_index: nextIdx < questions.length ? nextIdx : null,
    next_question: nextIdx < questions.length ? questions[nextIdx] : null,
    completed: nextIdx >= questions.length,
  }};
}}

function updateScorecardUI() {{
  const evals = interviewState.evaluations;
  const totalQ = interviewState.questions.length || 5;
  document.getElementById("score-progress").textContent = `${{evals.length}} / ${{totalQ}}`;
  if (evals.length === 0) {{
    document.getElementById("score-overall").textContent = "—";
    document.getElementById("score-clarity").textContent = "—";
    document.getElementById("score-depth").textContent = "—";
    return;
  }}
  const avg = (arr) => (arr.reduce((a, b) => a + b, 0) / arr.length).toFixed(1);
  document.getElementById("score-overall").textContent = `${{avg(evals.map(e => e.score))}}/100`;
  document.getElementById("score-clarity").textContent = `${{avg(evals.map(e => e.clarity_score))}}`;
  document.getElementById("score-depth").textContent = `${{avg(evals.map(e => e.depth_score))}}`;

  const last = evals[evals.length - 1];
  const fbBox = document.getElementById("latest-feedback-box");
  const strengthsHtml = (last.strengths || []).map(s => `<li>✅ ${{escapeHtml(s)}}</li>`).join("");
  const impHtml = (last.improvements || []).map(s => `<li>💡 ${{escapeHtml(s)}}</li>`).join("");
  fbBox.innerHTML = `
    <div><strong>Latest Answer Evaluation (Q${{last.question_index + 1}}):</strong> ${{escapeHtml(last.feedback)}}</div>
    <ul style="margin-top:6px;padding-left:18px;">${{strengthsHtml}}${{impHtml}}</ul>
  `;
}}

async function startInterviewSession(customRole, customSkills) {{
  const nameInput = document.getElementById("candidate-name");
  const roleInput = document.getElementById("target-role");
  const expInput = document.getElementById("experience-level");
  const skillsInput = document.getElementById("focus-skills");

  const role = (customRole || roleInput.value || "").trim();
  if (!role) {{
    appendChatMessage("assistant", "Please enter the <strong>Target Role</strong> you want to interview for (either in the left panel or by typing it here in the chat).");
    roleInput.focus();
    return;
  }}

  if (customRole && !roleInput.value) {{
    roleInput.value = customRole;
  }}
  if (customSkills && !skillsInput.value) {{
    skillsInput.value = customSkills;
  }}

  interviewState.candidateName = (nameInput.value || "").trim();
  interviewState.role = role;
  interviewState.experienceLevel = (expInput.value || "").trim();
  interviewState.skills = (skillsInput.value || "")
    .split(",")
    .map(s => s.trim())
    .filter(Boolean);
  interviewState.evaluations = [];
  interviewState.currentIndex = 0;

  const res = await callInterviewBackend({{
    action: "start",
    role: interviewState.role,
    experience_level: interviewState.experienceLevel,
    skills: interviewState.skills,
  }});

  interviewState.started = true;
  interviewState.questions = res.questions || [];
  updateScorecardUI();

  const statusPill = document.getElementById("interview-status-pill");
  statusPill.textContent = `In Progress • Question 1 of ${{interviewState.questions.length}}`;

  const q0 = res.first_question || interviewState.questions[0];
  appendChatMessage(
    "assistant",
    `<div><strong>[Question 1 of ${{interviewState.questions.length}} — ${{escapeHtml(q0.category)}}]</strong></div><div style="margin-top:6px;">${{escapeHtml(q0.question)}}</div>`
  );
}}

async function handleCandidateSubmission(e) {{
  if (e) e.preventDefault();
  const input = document.getElementById("chat-input");
  const text = (input.value || "").trim();
  if (!text) return;
  input.value = "";

  appendChatMessage("user", escapeHtml(text));

  if (!interviewState.started) {{
    // User typed the role/skills directly in the chat box to start
    await startInterviewSession(text, "");
    return;
  }}

  const currentQ = interviewState.questions[interviewState.currentIndex];
  if (!currentQ) {{
    appendChatMessage("assistant", "Your interview session is complete! Click <strong>Reset Session</strong> to start a new interview or <strong>Export Transcript & Scorecard</strong> to download your results.");
    return;
  }}

  const res = await callInterviewBackend({{
    action: "answer",
    role: interviewState.role,
    experience_level: interviewState.experienceLevel,
    skills: interviewState.skills,
    question_index: interviewState.currentIndex,
    question_text: currentQ.question,
    category: currentQ.category,
    answer: text,
  }});

  if (res.evaluation) {{
    interviewState.evaluations.push(res.evaluation);
    updateScorecardUI();
  }}

  const ev = res.evaluation || {{}};
  const evalSummaryHtml = `<div class="eval-badge">📊 <strong>Answer Score: ${{ev.score}}/100</strong> — ${{escapeHtml(ev.feedback || "")}}</div>`;

  if (res.next_question && !res.completed) {{
    interviewState.currentIndex = res.next_index;
    const qNum = interviewState.currentIndex + 1;
    const totalQ = interviewState.questions.length;
    document.getElementById("interview-status-pill").textContent = `In Progress • Question ${{qNum}} of ${{totalQ}}`;
    appendChatMessage(
      "assistant",
      `${{evalSummaryHtml}}<div style="margin-top:10px;"><strong>[Question ${{qNum}} of ${{totalQ}} — ${{escapeHtml(res.next_question.category)}}]</strong></div><div style="margin-top:6px;">${{escapeHtml(res.next_question.question)}}</div>`
    );
  }} else {{
    document.getElementById("interview-status-pill").textContent = "Interview Completed ✅";
    const avgScore = (
      interviewState.evaluations.reduce((s, item) => s + (item.score || 0), 0) /
      Math.max(1, interviewState.evaluations.length)
    ).toFixed(1);
    appendChatMessage(
      "assistant",
      `${{evalSummaryHtml}}<div style="margin-top:12px;">🎉 <strong>Interview Complete!</strong> You answered all ${{interviewState.questions.length}} questions for <strong>${{escapeHtml(interviewState.role)}}</strong> with an overall average score of <strong>${{avgScore}}/100</strong>. You can export your full scorecard using the top-right button.</div>`
    );
  }}
}}

document.addEventListener("DOMContentLoaded", () => {{
  document.getElementById("btn-start-interview").addEventListener("click", () => startInterviewSession());
  document.getElementById("chat-form").addEventListener("submit", handleCandidateSubmission);
  document.getElementById("chat-input").addEventListener("keydown", (e) => {{
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {{
      handleCandidateSubmission(e);
    }}
  }});

  document.getElementById("btn-reset-session").addEventListener("click", () => {{
    interviewState.started = false;
    interviewState.questions = [];
    interviewState.currentIndex = 0;
    interviewState.evaluations = [];
    document.getElementById("candidate-name").value = "";
    document.getElementById("target-role").value = "";
    document.getElementById("experience-level").value = "";
    document.getElementById("focus-skills").value = "";
    document.getElementById("interview-status-pill").textContent = "Waiting to Start";
    document.getElementById("chat-transcript").innerHTML = `
      <div class="msg assistant">
        <div class="msg-role">AI Interviewer</div>
        <div class="msg-bubble">
          Session reset! Enter your <strong>Target Role</strong> and <strong>Skills</strong> on the left (or type them in the chat) to start a fresh interview.
        </div>
      </div>
    `;
    updateScorecardUI();
    document.getElementById("latest-feedback-box").innerHTML = '<p class="empty-note">No answers evaluated yet. Configure your role above or type directly in the chat to begin.</p>';
  }});

  document.getElementById("btn-export-transcript").addEventListener("click", () => {{
    const blob = new Blob([JSON.stringify(interviewState, null, 2)], {{ type: "application/json" }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "interview_scorecard_transcript.json";
    a.click();
    URL.revokeObjectURL(url);
  }});
}});
'''

    test_project_py = '''#!/usr/bin/env python3
"""
Automated Unit & Edge-Case Verification Suite for AI Interviewer Studio (`tests/test_project.py`).
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import InterviewEngine


class TestInterviewEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = InterviewEngine()

    def test_01_ui_has_no_prefilled_input_values(self) -> None:
        html_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn('id="target-role" value=""', html)
        self.assertIn('id="focus-skills" value=""', html)

    def test_02_start_requires_non_empty_role(self) -> None:
        res = self.engine.handle_action({"action": "start", "role": "   "})
        self.assertEqual(res["status"], "error")

    def test_03_build_question_bank_tailored_to_role_and_skills(self) -> None:
        res = self.engine.handle_action({
            "action": "start",
            "role": "Backend Python Engineer",
            "skills": ["FastAPI", "PostgreSQL", "Redis"],
            "experience_level": "Senior",
        })
        self.assertEqual(res["status"], "ok")
        self.assertEqual(len(res["questions"]), 5)
        self.assertIn("FastAPI", res["questions"][1]["question"])

    def test_04_evaluate_detailed_answer_scores_high(self) -> None:
        ev = self.engine.evaluate_answer(
            question_index=0,
            question_text="How do you optimize API latency?",
            category="Technical Depth",
            candidate_answer=(
                "For example, when I built our FastAPI payment service, p99 latency spiked to 420ms. "
                "I profiled the database queries, added a composite index in PostgreSQL, and introduced "
                "a Redis read-through cache with a 60s TTL, which reduced p99 latency by 72% down to 115ms."
            ),
            role="Backend Engineer",
            skills=["FastAPI", "PostgreSQL", "Redis"],
        )
        self.assertGreaterEqual(ev.score, 80.0)
        self.assertTrue(len(ev.strengths) >= 1)

    def test_05_evaluate_empty_answer_boundary(self) -> None:
        ev = self.engine.evaluate_answer(
            question_index=0,
            question_text="Explain your architecture.",
            category="Technical",
            candidate_answer="   ",
        )
        self.assertEqual(ev.score, 0.0)
        self.assertGreaterEqual(len(ev.improvements), 1)

    def test_06_question_progression_and_completion(self) -> None:
        res = self.engine.handle_action({
            "action": "answer",
            "role": "Software Engineer",
            "skills": ["Python"],
            "question_index": 4,
            "question_text": "Final question",
            "category": "Behavioral",
            "answer": "I aligned the team around measurable milestones and delivered the release on schedule.",
        })
        self.assertEqual(res["status"], "ok")
        self.assertTrue(res["completed"])
        self.assertIsNone(res["next_question"])


if __name__ == "__main__":
    unittest.main()
'''

    readme_md = f'''# {project_name}

**Goal:** {resolved_goal}

## Overview
`{project_slug}` is a 100% local, interactive AI Interviewer web application built on disk by **SLMAgents**. All inputs start completely empty with zero pre-filled candidate data.

## Quick Start
```bash
python3 -m unittest discover -s tests -v
python3 server.py --port 5050
```
'''

    return [
        ("index.html", "Interactive Interviewer Studio UI (Zero Pre-Filled Fields)", index_html),
        ("styles.css", "Responsive Dark-Mode Interview Studio Stylesheet", styles_css),
        ("app.js", "Live Chat Interview & Real-Time Scorecard Controller", app_js),
        ("engine.py", "Adaptive Question Generator & Multi-Metric Answer Evaluator", engine_py),
        ("server.py", "Standalone Zero-Dependency Local HTTP & REST Server", server_py),
        ("tests/test_project.py", "Automated Unit & Boundary Verification Suite (unittest)", test_project_py),
        ("README.md", "Project Documentation & Local Run Guide", readme_md),
    ]


def generate_resume_builder_app_files(
    project_name: str,
    project_slug: str,
    resolved_goal: str,
) -> List[Tuple[str, str, str]]:
    """
    Generates a complete 7-file Interactive Conversational Resume Builder App with zero pre-filled fields.
    """
    engine_py = '''#!/usr/bin/env python3
"""
Interactive Conversational Resume Builder Engine (`engine.py`).

Interviews the user step-by-step via a chat interface (starting with 100% empty fields)
and compiles a clean, ATS-ready HTML & Markdown resume in real time.
"""

from __future__ import annotations

from typing import Any, Dict, List


INTERVIEW_STEPS: List[Dict[str, str]] = [
    {
        "field": "full_name",
        "label": "Full Name",
        "prompt": "What is your **Full Name** as you want it to appear at the top of your resume?",
    },
    {
        "field": "headline",
        "label": "Target Role / Professional Title",
        "prompt": "What is your **Professional Title or Target Role** (e.g., Senior Backend Engineer, Data Analyst)?",
    },
    {
        "field": "contact",
        "label": "Contact Information",
        "prompt": "Please share your **Contact Details** (Email, Phone, Location, LinkedIn/GitHub URL).",
    },
    {
        "field": "summary",
        "label": "Executive Summary",
        "prompt": "Write a brief **2-3 sentence Professional Summary** highlighting your experience and core strengths.",
    },
    {
        "field": "experience",
        "label": "Work Experience",
        "prompt": "Share your **Work Experience** (Company, Role, Dates, and key measurable achievements — separate multiple roles or bullet points with newlines or semicolons).",
    },
    {
        "field": "skills",
        "label": "Core Skills & Tools",
        "prompt": "List your **Core Technical & Domain Skills** (comma-separated).",
    },
    {
        "field": "education",
        "label": "Education & Certifications",
        "prompt": "Finally, share your **Education, Degrees, or Key Projects/Certifications**.",
    },
]


class ResumeBuilderEngine:
    """
    Stateful step-by-step conversational resume builder engine.
    """

    def get_steps(self) -> List[Dict[str, str]]:
        return INTERVIEW_STEPS

    def render_markdown(self, profile: Dict[str, Any]) -> str:
        name = str(profile.get("full_name") or "").strip()
        headline = str(profile.get("headline") or "").strip()
        contact = str(profile.get("contact") or "").strip()
        summary = str(profile.get("summary") or "").strip()
        experience = str(profile.get("experience") or "").strip()
        skills = str(profile.get("skills") or "").strip()
        education = str(profile.get("education") or "").strip()

        if not any([name, headline, contact, summary, experience, skills, education]):
            return ""

        lines: List[str] = []
        if name:
            lines.append(f"# {name}")
        if headline:
            lines.append(f"**{headline}**")
        if contact:
            lines.append(f"{contact}")
        lines.append("")
        if summary:
            lines.append("## Professional Summary")
            lines.append(summary)
            lines.append("")
        if experience:
            lines.append("## Work Experience")
            for raw_item in experience.replace(";", "\\n").splitlines():
                clean_item = raw_item.strip(" -•\\t")
                if clean_item:
                    lines.append(f"- {clean_item}")
            lines.append("")
        if skills:
            lines.append("## Core Skills")
            skill_list = [s.strip() for s in skills.split(",") if s.strip()]
            lines.append(", ".join(skill_list) if skill_list else skills)
            lines.append("")
        if education:
            lines.append("## Education & Projects")
            for raw_ed in education.replace(";", "\\n").splitlines():
                clean_ed = raw_ed.strip(" -•\\t")
                if clean_ed:
                    lines.append(f"- {clean_ed}")
            lines.append("")

        return "\\n".join(lines).strip()

    def handle_action(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        step_index = int(payload.get("step_index") or 0)
        answer = str(payload.get("answer") or "").strip()
        profile = dict(payload.get("profile") or {})

        if not answer:
            return {"status": "error", "error": "Please enter a response to continue building your resume."}

        if 0 <= step_index < len(INTERVIEW_STEPS):
            field_key = INTERVIEW_STEPS[step_index]["field"]
            profile[field_key] = answer

        next_index = step_index + 1
        next_step = INTERVIEW_STEPS[next_index] if next_index < len(INTERVIEW_STEPS) else None
        md = self.render_markdown(profile)

        return {
            "status": "ok",
            "profile": profile,
            "markdown": md,
            "next_index": next_index if next_step else None,
            "next_prompt": next_step["prompt"] if next_step else "Your resume is complete! You can edit any field on the right or download your finished resume.",
            "completed": next_step is None,
        }
'''

    server_py = '''#!/usr/bin/env python3
"""
Standalone Local Web Server for Interactive Resume Builder Studio (`server.py`).
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from engine import ResumeBuilderEngine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENGINE = ResumeBuilderEngine()


class ResumeRequestHandler(BaseHTTPRequestHandler):
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
        path = parsed.path
        if path == "/api/health":
            self._send_json(200, {"status": "ok", "service": "Interactive Resume Builder Studio"})
            return
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.abspath(os.path.join(BASE_DIR, rel))
        if not target.startswith(BASE_DIR) or not os.path.isfile(target):
            self._send_json(404, {"error": f"Not found: {path}"})
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
        self._send_json(404, {"error": f"Unknown endpoint: {parsed.path}"})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5050)
    args = parser.parse_args()
    httpd = HTTPServer((args.host, args.port), ResumeRequestHandler)
    print(f"Resume Builder running at http://{args.host}:{args.port}")
    httpd.serve_forever()


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
      <span class="brand-badge">📄</span>
      <div>
        <h1>{project_name}</h1>
        <p class="subtitle">Conversational Step-by-Step Resume Builder — Starts 100% Blank &amp; Builds Your Resume as You Chat</p>
      </div>
    </div>
    <div class="topbar-actions">
      <button class="btn-secondary" id="btn-reset" type="button">↺ Clear &amp; Restart</button>
      <button class="btn-secondary" id="btn-download-md" type="button">⬇ Download Markdown (.md)</button>
      <button class="btn-primary-sm" id="btn-print-pdf" type="button">🖨️ Print / Save PDF</button>
    </div>
  </header>

  <main class="workspace">
    <!-- Left Pane: Interactive Chat Assistant -->
    <section class="panel chat-panel">
      <div class="panel-header">
        <h2>1. Interactive Resume Assistant</h2>
        <span class="status-pill" id="step-badge">Step 1 of 7</span>
      </div>

      <div class="chat-transcript" id="chat-transcript">
        <div class="msg assistant">
          <div class="msg-role">Resume Assistant</div>
          <div class="msg-bubble">
            Hi! I will interview you step-by-step and build your resume live on the right (nothing is pre-filled).<br><br>
            <strong>Question 1 of 7:</strong> What is your <strong>Full Name</strong> as you want it to appear at the top of your resume?
          </div>
        </div>
      </div>

      <form class="chat-composer" id="chat-form">
        <textarea id="chat-input" rows="3" placeholder="Type your answer here..."></textarea>
        <div class="composer-actions">
          <span class="composer-tip">Press <strong>Ctrl+Enter</strong> or click <strong>Send</strong></span>
          <button type="submit" class="btn-send">Send ➤</button>
        </div>
      </form>
    </section>

    <!-- Right Pane: Live Resume Document Preview (Starts 100% Empty) -->
    <section class="panel preview-panel">
      <div class="panel-header">
        <h2>2. Live Resume Preview</h2>
        <span class="hint">Updates automatically as you answer each question</span>
      </div>
      <div class="resume-sheet" id="resume-sheet">
        <p class="empty-resume-msg" id="empty-resume-msg">Your resume is currently blank. Reply to Question 1 in the chat on the left to start building your resume in real time.</p>
        <div id="resume-rendered-content"></div>
      </div>
    </section>
  </main>

  <script src="app.js"></script>
</body>
</html>
'''

    styles_css = '''* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif; background: #0b0f19; color: #e2e8f0; min-height: 100vh; }
.topbar { display: flex; justify-content: space-between; align-items: center; padding: 16px 28px; background: #111827; border-bottom: 1px solid #1f2937; }
.brand { display: flex; align-items: center; gap: 14px; }
.brand-badge { font-size: 26px; background: #1e293b; padding: 10px; border-radius: 12px; border: 1px solid #334155; }
.brand h1 { font-size: 19px; font-weight: 700; color: #f8fafc; }
.subtitle { font-size: 13px; color: #94a3b8; margin-top: 2px; }
.topbar-actions { display: flex; gap: 10px; }
.btn-secondary { background: #1e293b; color: #e2e8f0; border: 1px solid #334155; border-radius: 8px; padding: 8px 13px; font-size: 12.5px; font-weight: 600; cursor: pointer; }
.btn-primary-sm { background: #4f46e5; color: #fff; border: none; border-radius: 8px; padding: 8px 14px; font-size: 12.5px; font-weight: 600; cursor: pointer; }
.workspace { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; padding: 20px 28px; max-width: 1560px; margin: 0 auto; height: calc(100vh - 82px); }
.panel { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 20px; display: flex; flex-direction: column; gap: 14px; overflow-y: auto; }
.panel-header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 10px; border-bottom: 1px solid #1f2937; }
.panel-header h2 { font-size: 16px; font-weight: 600; color: #f8fafc; }
.status-pill { font-size: 12px; background: #1e293b; color: #38bdf8; padding: 4px 10px; border-radius: 999px; border: 1px solid #334155; }
.hint { font-size: 12px; color: #94a3b8; }
.chat-transcript { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; padding: 6px 2px; }
.msg { display: flex; flex-direction: column; max-width: 86%; gap: 4px; }
.msg.assistant { align-self: flex-start; }
.msg.user { align-self: flex-end; }
.msg-role { font-size: 11px; font-weight: 600; color: #94a3b8; }
.msg-bubble { padding: 12px 15px; border-radius: 12px; font-size: 13.8px; line-height: 1.55; }
.msg.assistant .msg-bubble { background: #1e293b; border: 1px solid #334155; color: #f1f5f9; }
.msg.user .msg-bubble { background: #4f46e5; color: #fff; }
.chat-composer { border-top: 1px solid #1f2937; padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
textarea { background: #0f172a; color: #f8fafc; border: 1px solid #334155; border-radius: 8px; padding: 10px 12px; font-size: 13.5px; outline: none; }
.composer-actions { display: flex; justify-content: space-between; align-items: center; }
.composer-tip { font-size: 12px; color: #64748b; }
.btn-send { background: #10b981; color: #06281e; border: none; border-radius: 8px; padding: 9px 18px; font-size: 13.5px; font-weight: 700; cursor: pointer; }
.resume-sheet { background: #ffffff; color: #0f172a; border-radius: 10px; padding: 28px 32px; flex: 1; overflow-y: auto; line-height: 1.6; }
.empty-resume-msg { color: #64748b; font-style: italic; text-align: center; margin-top: 40px; }
.resume-sheet h1 { font-size: 24px; color: #0f172a; margin-bottom: 4px; }
.resume-sheet h2 { font-size: 14px; text-transform: uppercase; letter-spacing: 0.05em; color: #334155; border-bottom: 1.5px solid #cbd5e1; padding-bottom: 4px; margin-top: 16px; margin-bottom: 8px; }
.resume-sheet ul { padding-left: 20px; }
'''

    app_js = f'''/**
 * Conversational Resume Builder Controller (`app.js`).
 * Starts 100% blank with zero pre-filled resume details.
 */

const STEPS = [
  {{ field: "full_name", label: "Full Name", prompt: "What is your <strong>Full Name</strong> as you want it to appear at the top of your resume?" }},
  {{ field: "headline", label: "Professional Title", prompt: "What is your <strong>Professional Title or Target Role</strong>?" }},
  {{ field: "contact", label: "Contact Details", prompt: "Please share your <strong>Contact Details</strong> (Email, Phone, Location, LinkedIn/GitHub)." }},
  {{ field: "summary", label: "Executive Summary", prompt: "Write a brief <strong>2-3 sentence Professional Summary</strong> highlighting your background." }},
  {{ field: "experience", label: "Work Experience", prompt: "Share your <strong>Work Experience</strong> (Company, Role, Dates, and key achievements — separate items with semicolons or newlines)." }},
  {{ field: "skills", label: "Core Skills", prompt: "List your <strong>Core Technical &amp; Domain Skills</strong> (comma-separated)." }},
  {{ field: "education", label: "Education & Projects", prompt: "Finally, share your <strong>Education, Degrees, or Key Projects</strong>." }},
];

const resumeState = {{
  stepIndex: 0,
  profile: {{}},
  markdown: "",
}};

function escapeHtml(s) {{
  return String(s || "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}}

function appendMessage(role, html) {{
  const transcript = document.getElementById("chat-transcript");
  const div = document.createElement("div");
  div.className = `msg ${{role}}`;
  div.innerHTML = `<div class="msg-role">${{role === "assistant" ? "Resume Assistant" : "You"}}</div><div class="msg-bubble">${{html}}</div>`;
  transcript.appendChild(div);
  transcript.scrollTop = transcript.scrollHeight;
}}

function renderResumePreview() {{
  const p = resumeState.profile;
  const emptyEl = document.getElementById("empty-resume-msg");
  const out = document.getElementById("resume-rendered-content");
  const hasAny = Object.values(p).some(v => String(v || "").trim().length > 0);
  if (!hasAny) {{
    emptyEl.style.display = "block";
    out.innerHTML = "";
    return;
  }}
  emptyEl.style.display = "none";
  let html = "";
  if (p.full_name) html += `<h1>${{escapeHtml(p.full_name)}}</h1>`;
  if (p.headline) html += `<div style="font-weight:600;color:#475569;">${{escapeHtml(p.headline)}}</div>`;
  if (p.contact) html += `<div style="font-size:13px;color:#64748b;margin-top:2px;">${{escapeHtml(p.contact)}}</div>`;
  if (p.summary) html += `<h2>Professional Summary</h2><p>${{escapeHtml(p.summary)}}</p>`;
  if (p.experience) {{
    const items = String(p.experience).split(/[;\\n]+/).map(s => s.trim()).filter(Boolean);
    html += `<h2>Work Experience</h2><ul>${{items.map(i => `<li>${{escapeHtml(i)}}</li>`).join("")}}</ul>`;
  }}
  if (p.skills) html += `<h2>Core Skills</h2><p>${{escapeHtml(p.skills)}}</p>`;
  if (p.education) {{
    const eds = String(p.education).split(/[;\\n]+/).map(s => s.trim()).filter(Boolean);
    html += `<h2>Education &amp; Projects</h2><ul>${{eds.map(i => `<li>${{escapeHtml(i)}}</li>`).join("")}}</ul>`;
  }}
  out.innerHTML = html;
}}

async function handleChatSubmit(e) {{
  if (e) e.preventDefault();
  const input = document.getElementById("chat-input");
  const ans = (input.value || "").trim();
  if (!ans) return;
  input.value = "";

  appendMessage("user", escapeHtml(ans));

  try {{
    const resp = await fetch("/api/projects/{project_slug}/action", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        step_index: resumeState.stepIndex,
        answer: ans,
        profile: resumeState.profile,
      }}),
    }});
    if (resp.ok) {{
      const data = await resp.json();
      resumeState.profile = data.profile || resumeState.profile;
      resumeState.markdown = data.markdown || "";
    }} else {{
      throw new Error("fallback");
    }}
  }} catch (_) {{
    if (resumeState.stepIndex < STEPS.length) {{
      resumeState.profile[STEPS[resumeState.stepIndex].field] = ans;
    }}
  }}

  renderResumePreview();
  resumeState.stepIndex += 1;

  if (resumeState.stepIndex < STEPS.length) {{
    const nextStep = STEPS[resumeState.stepIndex];
    document.getElementById("step-badge").textContent = `Step ${{resumeState.stepIndex + 1}} of ${{STEPS.length}}`;
    appendMessage("assistant", `Saved! <strong>Question ${{resumeState.stepIndex + 1}} of ${{STEPS.length}}:</strong> ${{nextStep.prompt}}`);
  }} else {{
    document.getElementById("step-badge").textContent = "Resume Complete ✅";
    appendMessage("assistant", "🎉 <strong>Your resume is complete!</strong> Review it in the live preview on the right, and click <strong>Download Markdown (.md)</strong> or <strong>Print / Save PDF</strong> above.");
  }}
}}

document.addEventListener("DOMContentLoaded", () => {{
  document.getElementById("chat-form").addEventListener("submit", handleChatSubmit);
  document.getElementById("chat-input").addEventListener("keydown", (e) => {{
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") handleChatSubmit(e);
  }});
  document.getElementById("btn-reset").addEventListener("click", () => {{
    resumeState.stepIndex = 0;
    resumeState.profile = {{}};
    resumeState.markdown = "";
    renderResumePreview();
    document.getElementById("step-badge").textContent = "Step 1 of 7";
    document.getElementById("chat-transcript").innerHTML = `
      <div class="msg assistant">
        <div class="msg-role">Resume Assistant</div>
        <div class="msg-bubble">
          Cleared! Let's start fresh.<br><br>
          <strong>Question 1 of 7:</strong> What is your <strong>Full Name</strong> as you want it to appear at the top of your resume?
        </div>
      </div>
    `;
  }});
  document.getElementById("btn-download-md").addEventListener("click", () => {{
    const p = resumeState.profile;
    const md = `# ${{p.full_name || ""}}\\n**${{p.headline || ""}}**\\n${{p.contact || ""}}\\n\\n## Summary\\n${{p.summary || ""}}\\n\\n## Experience\\n${{p.experience || ""}}\\n\\n## Skills\\n${{p.skills || ""}}\\n\\n## Education\\n${{p.education || ""}}\\n`;
    const blob = new Blob([md], {{ type: "text/markdown" }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "resume.md";
    a.click();
    URL.revokeObjectURL(url);
  }});
  document.getElementById("btn-print-pdf").addEventListener("click", () => window.print());
}});
'''

    test_project_py = '''#!/usr/bin/env python3
"""
Automated Verification Suite for Interactive Resume Builder Studio (`tests/test_project.py`).
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import ResumeBuilderEngine


class TestResumeBuilderEngine(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ResumeBuilderEngine()

    def test_01_empty_profile_renders_empty_markdown(self) -> None:
        self.assertEqual(self.engine.render_markdown({}), "")

    def test_02_step_by_step_conversation_updates_profile(self) -> None:
        r1 = self.engine.handle_action({"step_index": 0, "answer": "Alex Rivera", "profile": {}})
        self.assertEqual(r1["status"], "ok")
        self.assertEqual(r1["profile"]["full_name"], "Alex Rivera")
        self.assertEqual(r1["next_index"], 1)

    def test_03_empty_answer_rejected(self) -> None:
        r = self.engine.handle_action({"step_index": 0, "answer": "   ", "profile": {}})
        self.assertEqual(r["status"], "error")

    def test_04_final_step_marks_completed(self) -> None:
        r = self.engine.handle_action({
            "step_index": 6,
            "answer": "B.Tech in Computer Science",
            "profile": {"full_name": "Alex Rivera"},
        })
        self.assertTrue(r["completed"])
        self.assertIn("Alex Rivera", r["markdown"])


if __name__ == "__main__":
    unittest.main()
'''

    readme_md = f'''# {project_name}

**Goal:** {resolved_goal}

## Overview
`{project_slug}` is a conversational step-by-step Resume Builder web application built on disk by **SLMAgents**. It starts 100% blank with zero pre-filled information and interviews the user in chat to construct their resume live.
'''

    return [
        ("index.html", "Interactive Chat + Live Resume Preview UI (Zero Pre-Filled Fields)", index_html),
        ("styles.css", "Responsive Split-Pane Studio & Print Stylesheet", styles_css),
        ("app.js", "Conversational Step-by-Step Intake & Live Preview Controller", app_js),
        ("engine.py", "Conversational Resume State Machine & Markdown Renderer", engine_py),
        ("server.py", "Standalone Local HTTP & REST API Server", server_py),
        ("tests/test_project.py", "Automated Unit & Boundary Verification Suite (unittest)", test_project_py),
        ("README.md", "Project Documentation & Local Run Guide", readme_md),
    ]


def generate_custom_interactive_app_files(
    project_name: str,
    project_slug: str,
    resolved_goal: str,
) -> List[Tuple[str, str, str]]:
    """
    Generates a complete 7-file Interactive Custom Application Studio for `resolved_goal`
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
          <label for="entry-title">Title / Prompt <span class="req">*</span></label>
          <input type="text" id="entry-title" value="" placeholder="Enter title or prompt..." autocomplete="off" />
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
`{project_slug}` is a local full-stack web application built on disk by **SLMAgents** with zero pre-filled data.
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
    Constructs a tailored, domain-specific plan for `q_clean` without any generic
    `<slug>` or `...` placeholders or pre-filled boilerplate.
    """
    live_url = f"http://localhost:8000/projects/{project_slug}/index.html"

    if project_kind == "interviewer_app":
        s1_title = "Interactive Interview Engine & Adaptive Question Bank"
        s1_desc = "Design the interview state machine (`engine.py`) with role/skill intake, adaptive technical & behavioral question generation, and multi-metric answer evaluation (Clarity, Depth, Examples) starting with zero pre-filled candidate details."
        s2_title = "Live Interview Chat Room & Real-Time Scorecard UI"
        s2_desc = "Build the interactive chat interface and scorecard UI (`index.html`, `styles.css`, `app.js`, `server.py`) where the user enters their target role and answers interview questions one by one."
        s3_title = "Automated Interview Flow & Scoring Verification"
        s3_desc = "Run unit & boundary tests (`tests/test_project.py`) verifying blank-state startup, question progression, and rubric scoring."
    elif project_kind == "resume_builder_app":
        s1_title = "Conversational Step-by-Step Resume Intake Engine"
        s1_desc = "Design the conversational resume builder engine (`engine.py`) that asks the user for their Name, Title, Contact, Summary, Experience, Skills, and Education starting from a clean, blank state."
        s2_title = "Split-Pane Chat Assistant & Live Resume Preview UI"
        s2_desc = "Build the interactive chat + live resume document preview (`index.html`, `styles.css`, `app.js`, `server.py`) with Markdown and PDF/Print export and zero pre-filled resume data."
        s3_title = "Automated State & Rendering Verification"
        s3_desc = "Run unit tests (`tests/test_project.py`) verifying blank startup, step-by-step intake, and live Markdown synthesis."
    elif project_kind == "autotest_suite":
        s1_title = "4-Plane Automated Evaluation & Security Engine"
        s1_desc = "Build the local evaluation engine (`engine.py`) covering Functional AST/DOM checks, Edge-Case boundary fuzzing, OWASP Security scanning, and CPU latency profiling."
        s2_title = "Clean Evaluation Studio UI & Local REST Server"
        s2_desc = "Build the interactive web studio (`index.html`, `styles.css`, `app.js`, `server.py`) starting with a clean, empty code workspace ready for your own code or file uploads."
        s3_title = "Automated Unit, Boundary & Security Test Suite"
        s3_desc = "Execute the verification test suite (`tests/test_project.py`) across valid, malformed, and vulnerable inputs."
    else:
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
