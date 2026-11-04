"""
Plane 9: Offline Web AI Studio REST/SSE API & OpenAI `/v1` Compatible Server (`api_server.py`).

Mounts onto FastAPI (`mount_harness_routes(app)`):
- `POST /api/harness/chat` (and powers `/api/chat` with 100% legacy + 10-plane typed SSE events)
- `POST /api/runs/{run_id}/cancel` (`<40ms` cooperative token stream preemption)
- `POST /api/runs/{run_id}/approve` (HITL L2-L4 capability approval resolution)
- `POST /api/runs/{run_id}/clarify` (Interactive ambiguity option chip resolution)
- `POST /api/runs/{run_id}/steer` (Mid-flight steering hint injection)
- `GET  /api/harness/status` & `POST /api/harness/profile` (Live CPU/RAM Arena HUD & Profile/Backend switcher)
- `GET  /api/harness/doctor` (Hardware SIMD, RAM & quantized model health report)
- `GET  /api/harness/traces` & `GET /api/harness/traces/{run_id}` (Waterfall Span Traces)
- `GET  /api/harness/artifacts` & `GET /api/harness/artifacts/{artifact_id}` (Artifact & Citation Drawer)
- `POST /api/harness/session/fork`, `/rewind`, `/undo`, `/compact`, `/branches`, `/export`
- `GET  /v1/models`, `POST /v1/chat/completions` (streaming & non-streaming + JSON Schema), `POST /v1/embeddings`
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading
import time
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..config import ApprovalPolicy, MODEL_CATALOG, get_config
from ..context.artifacts import get_artifact_store
from ..context.compactor import compact_session_history
from ..governance.brokers import get_approval_broker, get_clarification_broker
from ..tools.file_exporter import export_content_to_file, resolve_download_path
from ..kernel import (
    EventType,
    HarnessEvent,
    get_checkpoint_store,
    get_event_bus,
    get_kernel,
    get_shadow_vfs,
    get_steer_queue,
)
from ..memory import get_blackboard, get_procedural_memory, get_session_memory
from ..orchestrator.cascade_router import AGENT_PROTOTYPES, ExecutionMode
from ..orchestrator.skills import get_skill_registry
from ..runtime import (
    GrammarSpec,
    RunCancelledError,
    get_cancellation_registry,
    get_process_rss_mb,
    get_runtime,
)
from ..runtime.constrained import extract_and_repair_json
from ..telemetry import get_trace_store, run_doctor


# ---------------------------------------------------------------------------
# Attachment Materialization Helper
# ---------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(name or "attachment.bin"))
    return cleaned[:120] or "attachment.bin"


def materialize_web_attachments(
    session_id: str,
    attachments: Sequence[Any],
) -> List[str]:
    """
    Decodes Web Studio base64 / raw-text attachments into `<data_dir>/uploads/<session_id>/`
    and registers them in `ArtifactStore` (binding `.db`/`.sqlite` to `TurnBlackboard`).
    """
    if not attachments:
        return []

    cfg = get_config()
    sess_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", session_id or "default")[:64]
    upload_dir = os.path.join(cfg.data_dir, "uploads", sess_slug)
    os.makedirs(upload_dir, exist_ok=True)

    art_store = get_artifact_store()
    bb = get_blackboard()
    saved_paths: List[str] = []

    for idx, att in enumerate(attachments):
        if isinstance(att, dict):
            name = str(att.get("name") or f"file_{idx}")
            data = att.get("data") or att.get("content") or ""
        else:
            name = str(getattr(att, "name", "") or f"file_{idx}")
            data = getattr(att, "data", "") or getattr(att, "content", "") or ""

        if not data:
            continue

        raw_bytes: bytes
        if isinstance(data, bytes):
            raw_bytes = data
        elif isinstance(data, str):
            payload_str = data.split("base64,", 1)[1] if "base64," in data else data
            try:
                raw_bytes = base64.b64decode(payload_str, validate=False)
                if not raw_bytes and data:
                    raw_bytes = data.encode("utf-8", errors="ignore")
            except Exception:
                raw_bytes = data.encode("utf-8", errors="ignore")
        else:
            continue

        fname = _safe_filename(name)
        dest_path = os.path.join(upload_dir, fname)
        with open(dest_path, "wb") as f:
            f.write(raw_bytes)

        entry = art_store.ingest_file(dest_path)
        if entry.kind == "sqlite" or fname.lower().endswith((".db", ".sqlite", ".sqlite3")):
            bb.put(session_id, "active_sqlite_db", dest_path)
        saved_paths.append(dest_path)

    return saved_paths


# ---------------------------------------------------------------------------
# Unified Legacy + 10-Plane Harness Chat SSE Streamer
# ---------------------------------------------------------------------------

def _normalize_agent_pin(target_agent: Optional[str]) -> Optional[str]:
    if not target_agent:
        return None
    raw = target_agent.strip()
    if not raw or raw.lower() in ("auto", "orchestrator", "slm_orchestrator", "slmorchestrator"):
        return None
    if raw in AGENT_PROTOTYPES:
        return raw
    if f"slm_{raw.lower()}" in AGENT_PROTOTYPES:
        return f"slm_{raw.lower()}"

    alias_map = {
        "sql": "slm_text_to_sql",
        "texttosql": "slm_text_to_sql",
        "textsql": "slm_text_to_sql",
        "code": "slm_code_interpreter",
        "codeinterpreter": "slm_code_interpreter",
        "pdf": "slm_pdf",
        "pdfchat": "slm_pdf",
        "documentparser": "slm_document_parser",
        "docparser": "slm_document_parser",
        "vision": "slm_vision_parser",
        "visionparser": "slm_vision_parser",
        "data": "slm_data_analyst",
        "dataanalyst": "slm_data_analyst",
        "rag": "slm_rag",
        "planner": "slm_task_planner",
        "taskplanner": "slm_task_planner",
        "summarizer": "slm_summarizer",
        "web": "slm_web_agent",
        "webagent": "slm_web_agent",
        "scraper": "slm_web_scraper",
        "webscraper": "slm_web_scraper",
        "search": "slm_search_orchestrator",
        "searchorchestrator": "slm_search_orchestrator",
        "cli": "slm_cli",
        "cliagent": "slm_cli",
        "git": "slm_git",
        "gitrepomanager": "slm_git",
        "json": "slm_json_cleaner",
        "jsoncleaner": "slm_json_cleaner",
        "dbmigrator": "slm_db_migrator",
        "databasemigrator": "slm_db_migrator",
        "email": "slm_email",
        "emailassistant": "slm_email",
        "meeting": "slm_meeting",
        "meetingsummarizer": "slm_meeting",
        "memory": "slm_memory",
        "memorymanager": "slm_memory",
        "pkb": "slm_pkb",
        "pkbagent": "slm_pkb",
        "translation": "slm_translation",
        "translationhub": "slm_translation",
        "math": "slm_math",
        "mathagent": "slm_math",
        "security": "slm_security_audit",
        "securityaudit": "slm_security_audit",
        "embeddings": "slm_embeddings",
        "embeddingsserver": "slm_embeddings",
        "voice": "slm_voice",
        "voiceagent": "slm_voice",
    }
    norm = raw.lower().replace("slm", "").replace("_", "").replace("-", "")
    return alias_map.get(norm)


_HUMAN_AGENT_THOUGHTS: Dict[str, str] = {
    "slm_rag": "Analyzing your document sections and retrieving grounded facts & metrics...",
    "slm_pdf": "Parsing PDF layout, tables, and sections for grounded synthesis...",
    "slm_code_interpreter": "Preparing isolated Python execution environment and verifying logic...",
    "slm_text_to_sql": "Inspecting relational schema and compiling verified SQL query...",
    "slm_data": "Running tabular aggregations and statistical checks on your dataset...",
    "slm_math": "Solving mathematical expressions symbolically with step-by-step verification...",
    "slm_web_agent": "Searching live web sources and extracting benchmark insights...",
    "slm_web_scraper": "Extracting structured data from web pages...",
    "slm_search_orchestrator": "Synthesizing live web research and industry benchmarks...",
    "slm_summarizer": "Distilling key executive takeaways and action items...",
    "slm_task_planner": "Decomposing goal into a structured milestone roadmap...",
    "slm_vision_parser": "Analyzing visual layout, charts, and structural elements...",
    "direct_assistant": "Synthesizing a clear, structured response tailored to your request...",
}


def _human_routing_thought(primary_agent: str, user_message: str) -> str:
    q_low = (user_message or "").lower()
    if any(w in q_low for w in ("youtube.com", "youtu.be", "vimeo.com", "loom.com", "ted.com")):
        return "Inspecting online video metadata, creator chapters, and timestamped transcript..."
    if any(w in q_low for w in (".mp4", ".mov", ".webm", ".mkv", ".avi", "analyze video", "analyse video")):
        return "Inspecting video stream keyframes, running RapidOCR on visual frames, and transcribing audio..."
    if any(w in q_low for w in (".wav", ".mp3", ".m4a", ".ogg", ".flac", "analyze audio", "analyse audio", "transcribe")):
        return "Analyzing audio signal telemetry and running Whisper ONNX speech-to-text transcription..."
    if any(w in q_low for w in ("read this document", "read document", "analyze document", "give me all details", "don't have to go through")):
        return "Performing zero-omission deep document extraction across all pages, metrics, and sections..."
    if any(w in q_low for w in ("ppt", "pptx", "powerpoint", "presentation", "slide deck")):
        return "Designing a widescreen 16:9 presentation deck with executive layout, visual hierarchy, and KPI cards..."
    if any(w in q_low for w in ("resume", "cv")) and any(w in q_low for w in ("improve", "review", "rewrite", "critique", "upgrade")):
        return "Reviewing your resume against 2026 executive hiring benchmarks to strengthen impact metrics and positioning..."
    if any(w in q_low for w in ("download", "down load", "export", "convert")):
        return "Structuring content and preparing styled downloadable files (.pptx, .pdf, .docx, .html)..."
    return _HUMAN_AGENT_THOUGHTS.get(
        primary_agent,
        f"Routing request to {primary_agent.replace('slm_', 'SLM ').replace('_', ' ').title()} for grounded synthesis...",
    )


def build_harness_chat_streaming_response(
    *,
    session_id: str,
    req_id: str,
    message: str,
    target_agent: str = "auto",
    attachments: Sequence[Any] = (),
    forced_mode_str: Optional[str] = None,
    json_schema: Optional[Dict[str, Any]] = None,
    web_search: bool = False,
    design_tone: str = "",
    active_skills: Sequence[str] = (),
    max_tokens: int = 2400,
    reasoning_effort: str = "medium",
    clarification_choice: Optional[str] = None,
) -> StreamingResponse:
    """
    Runs `TurnStateMachine` on a MultiThreadExecutionScheduler worker lane and streams
    dual-compatible SSE frames (`thought`, `token`, `done`, `error` + rich 10-plane events
    `run_started`, `plan_created`, `clarification_needed`, `citations`, `cancelled`).
    """
    # Ensure web server mode uses async ticket resolution rather than blocking terminal stdin
    get_approval_broker().set_interactive_handler(None)
    get_clarification_broker().set_interactive_handler(None)

    run_id = req_id.strip() if req_id and req_id.strip() else f"run_{uuid.uuid4().hex[:10]}"
    sid = session_id.strip() if session_id and session_id.strip() else "default_session"
    pinned = _normalize_agent_pin(target_agent)
    eff_norm = (reasoning_effort or "medium").strip().lower()
    if eff_norm not in ("low", "medium", "high"):
        eff_norm = "medium"

    # Automatically preempt any lingering in-flight run before starting a new turn so it releases the CPU lock immediately
    try:
        for old_rid in get_cancellation_registry().active_runs():
            if old_rid != run_id:
                get_cancellation_registry().cancel(old_rid, reason="Preempted by new user request")
                get_approval_broker().resolve(old_rid, approved=False)
                get_clarification_broker().resolve(old_rid, answer="")
    except Exception:
        pass
    if design_tone:
        get_blackboard().put(sid, "preferred_design_tone", design_tone.strip().lower())

    forced_mode: Optional[ExecutionMode] = None
    if forced_mode_str:
        try:
            forced_mode = ExecutionMode(forced_mode_str)
        except ValueError:
            if forced_mode_str.lower() in ("plan", "dag", "plan_dag"):
                forced_mode = ExecutionMode.PLAN_DAG

    bus = get_event_bus()
    ev_queue = bus.subscribe_run_queue(run_id)
    worker_done = threading.Event()
    outcome_box: Dict[str, Any] = {}

    def _worker() -> None:
        try:
            file_paths = materialize_web_attachments(sid, attachments)
            query = (message or "").strip()
            if not query and file_paths:
                query = f"Analyze the attached file ({os.path.basename(file_paths[0])}) and summarize key insights."
            outcome = get_kernel().run_turn(
                user_query=query,
                session_id=sid,
                run_id=run_id,
                attachments=file_paths,
                pinned_agent=pinned,
                forced_mode=forced_mode,
                json_schema=json_schema,
                web_search=web_search,
                active_skills=active_skills,
                max_tokens=max_tokens,
                reasoning_effort=eff_norm,
                clarification_choice=clarification_choice,
            )
            outcome_box["outcome"] = outcome
        except Exception as exc:
            outcome_box["fatal_error"] = str(exc)
        finally:
            worker_done.set()

    lane = "fast" if eff_norm == "low" else "compute"
    try:
        get_runtime().arena.scheduler.submit(lane, _worker)
    except Exception:
        threading.Thread(target=_worker, daemon=True, name=f"slm-turn-{run_id}").start()

    async def _sse_gen() -> AsyncIterator[str]:
        all_thoughts: List[str] = []
        streamed_text_parts: List[str] = []
        routed_agent = pinned or "SLMOrchestrator"
        cfg = get_config()

        def _sse(obj: Dict[str, Any]) -> str:
            return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

        # Immediately announce `run_id` so UI Stop / Steer controls bind at t=0ms
        yield _sse(
            {
                "type": "run_started",
                "event_type": "run_started",
                "run_id": run_id,
                "req_id": run_id,
                "session_id": sid,
                "profile": cfg.profile.value,
                "reasoning_effort": eff_norm,
                "primary_model": cfg.resolve_model_for_reasoning("primary", eff_norm).model_id,
                "rss_mb": get_process_rss_mb(),
            }
        )

        try:
            while not worker_done.is_set() or not ev_queue.empty():
                drained = False
                while True:
                    try:
                        ev: HarnessEvent = ev_queue.get_nowait()
                    except Exception:
                        break
                    drained = True
                    et = ev.event_type
                    p = ev.payload

                    if et == EventType.ROUTE_DECIDED:
                        routed_agent = str(p.get("primary_agent") or routed_agent)
                        msg = _human_routing_thought(routed_agent, message)
                        all_thoughts.append(msg)
                        yield _sse(
                            {
                                "type": "thought",
                                "event_type": et.value,
                                "thought": msg,
                                "thoughts": all_thoughts,
                                "routed_agent": routed_agent,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et == EventType.PLAN_CREATED:
                        steps = p.get("steps") or []
                        if steps and isinstance(steps[0], dict):
                            msg = f"Compiled {len(steps)}-step topological DAG: " + " -> ".join(
                                f"{s.get('node_id')}:{s.get('agent')}" for s in steps
                            )
                        else:
                            msg = f"Formulated {len(steps)}-step Execution Plan ({p.get('reasoning_effort', eff_norm).upper()} reasoning)"
                        all_thoughts.append(msg)
                        yield _sse(
                            {
                                "type": "thought",
                                "event_type": et.value,
                                "thought": msg,
                                "thoughts": all_thoughts,
                                "plan_steps": steps,
                                "reasoning_effort": p.get("reasoning_effort", eff_norm),
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et in (EventType.STEP_STARTED, EventType.STEP_COMPLETED):
                        msg = str(p.get("message") or f"Executing {p.get('agent', 'step')} on CPU...")
                        if msg and msg not in all_thoughts:
                            all_thoughts.append(msg)
                        yield _sse(
                            {
                                "type": "thought",
                                "event_type": et.value,
                                "thought": msg,
                                "thoughts": all_thoughts,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et == EventType.REASONING_DELTA:
                        r_txt = str(p.get("text") or "").strip()
                        if r_txt:
                            yield _sse(
                                {
                                    "type": "reasoning",
                                    "event_type": et.value,
                                    "text": r_txt,
                                    "session_id": sid,
                                    "req_id": run_id,
                                    "run_id": run_id,
                                }
                            )

                    elif et == EventType.PREFILL_PROGRESS:
                        yield _sse(
                            {
                                "type": "prefill_progress",
                                "event_type": et.value,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et == EventType.TOOL_EXEC_STARTED:
                        t_args = json.dumps(p.get("arguments") or {}, ensure_ascii=False)[:110]
                        msg = f"Tool call: {p.get('tool')}({t_args})"
                        all_thoughts.append(msg)
                        yield _sse(
                            {
                                "type": "thought",
                                "event_type": et.value,
                                "thought": msg,
                                "thoughts": all_thoughts,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et == EventType.TOOL_RESULT:
                        status_lbl = "ok" if p.get("ok") else "failed"
                        msg = f"Tool result: {p.get('tool')} [{status_lbl}] ({p.get('duration_ms')}ms)"
                        all_thoughts.append(msg)
                        yield _sse(
                            {
                                "type": "thought",
                                "event_type": et.value,
                                "thought": msg,
                                "thoughts": all_thoughts,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et in (EventType.VERIFIER_PASSED, EventType.VERIFIER_FAILED, EventType.VERIFIER_RETRY):
                        if et == EventType.VERIFIER_PASSED:
                            msg = f"Deterministic verifier passed ({p.get('agent', 'check')})"
                        elif et == EventType.VERIFIER_RETRY:
                            msg = f"Architectural Self-Review Critic triggered refinement: {str(p.get('feedback', ''))[:120]}"
                        else:
                            msg = f"Verifier caught issue — auto-repairing: {str(p.get('feedback', ''))[:120]}"
                        all_thoughts.append(msg)
                        yield _sse(
                            {
                                "type": "thought",
                                "event_type": et.value,
                                "thought": msg,
                                "thoughts": all_thoughts,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et == EventType.APPROVAL_REQUIRED:
                        yield _sse(
                            {
                                "type": "approval_required",
                                "event_type": et.value,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et == EventType.APPROVAL_RESOLVED:
                        yield _sse(
                            {
                                "type": "approval_resolved",
                                "event_type": et.value,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et == EventType.CLARIFICATION_NEEDED:
                        yield _sse(
                            {
                                "type": "clarification_needed",
                                "event_type": et.value,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et == EventType.CLARIFICATION_RESOLVED:
                        yield _sse(
                            {
                                "type": "clarification_resolved",
                                "event_type": et.value,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et in (EventType.CITATIONS_EMITTED, EventType.ARTIFACT_CREATED):
                        yield _sse(
                            {
                                "type": et.value,
                                "event_type": et.value,
                                "payload": p,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )

                    elif et == EventType.TOKEN_DELTA:
                        tok = str(p.get("text") or "")
                        if tok:
                            streamed_text_parts.append(tok)
                            yield _sse(
                                {
                                    "type": "token",
                                    "event_type": et.value,
                                    "token": tok,
                                    "ttft_ms": p.get("ttft_ms"),
                                    "tok_per_sec": p.get("tok_per_sec"),
                                    "model_id": p.get("model_id"),
                                    "session_id": sid,
                                    "req_id": run_id,
                                    "run_id": run_id,
                                }
                            )

                    elif et == EventType.RUN_CANCELLED:
                        partial = "".join(streamed_text_parts).strip()
                        if partial:
                            partial += "\n\n*(Generation stopped by user)*"
                        else:
                            partial = "*(Generation stopped by user)*"
                        all_thoughts.append(f"Generation cancelled in {p.get('total_ms', 0)}ms")
                        yield _sse(
                            {
                                "type": "cancelled",
                                "event_type": et.value,
                                "reason": p.get("reason", "Cancelled by user"),
                                "response": partial,
                                "routed_agent": routed_agent,
                                "thoughts": all_thoughts,
                                "total_ms": p.get("total_ms", 0),
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )
                        return

                    elif et == EventType.RUN_ERROR:
                        yield _sse(
                            {
                                "type": "error",
                                "event_type": et.value,
                                "error": p.get("error", "Execution error"),
                                "thoughts": all_thoughts,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )
                        return

                    elif et == EventType.RUN_COMPLETED:
                        trace_rec = get_trace_store().get_trace(run_id)
                        final_txt = str(p.get("final_text") or "".join(streamed_text_parts))
                        routed_agent = str(p.get("agent_used") or routed_agent)
                        yield _sse(
                            {
                                "type": "done",
                                "event_type": et.value,
                                "response": final_txt,
                                "routed_agent": routed_agent,
                                "thoughts": all_thoughts,
                                "citations": p.get("citations") or [],
                                "artifacts": p.get("artifacts") or [],
                                "structured_output": p.get("structured_output"),
                                "mode": p.get("mode"),
                                "model_id": p.get("model_id"),
                                "ttft_ms": p.get("ttft_ms", 0.0),
                                "tok_per_sec": p.get("tok_per_sec", 0.0),
                                "total_ms": p.get("total_ms", 0.0),
                                "rss_mb": get_process_rss_mb(),
                                "trace": trace_rec.to_dict() if trace_rec else None,
                                "session_id": sid,
                                "req_id": run_id,
                                "run_id": run_id,
                            }
                        )
                        return

                await asyncio.sleep(0.005 if drained else 0.01)

            if "fatal_error" in outcome_box:
                yield _sse(
                    {
                        "type": "error",
                        "event_type": "run_error",
                        "error": outcome_box["fatal_error"],
                        "thoughts": all_thoughts,
                        "session_id": sid,
                        "req_id": run_id,
                        "run_id": run_id,
                    }
                )
        finally:
            if not worker_done.is_set():
                get_cancellation_registry().cancel(run_id, reason="Client disconnected from SSE stream")
                get_approval_broker().resolve(run_id, approved=False)
                get_clarification_broker().resolve(run_id, answer="")
            bus.unsubscribe_run_queue(run_id, ev_queue)

    return StreamingResponse(
        _sse_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Request Schemas
# ---------------------------------------------------------------------------

class HarnessChatRequest(BaseModel):
    session_id: str = "default_session"
    req_id: str = ""
    message: str = ""
    target_agent: str = "auto"
    attachments: List[Dict[str, Any]] = Field(default_factory=list)
    history: List[Dict[str, Any]] = Field(default_factory=list)
    system_prompt: str = ""
    web_search: bool = False
    design_tone: str = ""
    active_skills: List[str] = Field(default_factory=list)
    forced_mode: Optional[str] = None
    json_schema: Optional[Dict[str, Any]] = None
    max_tokens: int = 2400
    reasoning_effort: str = "medium"
    clarification_choice: Optional[str] = None


class CancelRunRequest(BaseModel):
    reason: str = "Cancelled by user from Web AI Studio"


class ApproveRunRequest(BaseModel):
    approved: bool
    remember_for_session: bool = False
    ticket_id: Optional[str] = None


class ClarifyRunRequest(BaseModel):
    answer: str
    ticket_id: Optional[str] = None


class SteerRunRequest(BaseModel):
    hint: str


class SwitchProfileRequest(BaseModel):
    profile: Optional[str] = None
    preferred_backend: Optional[str] = None
    approval_policy: Optional[str] = None


class SessionBranchRequest(BaseModel):
    session_id: str
    branch_name: str = ""


class SessionRewindRequest(BaseModel):
    session_id: str
    turns: int = 1


class SessionActionRequest(BaseModel):
    session_id: str


class MemoryFactRequest(BaseModel):
    fact: str


class ExportDocumentRequest(BaseModel):
    content: str = ""
    format: str = "pdf"
    filename: Optional[str] = None
    title: Optional[str] = None
    tone: str = ""
    session_id: Optional[str] = None


class SkillSaveRequest(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    instructions: str = ""
    preferred_role: str = "primary"
    preferred_agent: str = ""
    preferred_tone: str = ""
    output_formats: List[str] = Field(default_factory=list)
    triggers: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    enabled: bool = True


class SkillGenerateRequest(BaseModel):
    prompt: str = ""
    session_id: str = ""
    context_text: str = ""
    preferred_tone: str = ""


class SkillToggleRequest(BaseModel):
    enabled: Optional[bool] = None


class OpenAIChatCompletionRequest(BaseModel):
    model: str = "slm-auto"
    messages: List[Dict[str, Any]]
    temperature: float = 0.2
    top_p: float = 0.9
    max_tokens: int = 2400
    stream: bool = False
    stop: Optional[Union[str, List[str]]] = None
    response_format: Optional[Dict[str, Any]] = None
    user: Optional[str] = None


class OpenAIEmbeddingsRequest(BaseModel):
    input: Union[str, List[str]]
    model: str = "mxbai-embed-large-onnx"


# ---------------------------------------------------------------------------
# Route Mounting Function
# ---------------------------------------------------------------------------

def mount_harness_routes(app: FastAPI) -> None:
    """Attaches all 10-Plane Harness REST/SSE and OpenAI `/v1` routes to `app`."""

    @app.post("/api/harness/chat")
    async def harness_chat(req: HarnessChatRequest) -> StreamingResponse:
        return build_harness_chat_streaming_response(
            session_id=req.session_id,
            req_id=req.req_id,
            message=req.message,
            target_agent=req.target_agent,
            attachments=req.attachments,
            forced_mode_str=req.forced_mode,
            json_schema=req.json_schema,
            web_search=req.web_search,
            design_tone=req.design_tone,
            active_skills=req.active_skills,
            max_tokens=req.max_tokens,
            reasoning_effort=req.reasoning_effort,
            clarification_choice=req.clarification_choice,
        )

    @app.post("/api/harness/models/ensure")
    async def ensure_model_endpoint(req: Request) -> Dict[str, Any]:
        body = await req.json()
        role = str(body.get("role") or "primary")
        reasoning_effort = str(body.get("reasoning_effort") or "medium")
        model_id = body.get("model_id")
        return get_runtime().ensure_capability_model(
            role=role,
            reasoning_effort=reasoning_effort,
            model_id=model_id,
        )

    # --- Declarative Skill Repository (`.slm/skills/<name>/SKILL.md`) Endpoints ---

    @app.get("/api/harness/skills")
    async def list_skills_endpoint() -> Dict[str, Any]:
        reg = get_skill_registry()
        reg.reload()
        return {
            "ok": True,
            "repo_dir": reg.repo_dir(),
            "skills": [s.to_dict() for s in reg.list_skills()],
        }

    @app.post("/api/harness/skills")
    async def save_skill_endpoint(req: SkillSaveRequest) -> Dict[str, Any]:
        reg = get_skill_registry()
        sdef = reg.save_skill(
            name=req.name,
            title=req.title,
            description=req.description,
            instructions=req.instructions,
            preferred_role=req.preferred_role,
            preferred_agent=req.preferred_agent,
            preferred_tone=req.preferred_tone,
            output_formats=req.output_formats,
            triggers=req.triggers,
            tags=req.tags,
            enabled=req.enabled,
            source="user",
        )
        return {
            "ok": True,
            "skill": sdef.to_dict(),
            "skills": [s.to_dict() for s in reg.list_skills()],
        }

    @app.post("/api/harness/skills/generate")
    async def generate_skill_endpoint(req: SkillGenerateRequest) -> Dict[str, Any]:
        reg = get_skill_registry()
        ctx = (req.context_text or "").strip()
        fallback_skill = ""
        if req.session_id:
            bb = get_blackboard()
            if not ctx:
                ctx = str(bb.get(req.session_id, "last_substantial_content") or "").strip()
            fallback_skill = str(bb.get(req.session_id, "last_active_skill_name") or "").strip()
        if reg.is_skill_improvement_intent(req.prompt):
            sdef, summary_card = reg.improve_and_save_skill(
                user_request=req.prompt,
                fallback_skill_name=fallback_skill,
                session_context=ctx,
                preferred_tone=req.preferred_tone,
                use_llm=True,
            )
        else:
            sdef, summary_card = reg.synthesize_and_save_skill(
                user_request=req.prompt,
                session_context=ctx,
                preferred_tone=req.preferred_tone,
                use_llm=True,
            )
        return {
            "ok": True,
            "skill": sdef.to_dict(),
            "summary_markdown": summary_card,
            "skills": [s.to_dict() for s in reg.list_skills()],
        }

    @app.post("/api/harness/skills/{skill_name}/toggle")
    async def toggle_skill_endpoint(skill_name: str, req: Optional[SkillToggleRequest] = None) -> Dict[str, Any]:
        reg = get_skill_registry()
        sk = reg.toggle_skill(skill_name, enabled=req.enabled if req else None)
        if sk is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found in Skill Repository.")
        return {
            "ok": True,
            "skill": sk.to_dict(),
            "skills": [s.to_dict() for s in reg.list_skills()],
        }

    @app.delete("/api/harness/skills/{skill_name}")
    async def delete_skill_endpoint(skill_name: str) -> Dict[str, Any]:
        reg = get_skill_registry()
        deleted = reg.delete_skill(skill_name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found.")
        return {
            "ok": True,
            "deleted": skill_name,
            "skills": [s.to_dict() for s in reg.list_skills()],
        }

    # --- Universal Multi-Format Document Compiler & File Download Endpoints ---

    @app.post("/api/harness/export")
    async def export_document_endpoint(req: ExportDocumentRequest) -> Dict[str, Any]:
        body = (req.content or "").strip()
        fallback_doc = ""
        if req.session_id:
            bb = get_blackboard()
            fallback_doc = str(bb.get(req.session_id, "last_attached_doc_text") or "").strip()
            if not fallback_doc:
                prev_entries = bb.get(req.session_id, "last_attached_entries") or []
                for ent in prev_entries:
                    tpath = getattr(ent, "stored_text_path", None)
                    if tpath and os.path.isfile(tpath):
                        try:
                            with open(tpath, "r", encoding="utf-8", errors="ignore") as f:
                                att_txt = f.read(12000).strip()
                            if att_txt:
                                fallback_doc = att_txt
                                break
                        except Exception:
                            pass
        if not body and fallback_doc:
            body = fallback_doc
        if not body:
            raise HTTPException(status_code=400, detail="No document content provided to export.")
        ef = export_content_to_file(
            content=body,
            fmt=req.format,
            filename=req.filename,
            title=req.title,
            tone=req.tone,
            fallback_document_text=fallback_doc,
        )
        return {"ok": True, **ef.to_dict()}

    @app.get("/api/harness/download/{file_id}/{filename}")
    async def download_exported_file(file_id: str, filename: str) -> FileResponse:
        resolved = resolve_download_path(file_id, filename)
        if resolved is None:
            raise HTTPException(status_code=404, detail=f"Download file '{filename}' not found or expired.")
        abs_path, safe_name, mime = resolved
        return FileResponse(
            path=abs_path,
            filename=safe_name,
            media_type=mime,
            headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
        )

    # --- Mid-Flight Run Control (<40ms Cancel, HITL Approval, Clarification, Steer) ---

    @app.post("/api/runs/force-quit")
    async def force_quit_all_runs(req: Optional[CancelRunRequest] = None) -> Dict[str, Any]:
        reason = req.reason if req else "Force quit by user"
        reg = get_cancellation_registry()
        active_ids = reg.active_runs()
        count = reg.cancel_all(reason=reason)
        bus = get_event_bus()
        for rid in active_ids:
            get_approval_broker().resolve(rid, approved=False)
            get_clarification_broker().resolve(rid, answer="")
            bus.emit(EventType.RUN_CANCELLED, rid, "default_session", reason=reason, total_ms=0)
        return {"ok": True, "cancelled_count": count, "active_runs_cancelled": active_ids, "reason": reason}

    @app.post("/api/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, req: Optional[CancelRunRequest] = None) -> Dict[str, Any]:
        reason = req.reason if req else "Cancelled by user"
        found = get_cancellation_registry().cancel(run_id, reason=reason)
        # Wake any waiting approval or clarification ticket and unblock SSE stream immediately
        get_approval_broker().resolve(run_id, approved=False)
        get_clarification_broker().resolve(run_id, answer="")
        get_event_bus().emit(EventType.RUN_CANCELLED, run_id, "default_session", reason=reason, total_ms=0)
        return {"ok": True, "run_id": run_id, "cancelled": found, "reason": reason}

    @app.post("/api/runs/{run_id}/approve")
    async def approve_run(run_id: str, req: ApproveRunRequest) -> Dict[str, Any]:
        target_key = req.ticket_id or run_id
        resolved = get_approval_broker().resolve(
            target_key,
            approved=req.approved,
            remember_for_session=req.remember_for_session,
        )
        return {"ok": resolved, "run_id": run_id, "approved": req.approved}

    @app.post("/api/runs/{run_id}/clarify")
    async def clarify_run(run_id: str, req: ClarifyRunRequest) -> Dict[str, Any]:
        target_key = req.ticket_id or run_id
        resolved = get_clarification_broker().resolve(target_key, answer=req.answer)
        return {"ok": resolved, "run_id": run_id, "answer": req.answer}

    @app.post("/api/runs/{run_id}/steer")
    async def steer_run(run_id: str, req: SteerRunRequest) -> Dict[str, Any]:
        get_steer_queue().push(run_id, req.hint)
        return {"ok": True, "run_id": run_id, "queued_hint": req.hint}

    # --- CPU Status HUD, Profile/Backend Switcher, Doctor, Traces & Artifacts ---

    @app.get("/api/harness/status")
    async def harness_status() -> Dict[str, Any]:
        rt = get_runtime()
        cfg = get_config()
        topo = rt.topology
        arena = rt.arena.status()
        primary_spec = cfg.resolve_model_spec("primary")
        return {
            "healthy": True,
            "pure_cpu": cfg.pure_cpu,
            "offline_only": cfg.offline_only,
            "profile": cfg.profile.value,
            "preferred_backend": cfg.preferred_backend,
            "approval_policy": cfg.approval_policy.value,
            "primary_model": {
                "model_id": primary_spec.model_id,
                "backend": primary_spec.backend,
                "quantization": primary_spec.quantization,
                "est_ram_mb": primary_spec.est_ram_mb,
            },
            "cpu": topo.to_dict(),
            "arena": arena,
            "active_runs": get_cancellation_registry().active_runs(),
            "pending_approvals": get_approval_broker().list_pending(),
            "pending_clarifications": get_clarification_broker().list_pending(),
        }

    @app.post("/api/harness/profile")
    async def switch_harness_profile(req: SwitchProfileRequest) -> Dict[str, Any]:
        rt = get_runtime()
        cfg = get_config()
        res: Dict[str, Any] = {}
        if req.profile or req.preferred_backend:
            res = rt.switch_profile(
                req.profile or cfg.profile.value,
                preferred_backend=req.preferred_backend,
            )
        if req.approval_policy:
            try:
                cfg.approval_policy = ApprovalPolicy(req.approval_policy.lower())
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        status_now = await harness_status()
        return {"ok": True, "switch": res, "status": status_now}

    @app.get("/api/harness/doctor")
    async def harness_doctor() -> Dict[str, Any]:
        return run_doctor()

    @app.get("/api/harness/traces")
    async def list_traces(limit: int = 20) -> Dict[str, Any]:
        return {"traces": get_trace_store().list_recent(limit=limit)}

    @app.get("/api/harness/traces/{run_id}")
    async def get_trace_detail(run_id: str) -> Dict[str, Any]:
        tr = get_trace_store().get_trace(run_id)
        if tr is None:
            raise HTTPException(status_code=404, detail=f"Trace not found for run_id={run_id}")
        data = tr.to_dict()
        data["ascii_waterfall"] = tr.format_ascii_waterfall()
        return data

    @app.get("/api/harness/artifacts")
    async def list_artifacts() -> Dict[str, Any]:
        entries = get_artifact_store().list_artifacts()
        return {
            "artifacts": [
                {
                    "artifact_id": e.artifact_id,
                    "uri": e.uri,
                    "name": e.name,
                    "kind": e.kind,
                    "byte_size": e.byte_size,
                    "descriptor": e.descriptor,
                    "metadata": e.metadata,
                }
                for e in entries
            ]
        }

    @app.get("/api/harness/artifacts/{artifact_id}")
    async def get_artifact_preview(artifact_id: str, start_line: int = 1, max_lines: int = 80) -> Dict[str, Any]:
        store = get_artifact_store()
        entry = store.get(artifact_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")
        preview = store.read_slice(artifact_id, start_line=start_line, max_lines=max_lines)
        return {
            "artifact_id": entry.artifact_id,
            "uri": entry.uri,
            "name": entry.name,
            "kind": entry.kind,
            "byte_size": entry.byte_size,
            "descriptor": entry.descriptor,
            "metadata": entry.metadata,
            "preview": preview,
        }

    # --- Conversation Tree Branching (/fork), /rewind, /undo, /compact, /memory, /export ---

    @app.post("/api/harness/session/fork")
    async def fork_session_branch(req: SessionBranchRequest) -> Dict[str, Any]:
        bname = req.branch_name.strip() or f"fork-{uuid.uuid4().hex[:4]}"
        return get_checkpoint_store().fork_branch(req.session_id, bname)

    @app.get("/api/harness/session/{session_id}/branches")
    async def list_session_branches(session_id: str) -> Dict[str, Any]:
        return {"session_id": session_id, "branches": get_checkpoint_store().list_branches(session_id)}

    @app.post("/api/harness/session/rewind")
    async def rewind_session(req: SessionRewindRequest) -> Dict[str, Any]:
        return get_checkpoint_store().rewind_turns(
            req.session_id,
            turns_to_pop=max(1, req.turns),
            vfs=get_shadow_vfs(),
        )

    @app.post("/api/harness/session/undo")
    async def undo_session_mutations(req: SessionActionRequest) -> Dict[str, Any]:
        restored = get_shadow_vfs().undo_run(req.session_id)
        return {"ok": True, "session_id": req.session_id, "restored_files": restored}

    @app.post("/api/harness/session/compact")
    async def compact_session(req: SessionActionRequest) -> Dict[str, Any]:
        return compact_session_history(req.session_id)

    @app.get("/api/harness/session/{session_id}/export")
    async def export_session_endpoint(session_id: str, fmt: str = "markdown") -> Any:
        content = get_session_memory().export_session(session_id, fmt=fmt)
        if fmt.lower() == "json":
            return JSONResponse(content=json.loads(content))
        return PlainTextResponse(content=content, media_type="text/markdown; charset=utf-8")

    @app.get("/api/harness/memory")
    async def list_memory_facts() -> Dict[str, Any]:
        cfg = get_config()
        return {"facts": get_procedural_memory().list_facts(workspace=cfg.workspace_dir)}

    @app.post("/api/harness/memory")
    async def add_memory_fact(req: MemoryFactRequest) -> Dict[str, Any]:
        cfg = get_config()
        get_procedural_memory().remember_fact(req.fact, workspace=cfg.workspace_dir)
        return {"ok": True, "facts": get_procedural_memory().list_facts(workspace=cfg.workspace_dir)}

    # -----------------------------------------------------------------------
    # Opt-In Personalization & Daily Concierge Studio Endpoints
    # -----------------------------------------------------------------------

    @app.get("/api/harness/personalize")
    async def get_personalization_status() -> Dict[str, Any]:
        from ..personalization import get_concierge_engine

        return get_concierge_engine().get_status_bundle()

    @app.post("/api/harness/personalize")
    async def save_personalization_settings(request: Request) -> Dict[str, Any]:
        from ..personalization import get_concierge_engine

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        prof_updates = body.get("profile") if isinstance(body.get("profile"), dict) else body
        cred_updates = body.get("credentials") if isinstance(body.get("credentials"), dict) else None
        return get_concierge_engine().save_profile(prof_updates, cred_updates)

    @app.get("/api/harness/personalize/dashboard")
    async def get_personal_dashboard(refresh: bool = False) -> Dict[str, Any]:
        from ..personalization import get_concierge_engine

        eng = get_concierge_engine()
        if not refresh:
            cached = eng.get_cached_dashboard()
            if cached:
                return {"ok": True, "dashboard": cached}
        loop = asyncio.get_running_loop()
        dash = await loop.run_in_executor(None, eng.build_daily_dashboard)
        return {"ok": True, "dashboard": dash}

    @app.post("/api/harness/personalize/dashboard/run")
    async def run_personal_dashboard_now() -> Dict[str, Any]:
        from ..personalization import get_concierge_engine

        eng = get_concierge_engine()
        loop = asyncio.get_running_loop()
        dash = await loop.run_in_executor(None, eng.build_daily_dashboard)
        return {"ok": True, "dashboard": dash}

    @app.post("/api/harness/personalize/jobs/apply")
    async def apply_personal_job(request: Request) -> Dict[str, Any]:
        from ..personalization import get_concierge_engine

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        job_id = str(body.get("job_id") or "").strip()
        if not job_id:
            raise HTTPException(status_code=400, detail="Missing job_id")
        return get_concierge_engine().apply_to_job(
            job_id=job_id,
            job_title=str(body.get("title") or ""),
            portal=str(body.get("portal") or "Naukri.com"),
            job_url=str(body.get("url") or ""),
        )

    @app.post("/api/harness/personalize/emails/update")
    async def update_personal_email_reminder(request: Request) -> Dict[str, Any]:
        from ..personalization import get_concierge_engine

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        return get_concierge_engine().add_or_update_email_reminder(body)

    @app.get("/api/harness/personalize/chrome-profiles")
    async def get_chrome_profiles_discovery() -> Dict[str, Any]:
        from ..personalization import get_concierge_engine

        return get_concierge_engine().discover_chrome_profiles()

    @app.post("/api/harness/personalize/chrome-profiles/sync")
    async def sync_chrome_profiles_endpoint() -> Dict[str, Any]:
        from ..personalization import get_concierge_engine

        return get_concierge_engine().sync_chrome_profiles_to_personalization()

    @app.post("/api/harness/personalize/chrome-profiles/launch")
    async def launch_chrome_profile_endpoint(request: Request) -> Dict[str, Any]:
        from ..personalization import get_concierge_engine

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        return get_concierge_engine().launch_chrome_profile_url(
            profile_dir=str(body.get("profile_dir") or "Default"),
            url=str(body.get("url") or "https://mail.google.com"),
        )

    # -----------------------------------------------------------------------
    # OpenAI `/v1` Compatible Local CPU Endpoints
    # -----------------------------------------------------------------------

    @app.get("/v1/models")
    async def openai_list_models() -> Dict[str, Any]:
        cfg = get_config()
        now_ts = int(time.time())
        models_out: List[Dict[str, Any]] = [
            {
                "id": "slm-auto",
                "object": "model",
                "created": now_ts,
                "owned_by": "slmagents-cpu-harness",
                "metadata": {"role": "cascade_router_26_specialists", "profile": cfg.profile.value},
            }
        ]
        for spec in MODEL_CATALOG.values():
            if os.path.exists(spec.abs_path(cfg.models_dir)):
                models_out.append(
                    {
                        "id": spec.model_id,
                        "object": "model",
                        "created": now_ts,
                        "owned_by": "slmagents-cpu-harness",
                        "metadata": {
                            "role": spec.role,
                            "backend": spec.backend,
                            "quantization": spec.quantization,
                            "est_ram_mb": spec.est_ram_mb,
                        },
                    }
                )
        for agent_id in sorted(AGENT_PROTOTYPES.keys()):
            models_out.append(
                {
                    "id": agent_id,
                    "object": "model",
                    "created": now_ts,
                    "owned_by": "slmagents-cpu-specialist",
                }
            )
        return {"object": "list", "data": models_out}

    @app.post("/v1/embeddings")
    async def openai_embeddings(req: OpenAIEmbeddingsRequest) -> Dict[str, Any]:
        rt = get_runtime()
        texts = [req.input] if isinstance(req.input, str) else list(req.input)
        if req.model in ("mxbai-embed-large-onnx", "minilm-embed-onnx") and req.model in MODEL_CATALOG:
            spec = MODEL_CATALOG[req.model]
            emb_engine = rt.arena.acquire(spec, rt._load_spec_instance)
            vecs = emb_engine.embed_batch(texts)
            used_model = spec.model_id
        else:
            vecs = rt.embed_batch(texts)
            used_model = get_config().resolve_model_spec("embeddings").model_id
        prompt_toks = sum(max(1, len(t) // 4) for t in texts)
        return {
            "object": "list",
            "data": [
                {
                    "object": "embedding",
                    "index": idx,
                    "embedding": vec.tolist(),
                }
                for idx, vec in enumerate(vecs)
            ],
            "model": used_model,
            "usage": {"prompt_tokens": prompt_toks, "total_tokens": prompt_toks},
        }

    @app.post("/v1/chat/completions")
    async def openai_chat_completions(req: OpenAIChatCompletionRequest, raw_request: Request) -> Any:
        rt = get_runtime()
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created_ts = int(time.time())

        stop_list: Optional[List[str]] = None
        if isinstance(req.stop, str):
            stop_list = [req.stop]
        elif isinstance(req.stop, list):
            stop_list = [str(s) for s in req.stop]

        # Build constrained GrammarSpec if response_format requests JSON
        grammar: Optional[GrammarSpec] = None
        schema_dict: Optional[Dict[str, Any]] = None
        if req.response_format:
            rf_type = req.response_format.get("type")
            if rf_type == "json_schema":
                js_wrap = req.response_format.get("json_schema") or {}
                schema_dict = js_wrap.get("schema") or {"type": "object"}
                grammar = GrammarSpec.from_schema(schema_dict)
            elif rf_type == "json_object":
                schema_dict = {"type": "object"}
                grammar = GrammarSpec.json_object()

        # Case A: Direct catalog model ID requested (e.g. `qwen3-4b-gguf` or `qwen2.5-coder-3b-onnx`)
        is_direct_model = req.model in MODEL_CATALOG
        pinned_agent = _normalize_agent_pin(req.model) if not is_direct_model else None

        # Extract flat user query when routing through the 10-plane agent kernel
        last_user_msg = ""
        for m in reversed(req.messages):
            if m.get("role") == "user":
                c = m.get("content", "")
                last_user_msg = c if isinstance(c, str) else json.dumps(c)
                break

        if not req.stream:
            if is_direct_model or len(req.messages) > 2:
                res = rt.generate(
                    model_id=req.model if is_direct_model else None,
                    role="primary",
                    messages=req.messages,
                    max_tokens=req.max_tokens,
                    temperature=req.temperature,
                    top_p=req.top_p,
                    stop=stop_list,
                    grammar=grammar,
                )
                content_out = res.text
                if schema_dict is not None:
                    parsed, _err = extract_and_repair_json(content_out, schema=schema_dict)
                    if parsed is not None:
                        content_out = json.dumps(parsed, ensure_ascii=False)
                return {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": created_ts,
                    "model": res.model_id,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content_out},
                            "finish_reason": res.finish_reason or "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": res.prompt_tokens,
                        "completion_tokens": res.completion_tokens,
                        "total_tokens": res.prompt_tokens + res.completion_tokens,
                    },
                }

            # Route through full 10-plane TurnStateMachine
            outcome = get_kernel().run_turn(
                user_query=last_user_msg,
                session_id=req.user or f"oai_{uuid.uuid4().hex[:8]}",
                run_id=completion_id,
                pinned_agent=pinned_agent,
                json_schema=schema_dict,
                max_tokens=req.max_tokens,
            )
            content_out = (
                json.dumps(outcome.structured_output, ensure_ascii=False)
                if (schema_dict is not None and outcome.structured_output is not None)
                else outcome.final_text
            )
            p_toks = max(1, len(last_user_msg) // 4)
            c_toks = max(1, len(content_out) // 4)
            return {
                "id": completion_id,
                "object": "chat.completion",
                "created": created_ts,
                "model": outcome.model_id or req.model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content_out},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": p_toks,
                    "completion_tokens": c_toks,
                    "total_tokens": p_toks + c_toks,
                },
            }

        # Streaming OpenAI `chat.completion.chunk` SSE
        ctok = get_cancellation_registry().create(completion_id)
        chunk_q: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _stream_worker() -> None:
            try:
                for ch in rt.stream(
                    model_id=req.model if is_direct_model else None,
                    role="primary",
                    messages=req.messages,
                    max_tokens=req.max_tokens,
                    temperature=req.temperature,
                    top_p=req.top_p,
                    stop=stop_list,
                    grammar=grammar,
                    cancel_token=ctok,
                ):
                    if ch.kind == "token" and ch.text:
                        loop.call_soon_threadsafe(
                            chunk_q.put_nowait,
                            {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created_ts,
                                "model": ch.model_id or req.model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": ch.text},
                                        "finish_reason": None,
                                    }
                                ],
                            },
                        )
                    elif ch.kind == "done":
                        loop.call_soon_threadsafe(
                            chunk_q.put_nowait,
                            {
                                "id": completion_id,
                                "object": "chat.completion.chunk",
                                "created": created_ts,
                                "model": ch.model_id or req.model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {},
                                        "finish_reason": ch.finish_reason or "stop",
                                    }
                                ],
                            },
                        )
            except RunCancelledError:
                pass
            finally:
                get_cancellation_registry().finish(completion_id)
                loop.call_soon_threadsafe(chunk_q.put_nowait, None)

        threading.Thread(target=_stream_worker, daemon=True).start()

        async def _oai_sse() -> AsyncIterator[str]:
            # Initial role announcement chunk
            first_frame = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": req.model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(first_frame)}\n\n"
            try:
                while True:
                    if await raw_request.is_disconnected():
                        ctok.cancel("OpenAI client disconnected")
                        break
                    item = await chunk_q.get()
                    if item is None:
                        break
                    yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            finally:
                ctok.cancel("Stream closed")

        return StreamingResponse(_oai_sse(), media_type="text/event-stream")
