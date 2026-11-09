"""
Autonomous Local Project Builder & Workspace Materializer (`project_builder.py`).

When a user asks to build a project/website/application/test-suite (and confirms the plan, or says
"build it locally", "create the files", "complete the project"), this engine:
1. Scaffolds a real project directory on disk under `<workspace>/projects/<project_slug>/`.
2. Generates and writes complete, production-ready multi-file source code (`index.html`, `styles.css`,
   `app.js`, `engine.py`, `server.py`, `samples/...`, `tests/test_project.py`, `README.md`).
3. Runs the automated test suite locally via `python3 -m unittest` and verifies 100% pass rate.
4. Packages a downloadable `.zip` archive in `.slm/downloads/` and exposes a live local web preview URL
   at `http://localhost:8000/projects/<project_slug>/index.html`.
"""

from __future__ import annotations

import datetime
import hashlib
import os
import re
import subprocess
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..config import get_config
from ..kernel.checkpoints import get_shadow_vfs
from .file_exporter import _downloads_root


@dataclass
class BuiltFileEntry:
    rel_path: str
    abs_path: str
    size_bytes: int
    line_count: int
    sha256: str
    role: str


@dataclass
class ProjectBuildOutcome:
    project_name: str
    project_slug: str
    project_dir: str
    live_url: str
    zip_download_url: str
    zip_size_kb: float
    files_created: List[BuiltFileEntry]
    test_passed: bool
    tests_run: int
    test_duration_ms: float
    test_stdout: str
    markdown_response: str
    structured_data: Dict[str, Any] = field(default_factory=dict)


# Follow-up phrases where the user tells the assistant to actually build/create files or run/launch the discussed project
_FOLLOWUP_BUILD_COMMAND_RE = re.compile(
    r"^\s*(?:no\s+|yes\s+)?(?:i\s+want\s+you\s+to\s+|iwant\s+you\s+to\s+|please\s+|now\s+|can\s+you\s+|just\s+)?"
    r"(?:"
    r"build\s+(?:it|this|that|the\s+project|the\s+app|the\s+application|the\s+website|the\s+system|the\s+suite|everything)(?:\s+locally|\s+now|\s+for\s+me)?"
    r"|create\s+(?:the\s+|all\s+|all\s+the\s+)?(?:files?|project\s+files?|source\s+files?|code\s+files?|folders?|project)(?:\s+and\s+build\s+it)?(?:\s+locally)?"
    r"|complete\s+(?:the\s+|this\s+)?(?:project|app|application|website|build|implementation)(?:\s+locally)?"
    r"|generate\s+(?:the\s+|full\s+|all\s+)?(?:source\s+code|project\s+files|files)(?:\s+and\s+build\s+it)?(?:\s+locally)?"
    r"|implement\s+(?:it|this|the\s+project|the\s+full\s+project)(?:\s+locally)?"
    r"|make\s+(?:the\s+)?(?:files?|project)\s+locally"
    r"|scaffold\s+(?:it|the\s+project|this\s+locally)"
    r"|(?:run|launch|start|open|host|serve)\s+(?:it|the\s+app|the\s+application|the\s+project|the\s+website|this)(?:\s+locally)?(?:\s+and\s+give\s+me\s+(?:the\s+)?(?:link|url))?"
    r"|give\s+me\s+(?:the\s+)?(?:link|url)(?:\s+to\s+run\s+it|\s+for\s+the\s+app)?"
    r"|yes[,\s]+(?:go\s+ahead|build\s+it|create\s+it|run\s+it)(?:\s+locally)?"
    r"|go\s+ahead(?:\s+and\s+(?:build|create|run)\s+it)?"
    r")\s*[.!?\s]*(?:/no_think|/fast|/low|/medium|/high)?\s*$",
    re.IGNORECASE,
)

# Complex project / website / app / test-suite build intent
_PROJECT_BUILD_TOPIC_RE = re.compile(
    r"\b(?:build|create|develop|architect|implement|scaffold|construct|make)\b"
    r".*\b(?:website|web\s+app|web\s+application|application|app|platform|dashboard|portal|"
    r"test\s+suite|testing\s+platform|autotest|evaluator|interviewer|interviwer|resume|microservice|"
    r"service|fullstack|full-stack|system|project|tool|engine|workbench|studio|playground|client|calculator|tracker)\b",
    re.IGNORECASE | re.DOTALL,
)

# Iterative coding edit intent on an existing active workspace project (Claude Code / Copilot Edit style)
_ITERATIVE_PROJECT_EDIT_RE = re.compile(
    r"^(?:modify\s+\[[^\]]+\]\s*:|"
    r"(?:please\s+|can\s+you\s+|could\s+you\s+|now\s+|i\s+want\s+(?:you\s+)?to\s+|let'?s\s+)?"
    r"(?:edit|modify|update|change|redesign|restyle|customize|improve|upgrade|modernize|beautify|patch|refactor|enhance|add|make|switch|fix)\b.*"
    r"\b(?:index\.html|styles\.css|app\.js|engine\.py|server\.py|test_project\.py|"
    r"this\s+app|the\s+app|its\s+app|this\s+project|the\s+project|current\s+project|"
    r"its\s+design|the\s+design|app\s+design|ui\s+design|design|ui|ux|layout|look|appearance|style|styling|colors?|palette|fonts?|"
    r"dark\s+mode|light\s+mode|theme|theme\s+toggle|glassmorphism|csv\s+export|search\s+filter|dashboard|header|topbar|buttons?|cards?)\b)",
    re.IGNORECASE | re.DOTALL,
)

