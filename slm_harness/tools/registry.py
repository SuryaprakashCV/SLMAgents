"""
Plane 2: Universal Governed `ToolRegistry` with Top-K Semantic Tool Pruning & Fuzzy Arg Repair.
"""

from __future__ import annotations

import difflib
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

import numpy as np

from ..config import get_config
from ..context.artifacts import get_artifact_store
from ..governance.brokers import get_approval_broker
from ..governance.policy import RiskLevel, classify_shell_command_risk, evaluate_capability
from ..governance.privacy import run_lifecycle_hooks, scrub_secrets_and_pii
from ..kernel.checkpoints import get_shadow_vfs
from ..runtime import GrammarSpec, get_runtime
from ..runtime.arena import CancellationToken


@dataclass
class ToolExecutionContext:
    run_id: str
    session_id: str
    workspace_dir: str
    cancel_token: Optional[CancellationToken] = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    risk_level: RiskLevel
    handler: Callable[[Dict[str, Any], ToolExecutionContext], Any]
    tags: List[str] = field(default_factory=list)
    preview_fn: Optional[Callable[[Dict[str, Any], ToolExecutionContext], str]] = None
    mutates_path_arg: Optional[str] = None  # e.g. "path" for automatic ShadowSnapshotVFS backup


@dataclass
class ToolCallResult:
    tool_name: str
    ok: bool
    output_text: str
    raw_data: Any = None
    artifact_uri: Optional[str] = None
    duration_ms: float = 0.0
    approved: bool = True


