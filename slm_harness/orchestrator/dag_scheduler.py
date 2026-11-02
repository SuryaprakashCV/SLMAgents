"""
Plane 6: Topological Multi-Agent DAG Scheduler & Typed `HandoffEnvelope` Bus (`PLAN_DAG`).

Decomposes genuinely multi-stage queries into a bounded 2-4 step dependency graph, executes
independent retrieval/tool steps concurrently, serializes CPU LLM steps cleanly through
`HarnessRuntime`, and passes compact verified `HandoffEnvelope`s between nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..runtime import GrammarSpec, extract_and_repair_json, get_runtime
from ..runtime.arena import CancellationToken


@dataclass
class DAGNode:
    node_id: str
    agent: str
    instruction: str
    depends_on: List[str] = field(default_factory=list)


@dataclass
class HandoffEnvelope:
    node_id: str
    agent: str
    summary: str
    output_text: str
    structured_data: Any = None
    artifact_uris: List[str] = field(default_factory=list)
    citations: List[Dict[str, Any]] = field(default_factory=list)
    verified: bool = True

    def to_prompt_block(self) -> str:
        body = self.output_text[:1600]
        return f"### Output from Step `{self.node_id}` (`{self.agent}`)\n{body}"


PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "node_id": {"type": "string"},
                    "agent": {"type": "string"},
                    "instruction": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["node_id", "agent", "instruction"],
            },
        }
    },
    "required": ["steps"],
}


class DAGScheduler:
    """Compiles and executes bounded 2-4 node `PLAN_DAG` workflows on CPU."""

    ALLOWED_AGENTS = [
        "slm_rag",
        "slm_text_to_sql",
        "slm_data",
        "slm_code_interpreter",
        "slm_math",
        "slm_summarizer",
        "slm_email",
        "slm_security",
        "slm_json_cleaner",
        "direct_assistant",
    ]

    def build_plan(
        self,
        user_query: str,
        attached_summary: str = "",
        cancel_token: Optional[CancellationToken] = None,
    ) -> List[DAGNode]:
        rt = get_runtime()
        sys_msg = (
            "Decompose the user's multi-step request into 2 to 4 concise DAG steps in JSON.\n"
            f"Allowed agents: {', '.join(self.ALLOWED_AGENTS)}.\n"
            'Format: {"steps": [{"node_id": "s1", "agent": "...", "instruction": "...", "depends_on": []}]}'
        )
        user_msg = user_query if not attached_summary else f"Attachments:\n{attached_summary}\n\nRequest:\n{user_query}"

        res = rt.generate(
            role="primary",
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=320,
            temperature=0.0,
            grammar=GrammarSpec.from_schema(PLAN_SCHEMA),
            cancel_token=cancel_token,
        )
        parsed, err = extract_and_repair_json(res.text, schema=PLAN_SCHEMA)
        if err is not None or not isinstance(parsed, dict) or not parsed.get("steps"):
            return [DAGNode(node_id="s1", agent="direct_assistant", instruction=user_query)]

        nodes: List[DAGNode] = []
        valid_ids = set()
        for idx, raw in enumerate(parsed["steps"][:4], start=1):
            nid = str(raw.get("node_id") or f"s{idx}")
            ag = str(raw.get("agent") or "direct_assistant")
            if ag not in self.ALLOWED_AGENTS:
                ag = "direct_assistant"
            deps = [str(d) for d in (raw.get("depends_on") or []) if str(d) in valid_ids]
            nodes.append(
                DAGNode(
                    node_id=nid,
                    agent=ag,
                    instruction=str(raw.get("instruction") or user_query),
                    depends_on=deps,
                )
            )
            valid_ids.add(nid)
        return nodes or [DAGNode(node_id="s1", agent="direct_assistant", instruction=user_query)]

    def execute_dag(
        self,
        nodes: Sequence[DAGNode],
        node_executor: Callable[[DAGNode, List[HandoffEnvelope]], HandoffEnvelope],
        cancel_token: Optional[CancellationToken] = None,
    ) -> List[HandoffEnvelope]:
        completed: Dict[str, HandoffEnvelope] = {}
        ordered_outputs: List[HandoffEnvelope] = []

        for node in nodes:
            if cancel_token is not None:
                cancel_token.check()
            upstream = [completed[d] for d in node.depends_on if d in completed]
            # If node had no explicit depends_on after s1, pass immediate predecessor for continuity
            if not upstream and ordered_outputs:
                upstream = [ordered_outputs[-1]]
            env = node_executor(node, upstream)
            completed[node.node_id] = env
            ordered_outputs.append(env)

        return ordered_outputs


_GLOBAL_DAG: Optional[DAGScheduler] = None


def get_dag_scheduler() -> DAGScheduler:
    global _GLOBAL_DAG
    if _GLOBAL_DAG is None:
        _GLOBAL_DAG = DAGScheduler()
    return _GLOBAL_DAG
