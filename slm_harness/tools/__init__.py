"""
Plane 2: Universal Governed Tool Registry, Top-K Semantic Tool Selector, Built-in Tools & MCP Bridge.
"""

from .builtin_tools import register_builtin_tools
from .mcp_bridge import MCPBridge, get_mcp_bridge
from .registry import (
    ToolCallResult,
    ToolExecutionContext,
    ToolRegistry,
    ToolSpec,
    get_tool_registry,
)
from .web_search_engine import (
    build_smart_web_query,
    live_web_search,
    should_run_web_search,
)

__all__ = [
    "MCPBridge",
    "ToolCallResult",
    "ToolExecutionContext",
    "ToolRegistry",
    "ToolSpec",
    "build_smart_web_query",
    "get_mcp_bridge",
    "get_tool_registry",
    "live_web_search",
    "register_builtin_tools",
    "should_run_web_search",
]