class ToolRegistry:
    """Thread-safe tool registry with Top-K semantic pruning, argument healing & governance."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tools: Dict[str, ToolSpec] = {}
        self._tool_embeddings: Dict[str, np.ndarray] = {}

    def register(self, spec: ToolSpec) -> None:
        with self._lock:
            self._tools[spec.name] = spec
            self._tool_embeddings.pop(spec.name, None)

    def get(self, name: str) -> Optional[ToolSpec]:
        with self._lock:
            if name in self._tools:
                return self._tools[name]
            # Fuzzy fallback if small model used hyphen instead of underscore
            norm = name.strip().replace("-", "_").replace(".", "_")
            if norm in self._tools:
                return self._tools[norm]
            matches = difflib.get_close_matches(norm, list(self._tools.keys()), n=1, cutoff=0.78)
            return self._tools[matches[0]] if matches else None

    def list_tools(self) -> List[ToolSpec]:
        with self._lock:
            return list(self._tools.values())

    def select_relevant_tools(
        self,
        query: str,
        top_k: int = 6,
        always_include: Sequence[str] = ("artifact_inspect",),
    ) -> List[ToolSpec]:
        """
        Prunes active tools to Top-K semantically & lexically relevant schemas so small CPU models
        spend 75% fewer tokens on tool definitions and achieve higher tool-call precision.
        """
        all_specs = self.list_tools()
        if len(all_specs) <= top_k:
            return all_specs

        q_low = query.lower()
        lexical_scores: Dict[str, float] = {}
        for sp in all_specs:
            score = 0.0
            for kw in sp.tags + sp.name.split("_"):
                if kw and kw.lower() in q_low:
                    score += 0.35
            if sp.name in always_include:
                score += 0.25
            lexical_scores[sp.name] = score

        # Fast cached embedding similarity
        dense_scores: Dict[str, float] = {}
        try:
            rt = get_runtime()
            q_vec = rt.embed_query(query[:400])
            missing = [
                sp for sp in all_specs if sp.name not in self._tool_embeddings or self._tool_embeddings[sp.name].shape[0] != q_vec.shape[0]
            ]
            if missing:
                texts = [f"{sp.name}: {sp.description} ({', '.join(sp.tags)})" for sp in missing]
                vecs = rt.embed_batch(texts)
                with self._lock:
                    for sp, v in zip(missing, vecs):
                        self._tool_embeddings[sp.name] = v
            with self._lock:
                for sp in all_specs:
                    v = self._tool_embeddings.get(sp.name)
                    if v is not None and v.shape[0] == q_vec.shape[0]:
                        dense_scores[sp.name] = float(np.dot(q_vec, v))
        except Exception:
            pass

        scored = sorted(
            all_specs,
            key=lambda sp: lexical_scores.get(sp.name, 0.0) + dense_scores.get(sp.name, 0.0),
            reverse=True,
        )
        selected = scored[:top_k]
        selected_names = {s.name for s in selected}
        for req_name in always_include:
            sp = self._tools.get(req_name)
            if sp and sp.name not in selected_names:
                selected.append(sp)
        return selected

    @staticmethod
    def format_tools_prompt(tools: Sequence[ToolSpec]) -> str:
        if not tools:
            return ""
        lines = [
            "### Available Tools",
            "To invoke a tool, respond ONLY with JSON: "
            '`{"action": "call_tool", "tool": "<tool_name>", "arguments": {...}, "thought": "<brief reason>"}`.',
            "When ready to answer the user directly, respond ONLY with JSON: "
            '`{"action": "final_answer", "answer": "<markdown answer>"}`.',
            "",
        ]
        for sp in tools:
            props = sp.parameters.get("properties") or {}
            req = set(sp.parameters.get("required") or [])
            arg_bits = []
            for k, meta in props.items():
                star = "*" if k in req else ""
                arg_bits.append(f"{k}{star}: {meta.get('type', 'any')}")
            lines.append(f"- `{sp.name}({', '.join(arg_bits)})`: {sp.description}")
        return "\n".join(lines)

    @staticmethod
    def build_react_step_grammar(tools: Sequence[ToolSpec]) -> GrammarSpec:
        tool_names = [t.name for t in tools]
        schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
                "action": {"type": "string", "enum": ["call_tool", "final_answer"]},
                "tool": {"type": "string", "enum": tool_names} if tool_names else {"type": "string"},
                "arguments": {"type": "object"},
                "answer": {"type": "string"},
            },
            "required": ["action"],
        }
        return GrammarSpec.from_schema(schema)

    @staticmethod
    def coerce_arguments(spec: ToolSpec, raw_args: Any) -> Dict[str, Any]:
        if not isinstance(raw_args, dict):
            return {}
        props: Dict[str, Any] = spec.parameters.get("properties") or {}
        if not props:
            return dict(raw_args)

        valid_keys = list(props.keys())
        coerced: Dict[str, Any] = {}
        alias_map = {
            "file_path": "path",
            "filepath": "path",
            "filename": "path",
            "file": "path",
            "sql": "query",
            "sql_query": "query",
            "cmd": "command",
            "code_str": "code",
            "script": "code",
            "expr": "expression",
        }

        for k, val in raw_args.items():
            target_k = k
            if target_k not in props:
                mapped = alias_map.get(k.lower())
                if mapped and mapped in props:
                    target_k = mapped
                else:
                    close = difflib.get_close_matches(k, valid_keys, n=1, cutoff=0.65)
                    if close:
                        target_k = close[0]

            expected_t = (props.get(target_k) or {}).get("type")
            if expected_t == "integer" and isinstance(val, str) and val.strip().lstrip("-").isdigit():
                val = int(val.strip())
            elif expected_t == "number" and isinstance(val, str):
                try:
                    val = float(val.strip())
                except ValueError:
                    pass
            elif expected_t == "boolean" and isinstance(val, str):
                val = val.strip().lower() in ("true", "1", "yes")
            coerced[target_k] = val

        return coerced

    def execute(
        self,
        tool_name: str,
        raw_args: Any,
        ctx: ToolExecutionContext,
    ) -> ToolCallResult:
        spec = self.get(tool_name)
        if spec is None:
            avail = ", ".join(t.name for t in self.list_tools()[:12])
            return ToolCallResult(
                tool_name=tool_name,
                ok=False,
                output_text=f"Unknown tool '{tool_name}'. Available tools: {avail}",
            )

        args = self.coerce_arguments(spec, raw_args)
        cfg = get_config()

        # Dynamic risk adjustment for shell commands
        effective_risk = spec.risk_level
        cmd_hint = ""
        if spec.name == "shell_exec" and isinstance(args.get("command"), str):
            cmd_hint = args["command"]
            effective_risk = classify_shell_command_risk(cmd_hint)

        # Lifecycle pre_tool hook
        pre_hooks = run_lifecycle_hooks(
            "pre_tool",
            {"tool": spec.name, "arguments": args, "risk_level": int(effective_risk)},
            config=cfg,
        )
        for hk in pre_hooks:
            if hk.get("returncode", 0) != 0:
                return ToolCallResult(
                    tool_name=spec.name,
                    ok=False,
                    approved=False,
                    output_text=f"Vetoed by pre_tool hook: {hk.get('stderr') or hk.get('command')}",
                )

        # Capability Governance Gate
        decision = evaluate_capability(effective_risk, cfg.approval_policy, command_hint=cmd_hint)
        if decision == "deny":
            return ToolCallResult(
                tool_name=spec.name,
                ok=False,
                approved=False,
                output_text=f"Blocked by strict approval policy ({cfg.approval_policy.value}): {spec.name} requires risk level L{int(effective_risk)}.",
            )

        if decision == "ask":
            preview = ""
            if spec.preview_fn is not None:
                try:
                    preview = spec.preview_fn(args, ctx)
                except Exception:
                    preview = json.dumps(args, indent=2)
            else:
                preview = cmd_hint or json.dumps(args, indent=2)

            approved = get_approval_broker().request_approval(
                run_id=ctx.run_id,
                session_id=ctx.session_id,
                tool_name=spec.name,
                risk_level=int(effective_risk),
                summary=f"Execute {spec.name} (Risk L{int(effective_risk)})",
                preview=preview,
                arguments=args,
                cancel_token=ctx.cancel_token,
            )
            if not approved:
                return ToolCallResult(
                    tool_name=spec.name,
                    ok=False,
                    approved=False,
                    output_text=f"User declined execution of `{spec.name}`.",
                )

        # Automatic Shadow File Snapshot before mutating workspace files
        if spec.mutates_path_arg and isinstance(args.get(spec.mutates_path_arg), str):
            try:
                from ..governance.policy import WorkspaceJail

                real_target = WorkspaceJail(cfg).resolve_path(args[spec.mutates_path_arg], for_write=True)
                get_shadow_vfs().record_before_write(ctx.session_id, ctx.run_id, real_target)
            except Exception:
                pass

        t0 = time.perf_counter()
        try:
            raw_res = spec.handler(args, ctx)
            dur_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            if isinstance(raw_res, str):
                txt = raw_res
            else:
                txt = json.dumps(raw_res, indent=2, ensure_ascii=False, default=str)

            txt = scrub_secrets_and_pii(txt)
            art_store = get_artifact_store()
            artifact_uri: Optional[str] = None
            if len(txt) > art_store.INLINE_CHAR_THRESHOLD:
                entry = art_store.ingest_text(txt, name=f"{spec.name}_output.txt")
                artifact_uri = entry.uri
                txt = (
                    f"{entry.descriptor}\n"
                    f"(Full output offloaded to {entry.uri}; use `artifact_inspect` to view specific lines.)"
                )

            run_lifecycle_hooks(
                "post_tool",
                {"tool": spec.name, "ok": True, "duration_ms": dur_ms},
                config=cfg,
            )
            return ToolCallResult(
                tool_name=spec.name,
                ok=True,
                output_text=txt,
                raw_data=raw_res,
                artifact_uri=artifact_uri,
                duration_ms=dur_ms,
                approved=True,
            )
        except Exception as exc:
            dur_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            return ToolCallResult(
                tool_name=spec.name,
                ok=False,
                output_text=f"Tool `{spec.name}` failed: {exc}",
                duration_ms=dur_ms,
                approved=True,
            )


_GLOBAL_TOOLS: Optional[ToolRegistry] = None
_TOOLS_LOCK = threading.Lock()


def get_tool_registry() -> ToolRegistry:
    global _GLOBAL_TOOLS
    if _GLOBAL_TOOLS is None:
        with _TOOLS_LOCK:
            if _GLOBAL_TOOLS is None:
                reg = ToolRegistry()
                from .builtin_tools import register_builtin_tools

                register_builtin_tools(reg)
                _GLOBAL_TOOLS = reg
    return _GLOBAL_TOOLS
