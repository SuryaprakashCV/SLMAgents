---
name: application-project-builder
title: "Full-Stack Application, Project Builder & Coding Studio"
description: "End-to-end Claude/Copilot/Codex-style coding & project builder skill: prepares a dynamic Architecture & Execution Plan, gathers complete project requirements (features, theme, workspace path), scaffolds full-stack multi-file apps with zero pre-filled dummy data, runs automated unittest verification, and launches the Split-Screen Live Canvas."
role: coding
agent: slm_code_interpreter
tone: tech
output_formats: [html, css, js, py, zip, md]
triggers: [build app, create app, build a project, create a project, web app, web application, build website, create website, full stack, automation test studio, test studio, build studio, build dashboard, build platform, build tool, coding requirement, application requirement, project requirement, change its design, modify app, generate the full source code]
tags: [coding, application, project, builder, webapp, fullstack, frontend, backend, testing, canvas]
enabled: true
source: builtin
version: 1
created_at: 2026-11-10T15:50:00Z
---

# Full-Stack Application, Project Builder & Coding Studio

## Purpose
Provides a complete, production-ready coding and application/project engineering workflow modeled after Claude Artifacts, GitHub Copilot Workspace, and OpenAI Codex Canvas. Any coding, web application, automation studio, API service, or multi-file software project requirement is handled through a disciplined 5-stage lifecycle.

## Mandatory 5-Stage Execution Lifecycle

### Stage 1 — Dynamic Architecture Plan & Complete Requirement Discovery (Turn 1)
1. **Never Skip Planning on New Projects**: Whenever a user asks to build or create a new application, website, studio, tool, or software project, **first** formulate a tailored Architecture & Execution Plan and ask clarifying requirement questions before writing files to disk.
2. **Dynamic Requirement Extraction**: Parse the user's exact prompt to list the captured functional capabilities, domain entities, and workflows—never substitute unrelated templates (such as Fibonacci calculators or static dummy apps).
3. **Complete Requirement Checklist**: Present clear requirement questions covering:
   - **Additional Features / Fields / Endpoints**: Specific metrics, validations, or custom workflows to include.
   - **Preferred Visual Theme Palette**: Slate Dark (`default`), Clean Light (`light-theme`), Royal Indigo (`theme-royal`), or Emerald Cyber (`theme-cyber`).
   - **Target Workspace Folder & Permissions**: Confirm the local folder path where files will be created and verified.
4. **Interactive Confirmation Options**: Provide 1-click selectable options plus a free-form requirement reply box so the user can approve the plan or specify custom requirements effortlessly.

### Stage 2 — Workspace Folder & Permission Governance
1. **Explicit Folder Access**: Respect the user's selected workspace directory via `FolderPermissionManager` (`create`, `edit`, `delete`, `run`, `execute`).
2. **Real On-Disk Scaffolding**: Write all project files directly inside `<workspace>/<project_slug>/` with SHA-256 integrity tracking.

### Stage 3 — Full-Stack Multi-File Implementation (Zero Pre-Filled Dummy Data)
1. **Complete Multi-File Architecture**:
   - `engine.py` — Pure-Python 3.11 domain engine with PEP-484 type annotations, explicit boundary guards, and `O(n)` / `O(log n)` complexity.
   - `server.py` — Zero-dependency local HTTP REST server (`GET /api/state`, `POST /api/execute`, `POST /api/reset`, `GET /api/export`).
   - `index.html`, `styles.css`, `app.js` — Responsive, accessible frontend studio with a 1-click 4-theme switcher (`🎨 Theme`), CSV & JSON export (`📊 CSV`, `⬇ JSON`), and live state telemetry.
   - `tests/test_project.py` — Automated `unittest` suite covering core logic, boundary/empty inputs, and API contracts.
   - `README.md` — Quickstart documentation and architecture overview.
2. **Strictly Zero Pre-Filled Data**: All input fields, URL bars, and tables must start completely clean and empty—never hardcode dummy rows or pre-populated form values.

### Stage 4 — Sandbox Execution & Automated Verification
1. **Deterministic Unittest Suite**: Execute `python3 -m unittest discover -s tests -p "test_*.py" -v` in an isolated subprocess and report pass/fail telemetry and execution latency.
2. **Algorithmic & Snippet Verification**: For standalone coding or algorithm requests, verify AST syntax, runtime assertions, and edge-case guards (`n <= 0`, empty inputs, type safety) in the Plane-7 sandbox.

### Stage 5 — Split-Screen Studio Canvas & Iterative In-Place Evolution
1. **Live Studio Canvas**: Mount the built application live at `/projects/<project_slug>/index.html`, package a downloadable `<project_slug>.zip` archive, and open the **Split-Screen Code, Live Preview & Terminal Canvas**.
2. **Conversational Iterative Edits**: When the user asks follow-up modifications (e.g., *"can you change its design"*, *"add CSV export"*, *"Modify [App]: ..."*), update the existing project files in place, re-verify `unittest`, and hot-reload the live canvas preview.
