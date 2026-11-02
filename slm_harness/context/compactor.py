"""
Plane 3: Episodic Session Compactor (`/compact`).

Compresses long multi-turn conversations into a structured episodic checkpoint so CPU prefill
stays fast (`<300ms`) across 50+ turn interactive sessions without losing key entities or decisions.
"""

from __future__ import annotations

from typing import Any, Dict

from ..kernel.checkpoints import CheckpointStore, get_checkpoint_store
from ..runtime import get_runtime


def compact_session_history(
    session_id: str,
    keep_recent_nodes: int = 4,
    store: CheckpointStore | None = None,
) -> Dict[str, Any]:
    ckpt = store or get_checkpoint_store()
    lineage = ckpt.get_branch_lineage(session_id, limit=80)
    prior_summary = ckpt.get_compacted_summary(session_id)

    if len(lineage) <= keep_recent_nodes:
        return {
            "compacted": False,
            "reason": f"Only {len(lineage)} turns in active branch (nothing to compact yet).",
            "summary": prior_summary,
        }

    older = lineage[:-keep_recent_nodes]
    transcript_lines = []
    if prior_summary:
        transcript_lines.append(f"Previous Summary:\n{prior_summary}")
    for node in older:
        transcript_lines.append(f"{node.role.upper()}: {node.content[:600]}")

    transcript = "\n\n".join(transcript_lines)[-6000:]

    rt = get_runtime()
    try:
        res = rt.generate(
            role="router",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the conversation history into concise bullet points preserving:\n"
                        "1. User goals & preferences\n"
                        "2. Key facts, numbers, SQL tables, or file paths discussed\n"
                        "3. Completed actions & open next steps.\n"
                        "Keep under 180 words."
                    ),
                },
                {"role": "user", "content": transcript},
            ],
            max_tokens=240,
            temperature=0.1,
        )
        summary = res.text.strip()
    except Exception:
        summary = "\n".join(f"- {line[:180]}" for line in transcript_lines[-8:])

    ckpt.set_compacted_summary(session_id, summary, keep_recent_nodes=keep_recent_nodes)
    return {
        "compacted": True,
        "compacted_turns": len(older),
        "retained_recent_turns": keep_recent_nodes,
        "summary": summary,
    }
