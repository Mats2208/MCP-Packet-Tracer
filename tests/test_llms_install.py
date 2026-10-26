"""llms-install.md is what an agent follows to install the MCP; keep its commands honest."""

from __future__ import annotations

from pathlib import Path

DOC = Path("llms-install.md").read_text(encoding="utf-8")


def test_windows_registration_includes_the_ui_extra():
    """Without [ui] (comtypes) UI mode and window captures fail on Windows, which is the
    only platform that has them (found on the Windows run, 2026-10-26)."""
    assert '"--" uvx --from "packet-tracer-mcp[ui]" packet-tracer-mcp --stdio' in DOC


def test_skill_install_command_is_documented():
    assert "uvx packet-tracer-mcp install-skill" in DOC
