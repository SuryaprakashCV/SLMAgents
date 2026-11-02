"""
Plane 2: Zero-Dependency Model Context Protocol (MCP) JSON-RPC 2.0 Stdio Client Bridge.

Connects to external MCP servers configured in `.slm/mcp.json` or `~/.slm/mcp.json`,
performs `initialize` + `tools/list`, and registers discovered tools into `ToolRegistry`
under `mcp__<server>__<tool>` governed by `RiskLevel.L3_WORKSPACE_MUTATE`.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Any, Dict, List, Optional

from ..config import HarnessConfig, get_config
from ..governance.policy import RiskLevel
from .registry import ToolExecutionContext, ToolRegistry, ToolSpec, get_tool_registry


class MCPStdioClient:
    """Lightweight JSON-RPC 2.0 newline-delimited stdio client for an external MCP server."""

    def __init__(
        self,
        server_name: str,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> None:
        self.server_name = server_name
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._proc: Optional[subprocess.Popen[str]] = None
        self._lock = threading.Lock()
        self._req_id = 0

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        merged_env = dict(os.environ)
        merged_env.update(self.env)
        self._proc = subprocess.Popen(
            [self.command] + self.args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=merged_env,
        )
        self.request(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "slm-cpu-harness", "version": "1.0.0"},
            },
        )
        self.notify("notifications/initialized", {})

    def notify(self, method: str, params: Dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            return
        msg = json.dumps({"jsonrpc": "2.0", "method": method, "params": params})
        self._proc.stdin.write(msg + "\n")
        self._proc.stdin.flush()

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self.start()
            assert self._proc is not None and self._proc.stdin is not None and self._proc.stdout is not None
            self._req_id += 1
            rid = self._req_id
            payload = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
            self._proc.stdin.write(json.dumps(payload) + "\n")
            self._proc.stdin.flush()

            while True:
                line = self._proc.stdout.readline()
                if not line:
                    raise RuntimeError(f"MCP server '{self.server_name}' closed stdout unexpectedly")
                line = line.strip()
                if not line:
                    continue
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if resp.get("id") == rid:
                    if "error" in resp:
                        raise RuntimeError(f"MCP error from {self.server_name}: {resp['error']}")
                    return resp.get("result") or {}

    def list_tools(self) -> List[Dict[str, Any]]:
        res = self.request("tools/list", {})
        tools = res.get("tools")
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        res = self.request("tools/call", {"name": name, "arguments": arguments})
        content = res.get("content")
        if isinstance(content, list):
            texts = [c.get("text", str(c)) for c in content if isinstance(c, dict)]
            return "\n".join(texts) if texts else res
        return res

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None


class MCPBridge:
    """Discovers and registers `.slm/mcp.json` server tools into `ToolRegistry`."""

    def __init__(self, config: Optional[HarnessConfig] = None) -> None:
        self.config = config or get_config()
        self.clients: Dict[str, MCPStdioClient] = {}
        self.connected_tools: List[str] = []

    def sync_configured_servers(self, registry: Optional[ToolRegistry] = None) -> List[str]:
        reg = registry or get_tool_registry()
        newly_registered: List[str] = []
        for sname, scfg in (self.config.mcp_servers or {}).items():
            if sname in self.clients:
                continue
            cmd = scfg.get("command")
            if not cmd:
                continue
            client = MCPStdioClient(
                server_name=sname,
                command=str(cmd),
                args=[str(a) for a in (scfg.get("args") or [])],
                env={str(k): str(v) for k, v in (scfg.get("env") or {}).items()},
            )
            try:
                client.start()
                tools = client.list_tools()
                self.clients[sname] = client
                for t in tools:
                    orig_name = str(t.get("name", ""))
                    if not orig_name:
                        continue
                    fq_name = f"mcp__{sname}__{orig_name}"

                    def _make_handler(c: MCPStdioClient, tn: str):
                        def _h(args: Dict[str, Any], _ctx: ToolExecutionContext) -> Any:
                            return c.call_tool(tn, args)

                        return _h

                    reg.register(
                        ToolSpec(
                            name=fq_name,
                            description=f"[MCP:{sname}] {t.get('description', orig_name)}",
                            parameters=t.get("inputSchema") or {"type": "object", "properties": {}},
                            risk_level=RiskLevel.L3_WORKSPACE_MUTATE,
                            handler=_make_handler(client, orig_name),
                            tags=["mcp", sname, orig_name],
                        )
                    )
                    newly_registered.append(fq_name)
                    self.connected_tools.append(fq_name)
            except Exception:
                client.close()
        return newly_registered

    def close_all(self) -> None:
        for c in self.clients.values():
            c.close()
        self.clients.clear()


_GLOBAL_MCP: Optional[MCPBridge] = None


def get_mcp_bridge() -> MCPBridge:
    global _GLOBAL_MCP
    if _GLOBAL_MCP is None:
        _GLOBAL_MCP = MCPBridge()
    return _GLOBAL_MCP
