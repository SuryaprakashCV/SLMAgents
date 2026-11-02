"""
Plane 2 & Plane 9: Native JSON-RPC 2.0 Stdio MCP Server (`./slm mcp-serve`).

Exposes the SLMAgents Pure-CPU Harness (`slm_ask`, `slm_sql`, `slm_rag_search`, `slm_sympy`,
and all safe workspace/analytical tools) over the Model Context Protocol so external editors,
terminals, and orchestrators can invoke local CPU intelligence with zero cloud calls.
"""

from __future__ import annotations

import json
import sys
import uuid
from typing import Any, Dict, List

from ..config import get_config
from ..governance.policy import RiskLevel
from ..kernel import get_kernel
from ..tools.registry import ToolExecutionContext, get_tool_registry


def _build_exposed_mcp_tools() -> List[Dict[str, Any]]:
    reg = get_tool_registry()
    tools: List[Dict[str, Any]] = [
        {
            "name": "slm_ask_specialist",
            "description": "Route a natural language, SQL, coding, RAG, or math query through the local Pure-CPU SLMAgents Harness.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "User question or task"},
                    "agent": {"type": "string", "description": "Optional specialist name (e.g. slm_text_to_sql, slm_math, auto)"},
                },
                "required": ["query"],
            },
        }
    ]
    for sp in reg.list_tools():
        if sp.risk_level <= RiskLevel.L2_SANDBOX_COMPUTE and not sp.name.startswith("mcp__"):
            tools.append(
                {
                    "name": sp.name,
                    "description": sp.description,
                    "inputSchema": sp.parameters,
                }
            )
    return tools


def serve_mcp_stdio() -> None:
    """Runs blocking newline-delimited JSON-RPC 2.0 MCP loop on `sys.stdin` / `sys.stdout`."""
    # Redirect stray print logs to stderr so stdout remains pure JSON-RPC
    orig_stdout = sys.stdout
    sys.stdout = sys.stderr

    cfg = get_config()
    reg = get_tool_registry()
    kernel = get_kernel()

    def _send(obj: Dict[str, Any]) -> None:
        orig_stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        orig_stdout.flush()

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method", "")
        req_id = req.get("id")
        params = req.get("params") or {}

        if req_id is None:
            # Notification (e.g. notifications/initialized)
            continue

        try:
            if method == "initialize":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "slmagents-cpu-harness", "version": "1.0.0"},
                        },
                    }
                )
            elif method == "tools/list":
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {"tools": _build_exposed_mcp_tools()},
                    }
                )
            elif method == "tools/call":
                tname = str(params.get("name") or "")
                targs = params.get("arguments") or {}
                if tname == "slm_ask_specialist":
                    out = kernel.run_turn(
                        user_query=str(targs.get("query") or ""),
                        session_id="mcp_session",
                        pinned_agent=targs.get("agent"),
                    )
                    text_out = out.final_text
                else:
                    ctx = ToolExecutionContext(
                        run_id=f"mcp_{uuid.uuid4().hex[:8]}",
                        session_id="mcp_session",
                        workspace_dir=cfg.workspace_dir,
                    )
                    res = reg.execute(tname, targs, ctx)
                    text_out = res.output_text

                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [{"type": "text", "text": text_out}],
                            "isError": False,
                        },
                    }
                )
            elif method == "ping":
                _send({"jsonrpc": "2.0", "id": req_id, "result": {}})
            else:
                _send(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"},
                    }
                )
        except Exception as exc:
            _send(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
            )
