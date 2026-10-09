"""
MCP tool registry.

Registers every tool the LLM can call. The tools live in `tools/<topic>.py`
(one `register(mcp, ctx)` each) and share one BridgeContext; the device-panel
tools live in `device_panel_tools.py` / `desktop_service_tools.py`.
"""

from __future__ import annotations

import importlib
import inspect

from mcp.server.fastmcp import FastMCP

from .bridge_context import BridgeContext
from .device_panel_tools import register_device_panel_tools

# Registration order of tools/<name>.py.
TOOL_MODULES: tuple[str, ...] = ("planning", "live", "topology", "modules", "acl", "nat", "switching", "security", "inspect", "simulation", "canvas", "project", "services",)


def register_tools(mcp: FastMCP, ctx: BridgeContext | None = None) -> None:
    """Register every tool on the MCP server."""
    ctx = ctx or BridgeContext()
    for name in TOOL_MODULES:
        importlib.import_module(f"{__package__}.tools.{name}").register(mcp, ctx)
    register_device_panel_tools(
        mcp, send_and_wait=ctx.send_and_wait, check_bridge=ctx.check_bridge,
    )
    # Descriptions come from docstrings, which Python 3.13+ dedents and 3.11/3.12
    # do not: normalize once so every interpreter serves the same (shorter) text.
    for tool in mcp._tool_manager.list_tools():
        tool.description = inspect.cleandoc(tool.description or "")
