"""MCP prompts: atajos listos para el usuario.

Un cliente MCP los muestra como comandos (en Claude Code: `/mcp__packet-tracer__ui_on`).
Sirven para que el usuario le diga al asistente "mostrá todo en las ventanas
de PT" o "volvé a trabajar en segundo plano" sin tener que redactarlo.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_prompts(mcp: FastMCP) -> None:
    @mcp.prompt(
        name="ui_on",
        description="Packet Tracer: show device actions in PT's real windows (CLI tab, Desktop apps) for screenshots",
    )
    def ui_on() -> str:
        return (
            "Switch the Packet Tracer MCP to UI mode: call pt_ui_mode(mode='ui') now. From here on, "
            "when you use pt_cli, pt_host_command, pt_host_ip_config or other device-panel tools, "
            "let them open the device's window on the matching tab/app (CLI, Desktop > Command "
            "Prompt, Desktop > IP Configuration, Services...) so I can see it. When I ask for a "
            "screenshot, use capture=True or pt_ui_capture and tell me the saved file path. Never "
            "take over my screen or mouse; everything goes through the MCP tools."
        )

    @mcp.prompt(
        name="ui_off",
        description="Packet Tracer: work headless again (API only, no windows opened)",
    )
    def ui_off() -> str:
        return (
            "Switch the Packet Tracer MCP back to headless mode: call pt_ui_mode(mode='headless'). "
            "Keep doing everything through the MCP tools without opening device windows."
        )

    @mcp.prompt(
        name="ui_status",
        description="Packet Tracer: report whether device actions run headless or in PT's windows",
    )
    def ui_status() -> str:
        return "Call pt_ui_mode(mode='status') and tell me the current Packet Tracer UI mode."
