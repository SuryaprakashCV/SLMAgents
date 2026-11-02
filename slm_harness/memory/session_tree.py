"""
Plane 4 (Tier 2): Episodic Session Tree Manager & Markdown/JSON Session Exporter (`/export`).
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

from ..kernel.checkpoints import CheckpointStore, TurnNode, get_checkpoint_store


class SessionMemoryManager:
    """High-level episodic session tree wrapper with auto-compaction & Markdown/JSON export."""

    AUTO_COMPACT_THRESHOLD = 26

    def __init__(self, store: Optional[CheckpointStore] = None) -> None:
        self.store = store or get_checkpoint_store()

    def record_exchange(
        self,
        session_id: str,
        user_query: str,
        assistant_response: str,
        agent_used: str = "",
        mode: str = "",
        citations: Optional[List[Dict[str, Any]]] = None,
        artifacts: Optional[List[str]] = None,
        run_id: str = "",
    ) -> tuple[TurnNode, TurnNode]:
        u_node = self.store.append_turn(
            session_id=session_id,
            role="user",
            content=user_query,
            run_id=run_id,
        )
        a_node = self.store.append_turn(
            session_id=session_id,
            role="assistant",
            content=assistant_response,
            agent_used=agent_used,
            mode=mode,
            citations=citations,
            artifacts=artifacts,
            run_id=run_id,
        )
        lineage = self.store.get_branch_lineage(session_id, limit=40)
        if len(lineage) >= self.AUTO_COMPACT_THRESHOLD:
            try:
                from ..context.compactor import compact_session_history

                compact_session_history(session_id, keep_recent_nodes=8, store=self.store)
            except Exception:
                pass
        return u_node, a_node

    def export_session(self, session_id: str, fmt: str = "markdown") -> str:
        sess = self.store.ensure_session(session_id)
        lineage = self.store.get_branch_lineage(session_id, limit=500)
        summary = self.store.get_compacted_summary(session_id)

        if fmt.lower() == "json":
            return json.dumps(
                {
                    "session_id": session_id,
                    "active_branch": sess["active_branch"],
                    "exported_at": time.time(),
                    "compacted_summary": summary,
                    "turns": [t.to_dict() for t in lineage],
                },
                indent=2,
                ensure_ascii=False,
            )

        lines = [
            f"# SLMAgents Session Export — `{session_id}` (branch: `{sess['active_branch']}`)",
            "",
        ]
        if summary:
            lines.extend(["## Compacted Episodic Summary", summary, ""])

        for idx, t in enumerate(lineage, start=1):
            header = f"### {idx}. {t.role.capitalize()}"
            if t.agent_used:
                header += f" (`{t.agent_used}` | `{t.mode}`)"
            lines.append(header)
            lines.append(t.content)
            if t.citations:
                cites = ", ".join(
                    f"[{c.get('citation_id', i + 1)}] {c.get('source', '')}"
                    for i, c in enumerate(t.citations)
                )
                lines.append(f"\n*Citations:* {cites}")
            lines.append("")
        return "\n".join(lines)


_GLOBAL_SESSION_MEM: Optional[SessionMemoryManager] = None


def get_session_memory() -> SessionMemoryManager:
    global _GLOBAL_SESSION_MEM
    if _GLOBAL_SESSION_MEM is None:
        _GLOBAL_SESSION_MEM = SessionMemoryManager()
    return _GLOBAL_SESSION_MEM
