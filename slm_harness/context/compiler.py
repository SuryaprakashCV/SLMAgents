"""
Plane 3: Prefix-Cached Context Compiler (`ContextCompiler`).

Compiles static-first prompt slots so CPU prefix KV caches (`LlamaRAMCache` & ONNX state cache)
stay hot across turns while strictly bounding prompt tokens within the active CPU profile budget.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..config import HarnessConfig, get_config
from ..kernel.checkpoints import TurnNode
from ..runtime import get_runtime
from ..tools.file_exporter import clean_export_content
from .artifacts import ArtifactEntry


@dataclass
class CompiledContext:
    messages: List[Dict[str, str]]
    estimated_tokens: int
    static_prefix_tokens: int
    included_turns: int
    evidence_count: int
    attached_artifacts: List[str] = field(default_factory=list)


CORE_SYSTEM_PROMPT = """You are SLMAgents CPU Harness, an elite, locally-executed Principal AI Systems, Engineering & Career Advisor built and created by Suryaprakash.
Principles:
0. Identity & Creator: You are SLMAgents, built and created by Suryaprakash. Whenever asked who built you, who created you, who made you, or who your creator/developer/author/architect is, always answer Suryaprakash (never claim to be built by Alibaba Cloud, Qwen, OpenAI, Google, or Anthropic).
1. Ground factual answers in Attached User Documents, Live Web Search Evidence, SQLite/DataFrame outputs, or Tool results.
2. When numbered Evidence blocks ([1], [2], ...) are provided, cite supporting claims or web benchmarks inline as [1], [2].
3. When the user asks to IMPROVE, REVIEW, REWRITE, or BENCHMARK an attached Resume/CV or Document:
   - NEVER say "the provided context does not contain feedback on what to improve." YOU are the expert reviewer!
   - Keep any executive review commentary concise at the top, and then write out the **complete, full-length, publication-ready rewritten document** directly in clean Markdown (do NOT wrap the document inside a ```markdown code fence, do NOT omit or truncate earlier work history roles, and do NOT insert `[1], [2]` citation tags inside a Resume/CV).
   - Place the complete rewritten document after a `<!-- DOCUMENT_START -->` marker so the compiler exports 100% pure document content.
4. When the user asks to GENERATE, CONVERT, EXPORT, or DOWNLOAD a file/document/presentation in any format (PPT/PPTX, PDF, DOCX/Word, XLSX/Excel, CSV, Markdown, HTML, JSON, TXT):
   - NEVER say "I can't generate or provide a downloadable file directly in this chat interface."
   - NEVER output manual instructions like "How to Download as PPT: Open Microsoft PowerPoint and paste..."
   - NEVER write any URLs or markdown links (`http://...`, `https://...`, `example.com`, `designstudio...`, `sandbox:...`) for downloading files in your text response—the local offline compiler automatically builds `.pptx`, `.pdf`, `.docx`, `.xlsx`, and `.html` files on disk and attaches local download links above and below your response.
   - Simply write out the complete, rich, well-structured slide deck (`### Slide 1: Title`, `### Slide 2: ...`) or document content directly in Markdown.
5. Prefer exact computation (SQL, Python sandbox, SymPy) over mental arithmetic whenever data or equations are involved."""


class ContextCompiler:
    """Deterministic static-prefix slot compiler & token budget manager."""

    def __init__(self, config: Optional[HarnessConfig] = None) -> None:
        self.config = config or get_config()

    def compile(
        self,
        user_query: str,
        lineage: Sequence[TurnNode] = (),
        compacted_summary: str = "",
        procedural_facts: Sequence[str] = (),
        tool_declarations_text: str = "",
        evidence_chunks: Sequence[Dict[str, Any]] = (),
        artifacts: Sequence[ArtifactEntry] = (),
        exemplar_hint: str = "",
        steer_hints: Sequence[str] = (),
        extra_system_instructions: str = "",
        reserved_output_tokens: int = 2400,
    ) -> CompiledContext:
        rt = get_runtime()
        max_ctx = max(2048, int(self.config.context_window))
        reserved_for_budget = min(int(reserved_output_tokens), max_ctx // 2)
        input_budget = max(1024, max_ctx - reserved_for_budget - 96)

        # --- SLOT 0 + 1 + 2: Static & Semi-Static System Prefix (maximizes KV cache hits) ---
        sys_blocks: List[str] = [CORE_SYSTEM_PROMPT]
        if self.config.workspace_instructions:
            sys_blocks.append(self.config.workspace_instructions[:1600])
        if procedural_facts:
            facts_str = "\n".join(f"- {f}" for f in procedural_facts[:12])
            sys_blocks.append(f"### Remembered User & Project Preferences\n{facts_str}")
        if extra_system_instructions:
            sys_blocks.append(extra_system_instructions.strip())
        if tool_declarations_text:
            sys_blocks.append(tool_declarations_text.strip())

        system_text = "\n\n".join(sys_blocks)
        static_tokens = rt.count_tokens(system_text)

        # --- SLOT 4: Dynamic Turn Payload (Artifacts + RAG/Web Evidence + Exemplar + User Query) ---
        dynamic_sections: List[str] = []
        art_uris: List[str] = []
        for art in artifacts:
            art_uris.append(art.uri)
            if art.kind in ("text", "pdf", "code", "json") and art.stored_text_path and os.path.isfile(art.stored_text_path):
                try:
                    with open(art.stored_text_path, "r", encoding="utf-8", errors="replace") as af:
                        full_doc_txt = af.read(3800).strip()
                    if full_doc_txt:
                        dynamic_sections.append(
                            f"### Attached User Document: {art.name} ({art.uri})\n{full_doc_txt}"
                        )
                        continue
                except Exception:
                    pass
            dynamic_sections.append(art.descriptor)

        if evidence_chunks:
            ev_lines: List[str] = ["### Retrieved Grounding Evidence (cite inline as [1], [2], ...)"]
            for idx, ch in enumerate(evidence_chunks[:4], start=1):
                src = ch.get("source") or ch.get("title") or "document"
                loc = f" ({ch['location']})" if ch.get("location") else ""
                snippet = str(ch.get("text") or ch.get("content") or "").strip()[:480]
                ev_lines.append(f"[{idx}] Source: {src}{loc}\n{snippet}")
            dynamic_sections.append("\n\n".join(ev_lines))

        if exemplar_hint:
            dynamic_sections.append(f"### Verified Procedural Reference\n{exemplar_hint[:600]}")

        if steer_hints:
            dynamic_sections.append(
                "### Mid-Flight User Steering Instructions\n" + "\n".join(f"- {h}" for h in steer_hints)
            )

        dynamic_sections.append(f"### User Request\n{user_query.strip()}")
        user_payload = "\n\n".join(dynamic_sections)
        user_tokens = rt.count_tokens(user_payload)

        # Trim evidence if dynamic payload exceeds budget
        if static_tokens + user_tokens > input_budget and len(user_payload) > 2500:
            char_cap = max(1200, int((input_budget - min(static_tokens, input_budget // 2)) * 3.3))
            # Preserve tail user request while trimming middle evidence
            req_tail = f"\n\n### User Request\n{user_query.strip()}"
            head_part = user_payload[: max(400, char_cap - len(req_tail))]
            user_payload = head_part + "\n...[evidence trimmed to fit CPU context window]..." + req_tail
            user_tokens = rt.count_tokens(user_payload)

        # --- SLOT 3: Episodic Summary & Multi-Turn Lineage within remaining token budget ---
        remaining_budget = max(0, input_budget - static_tokens - user_tokens)
        messages: List[Dict[str, str]] = [{"role": "system", "content": system_text}]

        used_history_tokens = 0
        if compacted_summary and remaining_budget > 120:
            comp_msg = f"[Earlier Conversation Summary]\n{compacted_summary[:1400]}"
            ctoks = rt.count_tokens(comp_msg)
            if ctoks <= remaining_budget:
                messages.append({"role": "assistant", "content": comp_msg})
                used_history_tokens += ctoks
                remaining_budget -= ctoks

        selected_turns: List[Dict[str, str]] = []
        for t_idx, node in enumerate(reversed(list(lineage))):
            if node.role not in ("user", "assistant"):
                continue
            txt = clean_export_content(node.content) if node.role == "assistant" else node.content.strip()
            if not txt:
                continue
            char_limit = 2600 if t_idx < 2 else 1100
            if len(txt) > char_limit:
                txt = txt[: char_limit - 50] + " ...[truncated]"
            ntoks = rt.count_tokens(txt) + 6
            if ntoks > remaining_budget:
                break
            selected_turns.append({"role": node.role, "content": txt})
            remaining_budget -= ntoks
            used_history_tokens += ntoks

        selected_turns.reverse()
        messages.extend(selected_turns)
        messages.append({"role": "user", "content": user_payload})

        total_est = static_tokens + used_history_tokens + user_tokens
        return CompiledContext(
            messages=messages,
            estimated_tokens=total_est,
            static_prefix_tokens=static_tokens,
            included_turns=len(selected_turns),
            evidence_count=len(evidence_chunks),
            attached_artifacts=art_uris,
        )


_GLOBAL_COMPILER: Optional[ContextCompiler] = None


def get_context_compiler() -> ContextCompiler:
    global _GLOBAL_COMPILER
    if _GLOBAL_COMPILER is None:
        _GLOBAL_COMPILER = ContextCompiler()
    return _GLOBAL_COMPILER
