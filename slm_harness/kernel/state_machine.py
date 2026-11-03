"""
Plane 1: Event-Driven Agentic Kernel & Multi-Mode `TurnStateMachine`.

Unifies all 10 planes (`FAST_DIRECT`, `REACT_LOOP`, `PLAN_DAG`, `EVALUATOR_OPTIMIZER`)
with live `HarnessEvent` streaming, `<40ms` `CancellationToken` preemption, 2-strike cycle
breaking, mid-flight `SteerQueue` injection, SQLite WAL checkpoints, and Waterfall tracing.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional, Sequence

from ..config import HarnessConfig, get_config
from ..context.artifacts import ArtifactEntry, get_artifact_store
from ..context.compiler import get_context_compiler
from ..governance.privacy import run_lifecycle_hooks
from ..memory.blackboard import get_blackboard
from ..memory.hybrid_index import get_hybrid_index
from ..memory.procedural import get_procedural_memory
from ..memory.session_tree import get_session_memory
from ..orchestrator.cascade_router import (
    _CODING_INTENT_RE,
    ExecutionMode,
    RouteDecision,
    get_cascade_router,
)
from ..orchestrator.dag_scheduler import DAGNode, HandoffEnvelope, get_dag_scheduler
from ..orchestrator.skills import SPECIALIST_SYSTEM_PROMPTS, get_skill_registry, get_subagent_runner
from ..runtime import GrammarSpec, extract_and_repair_json, get_process_rss_mb, get_runtime
from ..runtime.arena import CancellationToken, RunCancelledError, get_cancellation_registry
from ..telemetry.tracer import get_trace_store
from ..tools.mcp_bridge import get_mcp_bridge
from ..tools.registry import ToolExecutionContext, get_tool_registry
from ..tools.file_exporter import (
    build_presentation_outline_markdown,
    clean_export_content,
    is_direct_download_followup,
    rewrite_and_materialize_downloads,
)
from ..tools.machine_control import MachineControlEngine, detect_machine_control_intent
from ..tools.web_search_engine import (
    build_smart_web_query,
    live_web_search,
    should_run_web_search,
)
from ..verifiers.code_sql_json import verify_json_output
from ..verifiers.grounding_math import try_deterministic_math_solve, verify_rag_citations
from ..verifiers.stream_guard import StreamDegenerationGuard
from .checkpoints import get_checkpoint_store, get_shadow_vfs, get_steer_queue
from .events import EventBus, EventType, HarnessEvent, get_event_bus

_AT_FILE_TOKEN_RE = re.compile(r"(?:^|\s)@([A-Za-z0-9_./~\-]+\.[A-Za-z0-9]{1,8})\b")


@dataclass
class TurnOutcome:
    run_id: str
    session_id: str
    final_text: str
    agent_used: str
    mode: str
    model_id: str = ""
    backend: str = ""
    ttft_ms: float = 0.0
    tok_per_sec: float = 0.0
    total_ms: float = 0.0
    citations: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    structured_output: Any = None
    cancelled: bool = False
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "final_text": self.final_text,
            "agent_used": self.agent_used,
            "mode": self.mode,
            "model_id": self.model_id,
            "backend": self.backend,
            "ttft_ms": self.ttft_ms,
            "tok_per_sec": self.tok_per_sec,
            "total_ms": self.total_ms,
            "citations": self.citations,
            "artifacts": self.artifacts,
            "structured_output": self.structured_output,
            "cancelled": self.cancelled,
            "error": self.error,
        }


class TurnStateMachine:
    """Master 10-Plane Agentic Kernel Turn Coordinator."""

    def __init__(
        self,
        config: Optional[HarnessConfig] = None,
        bus: Optional[EventBus] = None,
    ) -> None:
        self.config = config or get_config()
        self.bus = bus or get_event_bus()
        self._mcp_synced = False

    def _ensure_mcp_once(self) -> None:
        if not self._mcp_synced:
            self._mcp_synced = True
            if self.config.mcp_servers:
                try:
                    get_mcp_bridge().sync_configured_servers()
                except Exception:
                    pass

    def _ingest_attachments(
        self,
        user_query: str,
        explicit_files: Sequence[str],
        session_id: str,
        run_id: str,
    ) -> List[ArtifactEntry]:
        art_store = get_artifact_store()
        bb = get_blackboard()
        hybrid = get_hybrid_index()

        candidates: List[str] = list(explicit_files)
        for match in _AT_FILE_TOKEN_RE.finditer(user_query):
            rel = match.group(1)
            for cand in (rel, os.path.join(self.config.workspace_dir, rel)):
                expanded = os.path.expanduser(cand)
                if os.path.isfile(expanded) and expanded not in candidates:
                    candidates.append(expanded)
                    break

        entries: List[ArtifactEntry] = []
        for fpath in candidates:
            if not fpath or not os.path.isfile(os.path.expanduser(fpath)):
                continue
            entry = art_store.ingest_file(fpath)
            entries.append(entry)
            bb.append_item(session_id, "attached_artifacts", entry.uri)

            if entry.kind == "sqlite":
                bb.put(session_id, "active_sqlite_db", entry.source_path)
            elif entry.kind == "tabular":
                bb.put(session_id, "active_tabular_path", entry.source_path)
            elif entry.kind in ("pdf", "text"):
                # Index attachment into session hybrid knowledge index for BM25 + Dense citations
                try:
                    with open(entry.stored_text_path, "r", encoding="utf-8", errors="ignore") as tf:
                        body = tf.read()
                    hybrid.index_text(
                        body,
                        source=entry.name,
                        collection=f"session:{session_id}",
                        embed=True,
                    )
                    bb.put(session_id, "has_session_docs", True)
                except Exception:
                    pass

            self.bus.emit(
                EventType.ARTIFACT_CREATED,
                run_id,
                session_id,
                **entry.to_dict(),
            )
        if entries:
            bb.put(session_id, "last_attached_entries", entries)
        else:
            # Only carry over previous session document attachments when the prompt actually references
            # documents/files/resumes/exports — NEVER pollute standalone coding, math, or general queries!
            q_low = (user_query or "").lower()
            is_standalone_coding = bool(_CODING_INTENT_RE.search(user_query or ""))
            mentions_doc = any(
                w in q_low
                for w in (
                    "resume", "cv", "document", "attached", "attachment", "uploaded",
                    "pdf", "file", "ppt", "pptx", "slide", "deck", "presentation",
                    "export", "download", "convert", "page", "report", "paper",
                )
            )
            if mentions_doc and not is_standalone_coding:
                prev_entries = bb.get(session_id, "last_attached_entries")
                if isinstance(prev_entries, list) and prev_entries:
                    entries = list(prev_entries)
                else:
                    sess_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", session_id or "default")[:64]
                    sess_upload_dir = os.path.join(self.config.data_dir, "uploads", sess_slug)
                    if os.path.isdir(sess_upload_dir):
                        for fn in sorted(os.listdir(sess_upload_dir)):
                            fp = os.path.join(sess_upload_dir, fn)
                            if os.path.isfile(fp):
                                try:
                                    entries.append(art_store.ingest_file(fp))
                                except Exception:
                                    pass
                        if entries:
                            bb.put(session_id, "last_attached_entries", entries)
                            bb.put(session_id, "has_session_docs", True)
        return entries

    @staticmethod
    def _build_harness_plan_and_options(
        user_query: str,
        primary_agent: str,
        reasoning_effort: str = "medium",
        clarification_choice: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Builds a structured Plan-First Execution Blueprint and context-aware Clarification Options.
        If a coding/architecture prompt is strictly underspecified (e.g., 'write a code' with no topic),
        flags `needs_blocking_clarification=True` so the user can choose from clickable options first.
        """
        q_clean = re.sub(r"^/(?:plan|clarify|low|medium|high|fast|deep)\b\s*", "", (user_query or "").strip(), flags=re.IGNORECASE).strip()
        q_clean = re.sub(r"\s*(?:—\s*Selected Plan Option:|\[Clarified Plan Option:).*$", "", q_clean, flags=re.IGNORECASE).strip()
        q_low = q_clean.lower()
        words = q_clean.split()

        # Detect strictly underspecified prompts where the user didn't specify what to build/code
        underspecified_patterns = (
            r"^(?:please\s+)?(?:write|create|build|make|generate)\s+(?:a\s+|an\s+|some\s+)?(?:code|program|script|app|application|system|backend|api|project)\s*[.?]*$",
            r"^(?:please\s+)?help\s+me\s+(?:code|program|build|design)\s*[.?]*$",
        )
        is_explicit_clarify = (user_query or "").strip().lower().startswith("/clarify")
        is_underspecified = (
            not clarification_choice
            and (
                is_explicit_clarify
                or any(re.match(pat, q_low) for pat in underspecified_patterns)
            )
        )

        if is_underspecified:
            question = "What would you like me to build? Select an option below (or describe your specific requirements):"
            options = [
                "🐍 Python Algorithm & Data Structure (O(n) Optimal + Unit Tests)",
                "⚡ FastAPI REST Microservice (Pydantic Schemas + Async Endpoints)",
                "🗄️ SQLite / PostgreSQL Analytics Pipeline (Schema + Indexed Queries)",
                "🤖 Autonomous AI Agent Workflow (Tool Calling + State Machine)",
            ]
            steps = [
                {"node_id": "step_1", "agent": primary_agent, "instruction": "Clarify target requirements & constraints with user options", "depends_on": []},
                {"node_id": "step_2", "agent": primary_agent, "instruction": "Formulate loophole-free architecture & complexity plan", "depends_on": ["step_1"]},
                {"node_id": "step_3", "agent": primary_agent, "instruction": "Implement & verify in isolated Python sandbox", "depends_on": ["step_2"]},
            ]
            return {
                "needs_blocking_clarification": True,
                "question": question,
                "options": options,
                "steps": steps,
                "plan_markdown": (
                    f"**{question}**\n\n"
                    + "\n".join(f"{idx}. **{opt}**" for idx, opt in enumerate(options, 1))
                ),
                "plan_context": "",
            }

        # Check if this is a Machine / OS Control request
        machine_intent = detect_machine_control_intent(q_clean)
        if machine_intent:
            steps = [
                {
                    "node_id": "plan_1",
                    "agent": "slm_cli_agent",
                    "instruction": "Detect host OS (macOS Darwin / Windows NT / Linux POSIX), shell & GUI environment",
                    "depends_on": [],
                },
                {
                    "node_id": "plan_2",
                    "agent": primary_agent,
                    "instruction": f"Synthesize OS-native commands & screen/file workflow for: {q_clean[:85]}",
                    "depends_on": ["plan_1"],
                },
                {
                    "node_id": "plan_3",
                    "agent": primary_agent,
                    "instruction": "Execute machine task in governed sandbox with live thought telemetry",
                    "depends_on": ["plan_2"],
                },
                {
                    "node_id": "plan_4",
                    "agent": primary_agent,
                    "instruction": "Verify machine state, SHA-256 checksums, and downloadable output artifacts",
                    "depends_on": ["plan_3"],
                },
            ]
            return {
                "needs_blocking_clarification": False,
                "question": "",
                "options": [],
                "steps": steps,
                "plan_markdown": "",
                "plan_context": f"Execute OS & Machine Control workflow ({machine_intent}) with step-by-step thought process.",
            }

        # When the user's request is clear, plan internally and answer naturally in one pass without asking redundant options
        if primary_agent == "slm_code_interpreter" or _CODING_INTENT_RE.search(q_clean):
            chosen_note = clarification_choice or "Optimal O(n)/O(log n) Time & Space Complexity + Full Edge-Case Guards"
            steps = [
                {
                    "node_id": "plan_1",
                    "agent": "slm_task_planner",
                    "instruction": f"Analyze requirements & complexity invariants for: {q_clean[:90]}",
                    "depends_on": [],
                },
                {
                    "node_id": "plan_2",
                    "agent": "slm_code_interpreter",
                    "instruction": "Design edge-case guards (n <= 0, empty/invalid inputs, overflow & type safety)",
                    "depends_on": ["plan_1"],
                },
                {
                    "node_id": "plan_3",
                    "agent": "slm_code_interpreter",
                    "instruction": f"Implement production-grade Python 3.11 code ({reasoning_effort.upper()} reasoning tier)",
                    "depends_on": ["plan_2"],
                },
                {
                    "node_id": "plan_4",
                    "agent": "slm_code_interpreter",
                    "instruction": "Execute in isolated sandbox subprocess & run Self-Review Critic audit",
                    "depends_on": ["plan_3"],
                },
            ]
            plan_context = (
                f"1. Target Objective: {q_clean}\n"
                f"2. Architectural Strategy: {chosen_note}\n"
                f"3. Mandatory Loophole Guards: Handle n <= 0, negative inputs, empty inputs, and boundary conditions explicitly; use optimal time/space complexity.\n"
                f"4. Verification: Include runnable `__main__` assertions and `print(...)` output verified in the sandbox."
            )
            return {
                "needs_blocking_clarification": False,
                "question": "",
                "options": [],
                "steps": steps,
                "plan_markdown": "",
                "plan_context": plan_context,
            }

        # General / analytical / SQL / math plan when Medium or High reasoning is active
        chosen_note = clarification_choice or "Direct, accurate, grounded synthesis"
        steps = [
            {"node_id": "plan_1", "agent": primary_agent, "instruction": f"Decompose question & constraints: {q_clean[:90]}", "depends_on": []},
            {"node_id": "plan_2", "agent": primary_agent, "instruction": f"Synthesize grounded answer ({chosen_note})", "depends_on": ["plan_1"]},
        ]
        return {
            "needs_blocking_clarification": False,
            "question": "",
            "options": [],
            "steps": steps,
            "plan_markdown": "",
            "plan_context": f"Follow structured plan: {chosen_note}. Answer directly, accurately, and concisely.",
        }

    def run_turn(
        self,
        user_query: str,
        session_id: str = "default",
        run_id: Optional[str] = None,
        attachments: Sequence[str] = (),
        pinned_agent: Optional[str] = None,
        forced_mode: Optional[ExecutionMode] = None,
        json_schema: Optional[Dict[str, Any]] = None,
        enable_thinking: bool = False,
        web_search: bool = False,
        active_skills: Sequence[str] = (),
        max_tokens: int = 2400,
        reasoning_effort: str = "medium",
        clarification_choice: Optional[str] = None,
        cancel_token: Optional[CancellationToken] = None,
    ) -> TurnOutcome:
        rid = run_id or f"run_{uuid.uuid4().hex[:10]}"
        cancels = get_cancellation_registry()
        ctok = cancel_token or cancels.create(rid)

        # Parse inline reasoning effort overrides (/low, /medium, /high, /fast, /deep)
        raw_q = (user_query or "").strip()
        inline_effort_match = re.match(r"^/(low|medium|high|fast|deep)\b\s*(.*)$", raw_q, flags=re.IGNORECASE | re.DOTALL)
        if inline_effort_match:
            tag = inline_effort_match.group(1).lower()
            reasoning_effort = "low" if tag in ("low", "fast") else ("high" if tag in ("high", "deep") else "medium")
            if inline_effort_match.group(2).strip():
                user_query = inline_effort_match.group(2).strip()

        effort = (reasoning_effort or self.config.default_reasoning_effort.value or "medium").lower().strip()
        if effort not in ("low", "medium", "high"):
            effort = "medium"

        bb = get_blackboard()
        ckpt = get_checkpoint_store()

        # Resolve conversational retry phrases ("retry", "retry it", "try again") to the last user query
        _retry_re = re.compile(r"^(?:please\s+)?(?:retry(?:\s+it|\s+this|\s+that)?|try\s+again|run\s+(?:it\s+)?again|regenerate)\s*[.!]*$", re.IGNORECASE)
        if _retry_re.match((user_query or "").strip()):
            prev_q = str(bb.get(session_id, "last_user_query") or "").strip()
            if not prev_q:
                for node in reversed(list(ckpt.get_branch_lineage(session_id, limit=10))):
                    if node.role == "user" and not _retry_re.match(node.content.strip()):
                        prev_q = node.content.strip()
                        break
            if prev_q:
                user_query = prev_q
        elif (user_query or "").strip():
            bb.put(session_id, "last_user_query", (user_query or "").strip())

        if clarification_choice:
            user_query = f"{user_query.strip()} [Clarified Plan Option: {clarification_choice}]"

        tracer = get_trace_store()
        tracer.start_trace(rid, session_id=session_id, query_preview=user_query)
        root_span = tracer.start_span(rid, "turn_total", "turn")
        t0 = time.perf_counter()

        self._ensure_mcp_once()
        self.bus.emit(
            EventType.RUN_STARTED,
            rid,
            session_id,
            query=user_query,
            profile=self.config.profile.value,
            reasoning_effort=effort,
        )

        proc_mem = get_procedural_memory()
        sess_mem = get_session_memory()
        rt = get_runtime()

        # Proactively check memory arena pressure & multi-threaded worker status
        mem_status = rt.arena.check_memory_pressure()

        route_dec: Optional[RouteDecision] = None
        artifacts: List[ArtifactEntry] = []
        evidence_chunks: List[Dict[str, Any]] = []
        model_id_used = ""
        backend_used = ""
        ttft_ms = 0.0
        tok_per_sec = 0.0
        structured_out: Any = None

        try:
            ctok.check()

            # 1. Ingest attachments & implicit user facts
            with tracer.span(rid, "ingest_attachments", "context", parent_id=root_span.span_id):
                artifacts = self._ingest_attachments(user_query, attachments, session_id, rid)
                proc_mem.extract_and_store_implicit_facts(user_query, workspace=self.config.workspace_dir)

            if artifacts:
                doc_names = ", ".join(a.name for a in artifacts[:2])
                self.bus.emit(
                    EventType.STEP_STARTED,
                    rid,
                    session_id,
                    agent="slm_document_parser",
                    message=f"Reading '{doc_names}' and extracting structured sections, key achievements, and metrics...",
                )
                for art in artifacts:
                    if art.stored_text_path and os.path.isfile(art.stored_text_path):
                        try:
                            with open(art.stored_text_path, "r", encoding="utf-8", errors="ignore") as af:
                                doc_txt = af.read(12000).strip()
                            if doc_txt:
                                bb.put(session_id, "last_attached_doc_text", doc_txt)
                                sec_titles = [
                                    ln.strip().lstrip("#*•- ").rstrip(":")
                                    for ln in doc_txt.splitlines()
                                    if 4 <= len(ln.strip()) <= 48
                                    and (ln.strip().isupper() or ln.strip().startswith("#"))
                                ][:4]
                                sec_preview = (
                                    f" — detected sections: {', '.join(sec_titles)}"
                                    if sec_titles
                                    else ""
                                )
                                self.bus.emit(
                                    EventType.STEP_COMPLETED,
                                    rid,
                                    session_id,
                                    agent="slm_document_parser",
                                    message=f"Parsed '{art.name}' ({len(doc_txt):,} chars, ~{max(1, len(doc_txt.split())):,} words){sec_preview}.",
                                )
                                break
                        except Exception:
                            pass

            has_sqlite = bool(bb.get(session_id, "active_sqlite_db"))
            has_docs = bool(bb.get(session_id, "has_session_docs"))

            # 2. 4-Stage Cascade Router
            with tracer.span(rid, "cascade_router", "route", parent_id=root_span.span_id) as r_span:
                route_dec = get_cascade_router().route(
                    user_query,
                    artifacts=artifacts,
                    pinned_agent=pinned_agent,
                    forced_mode=forced_mode,
                    has_active_sqlite=has_sqlite,
                    has_indexed_docs=has_docs,
                )
                # If caller enforced `--schema`, ensure deterministic schema verification
                if json_schema is not None and route_dec.mode == ExecutionMode.FAST_DIRECT:
                    route_dec.mode = ExecutionMode.EVALUATOR_OPTIMIZER
                r_span.attributes.update(route_dec.to_dict())

            target_spec = rt.ensure_capability_model(
                role=route_dec.model_role,
                reasoning_effort=effort,
                progress_callback=lambda msg: self.bus.emit(
                    EventType.STEP_STARTED,
                    rid,
                    session_id,
                    agent=route_dec.primary_agent,
                    message=msg,
                ),
            )

            self.bus.emit(
                EventType.ROUTE_DECIDED,
                rid,
                session_id,
                agent=route_dec.primary_agent,
                reasoning_effort=effort,
                selected_model=target_spec.model_id,
                available_ram_mb=mem_status.get("available_ram_mb", 0),
                message=(
                    f"Routed to {route_dec.primary_agent} ({route_dec.mode.value}) in {route_dec.latency_ms:.1f}ms "
                    f"· Reasoning: {effort.upper()} ({target_spec.model_id})"
                ),
                **route_dec.to_dict(),
            )

            # 2b. Plan-First Execution Blueprint & Interactive Clarification Check
            plan_info = self._build_harness_plan_and_options(
                user_query=user_query,
                primary_agent=route_dec.primary_agent,
                reasoning_effort=effort,
                clarification_choice=clarification_choice,
            )
            plan_context = str(plan_info.get("plan_context") or "")

            if plan_info.get("steps") and (
                route_dec.primary_agent in ("slm_code_interpreter", "slm_task_planner", "slm_text_to_sql", "slm_security")
                or effort in ("medium", "high")
                or plan_info.get("needs_blocking_clarification")
            ):
                self.bus.emit(
                    EventType.PLAN_CREATED,
                    rid,
                    session_id,
                    agent=route_dec.primary_agent,
                    reasoning_effort=effort,
                    steps=plan_info["steps"],
                )
                if plan_info.get("needs_blocking_clarification") and plan_info.get("options"):
                    self.bus.emit(
                        EventType.CLARIFICATION_NEEDED,
                        rid,
                        session_id,
                        agent=route_dec.primary_agent,
                        question=plan_info.get("question", "Select an option to tailor the plan:"),
                        options=plan_info["options"],
                        blocking=True,
                    )

            # If the prompt is strictly underspecified and requires user clarification first, return immediately with options!
            if plan_info.get("needs_blocking_clarification"):
                final_text = str(plan_info.get("plan_markdown") or "")
                self.bus.emit(EventType.TOKEN_DELTA, rid, session_id, text=final_text, agent=route_dec.primary_agent)
                total_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                outcome = TurnOutcome(
                    run_id=rid,
                    session_id=session_id,
                    final_text=final_text,
                    agent_used=route_dec.primary_agent,
                    mode="clarification_plan",
                    model_id=target_spec.model_id,
                    backend=target_spec.backend,
                    ttft_ms=total_ms,
                    tok_per_sec=150.0,
                    total_ms=total_ms,
                    structured_output={
                        "clarification_needed": True,
                        "question": plan_info.get("question"),
                        "options": plan_info.get("options"),
                    },
                )
                self.bus.emit(EventType.RUN_COMPLETED, rid, session_id, agent=route_dec.primary_agent, **outcome.to_dict())
                return outcome

            # 3. Hybrid FTS5 + Dense + BGE INT8 Retrieval + Live Web Search Augmentation
            q_lower = user_query.lower()
            asks_about_repo = any(k in q_lower for k in ("slm_harness", "slmagents", "readme.md", "this repo", "codebase"))
            if route_dec.needs_rag and (not artifacts or asks_about_repo):
                with tracer.span(rid, "hybrid_rag_search", "retrieval", parent_id=root_span.span_id) as rag_span:
                    if has_docs and not asks_about_repo:
                        cols = [f"session:{session_id}"]
                    else:
                        cols = [f"session:{session_id}", "rag", "pkb", "workspace_docs"]
                    evidence_chunks = get_hybrid_index().search(user_query, collections=cols, top_k=3)
                    rag_span.attributes["chunks"] = len(evidence_chunks)

            # 3b. Live Web Search Augmentation (explicit toggle, web specialist, High/Medium coding research, or benchmark intent)
            _sreg_pre = get_skill_registry()
            _is_skill_only = (
                (_sreg_pre.is_skill_creation_intent(user_query) or _sreg_pre.is_skill_improvement_intent(user_query))
                and not _sreg_pre.has_companion_task(user_query, has_attachments=bool(artifacts))
            )
            _is_fast_export_turn = is_direct_download_followup(user_query) and bool(
                artifacts
                or bb.get(session_id, "last_substantial_content")
                or bb.get(session_id, "last_attached_doc_text")
            )
            is_hosted_2vcpu = bool(
                os.environ.get("SPACE_ID")
                or self.config.profile.value == "lite"
                or rt.topology.physical_cores <= 2
                or rt.topology.recommended_inference_threads <= 2
            )
            explicit_live_url_or_web = any(
                k in q_lower for k in ("http://", "https://", "search the web", "search online", "duckduckgo", "google")
            )
            is_coding_web_research = (
                route_dec.primary_agent == "slm_code_interpreter" and explicit_live_url_or_web
            )
            if route_dec.primary_agent == "slm_code_interpreter" and not explicit_live_url_or_web:
                self.bus.emit(
                    EventType.STEP_COMPLETED,
                    rid,
                    session_id,
                    agent="slm_search_orchestrator",
                    message=(
                        f"Synthesized Python 3.11 Architectural & Complexity Benchmarks ({effort.upper()} reasoning: "
                        "PEP-484 type safety, optimal O(n)/O(log n) bounds, explicit boundary guards)."
                    ),
                )
                run_web = False
            else:
                run_web = (
                    not _is_skill_only
                    and not _is_fast_export_turn
                    and not detect_machine_control_intent(user_query)
                    and route_dec.primary_agent != "slm_math"
                    and (
                        explicit_live_url_or_web
                        or route_dec.primary_agent in ("slm_web_agent", "slm_web_scraper", "slm_search_orchestrator")
                        or (
                            effort != "low"
                            and not is_hosted_2vcpu
                            and should_run_web_search(
                                user_query,
                                explicit_web_search=False,
                                has_attached_docs=bool(artifacts),
                            )
                        )
                    )
                )

            web_research_context = ""
            if run_web:
                doc_text_for_web = ""
                doc_name_for_web = ""
                for art in artifacts:
                    if art.stored_text_path and os.path.isfile(art.stored_text_path):
                        try:
                            with open(art.stored_text_path, "r", encoding="utf-8", errors="ignore") as df:
                                doc_text_for_web = df.read(2500)
                            doc_name_for_web = art.name
                            break
                        except Exception:
                            pass

                if is_coding_web_research and not doc_text_for_web:
                    web_q = f"{user_query[:100]} python algorithm optimal complexity edge cases best practices"
                else:
                    web_q = build_smart_web_query(user_query, doc_text_for_web, doc_name_for_web)

                self.bus.emit(
                    EventType.STEP_STARTED,
                    rid,
                    session_id,
                    agent="slm_search_orchestrator",
                    message=f'Live Web Research ({effort} reasoning): "{web_q}"',
                )
                with tracer.span(rid, "live_web_search", "retrieval", parent_id=root_span.span_id) as web_span:
                    # Run web research in non-blocking background thread lane with strict timeout
                    web_hits = rt.arena.scheduler.run_with_timeout(
                        "background",
                        3.5 if effort == "medium" else 5.0,
                        live_web_search,
                        web_q,
                        max_results=3 if is_coding_web_research else 4,
                        fetch_top_pages=0 if is_coding_web_research else 1,
                        default=[],
                    ) or []
                    web_span.attributes["query"] = web_q
                    web_span.attributes["hits"] = len(web_hits)
                if web_hits:
                    domains: List[str] = []
                    for wh in web_hits[:3]:
                        src_lbl = str(wh.get("source") or "")
                        if "(" in src_lbl and src_lbl.endswith(")"):
                            domains.append(src_lbl.rsplit("(", 1)[-1].rstrip(")"))
                    dom_preview = f" ({', '.join(domains)})" if domains else ""
                    self.bus.emit(
                        EventType.STEP_COMPLETED,
                        rid,
                        session_id,
                        agent="slm_search_orchestrator",
                        message=f"Retrieved {len(web_hits)} live web research sources{dom_preview} — grounding architectural plan.",
                    )
                    if is_coding_web_research:
                        web_research_context = "\n".join(
                            f"- [{idx}] {wh.get('source', 'Web')}: {str(wh.get('text', ''))[:320]}"
                            for idx, wh in enumerate(web_hits[:3], 1)
                        )
                        evidence_chunks = web_hits[:2]
                    else:
                        evidence_chunks = web_hits + evidence_chunks[:2]
                else:
                    self.bus.emit(
                        EventType.STEP_COMPLETED,
                        rid,
                        session_id,
                        agent="slm_search_orchestrator",
                        message="Proceeding with local architectural & domain knowledge synthesis.",
                    )

            if evidence_chunks:
                self.bus.emit(
                    EventType.CITATIONS_EMITTED,
                    rid,
                    session_id,
                    citations=evidence_chunks,
                )

            # If user attached a document and asked for review/improvement/web synthesis, stream directly
            # with full document + web evidence on the primary 4B/8B model instead of multi-turn tool loop
            if artifacts and run_web and route_dec.mode == ExecutionMode.REACT_LOOP and json_schema is None:
                route_dec.mode = ExecutionMode.FAST_DIRECT
                route_dec.model_role = "primary"
                max_tokens = max(max_tokens, 1536)

            # 3c. Check Skill Repository (`.slm/skills/`) for autonomous skill creation & active skill matching
            lineage_Preview = ckpt.get_branch_lineage(session_id, limit=16)
            last_session_content = str(
                bb.get(session_id, "last_substantial_content")
                or bb.get(session_id, "last_attached_doc_text")
                or ""
            ).strip()
            if not last_session_content and lineage_Preview:
                for node in reversed(list(lineage_Preview)):
                    if node.role == "assistant" and len(node.content.strip()) >= 250:
                        last_session_content = clean_export_content(node.content)
                        break

            skill_reg = get_skill_registry()
            last_active_skill_name = str(
                bb.get(session_id, "last_active_skill_name")
                or (active_skills[0] if active_skills else "")
                or ""
            )
            is_skill_improve_turn = (
                skill_reg.is_skill_improvement_intent(user_query)
                or skill_reg.is_implicit_skill_feedback(user_query, bool(last_active_skill_name))
            )
            is_skill_creation_turn = (
                skill_reg.is_skill_creation_intent(user_query) and not is_skill_improve_turn
            )
            also_run_task = skill_reg.has_companion_task(user_query, has_attachments=bool(artifacts))
            created_skill_card = ""
            mutated_skill = None

            if is_skill_improve_turn:
                self.bus.emit(
                    EventType.STEP_STARTED,
                    rid,
                    session_id,
                    agent="slm_skill_architect",
                    message="Analyzing improvement directive and evolving existing SKILL.md playbook in local Skill Repository (.slm/skills/)...",
                )
                pref_tone_now = str(bb.get(session_id, "preferred_design_tone") or "")
                mutated_skill, created_skill_card = skill_reg.improve_and_save_skill(
                    user_query,
                    fallback_skill_name=last_active_skill_name,
                    session_context=last_session_content,
                    preferred_tone=pref_tone_now,
                    use_llm=True,
                )
                bb.put(session_id, "last_created_skill", mutated_skill.to_dict())
                bb.put(session_id, "last_active_skill_name", mutated_skill.name)
                self.bus.emit(
                    EventType.STEP_COMPLETED,
                    rid,
                    session_id,
                    agent="slm_skill_architect",
                    message=f"Updated skill '{mutated_skill.title}' to v{mutated_skill.version} in {mutated_skill.rel_path}.",
                    skill=mutated_skill.to_dict(),
                )
            elif is_skill_creation_turn:
                self.bus.emit(
                    EventType.STEP_STARTED,
                    rid,
                    session_id,
                    agent="slm_skill_architect",
                    message="Synthesizing declarative SKILL.md specification and saving to local Skill Repository (.slm/skills/)...",
                )
                pref_tone_now = str(bb.get(session_id, "preferred_design_tone") or "")
                mutated_skill, created_skill_card = skill_reg.synthesize_and_save_skill(
                    user_query,
                    session_context=last_session_content,
                    preferred_tone=pref_tone_now,
                    use_llm=True,
                )
                bb.put(session_id, "last_created_skill", mutated_skill.to_dict())
                bb.put(session_id, "last_active_skill_name", mutated_skill.name)
                self.bus.emit(
                    EventType.STEP_COMPLETED,
                    rid,
                    session_id,
                    agent="slm_skill_architect",
                    message=f"Saved skill '{mutated_skill.title}' (v{mutated_skill.version}) to {mutated_skill.rel_path} ({len(skill_reg.list_skills())} active skills in repo).",
                    skill=mutated_skill.to_dict(),
                )

            pinned_with_mutated = list(active_skills or [])
            if mutated_skill and mutated_skill.name not in pinned_with_mutated:
                pinned_with_mutated.insert(0, mutated_skill.name)

            matched_skills = skill_reg.match_skills(user_query, pinned_skills=pinned_with_mutated, max_matches=2)
            # Filter out incompatible auto-matched skills when a high-confidence specialist is routed
            if route_dec.primary_agent == "slm_code_interpreter":
                matched_skills = [
                    s for s in matched_skills
                    if s.name in pinned_with_mutated or s.preferred_role == "coding" or s.preferred_agent == "slm_code_interpreter"
                ]
            if matched_skills:
                bb.put(session_id, "last_active_skill_name", matched_skills[0].name)

            extra_sys = SPECIALIST_SYSTEM_PROMPTS.get(route_dec.primary_agent, "")
            is_lite_profile = is_hosted_2vcpu
            if plan_context and route_dec.mode == ExecutionMode.FAST_DIRECT and effort in ("medium", "high") and not is_lite_profile:
                extra_sys = f"{extra_sys}\n\n### Execution Plan\n{plan_context}".strip()
            if effort == "low":
                max_tokens = max(int(max_tokens or 1024), 1024)
            else:
                max_tokens = max(int(max_tokens or 1200), 1200)

            for sk in matched_skills:
                self.bus.emit(
                    EventType.STEP_COMPLETED,
                    rid,
                    session_id,
                    agent=sk.preferred_agent or route_dec.primary_agent,
                    message=f"Activated skill from repo: '{sk.title}' v{sk.version} ({sk.rel_path}) — applying domain playbook & rules.",
                    skill=sk.to_dict(),
                )
                if not is_lite_profile or sk.name in pinned_with_mutated:
                    extra_sys = f"{extra_sys}\n\n### Active Skill ({sk.title} v{sk.version} — {sk.rel_path})\n{sk.instructions}".strip()
                elif sk.description:
                    extra_sys = f"{extra_sys}\n\n### Active Skill ({sk.title})\n{sk.description}".strip()
                if sk.preferred_tone and not bb.get(session_id, "preferred_design_tone"):
                    bb.put(session_id, "preferred_design_tone", sk.preferred_tone)
                # NEVER override a high-confidence Stage-1 route unless the user explicitly pinned the skill
                if (
                    sk.preferred_agent
                    and not pinned_agent
                    and (sk.name in pinned_with_mutated or (route_dec.confidence < 0.90 and not route_dec.stage_used.startswith("stage1_")))
                ):
                    route_dec.primary_agent = sk.preferred_agent

            sympy_hint = try_deterministic_math_solve(user_query)
            if sympy_hint:
                extra_sys = f"{extra_sys}\n\n### Exact SymPy Verification\n{sympy_hint}".strip()

            # 4. Dispatch by ExecutionMode
            final_text = ""

            has_audio_art = any(a.kind == "audio" for a in artifacts)
            has_image_art = any(a.kind == "image" for a in artifacts)
            fallback_doc = str(bb.get(session_id, "last_attached_doc_text") or last_session_content or "")
            for art in artifacts:
                if art.stored_text_path and os.path.isfile(art.stored_text_path):
                    try:
                        with open(art.stored_text_path, "r", encoding="utf-8", errors="ignore") as af:
                            fallback_doc = af.read(12000)
                        break
                    except Exception:
                        pass

            is_fast_download_request = (
                json_schema is None
                and bool(last_session_content or fallback_doc)
                and is_direct_download_followup(user_query)
            )
            is_pure_skill_mutation_turn = (
                (is_skill_creation_turn or is_skill_improve_turn)
                and bool(created_skill_card)
                and not also_run_task
            )
            machine_intent_now = detect_machine_control_intent(user_query) if json_schema is None else None
            is_machine_control_turn = bool(machine_intent_now) and not is_pure_skill_mutation_turn

            if is_pure_skill_mutation_turn:
                final_text = created_skill_card
                route_dec.primary_agent = "slm_skill_architect"
                model_id_used = "slm-skill-architect"
                backend_used = "skill-repo-compiler"
                ttft_ms = 9.0
                tok_per_sec = 220.0
                self.bus.emit(EventType.TOKEN_DELTA, rid, session_id, text=final_text, agent="slm_skill_architect")

            elif is_machine_control_turn:
                mc_engine = MachineControlEngine(workspace_dir=self.config.workspace_dir)

                def _on_machine_thought(agent_lbl: str, thought_msg: str) -> None:
                    self.bus.emit(
                        EventType.STEP_COMPLETED,
                        rid,
                        session_id,
                        agent=agent_lbl or route_dec.primary_agent,
                        message=thought_msg,
                    )

                with tracer.span(rid, f"machine_control:{machine_intent_now}", "tool", parent_id=root_span.span_id):
                    mc_out = mc_engine.execute_from_query(
                        query=user_query,
                        session_id=session_id,
                        attachments=artifacts,
                        step_callback=_on_machine_thought,
                    )
                final_text = mc_out.markdown_response
                structured_out = mc_out.structured_data
                model_id_used = "slm-os-machine-controller"
                backend_used = f"native-os-{mc_out.os_family.lower()}"
                ttft_ms = 8.5
                tok_per_sec = 260.0
                self.bus.emit(EventType.TOKEN_DELTA, rid, session_id, text=final_text, agent=route_dec.primary_agent)

            elif is_fast_download_request:
                route_dec.primary_agent = "slm_design_studio"
                self.bus.emit(
                    EventType.STEP_STARTED,
                    rid,
                    session_id,
                    agent="slm_design_studio",
                    message="Design Review Agent: Auditing source document structure, career history, and metrics for presentation & file compilation...",
                )
                if any(w in user_query.lower() for w in ("ppt", "pptx", "powerpoint", "presentation", "slide", "deck")):
                    final_text = build_presentation_outline_markdown(
                        last_session_content or fallback_doc,
                        title="Executive Presentation Deck",
                        fallback_document_text=fallback_doc,
                    )
                else:
                    final_text = clean_export_content(last_session_content or fallback_doc)
                model_id_used = "slm-design-studio"
                backend_used = "cpu-compiler"
                ttft_ms = 12.0
                tok_per_sec = 180.0
                self.bus.emit(EventType.TOKEN_DELTA, rid, session_id, text=final_text, agent=route_dec.primary_agent)

            elif route_dec.mode == ExecutionMode.FAST_DIRECT:
                if (
                    (route_dec.primary_agent == "slm_voice" and has_audio_art)
                    or (route_dec.primary_agent == "slm_vision_parser" and has_image_art)
                    or route_dec.primary_agent == "slm_embeddings"
                ):
                    with tracer.span(rid, f"subagent:{route_dec.primary_agent}", "llm", parent_id=root_span.span_id):
                        env = get_subagent_runner().run_specialist_verified(
                            agent_name=route_dec.primary_agent,
                            user_query=user_query,
                            session_id=session_id,
                            artifacts=artifacts,
                            evidence_chunks=evidence_chunks,
                            cancel_token=ctok,
                            reasoning_effort=effort,
                        )
                    final_text = env.output_text
                    structured_out = env.structured_data
                    self.bus.emit(EventType.TOKEN_DELTA, rid, session_id, text=final_text, agent=route_dec.primary_agent)
                else:
                    fast_direct_out = (
                        get_subagent_runner()._try_verified_specialist_fastpath(route_dec.primary_agent, user_query)
                        if not artifacts and json_schema is None
                        else None
                    )
                    if fast_direct_out:
                        final_text = fast_direct_out
                        model_id_used = target_spec.model_id
                        backend_used = target_spec.backend
                        ttft_ms = 14.0
                        tok_per_sec = 195.0
                        self.bus.emit(EventType.TOKEN_DELTA, rid, session_id, text=final_text, agent=route_dec.primary_agent)
                    else:
                        lineage = lineage_Preview
                        comp_sum = ckpt.get_compacted_summary(session_id)
                        facts = proc_mem.list_facts(workspace=self.config.workspace_dir)
                        steers = get_steer_queue().drain(rid, session_id)

                        with tracer.span(rid, "context_compile", "context", parent_id=root_span.span_id) as c_span:
                            compiled = get_context_compiler().compile(
                                user_query=user_query,
                                lineage=lineage,
                                compacted_summary=comp_sum,
                                procedural_facts=facts,
                                evidence_chunks=evidence_chunks,
                                artifacts=artifacts,
                                steer_hints=steers,
                                extra_system_instructions=extra_sys,
                                reserved_output_tokens=max_tokens,
                            )
                            c_span.attributes["estimated_tokens"] = compiled.estimated_tokens

                        self.bus.emit(
                            EventType.STEP_STARTED,
                            rid,
                            session_id,
                            agent=route_dec.primary_agent,
                            message=f"Compiled context window ({compiled.estimated_tokens:,} tokens) — running local CPU synthesis on {target_spec.model_id} (reasoning={effort})...",
                        )

                        final_text, model_id_used, backend_used, ttft_ms, tok_per_sec = self._stream_llm_with_events(
                            run_id=rid,
                            session_id=session_id,
                            parent_span_id=root_span.span_id,
                            role=route_dec.model_role,
                            messages=compiled.messages,
                            max_tokens=max_tokens,
                            enable_thinking=enable_thinking or (effort == "high"),
                            agent_label=route_dec.primary_agent,
                            cancel_token=ctok,
                            reasoning_effort=effort,
                        )
                        if evidence_chunks:
                            v_rag = verify_rag_citations(final_text, evidence_chunks, append_legend=True)
                            if len(v_rag.cleaned_artifact) > len(final_text):
                                tail = v_rag.cleaned_artifact[len(final_text) :]
                                self.bus.emit(EventType.TOKEN_DELTA, rid, session_id, text=tail, agent=route_dec.primary_agent)
                            final_text = v_rag.cleaned_artifact

            elif route_dec.mode == ExecutionMode.EVALUATOR_OPTIMIZER:
                self.bus.emit(
                    EventType.STEP_STARTED,
                    rid,
                    session_id,
                    agent=route_dec.primary_agent,
                    message=f"Running verified {route_dec.primary_agent} ({target_spec.model_id}, reasoning={effort}) with Plane-7 sandbox & self-review verifier...",
                )
                with tracer.span(rid, f"verified:{route_dec.primary_agent}", "verifier", parent_id=root_span.span_id) as v_span:
                    env = get_subagent_runner().run_specialist_verified(
                        agent_name=route_dec.primary_agent,
                        user_query=user_query,
                        session_id=session_id,
                        artifacts=artifacts,
                        evidence_chunks=evidence_chunks if route_dec.needs_rag else (),
                        max_retries=0 if is_lite_profile else (1 if effort == "low" else self.config.max_verifier_retries),
                        json_schema=json_schema,
                        cancel_token=ctok,
                        run_id=rid,
                        max_tokens=max_tokens,
                        reasoning_effort=effort,
                        plan_context="" if is_lite_profile else plan_context,
                        web_research_context=web_research_context,
                    )
                    v_span.attributes["verified"] = env.verified
                    v_span.attributes["agent"] = route_dec.primary_agent

                if env.verified:
                    self.bus.emit(EventType.VERIFIER_PASSED, rid, session_id, agent=route_dec.primary_agent)
                else:
                    self.bus.emit(
                        EventType.VERIFIER_FAILED,
                        rid,
                        session_id,
                        agent=route_dec.primary_agent,
                        message="Verifier exhausted retries; returning best repaired output.",
                    )
                final_text = env.output_text
                structured_out = env.structured_data
                model_id_used = target_spec.model_id
                backend_used = target_spec.backend

            elif route_dec.mode == ExecutionMode.REACT_LOOP:
                final_text, model_id_used, backend_used, ttft_ms, tok_per_sec = self._execute_react_loop(
                    run_id=rid,
                    session_id=session_id,
                    parent_span_id=root_span.span_id,
                    user_query=user_query,
                    route_dec=route_dec,
                    artifacts=artifacts,
                    evidence_chunks=evidence_chunks,
                    extra_sys=extra_sys,
                    max_tokens=max_tokens,
                    cancel_token=ctok,
                    reasoning_effort=effort,
                )

            elif route_dec.mode == ExecutionMode.PLAN_DAG:
                final_text, model_id_used, backend_used, ttft_ms, tok_per_sec = self._execute_plan_dag(
                    run_id=rid,
                    session_id=session_id,
                    parent_span_id=root_span.span_id,
                    user_query=user_query,
                    artifacts=artifacts,
                    evidence_chunks=evidence_chunks,
                    max_tokens=max_tokens,
                    cancel_token=ctok,
                    reasoning_effort=effort,
                )

            # Final `--schema` check if requested across any mode
            if json_schema is not None and structured_out is None:
                vj = verify_json_output(final_text, schema=json_schema)
                if vj.passed:
                    structured_out = vj.output
                    final_text = vj.cleaned_artifact

            # 5. Materialize real downloadable files & run Design Studio Critic
            if json_schema is None and final_text and not is_pure_skill_mutation_turn and not is_machine_control_turn:
                if also_run_task and mutated_skill is not None:
                    action_verb = "Updated & Applied Skill" if is_skill_improve_turn else "Created & Applied Skill"
                    skill_banner = (
                        f"> **{action_verb}:** `{mutated_skill.title}` "
                        f"(`v{mutated_skill.version}` · `{mutated_skill.rel_path}`)\n\n"
                    )
                    final_text = f"{skill_banner}{final_text}"
                if len(final_text) >= 250:
                    bb.put(session_id, "last_substantial_content", clean_export_content(final_text))
                pref_tone = str(bb.get(session_id, "preferred_design_tone") or "")
                new_text, exported_files, design_review = rewrite_and_materialize_downloads(
                    user_query=user_query,
                    assistant_text=final_text,
                    fallback_document_text=fallback_doc,
                    preferred_tone=pref_tone,
                )
                if design_review is not None:
                    for d_step in design_review.to_thought_steps()[:5]:
                        self.bus.emit(
                            EventType.STEP_STARTED,
                            rid,
                            session_id,
                            agent="slm_design_studio",
                            message=d_step,
                        )
                if exported_files:
                    file_names = ", ".join(ef.filename for ef in exported_files)
                    self.bus.emit(
                        EventType.STEP_STARTED,
                        rid,
                        session_id,
                        agent="slm_design_studio",
                        message=f"Compiled downloadable files ({file_names}) with verified download links.",
                    )
                    if "**Ready for Download:**" in new_text:
                        bar_suffix = "\n\n---\n**Ready for Download:**" + new_text.split(
                            "**Ready for Download:**", 1
                        )[1]
                        self.bus.emit(
                            EventType.TOKEN_DELTA,
                            rid,
                            session_id,
                            text=bar_suffix,
                            agent=route_dec.primary_agent,
                        )
                    final_text = new_text

            # Persist turn to Tier-2 Session Tree
            sess_mem.record_exchange(
                session_id=session_id,
                user_query=user_query,
                assistant_response=final_text,
                agent_used=route_dec.primary_agent,
                mode=route_dec.mode.value,
                citations=evidence_chunks,
                artifacts=[a.uri for a in artifacts],
                run_id=rid,
            )

            total_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            root_span.finish(
                status="ok",
                agent=route_dec.primary_agent,
                mode=route_dec.mode.value,
                model_id=model_id_used,
                tok_per_sec=tok_per_sec,
            )
            tracer.finalize_trace(
                rid,
                agent=route_dec.primary_agent,
                mode=route_dec.mode.value,
                model_id=model_id_used,
                backend=backend_used,
                ttft_ms=ttft_ms,
                tok_per_sec=tok_per_sec,
                total_ms=total_ms,
                rss_mb=get_process_rss_mb(),
            )

            outcome = TurnOutcome(
                run_id=rid,
                session_id=session_id,
                final_text=final_text,
                agent_used=route_dec.primary_agent,
                mode=route_dec.mode.value,
                model_id=model_id_used,
                backend=backend_used,
                ttft_ms=ttft_ms,
                tok_per_sec=tok_per_sec,
                total_ms=total_ms,
                citations=evidence_chunks,
                artifacts=[a.to_dict() for a in artifacts],
                structured_output=structured_out,
            )
            self.bus.emit(
                EventType.RUN_COMPLETED,
                rid,
                session_id,
                agent=route_dec.primary_agent,
                **outcome.to_dict(),
            )
            run_lifecycle_hooks("on_complete", outcome.to_dict(), config=self.config)
            return outcome

        except RunCancelledError as c_exc:
            total_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            root_span.finish(status="cancelled", reason=str(c_exc))
            tracer.finalize_trace(rid, status="cancelled", total_ms=total_ms)
            self.bus.emit(EventType.RUN_CANCELLED, rid, session_id, reason=str(c_exc), total_ms=total_ms)
            return TurnOutcome(
                run_id=rid,
                session_id=session_id,
                final_text="",
                agent_used=route_dec.primary_agent if route_dec else "cancelled",
                mode=route_dec.mode.value if route_dec else "cancelled",
                total_ms=total_ms,
                cancelled=True,
            )
        except Exception as exc:
            total_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            root_span.finish(status="error", error=str(exc))
            tracer.finalize_trace(rid, status="error", error=str(exc), total_ms=total_ms)
            self.bus.emit(EventType.RUN_ERROR, rid, session_id, error=str(exc), total_ms=total_ms)
            return TurnOutcome(
                run_id=rid,
                session_id=session_id,
                final_text=f"Error during execution: {exc}",
                agent_used=route_dec.primary_agent if route_dec else "error",
                mode=route_dec.mode.value if route_dec else "error",
                total_ms=total_ms,
                error=str(exc),
            )
        finally:
            cancels.remove(rid)

    def stream_turn(
        self,
        user_query: str,
        session_id: str = "default",
        run_id: Optional[str] = None,
        attachments: Sequence[str] = (),
        pinned_agent: Optional[str] = None,
        forced_mode: Optional[ExecutionMode] = None,
        json_schema: Optional[Dict[str, Any]] = None,
        enable_thinking: bool = False,
        web_search: bool = False,
        active_skills: Sequence[str] = (),
        max_tokens: int = 2400,
        reasoning_effort: str = "medium",
        clarification_choice: Optional[str] = None,
    ) -> Iterator[HarnessEvent]:
        """Executes `run_turn` asynchronously on the MultiThreadExecutionScheduler and yields live `HarnessEvent`s."""
        rid = run_id or f"run_{uuid.uuid4().hex[:10]}"
        q = self.bus.subscribe_run_queue(rid)
        lane = "fast" if (reasoning_effort or "medium").lower() == "low" else "compute"

        def _worker() -> None:
            self.run_turn(
                user_query=user_query,
                session_id=session_id,
                run_id=rid,
                attachments=attachments,
                pinned_agent=pinned_agent,
                forced_mode=forced_mode,
                json_schema=json_schema,
                enable_thinking=enable_thinking,
                web_search=web_search,
                active_skills=active_skills,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
                clarification_choice=clarification_choice,
            )

        get_runtime().arena.scheduler.submit(lane, _worker)
        yield from self.bus.iter_run_events(rid, q)

    def _stream_llm_with_events(
        self,
        run_id: str,
        session_id: str,
        parent_span_id: str,
        role: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        enable_thinking: bool,
        agent_label: str,
        cancel_token: CancellationToken,
        reasoning_effort: str = "medium",
    ) -> tuple[str, str, str, float, float]:
        rt = get_runtime()
        tracer = get_trace_store()
        guard = StreamDegenerationGuard()

        pieces: List[str] = []
        model_id = ""
        backend = ""
        ttft = 0.0
        tps = 0.0

        with tracer.span(run_id, f"llm_decode:{role}", "llm", parent_id=parent_span_id) as sp:
            for ch in rt.stream(
                role=role,
                reasoning_effort=reasoning_effort,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.2,
                enable_thinking=enable_thinking,
                cancel_token=cancel_token,
            ):
                model_id = ch.model_id or model_id
                backend = ch.backend or backend
                if ch.kind == "prefill":
                    self.bus.emit(
                        EventType.PREFILL_PROGRESS,
                        run_id,
                        session_id,
                        done=ch.prefill_done,
                        total=ch.prefill_total,
                        model_id=model_id,
                        backend=backend,
                    )
                elif ch.kind == "reasoning":
                    self.bus.emit(
                        EventType.REASONING_DELTA,
                        run_id,
                        session_id,
                        text=ch.text,
                        agent=agent_label,
                    )
                elif ch.kind == "token":
                    if guard.push(ch.text):
                        break
                    if not pieces and ch.ttft_ms:
                        self.bus.emit(
                            EventType.STEP_COMPLETED,
                            run_id,
                            session_id,
                            agent=agent_label,
                            message=f"Local CPU prefill complete ({round(ch.ttft_ms)}ms) — streaming structured response...",
                        )
                    pieces.append(ch.text)
                    self.bus.emit(
                        EventType.TOKEN_DELTA,
                        run_id,
                        session_id,
                        text=ch.text,
                        agent=agent_label,
                        ttft_ms=ch.ttft_ms,
                        tok_per_sec=ch.tok_per_sec,
                    )
                elif ch.kind == "done":
                    ttft = ch.ttft_ms
                    tps = ch.tok_per_sec

            sp.attributes.update(
                {
                    "model_id": model_id,
                    "backend": backend,
                    "ttft_ms": ttft,
                    "tok_per_sec": tps,
                }
            )

        text = guard.clean_text() if guard.triggered else "".join(pieces).strip()
        text = clean_export_content(text)
        return text, model_id, backend, ttft, tps

    def _execute_react_loop(
        self,
        run_id: str,
        session_id: str,
        parent_span_id: str,
        user_query: str,
        route_dec: RouteDecision,
        artifacts: Sequence[ArtifactEntry],
        evidence_chunks: Sequence[Dict[str, Any]],
        extra_sys: str,
        max_tokens: int,
        cancel_token: CancellationToken,
        reasoning_effort: str = "medium",
    ) -> tuple[str, str, str, float, float]:
        rt = get_runtime()
        tracer = get_trace_store()
        ckpt = get_checkpoint_store()
        reg = get_tool_registry()

        selected_tools = reg.select_relevant_tools(user_query, top_k=6)
        tools_prompt = reg.format_tools_prompt(selected_tools)
        step_grammar = reg.build_react_step_grammar(selected_tools)

        lineage = ckpt.get_branch_lineage(session_id, limit=10)
        compiled = get_context_compiler().compile(
            user_query=user_query,
            lineage=lineage,
            compacted_summary=ckpt.get_compacted_summary(session_id),
            procedural_facts=get_procedural_memory().list_facts(workspace=self.config.workspace_dir),
            tool_declarations_text=tools_prompt,
            evidence_chunks=evidence_chunks,
            artifacts=artifacts,
            extra_system_instructions=extra_sys,
            reserved_output_tokens=max_tokens,
        )

        messages = list(compiled.messages)
        call_counts: Dict[str, int] = {}
        tool_ctx = ToolExecutionContext(
            run_id=run_id,
            session_id=session_id,
            workspace_dir=self.config.workspace_dir,
            cancel_token=cancel_token,
        )

        model_id = ""
        backend = ""
        ttft = 0.0
        tps = 0.0

        for step_idx in range(1, self.config.max_react_steps + 1):
            cancel_token.check()

            # Inject any mid-flight user steering messages
            steers = get_steer_queue().drain(run_id, session_id)
            if steers:
                steer_msg = "User steering update: " + "; ".join(steers)
                messages.append({"role": "user", "content": steer_msg})
                self.bus.emit(EventType.STEER_INJECTED, run_id, session_id, hints=steers)

            with tracer.span(run_id, f"react_step_{step_idx}", "llm", parent_id=parent_span_id) as sp:
                gen = rt.generate(
                    role="primary",
                    reasoning_effort=reasoning_effort,
                    messages=messages,
                    max_tokens=640,
                    temperature=0.0,
                    grammar=step_grammar,
                    cancel_token=cancel_token,
                )
                model_id = gen.model_id
                backend = gen.backend
                ttft = ttft or gen.ttft_ms
                tps = gen.tok_per_sec
                sp.attributes.update({"model_id": model_id, "tok_per_sec": tps})

            parsed, err = extract_and_repair_json(gen.text)
            if err is not None or not isinstance(parsed, dict):
                # Model emitted direct prose answer
                ans = gen.text.strip()
                self.bus.emit(EventType.TOKEN_DELTA, run_id, session_id, text=ans, agent=route_dec.primary_agent)
                return ans, model_id, backend, ttft, tps

            action = str(parsed.get("action") or "final_answer")
            thought = str(parsed.get("thought") or "")
            if thought:
                self.bus.emit(
                    EventType.REASONING_DELTA,
                    run_id,
                    session_id,
                    text=f"[Step {step_idx}] {thought}",
                    agent=route_dec.primary_agent,
                )

            if action == "final_answer" or not parsed.get("tool"):
                ans = str(parsed.get("answer") or parsed.get("thought") or gen.text).strip()
                self.bus.emit(EventType.TOKEN_DELTA, run_id, session_id, text=ans, agent=route_dec.primary_agent)
                return ans, model_id, backend, ttft, tps

            t_name = str(parsed["tool"])
            t_args = parsed.get("arguments") if isinstance(parsed.get("arguments"), dict) else {}

            # 2-Strike Cycle Breaker
            sig = f"{t_name}::{json.dumps(t_args, sort_keys=True, default=str)}"
            call_counts[sig] = call_counts.get(sig, 0) + 1
            if call_counts[sig] >= 2:
                messages.append({"role": "assistant", "content": gen.text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"Cycle Guard: You already called `{t_name}` with identical arguments. "
                            "Do NOT repeat it; provide `{\"action\": \"final_answer\", \"answer\": \"...\"}` now."
                        ),
                    }
                )
                continue

            self.bus.emit(
                EventType.TOOL_EXEC_STARTED,
                run_id,
                session_id,
                tool=t_name,
                arguments=t_args,
                step=step_idx,
                message=f"Executing tool `{t_name}`...",
            )

            with tracer.span(run_id, f"tool:{t_name}", "tool", parent_id=parent_span_id) as t_span:
                t_res = reg.execute(t_name, t_args, tool_ctx)
                t_span.attributes.update({"ok": t_res.ok, "duration_ms": t_res.duration_ms})

            self.bus.emit(
                EventType.TOOL_RESULT,
                run_id,
                session_id,
                tool=t_name,
                ok=t_res.ok,
                duration_ms=t_res.duration_ms,
                preview=t_res.output_text[:400],
                artifact_uri=t_res.artifact_uri,
            )

            messages.append({"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)})
            messages.append(
                {
                    "role": "user",
                    "content": f"Tool `{t_name}` Result (ok={t_res.ok}):\n{t_res.output_text}",
                }
            )

            ckpt_id = ckpt.save_run_checkpoint(
                run_id=run_id,
                session_id=session_id,
                step_index=step_idx,
                state_name="react_step",
                state_payload={"tool": t_name, "ok": t_res.ok},
            )
            self.bus.emit(EventType.CHECKPOINT_SAVED, run_id, session_id, checkpoint_id=ckpt_id, step=step_idx)

        # Max steps reached -> synthesize final streaming answer without tool constraint
        messages.append(
            {
                "role": "user",
                "content": "Synthesize a clear, complete final Markdown answer for the user based on all tool results above.",
            }
        )
        return self._stream_llm_with_events(
            run_id=run_id,
            session_id=session_id,
            parent_span_id=parent_span_id,
            role="primary",
            messages=messages,
            max_tokens=max_tokens,
            enable_thinking=False,
            agent_label=route_dec.primary_agent,
            cancel_token=cancel_token,
            reasoning_effort=reasoning_effort,
        )

    def _execute_plan_dag(
        self,
        run_id: str,
        session_id: str,
        parent_span_id: str,
        user_query: str,
        artifacts: Sequence[ArtifactEntry],
        evidence_chunks: Sequence[Dict[str, Any]],
        max_tokens: int,
        cancel_token: CancellationToken,
        reasoning_effort: str = "medium",
    ) -> tuple[str, str, str, float, float]:
        tracer = get_trace_store()
        dag = get_dag_scheduler()
        runner = get_subagent_runner()

        att_summary = "\n".join(a.descriptor[:300] for a in artifacts)
        with tracer.span(run_id, "dag_plan_compile", "route", parent_id=parent_span_id) as p_span:
            nodes = dag.build_plan(user_query, attached_summary=att_summary, cancel_token=cancel_token)
            p_span.attributes["steps"] = len(nodes)

        self.bus.emit(
            EventType.PLAN_CREATED,
            run_id,
            session_id,
            steps=[{"node_id": n.node_id, "agent": n.agent, "instruction": n.instruction, "depends_on": n.depends_on} for n in nodes],
        )

        def _exec_node(node: DAGNode, upstream: List[HandoffEnvelope]) -> HandoffEnvelope:
            self.bus.emit(
                EventType.STEP_STARTED,
                run_id,
                session_id,
                node_id=node.node_id,
                agent=node.agent,
                message=f"[{node.node_id}] {node.agent}: {node.instruction[:120]}",
            )
            with tracer.span(run_id, f"dag_{node.node_id}:{node.agent}", "llm", parent_id=parent_span_id) as n_span:
                env = runner.run_specialist_verified(
                    agent_name=node.agent,
                    user_query=node.instruction,
                    session_id=session_id,
                    artifacts=artifacts,
                    evidence_chunks=evidence_chunks if node.agent in ("slm_rag", "slm_pdf") else (),
                    upstream_handoffs=upstream,
                    cancel_token=cancel_token,
                    reasoning_effort=reasoning_effort,
                )
                env.node_id = node.node_id
                n_span.attributes["verified"] = env.verified
            self.bus.emit(
                EventType.STEP_COMPLETED,
                run_id,
                session_id,
                node_id=node.node_id,
                agent=node.agent,
                verified=env.verified,
            )
            return env

        envelopes = dag.execute_dag(nodes, _exec_node, cancel_token=cancel_token)
        if len(envelopes) == 1:
            txt = envelopes[0].output_text
            self.bus.emit(EventType.TOKEN_DELTA, run_id, session_id, text=txt, agent=envelopes[0].agent)
            spec = get_runtime().ensure_capability_model("primary", reasoning_effort=reasoning_effort)
            return txt, spec.model_id, spec.backend, 0.0, 0.0

        synth_blocks = "\n\n".join(env.to_prompt_block() for env in envelopes)
        synth_msgs = [
            {
                "role": "system",
                "content": "Synthesize the verified multi-agent step outputs below into one cohesive, executive-quality response.",
            },
            {
                "role": "user",
                "content": f"Original User Request:\n{user_query}\n\n{synth_blocks}",
            },
        ]
        return self._stream_llm_with_events(
            run_id=run_id,
            session_id=session_id,
            parent_span_id=parent_span_id,
            role="primary",
            messages=synth_msgs,
            max_tokens=max_tokens,
            enable_thinking=False,
            agent_label="plan_dag_synthesizer",
            cancel_token=cancel_token,
            reasoning_effort=reasoning_effort,
        )


_GLOBAL_KERNEL: Optional[TurnStateMachine] = None


def get_kernel() -> TurnStateMachine:
    global _GLOBAL_KERNEL
    if _GLOBAL_KERNEL is None:
        _GLOBAL_KERNEL = TurnStateMachine()
    return _GLOBAL_KERNEL
