"""
Plane 2: First-Class Governed Built-in Tools for Workspace, SQLite, DataFrames, Python Sandbox,
Hybrid RAG, SymPy Math, Procedural Memory, Shell & Human Clarification.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
from typing import Any, Dict, List

from ..config import get_config
from ..context.artifacts import get_artifact_store
from ..governance.policy import RiskLevel, WorkspaceJail, run_sandboxed_subprocess
from ..governance.privacy import scrub_secrets_and_pii
from ..memory.blackboard import get_blackboard
from ..memory.hybrid_index import get_hybrid_index
from ..memory.procedural import get_procedural_memory
from ..verifiers.code_sql_json import verify_and_execute_sql, verify_python_code
from ..verifiers.grounding_math import verify_or_solve_sympy
from .registry import ToolExecutionContext, ToolRegistry, ToolSpec


def _unified_diff_preview(old_text: str, new_text: str, path_label: str) -> str:
    diff_lines = list(
        difflib.unified_diff(
            old_text.splitlines(),
            new_text.splitlines(),
            fromfile=f"a/{path_label}",
            tofile=f"b/{path_label}",
            lineterm="",
        )
    )
    return "\n".join(diff_lines[:120]) or "(no textual changes)"


def register_builtin_tools(registry: ToolRegistry) -> None:
    # 1. fs_list
    def _fs_list(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        jail = WorkspaceJail()
        rel_dir = str(args.get("path") or ".")
        pattern = str(args.get("pattern") or "*")
        target = jail.resolve_path(rel_dir, for_write=False)
        if not os.path.isdir(target):
            return f"Not a directory: {rel_dir}"

        matches: List[str] = []
        skip = {".git", ".venv", "models", "__pycache__", "node_modules"}
        for root, dirs, files in os.walk(target):
            dirs[:] = [d for d in sorted(dirs) if d not in skip]
            for fname in sorted(files):
                if fnmatch.fnmatch(fname, pattern):
                    full = os.path.join(root, fname)
                    matches.append(os.path.relpath(full, jail.workspace_real))
                    if len(matches) >= 150:
                        return "\n".join(matches) + "\n... (capped at 150 entries)"
        return "\n".join(matches) if matches else "(no matching files)"

    registry.register(
        ToolSpec(
            name="fs_list",
            description="List files in workspace directory matching optional glob pattern.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pattern": {"type": "string"},
                },
            },
            risk_level=RiskLevel.L1_WORKSPACE_READ,
            handler=_fs_list,
            tags=["file", "directory", "list", "glob", "workspace", "project"],
        )
    )

    # 2. fs_read
    def _fs_read(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        jail = WorkspaceJail()
        target = jail.resolve_path(str(args.get("path", "")), for_write=False)
        if not os.path.isfile(target):
            raise FileNotFoundError(f"File does not exist: {args.get('path')}")
        start = max(1, int(args.get("start_line") or 1))
        end = max(start, int(args.get("end_line") or (start + 119)))
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
        e_clamped = min(len(lines), end)
        numbered = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, e_clamped + 1))
        return f"[{os.path.relpath(target, jail.workspace_real)} lines {start}-{e_clamped} of {len(lines)}]\n{numbered}"

    registry.register(
        ToolSpec(
            name="fs_read",
            description="Read line-numbered slice of a file inside the workspace.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
            },
            risk_level=RiskLevel.L1_WORKSPACE_READ,
            handler=_fs_read,
            tags=["read", "file", "code", "view", "open", "inspect"],
        )
    )

    # 3. fs_grep
    def _fs_grep(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        jail = WorkspaceJail()
        pat_raw = str(args.get("pattern") or "").strip()
        if not pat_raw:
            return "Empty grep pattern."
        sub_dir = jail.resolve_path(str(args.get("path") or "."), for_write=False)
        try:
            regex = re.compile(pat_raw, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pat_raw), re.IGNORECASE)

        hits: List[str] = []
        skip = {".git", ".venv", "models", "__pycache__", "node_modules"}
        targets = [sub_dir] if os.path.isfile(sub_dir) else []
        if not targets:
            for root, dirs, files in os.walk(sub_dir):
                dirs[:] = [d for d in dirs if d not in skip]
                for fn in sorted(files):
                    if os.path.splitext(fn)[1].lower() in (
                        ".py",
                        ".md",
                        ".js",
                        ".ts",
                        ".html",
                        ".css",
                        ".json",
                        ".yaml",
                        ".yml",
                        ".sql",
                        ".txt",
                        ".sh",
                    ):
                        targets.append(os.path.join(root, fn))

        for fp in targets:
            try:
                if os.path.getsize(fp) > 400_000:
                    continue
                with open(fp, "r", encoding="utf-8", errors="ignore") as f:
                    for ln_no, line in enumerate(f, start=1):
                        if regex.search(line):
                            rel = os.path.relpath(fp, jail.workspace_real)
                            hits.append(f"{rel}:{ln_no}: {line.strip()[:180]}")
                            if len(hits) >= 60:
                                return "\n".join(hits) + "\n... (capped at 60 matches)"
            except OSError:
                pass
        return "\n".join(hits) if hits else f"No matches found for {pat_raw!r}."

    registry.register(
        ToolSpec(
            name="fs_grep",
            description="Search regex or text pattern across workspace source/text files.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
            risk_level=RiskLevel.L1_WORKSPACE_READ,
            handler=_fs_grep,
            tags=["grep", "search", "find", "symbol", "function", "class", "codebase"],
        )
    )

    # 4. fs_write
    def _fs_write_preview(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        jail = WorkspaceJail()
        p = str(args.get("path", ""))
        target = jail.resolve_path(p, for_write=True, operation="create")
        old = ""
        if os.path.isfile(target):
            with open(target, "r", encoding="utf-8", errors="ignore") as f:
                old = f.read()
        return _unified_diff_preview(old, str(args.get("content", "")), p)

    def _fs_write(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        jail = WorkspaceJail()
        target = jail.resolve_path(str(args["path"]), for_write=True, operation="create")
        content = str(args.get("content", ""))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Wrote {len(content)} chars to {target}"

    registry.register(
        ToolSpec(
            name="fs_write",
            description="Create or overwrite a file inside the workspace or a permitted project folder (undoable via /undo).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            risk_level=RiskLevel.L3_WORKSPACE_MUTATE,
            handler=_fs_write,
            preview_fn=_fs_write_preview,
            mutates_path_arg="path",
            tags=["write", "create", "save", "file"],
        )
    )

    # 5. fs_edit
    def _fs_edit_preview(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        jail = WorkspaceJail()
        p = str(args.get("path", ""))
        target = jail.resolve_path(p, for_write=True, operation="edit")
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            old = f.read()
        new = old.replace(str(args.get("target", "")), str(args.get("replacement", "")), 1)
        return _unified_diff_preview(old, new, p)

    def _fs_edit(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        jail = WorkspaceJail()
        target = jail.resolve_path(str(args["path"]), for_write=True, operation="edit")
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            old = f.read()
        needle = str(args["target"])
        if needle not in old:
            raise ValueError(f"Target snippet not found in {args['path']}. Read file first with fs_read.")
        updated = old.replace(needle, str(args.get("replacement", "")), 1)
        with open(target, "w", encoding="utf-8") as f:
            f.write(updated)
        return f"Edited {target} successfully."

    registry.register(
        ToolSpec(
            name="fs_edit",
            description="Replace an exact target snippet in a workspace or permitted project file (undoable via /undo).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "target": {"type": "string"},
                    "replacement": {"type": "string"},
                },
                "required": ["path", "target", "replacement"],
            },
            risk_level=RiskLevel.L3_WORKSPACE_MUTATE,
            handler=_fs_edit,
            preview_fn=_fs_edit_preview,
            mutates_path_arg="path",
            tags=["edit", "patch", "modify", "refactor", "fix"],
        )
    )

    # 5b. fs_delete
    def _fs_delete(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        jail = WorkspaceJail()
        target = jail.resolve_path(str(args["path"]), for_write=True, operation="delete")
        if not os.path.exists(target):
            return f"Path does not exist: {target}"
        if os.path.isfile(target) or os.path.islink(target):
            os.remove(target)
            return f"Deleted file: {target}"
        raise ValueError(f"Refusing to delete directory via fs_delete: {target}")

    registry.register(
        ToolSpec(
            name="fs_delete",
            description="Delete a file inside the workspace or a permitted project folder.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
            risk_level=RiskLevel.L3_WORKSPACE_MUTATE,
            handler=_fs_delete,
            mutates_path_arg="path",
            tags=["delete", "remove", "clean", "file"],
        )
    )

    # 6. artifact_inspect
    def _artifact_inspect(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        uri = str(args.get("uri") or args.get("artifact_id") or "")
        start = int(args.get("start_line") or 1)
        end = int(args.get("end_line") or (start + 99))
        pat = args.get("search_pattern")
        return get_artifact_store().read_slice(
            uri,
            start_line=start,
            end_line=end,
            search_pattern=str(pat) if pat else None,
        )

    registry.register(
        ToolSpec(
            name="artifact_inspect",
            description="Read line slices or search inside an offloaded artifact://<id> handle.",
            parameters={
                "type": "object",
                "properties": {
                    "uri": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "search_pattern": {"type": "string"},
                },
                "required": ["uri"],
            },
            risk_level=RiskLevel.L1_WORKSPACE_READ,
            handler=_artifact_inspect,
            tags=["artifact", "slice", "attachment", "inspect"],
        )
    )

    # 7. sqlite_query
    def _sqlite_query(args: Dict[str, Any], ctx: ToolExecutionContext) -> Any:
        bb = get_blackboard()
        raw_db = args.get("db_path") or bb.get(ctx.session_id, "active_sqlite_db")
        if not raw_db:
            return "No SQLite db_path provided or attached to session."
        jail = WorkspaceJail()
        db_real = jail.resolve_path(str(raw_db), for_write=False)
        query = str(args.get("query") or "SELECT name, sql FROM sqlite_master WHERE type='table';")
        outcome = verify_and_execute_sql(query, db_path=db_real, read_only=True, max_rows=50)
        if not outcome.passed:
            raise ValueError(outcome.error_feedback)
        bb.put(ctx.session_id, "last_sql_result", outcome.output)
        return outcome.output

    registry.register(
        ToolSpec(
            name="sqlite_query",
            description="Execute a read-only SQLite SELECT query or inspect schema on a .db/.sqlite file.",
            parameters={
                "type": "object",
                "properties": {
                    "db_path": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
            risk_level=RiskLevel.L1_WORKSPACE_READ,
            handler=_sqlite_query,
            tags=["sql", "sqlite", "database", "table", "select", "schema", "query"],
        )
    )

    # 8. dataframe_query
    def _dataframe_query(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        import pandas as pd

        bb = get_blackboard()
        raw_path = args.get("path") or bb.get(ctx.session_id, "active_tabular_path")
        if not raw_path:
            return "No CSV/Excel/Parquet file path provided."
        jail = WorkspaceJail()
        f_real = jail.resolve_path(str(raw_path), for_write=False)
        ext = os.path.splitext(f_real)[1].lower()
        df = (
            pd.read_csv(f_real)
            if ext == ".csv"
            else (pd.read_csv(f_real, sep="\t") if ext == ".tsv" else pd.read_excel(f_real))
        )
        bb.put(ctx.session_id, "active_df", df)

        expr = str(args.get("pandas_expr") or "").strip()
        if not expr or expr == "describe":
            return (
                f"Shape: {df.shape}\nColumns: {list(df.columns)}\n\n"
                f"Summary:\n{df.describe(include='all').to_string()}"
            )
        # Execute read-only pandas expression in verify_python_code sandbox
        prelude = f"import pandas as pd\ndf = pd.read_csv({f_real!r})\n" if ext == ".csv" else ""
        script = f"res = {expr}\nprint(res.to_string() if hasattr(res, 'to_string') else res)"
        ver = verify_python_code(script, execute=True, prelude=prelude, cancel_token=ctx.cancel_token)
        if not ver.passed:
            raise ValueError(ver.error_feedback)
        return str(ver.output)

    registry.register(
        ToolSpec(
            name="dataframe_query",
            description="Inspect or evaluate a Pandas expression `df...` on a CSV/TSV/Excel dataset.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "pandas_expr": {"type": "string"},
                },
            },
            risk_level=RiskLevel.L2_SANDBOX_COMPUTE,
            handler=_dataframe_query,
            tags=["csv", "excel", "dataframe", "pandas", "analytics", "statistics", "table"],
        )
    )

    # 9. python_sandbox
    def _python_sandbox(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        code = str(args.get("code") or "")
        ver = verify_python_code(
            code,
            execute=True,
            timeout_sec=12.0,
            cwd=ctx.workspace_dir,
            cancel_token=ctx.cancel_token,
        )
        if not ver.passed:
            raise RuntimeError(ver.error_feedback)
        return str(ver.output) or "(executed cleanly with empty stdout — remember to `print(...)` results)"

    registry.register(
        ToolSpec(
            name="python_sandbox",
            description="Execute Python code inside an isolated CPU/timeout sandbox and capture printed stdout.",
            parameters={
                "type": "object",
                "properties": {"code": {"type": "string"}},
                "required": ["code"],
            },
            risk_level=RiskLevel.L2_SANDBOX_COMPUTE,
            handler=_python_sandbox,
            tags=["python", "compute", "script", "algorithm", "calculate", "plot", "code"],
        )
    )

    # 10. sympy_math
    def _sympy_math(args: Dict[str, Any], ctx: ToolExecutionContext) -> Any:
        out = verify_or_solve_sympy(
            expression_or_equation=str(args.get("expression", "")),
            operation=str(args.get("operation") or "auto"),
            variable=str(args.get("variable") or "x"),
        )
        if not out.passed:
            raise ValueError(out.error_feedback)
        return {"result": out.cleaned_artifact, "details": out.output}

    registry.register(
        ToolSpec(
            name="sympy_math",
            description="Deterministically solve equations, simplify, differentiate, integrate, or evaluate exact math via SymPy.",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {"type": "string"},
                    "operation": {
                        "type": "string",
                        "enum": ["auto", "evaluate", "solve", "differentiate", "integrate", "simplify"],
                    },
                    "variable": {"type": "string"},
                },
                "required": ["expression"],
            },
            risk_level=RiskLevel.L0_PURE,
            handler=_sympy_math,
            tags=["math", "algebra", "equation", "calculus", "derivative", "integral", "solve"],
        )
    )

    # 11. rag_search
    def _rag_search(args: Dict[str, Any], ctx: ToolExecutionContext) -> Any:
        q = str(args.get("query") or "")
        top_k = min(10, max(1, int(args.get("top_k") or 5)))
        hits = get_hybrid_index().search(q, top_k=top_k)
        get_blackboard().put(ctx.session_id, "last_rag_citations", hits)
        return hits if hits else "No indexed documents matched query."

    registry.register(
        ToolSpec(
            name="rag_search",
            description="Hybrid FTS5 BM25 + Dense ONNX + BGE INT8 reranked search over indexed workspace & session documents.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "top_k": {"type": "integer"},
                },
                "required": ["query"],
            },
            risk_level=RiskLevel.L1_WORKSPACE_READ,
            handler=_rag_search,
            tags=["rag", "document", "pdf", "knowledge", "citation", "docs", "manual"],
        )
    )

    # 12. memory_store
    def _memory_store(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        fact = str(args.get("fact") or "").strip()
        ok = get_procedural_memory().remember_fact(fact, workspace=ctx.workspace_dir)
        return f"Remembered fact: {fact}" if ok else "Fact too short to store."

    registry.register(
        ToolSpec(
            name="memory_store",
            description="Persist an important user preference or project fact across sessions.",
            parameters={
                "type": "object",
                "properties": {"fact": {"type": "string"}},
                "required": ["fact"],
            },
            risk_level=RiskLevel.L0_PURE,
            handler=_memory_store,
            tags=["memory", "remember", "preference", "fact", "note"],
        )
    )

    # 13. shell_exec
    def _shell_exec(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        cmd = str(args.get("command") or "").strip()
        res = run_sandboxed_subprocess(
            cmd,
            cwd=ctx.workspace_dir,
            timeout_sec=20.0,
            shell=True,
            cancel_token=ctx.cancel_token,
        )
        combined = (res.stdout + ("\nSTDERR:\n" + res.stderr if res.stderr.strip() else "")).strip()
        return f"[exit={res.returncode}, {res.duration_ms}ms]\n{combined}"

    registry.register(
        ToolSpec(
            name="shell_exec",
            description="Run a shell command in the workspace (subject to capability governance & user approval).",
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            risk_level=RiskLevel.L3_WORKSPACE_MUTATE,
            handler=_shell_exec,
            tags=["shell", "cli", "git", "terminal", "bash", "test", "command"],
        )
    )

    # 14. web_search
    def _web_search(args: Dict[str, Any], ctx: ToolExecutionContext) -> Any:
        if get_config().offline_only:
            return "Offline mode (`offline_only=True`) is active; external web search disabled."
        from .web_search_engine import live_web_search

        q = str(args.get("query") or "")
        results = live_web_search(q, max_results=5, fetch_top_pages=2)
        if results:
            get_blackboard().put(ctx.session_id, "last_web_citations", results)
            return results
        return "Web search returned no hits; rely on local workspace & knowledge."

    registry.register(
        ToolSpec(
            name="web_search",
            description="Search live web via DuckDuckGo when offline_only is False.",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            risk_level=RiskLevel.L2_SANDBOX_COMPUTE,
            handler=_web_search,
            tags=["web", "internet", "online", "news", "latest", "url"],
        )
    )

    # 15. ask_clarification
    def _ask_clarification(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        from ..governance.brokers import get_clarification_broker

        question = str(args.get("question") or "Could you clarify your preference?")
        raw_opts = args.get("options")
        opts = [str(o) for o in raw_opts[:4]] if isinstance(raw_opts, list) else []
        ans = get_clarification_broker().ask_user(
            run_id=ctx.run_id,
            session_id=ctx.session_id,
            question=question,
            options=opts,
            cancel_token=ctx.cancel_token,
        )
        return f"User clarified: {ans}"

    registry.register(
        ToolSpec(
            name="ask_clarification",
            description="Pause and ask the user a structured multiple-choice clarifying question when critical parameters are ambiguous.",
            parameters={
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question"],
            },
            risk_level=RiskLevel.L0_PURE,
            handler=_ask_clarification,
            tags=["clarify", "ambiguous", "question", "ask"],
        )
    )

    # 16. machine_control (Universal OS & Machine Control Plane)
    def _machine_control(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        from .machine_control import MachineControlEngine

        task_query = str(args.get("task") or args.get("query") or "")
        engine = MachineControlEngine(workspace_dir=ctx.workspace_dir)
        outcome = engine.execute_from_query(task_query, session_id=ctx.session_id)
        return outcome.markdown_response

    registry.register(
        ToolSpec(
            name="machine_control",
            description=(
                "Execute OS-native machine actions across macOS/Windows/Linux: git checkout/status/branch, "
                "copy/backup files, merge 2+ Excel/CSV sheets, play music/audio, search & find files, "
                "schedule cron/launchd/schtasks jobs, and traverse desktop screen/window hierarchy."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                },
                "required": ["task"],
            },
            risk_level=RiskLevel.L2_SANDBOX_COMPUTE,
            handler=_machine_control,
            tags=[
                "machine",
                "os",
                "git",
                "checkout",
                "copy",
                "excel",
                "merge",
                "music",
                "play",
                "search",
                "schedule",
                "screen",
                "automation",
            ],
        )
    )

    # 17. analyze_media_or_document (YouTube, Online Video, Local Video/Audio & Deep Document Intelligence)
    def _analyze_media_or_document(args: Dict[str, Any], ctx: ToolExecutionContext) -> str:
        from .media_doc_intelligence import (
            MediaDocIntelligenceEngine,
            extract_local_media_path_from_query,
            extract_video_or_media_url,
        )

        target = str(args.get("target") or args.get("url") or args.get("path") or args.get("query") or "")
        question = str(args.get("question") or "")
        engine = MediaDocIntelligenceEngine(workspace_dir=ctx.workspace_dir)

        url = extract_video_or_media_url(target)
        if url:
            return engine.analyze_online_video_url(url, user_query=question or target).markdown_response

        local_hit = extract_local_media_path_from_query(target, ctx.workspace_dir)
        if local_hit:
            lpath, lkind = local_hit
            if lkind == "video":
                return engine.analyze_video_file(lpath, user_query=question).markdown_response
            if lkind == "audio":
                return engine.analyze_audio_file(lpath, user_query=question).markdown_response
            return engine.analyze_document_deep("", doc_name=os.path.basename(lpath), user_query=question, source_path=lpath).markdown_response

        return engine.analyze_document_deep(target, doc_name="Document", user_query=question).markdown_response

    registry.register(
        ToolSpec(
            name="analyze_media_or_document",
            description=(
                "Analyze YouTube URLs, online video links, local video files (.mp4/.mov/.webm), "
                "audio files (.wav/.mp3/.m4a), or documents (.pdf/.docx/.pptx/.xlsx/.txt) with "
                "timestamped transcripts, keyframe OCR, and section-by-section deep breakdowns."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "target": {"type": "string"},
                    "question": {"type": "string"},
                },
                "required": ["target"],
            },
            risk_level=RiskLevel.L2_SANDBOX_COMPUTE,
            handler=_analyze_media_or_document,
            tags=["youtube", "video", "audio", "document", "pdf", "transcript", "analyze", "summarize"],
        )
    )


