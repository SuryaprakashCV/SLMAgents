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
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..runtime import GrammarSpec, get_runtime

if TYPE_CHECKING:
    from ..context.artifacts import ArtifactEntry


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
        "coding",
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

# High-precision Coding & Algorithm Intent Signature (catches misspellings like "fibinocci", "write a code", etc.)
_CODING_INTENT_RE = re.compile(
    r"(?:\b(?:write|create|build|implement|generate|develop|debug|refactor|optimize|give\s+me|show\s+me)\b.{0,40}\b(?:a\s+code|code|program|script|function|class|method|algorithm|api|endpoint|service|app|module|solution)\b)"
    r"|(?:\b(?:code|program|script|function|algorithm|implementation)\s+(?:for|to|in|of|that)\b)"
    r"|(?:\b(?:fib[ioa]+n[oa]+c+i|factorial|palindrome|prime\s+numbers?|binary\s+search|merge\s+sort|quick\s*sort|bubble\s+sort|heap\s*sort|insertion\s+sort|linked\s+list|binary\s+tree|bst|avl\s+tree|trie|graph\s+traversal|bfs|dfs|dijkstra|bellman\s+ford|dynamic\s+programming|knapsack|two\s+sum|lru\s+cache|rate\s+limiter|anagram|reverse\s+a\s+string|matrix\s+multiplication|leetcode|recursion|backtracking|sliding\s+window|monotonic\s+stack)\b)"
    r"|(?:\b(?:in\s+python|in\s+javascript|in\s+typescript|in\s+java|in\s+c\+\+|in\s+rust|in\s+golang|in\s+go|python\s+code|python\s+program|python\s+script|js\s+code|fastapi|flask|django|react\s+component|node\.?js|pytest|unit\s+tests?)\b)"
    r"|(?:```(?:python|js|ts|java|cpp|rust|go|bash|sh)\b|\bdef\s+[a-zA-Z_][a-zA-Z0-9_]*\s*\(|\bclass\s+[A-Z][a-zA-Z0-9_]*\s*[:(])",
    re.IGNORECASE | re.DOTALL,
)

_EMAIL_EXPLICIT_RE = re.compile(
    r"\b(?:email|e-mail|mail\s+to|subject\s+line|cold\s+outreach|follow[\s\-]?up\s+email|inbox|dear\s+hiring\s+manager|sincerely)\b",
    re.IGNORECASE,
)