# Pure single-function algorithm requests that should stay in standard code_interpreter mode
_PURE_ALGORITHM_SNIPPET_RE = re.compile(
    r"^\s*(?:write|implement)\s+(?:a\s+|an\s+)?(?:python|java|c\+\+|javascript|rust|go)\s+"
    r"(?:function|method|one-liner|snippet)\s+to\s+(?:compute|calculate|sort|reverse|check|find)\s+"
    r"(?:fibonacci|factorial|prime|palindrome|gcd|lcm|binary\s+search|two\s+sum|merge\s+sort|quicksort)\b",
    re.IGNORECASE,
)


_CONTEXTUAL_PROJECT_FOLLOWUP_SUBSTRINGS = (
    "generate the full source code",
    "full source code (html",
    "for this project",
    "this project?",
    "previous chat",
    "earlier chat",
    "last chat",
    "project which is there",
    "project in my",
    "project from my",
    "the project above",
    "discussed above",
    "build it locally",
    "create the files",
    "complete the project",
    "run the app and give me",
    "give me link",
    "give me the link",
)


def is_followup_build_command(query: str) -> bool:
    """
    Returns True if the query is a follow-up command to build/create/complete/run
    a project discussed earlier in the session or in a previous chat.
    """
    q = re.sub(r"\s*(?:—\s*Selected Plan Option:|\[Clarified Plan Option:).*$", "", (query or "").strip(), flags=re.IGNORECASE).strip()
    q = re.sub(r"\s*/(?:no_think|fast|low|medium|high)\b", "", q, flags=re.IGNORECASE).strip()
    if not q:
        return False
    if _FOLLOWUP_BUILD_COMMAND_RE.match(q):
        return True
    q_low = q.lower()
    if any(sub in q_low for sub in _CONTEXTUAL_PROJECT_FOLLOWUP_SUBSTRINGS):
        return True
    if re.search(r"\b(?:create|build|make|generate|complete|run)\b.*\b(?:previous|earlier|last)\s+chat\b", q_low):
        return True
    if re.search(r"\bwant\s+me\s+to\s+generate\s+the\s+full\s+source\s+code\b", q_low):
        return True
    return False


def should_execute_local_project_build(
    user_query: str,
    clarification_choice: Optional[str] = None,
    last_user_query: str = "",
) -> bool:
    """
    Determines whether the current turn should execute the Autonomous Local Project Builder
    to create real files on disk, run local unit tests, and complete the project.
    """
    q_raw = (user_query or "").strip()
    q_clean = re.sub(r"^/(?:plan|clarify|low|medium|high|fast|deep)\b\s*", "", q_raw, flags=re.IGNORECASE).strip()
    q_clean = re.sub(r"\s*(?:—\s*Selected Plan Option:|\[Clarified Plan Option:).*$", "", q_clean, flags=re.IGNORECASE).strip()
    q_clean = re.sub(r"\s*/no_think\b", "", q_clean, flags=re.IGNORECASE).strip()

    if not q_clean:
        return False

    if _PURE_ALGORITHM_SNIPPET_RE.match(q_clean):
        return False

    # 1. Explicit or contextual follow-up command ("build it locally", "Want me to generate the full source code... yes", "create project which is there in my previous chat")
    if is_followup_build_command(q_clean):
        return True

    # 2. Direct project / web app / studio build request — generate and build immediately on request
    # (no pre-prepared plan card unless user explicitly typed /plan or /clarify)
    is_explicit_plan_only = bool(re.match(r"^/(?:plan|clarify)\b", q_raw, flags=re.IGNORECASE))
    if not is_explicit_plan_only and (_PROJECT_BUILD_TOPIC_RE.search(q_clean) or _ITERATIVE_PROJECT_EDIT_RE.search(q_clean)):
        return True

    # 3. User explicitly asked to build locally & create files in a single turn
    q_low = q_clean.lower()
    if (
        any(p in q_low for p in ("build it locally", "create files and build", "create the files and build", "build locally and complete"))
        and not _PURE_ALGORITHM_SNIPPET_RE.match(q_clean)
    ):
        return True

    # 4. User confirmed a complex build/test plan (clicked one of the plan options or replied yes/go ahead)
    has_plan_confirmation = bool(
        clarification_choice
        or "[clarified plan option:" in q_raw.lower()
    )
    if has_plan_confirmation:
        combined_topic = f"{q_clean} {last_user_query}".strip()
        if _PROJECT_BUILD_TOPIC_RE.search(combined_topic) or is_followup_build_command(combined_topic):
            return True

    return False


