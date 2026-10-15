"""CODEMAP.md is generated; it must match the code so an agent can trust it."""

from __future__ import annotations

from pathlib import Path

from src.packet_tracer_mcp.devtools.codemap import render


def test_codemap_is_current():
    assert Path("CODEMAP.md").read_text(encoding="utf-8") == render(), (
        "CODEMAP.md is stale: run python -m src.packet_tracer_mcp.devtools.codemap"
    )


def test_codemap_is_small():
    assert len(render()) <= 6000


def test_every_tool_is_indexed():
    import asyncio

    from src.packet_tracer_mcp.server import mcp

    text = render()
    for tool in asyncio.run(mcp.list_tools()):
        assert tool.name in text, tool.name