def _safe_normalize_rows(mat: np.ndarray) -> np.ndarray:
    arr = np.nan_to_num(np.asarray(mat, dtype=np.float32), nan=0.0, posinf=0.0, neginf=0.0)
    if arr.ndim == 1:
        norm = float(np.linalg.norm(arr))
        return arr / norm if norm > 1e-8 else arr
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms > 1e-8, norms, 1.0)
    return arr / norms


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
            raw_mat = rt.embed_batch(texts)
            self._proto_matrix = _safe_normalize_rows(raw_mat)
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
            if "video" in kinds:
                return self._finish(t0, "slm_vision_parser", forced_mode or ExecutionMode.FAST_DIRECT, "vision", 0.99, "stage0_mime_video")
            if "audio" in kinds:
                return self._finish(t0, "slm_voice", forced_mode or ExecutionMode.FAST_DIRECT, "stt", 0.99, "stage0_mime_audio")
            if "image" in kinds:
                return self._finish(t0, "slm_vision_parser", forced_mode or ExecutionMode.FAST_DIRECT, "vision", 0.99, "stage0_mime_image")
            if "sqlite" in kinds:
                return self._finish(t0, "slm_text_to_sql", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "sql", 0.98, "stage0_mime_sqlite")
            if "tabular" in kinds:
                return self._finish(t0, "slm_data", forced_mode or ExecutionMode.REACT_LOOP, "coding", 0.97, "stage0_mime_tabular")
            if not _CODING_INTENT_RE.search(q):
                if "pdf" in kinds:
                    return self._finish(t0, "slm_pdf", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.97, "stage0_mime_pdf", needs_rag=True)
                if "text" in kinds:
                    return self._finish(t0, "slm_rag", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.97, "stage0_mime_document", needs_rag=True)

        # --- STAGE 1: Deterministic High-Precision Signatures (<1ms) ---
        from ..tools.media_doc_intelligence import extract_video_or_media_url
        from ..tools.machine_control import detect_machine_control_intent

        if extract_video_or_media_url(q):
            return self._finish(
                t0,
                "slm_web_agent",
                forced_mode or ExecutionMode.FAST_DIRECT,
                "primary",
                0.99,
                "stage1_online_video_url",
            )

        machine_intent = detect_machine_control_intent(q)
        if machine_intent:
            intent_agent_map = {
                "merge_excel": ("slm_data", "coding"),
                "git_control": ("slm_git_repo_manager", "coding"),
                "file_copy": ("slm_cli_agent", "coding"),
                "search_files": ("slm_cli_agent", "coding"),
                "play_music": ("slm_cli_agent", "primary"),
                "schedule_job": ("slm_task_planner", "primary"),
                "screen_traverse": ("slm_cli_agent", "primary"),
                "multi_machine": ("slm_task_planner", "primary"),
            }
            m_agent, m_role = intent_agent_map.get(machine_intent, ("slm_cli_agent", "primary"))
            return self._finish(
                t0,
                m_agent,
                forced_mode or ExecutionMode.FAST_DIRECT,
                m_role,
                0.99,
                f"stage1_machine_{machine_intent}",
            )

        from ..personalization import detect_concierge_intent

        concierge_intent = detect_concierge_intent(q)
        if concierge_intent:
            c_map = {
                "email_tracker": ("slm_email", "primary"),
                "job_assistant": ("slm_web_agent", "primary"),
                "learning_opportunities": ("slm_search_orchestrator", "primary"),
                "daily_dashboard": ("slm_task_planner", "primary"),
                "personalize_setup": ("slm_task_planner", "primary"),
                "multi_concierge": ("slm_task_planner", "primary"),
            }
            c_agent, c_role = c_map.get(concierge_intent, ("slm_task_planner", "primary"))
            return self._finish(
                t0,
                c_agent,
                forced_mode or ExecutionMode.FAST_DIRECT,
                c_role,
                0.99,
                f"stage1_concierge_{concierge_intent}",
            )

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
                return self._finish(t0, "slm_db_migration", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "coding", 0.94, "stage1_migration")
            return self._finish(t0, "slm_text_to_sql", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "sql", 0.96, "stage1_sql")

        if re.search(r"\b(?:alembic|flyway|database\s+migration|schema\s+migration|alter\s+table\b.*\badd\s+column)\b", q_low):
            return self._finish(t0, "slm_db_migration", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "coding", 0.95, "stage1_migration")

        if re.search(r"\b(?:fix|clean|repair|parse)\b.*\bjson\b", q_low) or (
            "{" in q and re.search(r"\bvalid\s+json\b", q_low)
        ):
            return self._finish(t0, "slm_json_cleaner", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "primary", 0.96, "stage1_json")

        if re.search(r"\btranslate\b.*\b(?:to|into)\s+(?:spanish|french|german|hindi|tamil|telugu|japanese|chinese|arabic|italian|portuguese|korean|russian)\b", q_low):
            return self._finish(t0, "slm_translation", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.96, "stage1_translation")

        if re.search(r"\b(?:compute\s+embedding|vector\s+embedding|embedding\s+vector|cosine\s+similarity\s+between|embed\s+this\s+sentence)\b", q_low):
            return self._finish(t0, "slm_embeddings", forced_mode or ExecutionMode.FAST_DIRECT, "embeddings", 0.96, "stage1_embeddings")

        if re.search(r"\b(?:remember\s+that|save\s+to\s+memory|what\s+do\s+you\s+remember\s+about\s+me|recall\s+my\s+preferences)\b", q_low):
            return self._finish(t0, "slm_memory", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.95, "stage1_memory")

        if re.search(r"\b(?:personal\s+knowledge\s+base|second\s+brain|zettelkasten|save\s+this\s+note\s+to\s+pkb)\b", q_low):
            return self._finish(t0, "slm_pkb", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.95, "stage1_pkb", needs_rag=True)

        if re.search(r"\b(?:milestone\s+plan|project\s+roadmap|work\s+breakdown\s+structure|launch\s+roadmap)\b", q_low) and not _CODING_INTENT_RE.search(q):
            return self._finish(t0, "slm_task_planner", forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER, "primary", 0.95, "stage1_task_planner")

        if re.search(r"\b(?:summarize\s+this\s+(?:article|passage|text)|tl;?dr\s+of|condense\s+this\s+text)\b", q_low) and not _CODING_INTENT_RE.search(q):
            return self._finish(t0, "slm_summarizer", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.95, "stage1_summarizer")

        if re.search(r"\b(?:analyze\s+(?:this\s+)?(?:csv|dataset|dataframe|spreadsheet)|cohort\s+analysis)\b", q_low):
            return self._finish(t0, "slm_data", forced_mode or ExecutionMode.FAST_DIRECT, "coding", 0.95, "stage1_data")

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

        if re.search(r"\b(?:draft an? email|write an? email|email reply|subject line)\b", q_low) and not _CODING_INTENT_RE.search(q):
            return self._finish(t0, "slm_email", forced_mode or ExecutionMode.FAST_DIRECT, "primary", 0.96, "stage1_email")

        if _CODING_INTENT_RE.search(q):
            return self._finish(
                t0,
                "slm_code_interpreter",
                forced_mode or ExecutionMode.EVALUATOR_OPTIMIZER,
                "coding",
                0.99,
                "stage1_code",
                needs_rag=False,
            )

        if re.search(r"\b(?:bash|shell command|terminal command|cli command|chmod|crontab|find command)\b", q_low):
            return self._finish(t0, "slm_cli_agent", forced_mode or ExecutionMode.FAST_DIRECT, "coding", 0.95, "stage1_cli")

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
                q_vec = _safe_normalize_rows(get_runtime().embed_query(q[:450]))
                with np.errstate(all="ignore"):
                    raw_sims = np.nan_to_num(mat @ q_vec, nan=0.0, posinf=0.0, neginf=0.0)
                sims = raw_sims.tolist()
                paired = sorted(zip(self._proto_names, sims), key=lambda x: x[1], reverse=True)
                top_candidates = [(name, float(sc)) for name, sc in paired[:4]]
        except Exception:
            top_candidates = [("direct_assistant", 0.6)]

        if not top_candidates:
            top_candidates = [("direct_assistant", 0.6)]

        best_agent, best_sim = top_candidates[0]
        second_sim = top_candidates[1][1] if len(top_candidates) > 1 else 0.0
        margin = best_sim - second_sim

        stage_label = "stage2_embedding_margin"
        # Prevent multimodal and narrow domain specialists from being selected without matching domain cues
        has_image_art = any(a.kind == "image" for a in artifacts)
        has_audio_art = any(a.kind == "audio" for a in artifacts)
        has_pdf_art = any(a.kind == "pdf" for a in artifacts)
        has_sql_cue = bool(
            has_active_sqlite
            or re.search(
                r"\b(?:sql|sqlite|postgres|postgresql|mysql|database|db|table|tables|schema|alembic|migration|column|columns|foreign\s+key)\b",
                q_low,
            )
        )
        best_agent = self._validate_domain_specialist_guard(
            best_agent,
            q_low=q_low,
            has_image_art=has_image_art,
            has_audio_art=has_audio_art,
            has_pdf_art=has_pdf_art,
            has_sql_cue=has_sql_cue,
            has_indexed_docs=has_indexed_docs,
        )

        # If session has indexed RAG docs and query asks factual document questions (not code/math/sql), favor RAG grounding
        if has_indexed_docs and best_agent in ("direct_assistant", "slm_vision_parser", "slm_document_parser"):
            best_agent = "slm_rag"

        # --- STAGE 3: Enum-Constrained 0.8B Tie-Breaker ONLY on close multi-specialist ties ---
        if best_sim >= 0.45 and margin < 0.025 and len(q.split()) >= 10 and len(top_candidates) >= 3:
            shortlist_names = [
                c[0]
                for c in top_candidates[:3]
                if not (c[0] == "slm_email" and not _EMAIL_EXPLICIT_RE.search(q_low))
            ]
            if "direct_assistant" not in shortlist_names:
                shortlist_names.append("direct_assistant")
            try:
                res = get_runtime().generate(
                    role="router",
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
                    picked = self._validate_domain_specialist_guard(
                        picked,
                        q_low=q_low,
                        has_image_art=has_image_art,
                        has_audio_art=has_audio_art,
                        has_pdf_art=has_pdf_art,
                        has_sql_cue=has_sql_cue,
                        has_indexed_docs=has_indexed_docs,
                    )
                    best_agent = picked
                    stage_label = "stage3_onnx_enum_tiebreak"
            except Exception:
                pass

        if best_sim < 0.46 and not has_indexed_docs:
            best_agent = "direct_assistant"

        role = AGENT_PROTOTYPES.get(best_agent, ("primary", ""))[0]
        mode = forced_mode or self._select_mode(best_agent, q, artifacts)
        secondary = [c[0] for c in top_candidates[1:3] if c[1] >= best_sim - 0.03 and c[0] != "direct_assistant"]
        needs_rag = best_agent in ("slm_rag", "slm_pdf", "slm_pkb")

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

    @staticmethod
    def _validate_domain_specialist_guard(
        agent: str,
        *,
        q_low: str,
        has_image_art: bool,
        has_audio_art: bool,
        has_pdf_art: bool,
        has_sql_cue: bool,
        has_indexed_docs: bool,
    ) -> str:
        """
        Ensures narrow domain specialists selected via Stage-2 embeddings or Stage-3 tie-breakers
        have matching domain cues in the prompt or attachments, falling back to `direct_assistant`
        (or `slm_code_interpreter` / `slm_rag` when appropriate) if not.
        """
        if agent == "slm_vision_parser" and not (
            has_image_art or re.search(r"\b(?:image|photo|screenshot|picture|diagram|ocr)\b", q_low)
        ):
            return "slm_rag" if has_indexed_docs else "direct_assistant"
        if agent == "slm_voice" and not (
            has_audio_art or re.search(r"\b(?:audio|voice|speech|transcribe|whisper|wav|mp3|recording)\b", q_low)
        ):
            return "slm_rag" if has_indexed_docs else "direct_assistant"
        if agent == "slm_pdf" and not (has_pdf_art or re.search(r"\bpdf\b", q_low)):
            return "slm_rag" if has_indexed_docs else "direct_assistant"
        if agent == "slm_email" and not _EMAIL_EXPLICIT_RE.search(q_low):
            return "direct_assistant"
        if agent in ("slm_db_migration", "slm_text_to_sql") and not has_sql_cue:
            if re.search(r"\b(?:design|ui|theme|layout|style|color|css|html|app|project|button|page)\b", q_low):
                return "slm_code_interpreter"
            return "direct_assistant"
        if agent == "slm_json_cleaner" and not ("{" in q_low or re.search(r"\b(?:json|schema)\b", q_low)):
            return "direct_assistant"
        if agent == "slm_embeddings" and not re.search(
            r"\b(?:embedding|embeddings|cosine\s+similarity|vector\s+similarity|semantic\s+distance)\b", q_low
        ):
            return "direct_assistant"
        if agent == "slm_translation" and not re.search(
            r"\b(?:translat|spanish|french|german|hindi|tamil|telugu|japanese|chinese|arabic|italian|portuguese|korean|russian)\b",
            q_low,
        ):
            return "direct_assistant"
        if agent == "slm_git_repo_manager" and not re.search(
            r"\b(?:git|commit|diff|branch|rebase|merge\s+conflict|pull\s+request|changelog)\b", q_low
        ):
            return "direct_assistant"
        if agent == "slm_security" and not re.search(
            r"\b(?:security|vulnerabilit|owasp|injection|xss|csrf|cve|cwe|exploit|pentest|secret|threat\s+model)\b",
            q_low,
        ):
            return "direct_assistant"
        if agent == "slm_math" and not (
            re.search(
                r"\b(?:solve|math|equation|calculus|derivative|integral|integrate|differentiate|algebra|probability|statistics|matrix|eigenvalue|quadratic|simplify|factor|polynomial|root|roots|formula|theorem|proof|arithmetic)\b",
                q_low,
            )
            or re.search(r"\d+\s*[+\-*/^=]\s*\d+", q_low)
        ):
            return "direct_assistant"
        if agent == "slm_meeting" and not re.search(
            r"\b(?:meeting|transcript|minutes|action\s+items|standup|attendees)\b", q_low
        ):
            return "direct_assistant"
        if agent == "slm_web_scraper" and not re.search(
            r"\b(?:scrape|scraper|scraping|html|dom|xpath|css\s+selector|beautifulsoup)\b", q_low
        ):
            return "direct_assistant"
        return agent

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
        if agent in ("slm_text_to_sql", "slm_db_migration", "slm_code_interpreter", "slm_math", "slm_json_cleaner"):
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