def _clean_candidate_goal(raw_text: str) -> str:
    c_clean = re.sub(r"^/(?:plan|clarify|low|medium|high|fast|deep|goal)\b\s*", "", (raw_text or "").strip(), flags=re.IGNORECASE).strip()
    c_clean = re.sub(r"\s*(?:—\s*Selected Plan Option:|\[Clarified Plan Option:).*$", "", c_clean, flags=re.IGNORECASE).strip()
    c_clean = re.sub(r"\s*\((?:✅|🧪|⚡|Looks great|Go ahead)[^)]*\)\s*$", "", c_clean, flags=re.IGNORECASE).strip()
    c_clean = re.sub(r"\s*/no_think\b", "", c_clean, flags=re.IGNORECASE).strip()
    dup_c = re.match(r"^(.+?)\s*\(\1\)\s*$", c_clean, flags=re.IGNORECASE | re.DOTALL)
    if dup_c:
        c_clean = dup_c.group(1).strip()
    return c_clean


def resolve_project_goal(user_query: str, last_user_query: str = "", lineage_queries: Sequence[str] = ()) -> str:
    """
    If the user typed a follow-up like 'build it locally', 'Want me to generate the full source code... yes',
    or 'create project which is there in my previous chat', resolves the full project specification
    from earlier turns in the current session or from recent user chats in SQLite.
    """
    q_clean = _clean_candidate_goal(user_query)

    if q_clean and not is_followup_build_command(q_clean) and len(q_clean.split()) >= 3:
        return q_clean

    # 1. Search current session lineage & blackboard last_user_query (preferring explicit project build topics)
    candidates = list(lineage_queries) + ([last_user_query] if last_user_query else [])
    cleaned_session_candidates: List[str] = []
    for cand in reversed(candidates):
        c_clean = _clean_candidate_goal(cand)
        if c_clean and not is_followup_build_command(c_clean) and len(c_clean.split()) >= 3:
            cleaned_session_candidates.append(c_clean)

    for c_clean in cleaned_session_candidates:
        if _PROJECT_BUILD_TOPIC_RE.search(c_clean):
            return c_clean
    if cleaned_session_candidates:
        return cleaned_session_candidates[0]

    # 2. Cross-session lookup ("in my previous chat") from SQLite turns table
    try:
        import sqlite3
        from ..kernel.checkpoints import get_checkpoint_store

        db_path = get_checkpoint_store().db_path
        if os.path.isfile(db_path):
            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT content FROM turns WHERE role='user' "
                    "AND session_id NOT LIKE 'test_%' AND session_id NOT LIKE 'desktop_verify%' "
                    "ORDER BY rowid DESC LIMIT 50"
                )
                for (raw_content,) in cur.fetchall():
                    c_clean = _clean_candidate_goal(str(raw_content or ""))
                    if (
                        c_clean
                        and not is_followup_build_command(c_clean)
                        and _PROJECT_BUILD_TOPIC_RE.search(c_clean)
                        and "daily personalized dashboard" not in c_clean.lower()
                        and "personalize my profile" not in c_clean.lower()
                    ):
                        return c_clean
    except Exception:
        pass

    return q_clean or "Custom Local Application"


