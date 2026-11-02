"""
Plane 6: 4-Stage `<35ms` Calibrated Cascade Router (`CascadeRouter`).

Stages:
  0. Explicit `/agent`, `/plan`, or attached artifact kind (`audio`, `image`, `sqlite`, `tabular`, `pdf`)
  1. High-precision structural regex/AST signatures (<1ms)
  2. Calibrated Embedding Prototype Margin Classifier across all 26 specialist domains (<12ms)
  3. Enum-constrained `qwen3.5-0.8b-onnx` tie-breaker ONLY when margin is ambiguous
  4. Execution Mode Selector (`FAST_DIRECT`, `REACT_LOOP`, `PLAN_DAG`, `EVALUATOR_OPTIMIZER`)
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..context.artifacts import ArtifactEntry
from ..runtime import GrammarSpec, get_runtime


class ExecutionMode(str, Enum):
    FAST_DIRECT = "fast_direct"
    REACT_LOOP = "react_loop"
    PLAN_DAG = "plan_dag"
    EVALUATOR_OPTIMIZER = "evaluator_optimizer"


@dataclass
class RouteDecision:
    primary_agent: str
    mode: ExecutionMode
    model_role: str = "primary"
    secondary_agents: List[str] = field(default_factory=list)
    confidence: float = 0.9
    stage_used: str = "stage1_signature"
    needs_rag: bool = False
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary_agent": self.primary_agent,
            "mode": self.mode.value,
            "model_role": self.model_role,
            "secondary_agents": self.secondary_agents,
            "confidence": round(self.confidence, 3),
            "stage_used": self.stage_used,
            "needs_rag": self.needs_rag,
            "latency_ms": round(self.latency_ms, 2),
        }


# Semantic prototypes for all 26 SLMAgents specialist domains + general assistant
AGENT_PROTOTYPES: Dict[str, Tuple[str, str]] = {
    "direct_assistant": (
        "primary",
        "general conversation greeting explanation brainstorming advice concept overview creative writing question answering",
    ),
    "slm_text_to_sql": (
        "sql",
        "write SQL SELECT JOIN GROUP BY query database schema tables sqlite postgres analytics columns rows",
    ),
    "slm_code_interpreter": (
        "coding",
        "write Python code function algorithm debug script implement class data structure execute program",
    ),
    "slm_math": (
        "math",
        "solve math equation calculus derivative integral algebra probability statistics matrix arithmetic proof",
    ),
    "slm_rag": (
        "primary",
        "search knowledge base retrieve grounded answer from indexed enterprise documents with citations",
    ),
    "slm_pdf": (
        "primary",
        "read PDF document extract sections answer questions from attached PDF pages report paper",
    ),
    "slm_data": (
        "coding",
        "analyze CSV Excel dataframe dataset summary statistics trends outliers pandas columns",
    ),
    "slm_cli_agent": (
        "coding",
        "terminal shell bash zsh command line flags find awk sed process disk network command",
    ),
    "slm_git_repo_manager": (
        "coding",
        "git commit message review git diff branch rebase merge conflict pull request changelog",
    ),
    "slm_json_cleaner": (
        "primary",
        "fix repair clean broken malformed invalid JSON syntax extract valid JSON schema object",
    ),
    "slm_security": (
        "coding",
        "security audit vulnerability OWASP SQL injection XSS command injection hardcoded secret CVE review",
    ),
    "slm_db_migration": (
        "sql",
        "database schema migration ALTER TABLE Alembic Flyway SQL DDL upgrade rollback script",
    ),
    "slm_translation": (
        "primary",
        "translate text sentence paragraph into Spanish French German Hindi Tamil Telugu Japanese Chinese",
    ),
    "slm_summarizer": (
        "primary",
        "summarize long article text executive TLDR bullet summary key takeaways condense",
    ),
    "slm_meeting": (
        "primary",
        "summarize meeting transcript speakers action items owners deadlines decisions minutes",
    ),
    "slm_email": (
        "primary",
        "draft professional email reply subject line follow up outreach customer message",
    ),
    "slm_document_parser": (
        "primary",
        "parse invoice receipt resume contract extract structured fields entities key value JSON from document",
    ),
    "slm_vision_parser": (
        "vision",
        "describe image screenshot photo diagram OCR extract text from picture visual analysis",
    ),
    "slm_voice": (
        "stt",
        "transcribe voice audio recording wav mp3 speech to text spoken command",
    ),
    "slm_web_scraper": (
        "primary",
        "parse HTML DOM extract links tables article text CSS selectors from webpage markup",
    ),
    "slm_web_agent": (
        "primary",
        "automate browser workflow navigate webpage click form steps playwright web interaction",
    ),
    "slm_search_orchestrator": (
        "primary",
        "search the web internet live online news research multiple sources synthesize report",
    ),
    "slm_pkb": (
        "primary",
        "personal knowledge base save note recall personal notes second brain zettelkasten",
    ),
    "slm_memory": (
        "primary",
        "remember user preference recall past facts profile memory store",
    ),
    "slm_embeddings": (
        "embeddings",
        "compute vector embedding cosine similarity semantic distance between sentences",
    ),
    "slm_task_planner": (
        "primary",
        "break down complex project architecture roadmap into step by step milestone plan DAG",
    ),
}

_WORKSPACE_TOOL_RE = re.compile(
    r"\b(?:in this repo|in the workspace|read file|edit file|create file|write to|grep|find in codebase|list files|run command|git status)\b"
    r"|(?:[A-Za-z0-9_\-]+/[A-Za-z0-9_.\-]+\.(?:py|js|ts|md|json|yaml|sql|html|css))\b",
    re.IGNORECASE,
)

_MULTI_STEP_CONJUNCTION_RE = re.compile(
    r"\b(?:and then|after that|followed by|step 1.*step 2|first\b.*\bthen\b.*\bfinally)\b",
    re.IGNORECASE | re.DOTALL,
)


class CascadeRouter:
    """Sub-35ms 4-stage router mapping user queries & attachments to agent + execution mode."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proto_names: List[str] = list(AGENT_PROTOTYPES.keys())
        self._proto_matrix: Optional[np.ndarray] = None
        self._proto_dim: int = 0

    def _ensure_prototypes(self) -> Optional[np.ndarray]:
        with self._lock:
            rt = get_runtime()
            sample = rt.embed_query("test")
            dim = int(sample.shape[0])
            if self._proto_matrix is not None and self._proto_dim == dim:
                return self._proto_matrix
            texts = [AGENT_PROTOTYPES[name][1] for name in self._proto_names]
            self._proto_matrix = rt.embed_batch(texts)
            self._proto_dim = dim
            return self._proto_matrix

    def route(
        self,
        query: str,
        artifacts: Sequence[ArtifactEntry] = (),
        pinned_agent: Optional[str] = None,
        forced_mode: Optional[ExecutionMode] = None,
        has_active_sqlite: bool = False,
        has_indexed_docs: bool = False,
    ) -> RouteDecision:
        t0 = time.perf_counter()
        q = query.strip()
        q_low = q.lower()

        # --- STAGE 0: Explicit Agent Pin or Attachment Kind ---
        if pinned_agent and pinned_agent != "auto":
            norm_agent = pinned_agent if pinned_agent in AGENT_PROTOTYPES else f"slm_{pinned_agent}"
            if norm_agent not in AGENT_PROTOTYPES:
                norm_agent = "direct_assistant"
            role = AGENT_PROTOTYPES[norm_agent][0]
            mode = forced_mode or self._select_mode(norm_agent, q, artifacts)
            return RouteDecision(
                primary_agent=norm_agent,
                mode=mode,
                model_role=role,
                confidence=1.0,
                stage_used="stage0_pinned",
                needs_rag=(norm_agent in ("slm_rag", "slm_pdf", "slm_pkb")),
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )

        if artifacts:
            kinds = [a.kind for a in artifacts]
            if "audio" in kinds:
                return self._finish(t0, "slm_voice", forced_mode or ExecutionMode.FAST_DIRECT, "stt", 0.99, "stage0_mime_audio")
            if "image" in kinds:
                return self._finish(t0, "slm_vision_parser", forced_mode or ExecutionMode.FAST_DIRECT, "vision", 0.99, "stage0_mime_image")
            if "sqlite" in kinds:
                return self._finish(t0, "slm_text_to_sql", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "sql", 0.98, "stage0_mime_sqlite")
            if "tabular" in kinds:
                return self._finish(t0, "slm_data", forced_mode or ExecutionMode.REACT_LOOP, "coding", 0.97, "stage0_mime_tabular")
            if "pdf" in kinds:
                return self._finish(t0, "slm_pdf", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.97, "stage0_mime_pdf", needs_rag=True)
            if "text" in kinds:
                return self._finish(t0, "slm_rag", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.97, "stage0_mime_document", needs_rag=True)

        # --- STAGE 1: Deterministic High-Precision Signatures (<1ms) ---
        if _WORKSPACE_TOOL_RE.search(q):
            return self._finish(
                t0,
                "slm_code_interpreter",
                forced_mode or ExecutionMode.REACT_LOOP,
                "coding",
                0.95,
                "stage1_workspace_react",
            )

        if re.search(r"\b(?:create\s+table|select\s+.+\s+from|sql\s+query|text\s*to\s*sql|sqlite)\b", q_low) or (
            has_active_sqlite and re.search(r"\b(?:table|rows|count|average|sum|top|group by|query)\b", q_low)
        ):
            if "migration" in q_low or "alembic" in q_low or "alter table" in q_low:
                return self._finish(t0, "slm_db_migration", forced_mode or ExecutionMode.FAST_DIRECT, "sql", 0.94, "stage1_migration")
            return self._finish(t0, "slm_text_to_sql", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "sql", 0.96, "stage1_sql")

        if re.search(r"\b(?:fix|clean|repair|parse)\b.*\bjson\b", q_low) or (
            "{" in q and re.search(r"\bvalid\s+json\b", q_low)
        ):
            return self._finish(t0, "slm_json_cleaner", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "primary", 0.96, "stage1_json")

        if re.search(r"\btranslate\b.*\b(?:to|into)\s+(?:spanish|french|german|hindi|tamil|telugu|japanese|chinese|arabic|italian|portuguese)\b", q_low):
            return self._finish(t0, "slm_translation", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.96, "stage1_translation")

        if re.search(r"\b(?:solve|derivative|integral|integrate|differentiate|eigenvalue|quadratic|equation|simplify)\b", q_low) or re.fullmatch(
            r"[\d\sxXyY+\-*/^().=?]+", q
        ):
            return self._finish(t0, "slm_math", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "math", 0.95, "stage1_math")

        if re.search(r"\b(?:security audit|vulnerability|sql injection|xss|cve-|owasp|hardcoded secret)\b", q_low):
            return self._finish(t0, "slm_security", forced_mode or ExecutionMode.FAST_DIRECT, "coding", 0.94, "stage1_security")

        if re.search(r"\b(?:git commit|git diff|rebase|merge conflict|pull request)\b", q_low):
            return self._finish(t0, "slm_git_repo_manager", forced_mode or ExecutionMode.FAST_DIRECT, "coding", 0.94, "stage1_git")

        if re.search(r"<html|<div|<table|<body", q_low) and re.search(r"\b(?:scrape|extract|parse)\b", q_low):
            return self._finish(t0, "slm_web_scraper", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.94, "stage1_html")

        if re.search(r"\b(?:action items|meeting transcript|meeting notes)\b", q_low) or len(
            re.findall(r"^[A-Z][a-z]+:\s+", q, flags=re.MULTILINE)
        ) >= 3:
            return self._finish(t0, "slm_meeting", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.93, "stage1_meeting")

        if re.search(r"\b(?:draft an? email|write an? email|email reply|subject line)\b", q_low):
            return self._finish(t0, "slm_email", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.93, "stage1_email")

        if re.search(r"\b(?:write|implement|debug)\b.*\b(?:python|function|class|script|algorithm)\b", q_low) or "def " in q:
            return self._finish(t0, "slm_code_interpreter", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "coding", 0.94, "stage1_code")

        if re.search(r"\b(?:bash|shell command|terminal command|cli command|chmod|crontab|find command)\b", q_low):
            return self._finish(t0, "slm_cli_agent", forced_mode or ExecutionMode.FAST_DIRECT, "coding", 0.92, "stage1_cli")

        if re.search(
            r"\b(?:ppt|pptx|powerpoint|presentation|slide\s*deck|slides|resume|curriculum\s*vitae|cv|down\s*load|linjk|export\s+to|convert\s+it\s+to)\b",
            q_low,
        ):
            doc_agent = "slm_rag" if (has_indexed_docs or artifacts) else "direct_assistant"
            return self._finish(
                t0,
                doc_agent,
                forced_mode or ExecutionMode.FAST_DIRECT,
                "primary",
                0.96,
                "stage1_document_studio",
                needs_rag=bool(has_indexed_docs or artifacts),
            )

        # Short conversational greetings/questions -> immediate FAST_DIRECT (<1ms)
        if len(q.split()) <= 7 and not has_indexed_docs and re.match(r"^(?:hi|hello|hey|thanks|who are you|what can you do)\b", q_low):
            return self._finish(t0, "direct_assistant", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.98, "stage1_greeting")

        # --- STAGE 2: Calibrated Embedding Prototype Margin Classifier (<12ms) ---
        top_candidates: List[Tuple[str, float]] = []
        try:
            mat = self._ensure_prototypes()
            if mat is not None:
                q_vec = get_runtime().embed_query(q[:450])
                sims = (mat @ q_vec).tolist()
                paired = sorted(zip(self._proto_names, sims), key=lambda x: x[1], reverse=True)
                top_candidates = [(name, float(sc)) for name, sc in paired[:4]]
        except Exception:
            top_candidates = [("direct_assistant", 0.6)]

        best_agent, best_sim = top_candidates[0]
        second_sim = top_candidates[1][1] if len(top_candidates) > 1 else 0.0
        margin = best_sim - second_sim

        stage_label = "stage2_embedding_margin"
        # Prevent multimodal specialists from being selected without matching media attachments
        has_image_art = any(a.kind == "image" for a in artifacts)
        has_audio_art = any(a.kind == "audio" for a in artifacts)
        if best_agent == "slm_vision_parser" and not has_image_art:
            best_agent = "slm_rag" if has_indexed_docs else "direct_assistant"
        elif best_agent == "slm_voice" and not has_audio_art:
            best_agent = "slm_rag" if has_indexed_docs else "direct_assistant"

        # If session has indexed RAG docs and query asks factual domain questions, favor RAG grounding
        if has_indexed_docs and best_agent in ("direct_assistant", "slm_vision_parser", "slm_document_parser"):
            best_agent = "slm_rag"

        # --- STAGE 3: Enum-Constrained 0.8B Tie-Breaker ONLY on close multi-specialist ties ---
        if best_sim >= 0.45 and margin < 0.025 and len(q.split()) >= 10 and len(top_candidates) >= 3:
            shortlist_names = [c[0] for c in top_candidates[:3]]
            if "direct_assistant" not in shortlist_names:
                shortlist_names.append("direct_assistant")
            try:
                res = get_runtime().generate(
                    role="router",
                    model_id="qwen3.5-0.8b-onnx",
                    messages=[
                        {"role": "system", "content": "Select the single best specialist agent for the user request."},
                        {"role": "user", "content": q[:350]},
                    ],
                    max_tokens=10,
                    temperature=0.0,
                    grammar=GrammarSpec.from_enum(shortlist_names),
                )
                picked = res.text.strip()
                if picked in AGENT_PROTOTYPES:
                    best_agent = picked
                    stage_label = "stage3_onnx_enum_tiebreak"
            except Exception:
                pass

        if best_sim < 0.46 and not has_indexed_docs:
            best_agent = "direct_assistant"

        role = AGENT_PROTOTYPES.get(best_agent, ("primary", ""))[0]
        mode = forced_mode or self._select_mode(best_agent, q, artifacts)
        secondary = [c[0] for c in top_candidates[1:3] if c[1] >= best_sim - 0.03 and c[0] != "direct_assistant"]
        needs_rag = has_indexed_docs or best_agent in ("slm_rag", "slm_pdf", "slm_pkb")

        return RouteDecision(
            primary_agent=best_agent,
            mode=mode,
            model_role=role,
            secondary_agents=secondary if mode == ExecutionMode.PLAN_DAG else [],
            confidence=max(0.55, min(0.99, best_sim)),
            stage_used=stage_label,
            needs_rag=needs_rag,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )

    def _select_mode(
        self,
        agent: str,
        query: str,
        artifacts: Sequence[ArtifactEntry],
    ) -> ExecutionMode:
        if _MULTI_STEP_CONJUNCTION_RE.search(query) and len(query.split()) >= 16:
            return ExecutionMode.PLAN_DAG
        if _WORKSPACE_TOOL_RE.search(query):
            return ExecutionMode.REACT_LOOP
        if agent in ("slm_text_to_sql", "slm_code_interpreter", "slm_math", "slm_json_cleaner"):
            return ExecutionMode.EVALUATOR_OPTIMIZER
        if agent == "slm_data" and artifacts:
            return ExecutionMode.REACT_LOOP
        return ExecutionMode.FAST_DIRECT

    @staticmethod
    def _finish(
        t0: float,
        agent: str,
        mode: ExecutionMode,
        role: str,
        conf: float,
        stage: str,
        needs_rag: bool = False,
    ) -> RouteDecision:
        return RouteDecision(
            primary_agent=agent,
            mode=mode,
            model_role=role,
            confidence=conf,
            stage_used=stage,
            needs_rag=needs_rag,
            latency_ms=(time.perf_counter() - t0) * 1000.0,
        )


_GLOBAL_ROUTER: Optional[CascadeRouter] = None


def get_cascade_router() -> CascadeRouter:
    global _GLOBAL_ROUTER
    if _GLOBAL_ROUTER is None:
        _GLOBAL_ROUTER = CascadeRouter()
    return _GLOBAL_ROUTER
