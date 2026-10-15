"""Shared helpers for tests that guard the MCP tool layer.

Many tools are closures registered inside a `register(...)` function, so
several tests check them by reading their source. Those tests must not care
which module a tool lives in: `registry_source()` returns all of it.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

ADAPTERS = Path("src/packet_tracer_mcp/adapters/mcp")


def registry_source() -> str:
    """Source of tool_registry.py + bridge_context.py + tools/*.py (registration order)."""
    parts = [ADAPTERS / "tool_registry.py", ADAPTERS / "bridge_context.py"]
    registry = importlib.import_module("src.packet_tracer_mcp.adapters.mcp.tool_registry")
    for name in getattr(registry, "TOOL_MODULES", ()):
        parts.append(ADAPTERS / "tools" / f"{name}.py")
    return "\n".join(p.read_text(encoding="utf-8") for p in parts if p.exists())


def tool_api() -> dict[str, dict]:
    """name → {description, inputSchema} for every tool the server exposes."""
    server = importlib.import_module("src.packet_tracer_mcp.server")
    tools = asyncio.run(server.mcp.list_tools())
    return {t.name: {"description": t.description, "inputSchema": t.inputSchema} for t in tools}