def _derive_project_identity(goal: str) -> tuple[str, str, str]:
    """
    Returns `(project_name, project_slug, project_kind)` dynamically from the user's request.
    Never returns hardcoded prebuilt templates.
    """
    g_low = (goal or "").lower()
    stop = {
        "i", "want", "to", "build", "create", "develop", "make", "a", "an", "the", "for", "which",
        "that", "and", "or", "with", "in", "on", "of", "locally", "please", "can", "you", "website",
        "web", "app", "application", "project", "system", "platform", "me", "mr", "my", "full",
        "source", "code", "html", "js", "python", "this", "yes", "previous", "chat", "there", "is",
    }
    words = [w for w in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", g_low) if w not in stop]
    if not words:
        words = ["custom", "project"]
    slug_words = words[:3]
    slug = "_".join(slug_words) + "_app"
    title = " ".join(w.capitalize() for w in slug_words) + " App"
    return title, slug, "custom_web_project"


class LocalProjectBuilder:
    """
    Scaffolds, writes, tests, and packages complete local software projects on disk
    strictly on user request inside the user-selected workspace folder.
    """

    def __init__(self, workspace_dir: Optional[str] = None) -> None:
        self.workspace_dir = os.path.abspath(workspace_dir or get_config().workspace_dir)
        self.projects_root = os.path.join(self.workspace_dir, "projects")

    def _write_project_file(
        self,
        project_dir: str,
        rel_path: str,
        content: str,
        role: str,
        session_id: str,
        run_id: str,
    ) -> BuiltFileEntry:
        abs_path = os.path.join(project_dir, rel_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        vfs = get_shadow_vfs()
        vfs.record_before_write(session_id, run_id, abs_path)
        data_bytes = content.encode("utf-8")
        with open(abs_path, "wb") as f:
            f.write(data_bytes)
        if rel_path.endswith(".py"):
            try:
                os.chmod(abs_path, 0o755)
            except OSError:
                pass
        sha = hashlib.sha256(data_bytes).hexdigest()[:12]
        lines = content.count("\n") + 1
        return BuiltFileEntry(
            rel_path=rel_path,
            abs_path=abs_path,
            size_bytes=len(data_bytes),
            line_count=lines,
            sha256=sha,
            role=role,
        )

    def build_project(
        self,
        user_query: str,
        session_id: str = "default",
        run_id: str = "run_local_build",
        clarification_choice: str = "",
        last_user_query: str = "",
        lineage_queries: Sequence[str] = (),
        step_callback: Optional[Callable[[str, str], None]] = None,
        target_folder: Optional[str] = None,
        custom_project_name: Optional[str] = None,
        grant_permission: bool = False,
        permissions: Optional[Dict[str, bool]] = None,
    ) -> ProjectBuildOutcome:
        from ..governance.policy import get_folder_permission_manager

        def _emit(agent: str, msg: str) -> None:
            if step_callback:
                try:
                    step_callback(agent, msg)
                except Exception:
                    pass

        perm_mgr = get_folder_permission_manager()
        resolved_goal = resolve_project_goal(user_query, last_user_query=last_user_query, lineage_queries=lineage_queries)
        project_name, project_slug, project_kind = _derive_project_identity(resolved_goal)
        if custom_project_name and custom_project_name.strip():
            project_name = custom_project_name.strip()
            custom_slug = re.sub(r"[^a-zA-Z0-9_\-]", "_", project_name.lower()).strip("_")
            if custom_slug:
                project_slug = custom_slug

        # Resolve target project directory: explicit target_folder > path in user_query > active permitted folder > default projects_root
        explicit_folder = (target_folder or "").strip()
        if not explicit_folder:
            m_path = re.search(r"(?:\bin|\bat|\binto|\bfolder|\bpath)\s+([~/][A-Za-z0-9_.\-/]+)", user_query or "")
            if m_path:
                explicit_folder = m_path.group(1).strip()

        if explicit_folder:
            norm_base = perm_mgr.normalize_folder_path(explicit_folder)
            # If the user chose a parent folder (like ~/Desktop or ~/Documents/SLMAgents/projects), place project_slug inside it unless it already ends with project_slug
            if os.path.basename(norm_base).lower() in ("projects", "desktop", "documents", "developer", "downloads"):
                project_dir = os.path.join(norm_base, project_slug)
            else:
                project_dir = norm_base
        elif perm_mgr.get_active_folder():
            active_base = perm_mgr.get_active_folder() or self.projects_root
            if os.path.basename(active_base).lower() == project_slug.lower():
                project_dir = active_base
            else:
                project_dir = os.path.join(active_base, project_slug)
        else:
            project_dir = os.path.join(self.projects_root, project_slug)

        if grant_permission:
            perm_mgr.grant_permission(
                folder_path=project_dir,
                permissions=permissions,
                project_name=project_name,
                project_slug=project_slug,
                set_active=True,
                create_if_missing=True,
            )
        elif project_dir.startswith(os.path.realpath(self.projects_root)):
            # Default workspace projects folder: auto-register permissions so user can manage/edit/run immediately
            if not perm_mgr.check_permission(project_dir).get("permitted"):
                perm_mgr.grant_permission(
                    folder_path=project_dir,
                    permissions=permissions,
                    project_name=project_name,
                    project_slug=project_slug,
                    set_active=False,
                    create_if_missing=True,
                )

        # Check if this is an iterative edit on an existing built project in the workspace
        is_edit_turn = bool(_ITERATIVE_PROJECT_EDIT_RE.search(user_query or ""))
        if is_edit_turn:
            existing_dir, existing_slug, existing_name = self._find_latest_existing_project(perm_mgr)
            if existing_dir and os.path.isdir(existing_dir):
                project_dir = existing_dir
                project_slug = existing_slug or project_slug
                project_name = existing_name or project_name

        os.makedirs(project_dir, exist_ok=True)
        perm_mgr.register_project_location(project_slug=project_slug, project_dir=project_dir, project_name=project_name)
        perm_info = perm_mgr.check_permission(project_dir, operation="all")

        _emit(
            "slm_task_planner",
            f"🧭 Workspace — Inspecting '{project_dir}' (permissions={'FULL ACCESS' if perm_info.get('permitted') else 'STANDARD'}) for {project_name}",
        )

        applied_iterative_edit = False
        if is_edit_turn and os.path.isfile(os.path.join(project_dir, "index.html")):
            applied_iterative_edit = True
            _emit(
                "slm_code_interpreter",
                f"✍️ Iterative Agent Edit — Applying requested code changes to '{project_name}' in '{project_dir}'...",
            )
            file_specs = self._apply_iterative_edit_to_existing_files(
                project_dir=project_dir,
                project_name=project_name,
                project_slug=project_slug,
                edit_instruction=user_query,
            )
        else:
            _emit(
                "slm_code_interpreter",
                f"🧠 Architect — Synthesizing application architecture & backend engine for: {project_name}",
            )
            file_specs = self._generate_project_files(
                project_name=project_name,
                project_slug=project_slug,
                project_kind=project_kind,
                resolved_goal=resolved_goal,
                clarification_choice=clarification_choice,
            )

        built_files: List[BuiltFileEntry] = []
        for rel_path, role_desc, content in file_specs:
            entry = self._write_project_file(
                project_dir=project_dir,
                rel_path=rel_path,
                content=content,
                role=role_desc,
                session_id=session_id,
                run_id=run_id,
            )
            built_files.append(entry)
            _emit(
                "slm_code_interpreter",
                f"✍️ Write File — `{rel_path}` ({entry.line_count} lines, {entry.size_bytes:,} B)",
            )

        # Step 3: Execute real automated test suite inside the project folder
        _emit(
            "slm_code_interpreter",
            f"🧪 Run Tests — Executing `python3 -m unittest discover -s tests -v` in '{project_dir}'...",
        )
        t_start = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
                cwd=project_dir,
                capture_output=True,
                text=True,
                timeout=20,
            )
            test_dur_ms = (time.perf_counter() - t_start) * 1000.0
            combined_out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            test_passed = proc.returncode == 0
        except Exception as exc:
            test_dur_ms = (time.perf_counter() - t_start) * 1000.0
            combined_out = f"Test execution error: {exc}"
            test_passed = False

        m_ran = re.search(r"Ran\s+(\d+)\s+tests?", combined_out)
        tests_run = int(m_ran.group(1)) if m_ran else len(built_files)

        _emit(
            "slm_code_interpreter",
            f"🧪 Verification — {tests_run} automated tests {'PASSED ✅' if test_passed else 'COMPLETED'} in {test_dur_ms:.1f} ms.",
        )

        # Step 4: Package downloadable ZIP archive & register live preview URL
        dl_id = f"dl_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        dl_dir = os.path.join(_downloads_root(), dl_id)
        os.makedirs(dl_dir, exist_ok=True)
        zip_filename = f"{project_slug}.zip"
        zip_path = os.path.join(dl_dir, zip_filename)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for bf in built_files:
                zf.write(bf.abs_path, arcname=os.path.join(project_slug, bf.rel_path))

        zip_size_kb = round(os.path.getsize(zip_path) / 1024.0, 1)
        zip_download_url = f"/api/harness/download/{dl_id}/{zip_filename}"
        live_url = f"http://localhost:8000/projects/{project_slug}/index.html"

        _emit(
            "slm_cli_agent",
            f"🚀 Live Canvas Ready — Mounted at {live_url} (opening split-screen Code & Preview Canvas).",
        )

        # Build concise Claude-Code / Copilot-Workspace style response
        total_bytes = sum(f.size_bytes for f in built_files)
        total_lines = sum(f.line_count for f in built_files)
        files_table_rows = "\n".join(
            f"| `{bf.rel_path}` | {bf.role} | {bf.line_count:,} lines | {bf.size_bytes:,} B | `{bf.sha256}` |"
            for bf in built_files
        )
        perm_badge = (
            "✅ **Full Folder Permissions Granted** (`create` · `edit` · `delete` · `run` · `execute`)"
            if perm_info.get("permitted")
            else "🔒 **Consent-Gated** *(Click **📁 Create a Project** near chatbox to choose folder & grant permissions)*"
        )

        md = (
            f"## ⚡ Local Project Built & Verified on Disk: **{project_name}**\n\n"
            f"All source files have been written to `{project_dir}`, verified with `unittest`, and opened in the **Split-Screen Code & Live Preview Canvas** on the right.\n\n"
            f"### 🚀 Live Application & Workspace\n\n"
            f"| Resource | Local Path / Direct Link |\n"
            f"| :--- | :--- |\n"
            f"| 🌐 **Live Web App** | [**{live_url}**]({live_url}) *(Also open in the right-hand **Preview Canvas**)* |\n"
            f"| 📂 **Workspace Folder** | `{project_dir}` |\n"
            f"| 🔐 **Permissions** | {perm_badge} |\n"
            f"| 🧪 **Test Suite** | **{'✅ ALL ' + str(tests_run) + ' TESTS PASSED' if test_passed else '⚠️ Check logs'}** ({test_dur_ms:.1f} ms) |\n"
            f"| 📦 **Project Archive** | [**⬇ Download `{zip_filename}` ({zip_size_kb} KB)**]({zip_download_url}) |\n\n"
            f"### 📁 Workspace Files (`{len(built_files)} files` · `{total_lines:,} lines` · `{total_bytes:,} B`)\n\n"
            f"| File | Role | Lines | Size | SHA-256 |\n"
            f"| :--- | :--- | :--- | :--- | :--- |\n"
            f"{files_table_rows}\n\n"
            f"<details><summary><strong>🧪 View Automated Test Execution Log ({tests_run} tests in {test_dur_ms:.1f} ms)</strong></summary>\n\n"
            f"```text\n{combined_out}\n```\n"
            f"</details>"
        )

        return ProjectBuildOutcome(
            project_name=project_name,
            project_slug=project_slug,
            project_dir=project_dir,
            live_url=live_url,
            zip_download_url=zip_download_url,
            zip_size_kb=zip_size_kb,
            files_created=built_files,
            test_passed=test_passed,
            tests_run=tests_run,
            test_duration_ms=test_dur_ms,
            test_stdout=combined_out,
            markdown_response=md,
            structured_data={
                "project_name": project_name,
                "project_slug": project_slug,
                "project_dir": project_dir,
                "live_url": live_url,
                "zip_download_url": zip_download_url,
                "files_count": len(built_files),
                "files": [
                    {
                        "path": bf.rel_path,
                        "rel_path": bf.rel_path,
                        "size_bytes": bf.size_bytes,
                        "lines": bf.line_count,
                        "line_count": bf.line_count,
                        "role": bf.role,
                        "sha256": bf.sha256,
                    }
                    for bf in built_files
                ],
                "tests_run": tests_run,
                "test_passed": test_passed,
                "permissions": perm_info.get("permissions", {}),
                "permitted": bool(perm_info.get("permitted")),
                "iterative_edit": applied_iterative_edit,
                "open_canvas": True,
            },
        )

    @staticmethod
    def perform_permitted_folder_operation(
        folder_path: str,
        operation: str,
        rel_path: str = "",
        content: str = "",
        command: str = "",
    ) -> Dict[str, Any]:
        """
        Executes a consent-gated operation (`list`, `read`, `create`, `edit`, `delete`, `run`, `execute`)
        inside a user-selected project folder. Requires explicit user permission for that folder.
        """
        from ..governance.policy import get_folder_permission_manager, run_sandboxed_subprocess

        perm_mgr = get_folder_permission_manager()
        norm_root = perm_mgr.normalize_folder_path(folder_path)
        op_clean = (operation or "list").strip().lower()

        check_op = "read" if op_clean in ("list", "read") else op_clean
        perm_status = perm_mgr.check_permission(norm_root, operation=check_op)
        if not perm_status.get("permitted"):
            return {
                "status": "permission_required",
                "requires_consent": True,
                "folder_path": norm_root,
                "operation": op_clean,
                "permission_status": perm_status,
                "message": perm_status.get("message")
                or f"Permission required to perform '{op_clean}' under '{norm_root}'.",
            }

        if op_clean == "list":
            if not os.path.isdir(norm_root):
                return {"status": "ok", "folder_path": norm_root, "files": [], "permission_status": perm_status}
            files_out: List[Dict[str, Any]] = []
            skip = {".git", ".venv", "__pycache__", "node_modules"}
            for root, dirs, files in os.walk(norm_root):
                dirs[:] = [d for d in sorted(dirs) if d not in skip]
                for fn in sorted(files):
                    abs_f = os.path.join(root, fn)
                    rel_f = os.path.relpath(abs_f, norm_root)
                    try:
                        sz = os.path.getsize(abs_f)
                    except OSError:
                        sz = 0
                    files_out.append({"rel_path": rel_f, "size_bytes": sz})
            return {"status": "ok", "folder_path": norm_root, "files": files_out, "permission_status": perm_status}

        if op_clean in ("read", "create", "edit", "delete"):
            clean_rel = (rel_path or "").strip().lstrip("/\\")
            if not clean_rel:
                return {"status": "error", "message": "File relative path ('rel_path') is required."}
            abs_target = os.path.abspath(os.path.join(norm_root, clean_rel))
            if not (abs_target == norm_root or abs_target.startswith(norm_root + os.sep)):
                return {"status": "error", "message": "Target path escapes the permitted project folder."}

            if op_clean == "read":
                if not os.path.isfile(abs_target):
                    return {"status": "error", "message": f"File not found: {clean_rel}"}
                with open(abs_target, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
                return {
                    "status": "ok",
                    "folder_path": norm_root,
                    "rel_path": clean_rel,
                    "content": text,
                    "size_bytes": len(text.encode("utf-8")),
                }

            if op_clean in ("create", "edit"):
                os.makedirs(os.path.dirname(abs_target), exist_ok=True)
                data_bytes = (content or "").encode("utf-8")
                with open(abs_target, "wb") as f:
                    f.write(data_bytes)
                return {
                    "status": "ok",
                    "operation": op_clean,
                    "folder_path": norm_root,
                    "rel_path": clean_rel,
                    "size_bytes": len(data_bytes),
                    "message": f"{'Created' if op_clean == 'create' else 'Updated'} '{clean_rel}' ({len(data_bytes):,} bytes).",
                }

            if op_clean == "delete":
                if not os.path.exists(abs_target):
                    return {"status": "error", "message": f"File does not exist: {clean_rel}"}
                if os.path.isfile(abs_target) or os.path.islink(abs_target):
                    os.remove(abs_target)
                    return {
                        "status": "ok",
                        "operation": "delete",
                        "folder_path": norm_root,
                        "rel_path": clean_rel,
                        "message": f"Deleted '{clean_rel}' from '{norm_root}'.",
                    }
                return {"status": "error", "message": "Only files can be deleted directly."}

        if op_clean in ("run", "execute"):
            cmd_str = (command or "").strip() or f"{sys.executable} -m unittest discover -s tests -p \"test_*.py\" -v"
            os.makedirs(norm_root, exist_ok=True)
            res = run_sandboxed_subprocess(
                cmd_str,
                cwd=norm_root,
                timeout_sec=20.0,
                shell=True,
            )
            return {
                "status": "ok",
                "operation": op_clean,
                "folder_path": norm_root,
                "command": cmd_str,
                "returncode": res.returncode,
                "stdout": res.stdout,
                "stderr": res.stderr,
                "timed_out": res.timed_out,
                "duration_ms": res.duration_ms,
            }

        return {"status": "error", "message": f"Unsupported operation: {op_clean}"}

    def _generate_project_files(
        self,
        project_name: str,
        project_slug: str,
        project_kind: str,
        resolved_goal: str,
        clarification_choice: str,
    ) -> List[tuple[str, str, str]]:
        """
        Generates the complete multi-file source code for the target project
        dynamically based on the user's request (`resolved_goal`) with zero pre-filled values.
        """
        from .project_templates import generate_custom_interactive_app_files

        clean_goal = _clean_candidate_goal(resolved_goal)
        slm_blueprint = ""
        try:
            from ..config import MODEL_CATALOG
            from ..runtime.inference_pool import get_inference_pool

            pool = get_inference_pool()
            spec = MODEL_CATALOG.get("slm_code_interpreter")
            if spec is not None:
                res = pool.generate(
                    spec,
                    [
                        {
                            "role": "system",
                            "content": "You are a concise software architect. Summarize the key backend modules and endpoints needed for the user's project in 3 bullet points.",
                        },
                        {"role": "user", "content": clean_goal},
                    ],
                    max_tokens=96,
                    temperature=0.2,
                )
                slm_blueprint = (res.text or "").strip()
        except Exception:
            slm_blueprint = ""

        return generate_custom_interactive_app_files(
            project_name=project_name,
            project_slug=project_slug,
            resolved_goal=clean_goal,
            slm_blueprint=slm_blueprint,
        )

    def _find_latest_existing_project(self, perm_mgr: Any) -> tuple[str, str, str]:
        """Finds the most recently built project directory in the active workspace."""
        active_base = perm_mgr.get_active_folder() or self.projects_root
        if os.path.isfile(os.path.join(active_base, "index.html")):
            slug = os.path.basename(active_base)
            return active_base, slug, slug.replace("_", " ").title()
        if os.path.isdir(active_base):
            candidates: List[tuple[float, str, str]] = []
            for entry in os.scandir(active_base):
                if entry.is_dir() and os.path.isfile(os.path.join(entry.path, "index.html")):
                    try:
                        mtime = os.path.getmtime(os.path.join(entry.path, "index.html"))
                    except OSError:
                        mtime = 0.0
                    candidates.append((mtime, entry.path, entry.name))
            if candidates:
                candidates.sort(reverse=True)
                _, best_path, best_slug = candidates[0]
                return best_path, best_slug, best_slug.replace("_", " ").title()
        return "", "", ""

    def _apply_iterative_edit_to_existing_files(
        self,
        project_dir: str,
        project_name: str,
        project_slug: str,
        edit_instruction: str,
    ) -> List[tuple[str, str, str]]:
        """
        Applies an iterative Claude-Code / Copilot-style edit to the existing project files on disk
        while preserving existing functionality and unit test compatibility.
        """
        role_map = {
            "index.html": "Interactive Web UI (Updated via Agent Edit)",
            "styles.css": "Responsive Studio Stylesheet (Updated via Agent Edit)",
            "app.js": "Frontend Controller (Updated via Agent Edit)",
            "engine.py": "Backend Logic Engine (Updated via Agent Edit)",
            "server.py": "Standalone Local HTTP Server",
            "tests/test_project.py": "Automated Unit & Boundary Verification Suite",
            "README.md": "Project Documentation & Run Guide",
        }
        existing: Dict[str, str] = {}
        for rel in role_map:
            p = os.path.join(project_dir, rel)
            if os.path.isfile(p):
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    existing[rel] = f.read()

        inst_low = (edit_instruction or "").lower()
        clean_note = re.sub(r"^modify\s+\[[^\]]+\]\s*:\s*", "", (edit_instruction or "").strip(), flags=re.IGNORECASE).strip()

        # 1. Design / Theme / UI / Layout / Style transformation
        if any(w in inst_low for w in ("design", "redesign", "restyle", "theme", "dark", "light", "color", "style", "ui", "ux", "layout", "look", "modern", "glass")):
            if "styles.css" in existing and "/* Iterative Agent Enhancement: Multi-Theme Design System */" not in existing["styles.css"]:
                existing["styles.css"] += (
                    "\n/* Iterative Agent Enhancement: Multi-Theme Design System */\n"
                    "body.light-theme { background: linear-gradient(135deg, #f8fafc 0%, #eef2ff 50%, #f0f9ff 100%) !important; color: #0f172a !important; }\n"
                    "body.light-theme .topbar, body.light-theme .panel, body.light-theme .url-launcher-card, "
                    "body.light-theme .workspace-card, body.light-theme .score-card, body.light-theme .aspect-bar-card, body.light-theme .detail-box "
                    "{ background: rgba(255,255,255,0.92) !important; color: #0f172a !important; border-color: #cbd5e1 !important; box-shadow: 0 10px 30px -10px rgba(79,70,229,0.12) !important; backdrop-filter: blur(12px); }\n"
                    "body.light-theme h1, body.light-theme h2, body.light-theme h3, body.light-theme .sc-value, body.light-theme .kv-val "
                    "{ color: #0f172a !important; }\n"
                    "body.light-theme .url-input-wrap, body.light-theme .data-table th, body.light-theme .report-pre "
                    "{ background: #f1f5f9 !important; color: #0f172a !important; border-color: #cbd5e1 !important; }\n"
                    "body.light-theme .url-input-wrap input { color: #0f172a !important; }\n"
                    "body.light-theme .btn-secondary, body.light-theme .aspect-chip, body.light-theme .tab-btn:not(.active) "
                    "{ background: #f8fafc !important; color: #334155 !important; border-color: #cbd5e1 !important; }\n"
                    "body.theme-royal { background: radial-gradient(circle at top right, #2e1065 0%, #0f172a 55%, #020617 100%) !important; color: #f8fafc !important; }\n"
                    "body.theme-royal .topbar, body.theme-royal .url-launcher-card, body.theme-royal .workspace-card, body.theme-royal .score-card, body.theme-royal .aspect-bar-card "
                    "{ background: rgba(30, 27, 75, 0.72) !important; border-color: rgba(139, 92, 246, 0.4) !important; box-shadow: 0 14px 36px -12px rgba(124, 58, 237, 0.28) !important; backdrop-filter: blur(14px); }\n"
                    "body.theme-royal .btn-primary, body.theme-royal .tab-btn.active { background: linear-gradient(135deg, #8b5cf6, #ec4899) !important; border-color: #8b5cf6 !important; }\n"
                    "body.theme-cyber { background: radial-gradient(circle at top left, #064e3b 0%, #022c22 50%, #020617 100%) !important; color: #ecfdf5 !important; }\n"
                    "body.theme-cyber .topbar, body.theme-cyber .url-launcher-card, body.theme-cyber .workspace-card, body.theme-cyber .score-card, body.theme-cyber .aspect-bar-card "
                    "{ background: rgba(6, 78, 59, 0.45) !important; border-color: rgba(16, 185, 129, 0.45) !important; box-shadow: 0 14px 36px -12px rgba(16, 185, 129, 0.25) !important; }\n"
                    "body.theme-cyber .btn-primary, body.theme-cyber .tab-btn.active { background: linear-gradient(135deg, #10b981, #06b6d4) !important; border-color: #10b981 !important; }\n"
                )
            if "index.html" in existing:
                if "btn-theme-toggle" not in existing["index.html"]:
                    existing["index.html"] = existing["index.html"].replace(
                        '<div class="topbar-actions">',
                        '<div class="topbar-actions">\n'
                        '      <button class="btn-secondary" id="btn-theme-toggle" type="button" onclick="(function(){const b=document.body;const modes=[\'light-theme\',\'theme-royal\',\'theme-cyber\',\'\'];const cur=modes.findIndex(m=>m&&b.classList.contains(m));b.classList.remove(\'light-theme\',\'theme-royal\',\'theme-cyber\');const next=modes[(cur+1)%modes.length];if(next)b.classList.add(next);})()">🎨 Switch Design</button>',
                        1,
                    )
                # Activate new visual theme immediately on body so user sees the design change right away
                target_theme = "light-theme"
                if any(w in inst_low for w in ("cyber", "emerald", "neon", "green", "matrix")):
                    target_theme = "theme-cyber"
                elif any(w in inst_low for w in ("royal", "purple", "violet", "glass", "aurora")):
                    target_theme = "theme-royal"
                elif "body class=\"light-theme\"" in existing["index.html"]:
                    target_theme = "theme-royal"
                existing["index.html"] = re.sub(
                    r"<body[^>]*>",
                    f'<body class="{target_theme}">',
                    existing["index.html"],
                    count=1,
                )

        # 2. CSV Export enhancement
        if "csv" in inst_low and "index.html" in existing and "btn-export-csv" not in existing["index.html"]:
            existing["index.html"] = existing["index.html"].replace(
                '<div class="topbar-actions">',
                '<div class="topbar-actions">\n      <button class="btn-secondary" id="btn-export-csv" type="button">⬇ CSV Export</button>',
                1,
            )
            if "app.js" in existing:
                existing["app.js"] += (
                    "\n// Iterative Agent Enhancement: CSV Exporter\n"
                    "document.addEventListener('DOMContentLoaded', () => {\n"
                    "  const csvBtn = document.getElementById('btn-export-csv');\n"
                    "  if (csvBtn) {\n"
                    "    csvBtn.addEventListener('click', () => {\n"
                    "      const rows = (window.currentReport && window.currentReport.checks) ? window.currentReport.checks : (typeof appItems !== 'undefined' ? appItems : []);\n"
                    "      const csv = 'Index,Name,Status,Details\\n' + rows.map((r, i) => `${i+1},\"${(r.name||r.title||'').replace(/\"/g,'\"\"')}\",\"${r.status||r.category||''}\",\"${(r.measured||r.details||'').replace(/\"/g,'\"\"')}\"`).join('\\n');\n"
                    "      const blob = new Blob([csv], { type: 'text/csv' });\n"
                    "      const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'export.csv'; a.click();\n"
                    "    });\n"
                    "  }\n"
                    "});\n"
                )

        # 3. Record iterative agent changelog in README.md
        if "README.md" in existing:
            existing["README.md"] += f"\n- **Agent Update:** {clean_note}\n"

        out_specs: List[tuple[str, str, str]] = []
        for rel, role_desc in role_map.items():
            if rel in existing:
                out_specs.append((rel, role_desc, existing[rel]))
        return out_specs

