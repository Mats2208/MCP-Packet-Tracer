"""Exports must not land wherever the MCP client happened to start the server.

Seen 2026-10-25: tools write to relative folders ("projects", "screenshots"), so a
server launched by uvx from Claude Desktop could write into / or the user's cwd.
"""

from __future__ import annotations

from pathlib import Path

from src.packet_tracer_mcp import server


def test_default_workdir_is_in_home(monkeypatch, tmp_path):
    monkeypatch.delenv("PT_MCP_WORKDIR", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert server.workdir() == tmp_path / "PacketTracerMCP"


def test_env_overrides_workdir(monkeypatch, tmp_path):
    monkeypatch.setenv("PT_MCP_WORKDIR", str(tmp_path / "labs"))
    assert server.workdir() == tmp_path / "labs"


def test_main_enters_workdir_before_serving():
    src = Path("src/packet_tracer_mcp/server.py").read_text(encoding="utf-8")
    body = src[src.index("def main("):]
    assert body.index("os.chdir(") < body.index("mcp.run(")


def test_main_starts_the_bridge_before_serving():
    """So PT's panel says "online" as soon as the agent starts, not after the first tool
    call (the panel read "offline" until then — screenshot 2026-10-25)."""
    src = Path("src/packet_tracer_mcp/server.py").read_text(encoding="utf-8")
    body = src[src.index("def main("):]
    assert body.index("BRIDGE.ensure_bridge(grace=False)") < body.index("mcp.run(")
