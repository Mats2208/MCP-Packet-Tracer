"""
Registry of MCP Resources.

Defines static resources that the LLM can query.
"""

from __future__ import annotations
from mcp.server.fastmcp import FastMCP

from ...infrastructure.catalog.devices import ALL_MODELS
from ...infrastructure.catalog.cables import CABLE_TYPES
from ...infrastructure.catalog.aliases import MODEL_ALIASES
from ...infrastructure.catalog.templates import list_templates
from ...shared.constants import CAPABILITIES
from ...shared.utils import reply_json
from ...settings import GUIDE


def register_resources(mcp: FastMCP) -> None:
    """Registers all resources on the MCP server."""

    @mcp.resource("pt://catalog/devices")
    def resource_device_catalog() -> str:
        """Full catalog of devices available in Packet Tracer."""
        catalog = {}
        for name, model in ALL_MODELS.items():
            catalog[name] = {
                "display_name": model.display_name,
                "category": model.category,
                "ports": [p.full_name for p in model.ports],
            }
        return reply_json(catalog)

    @mcp.resource("pt://catalog/cables")
    def resource_cable_catalog() -> str:
        """Cable types available in Packet Tracer."""
        return reply_json(CABLE_TYPES)

    @mcp.resource("pt://catalog/aliases")
    def resource_aliases() -> str:
        """Common aliases for device models."""
        return reply_json(MODEL_ALIASES)

    @mcp.resource("pt://catalog/templates")
    def resource_templates() -> str:
        """Available topology templates with descriptions."""
        templates = list_templates()
        data = []
        for t in templates:
            data.append({
                "name": t.name,
                "key": t.key.value,
                "description": t.description,
                "routers": f"{t.min_routers}-{t.max_routers}",
                "default_routing": t.default_routing.value,
                "tags": list(t.tags),
            })
        return reply_json(data)

    @mcp.resource("pt://guide")
    def resource_guide() -> str:
        """Full usage guide: every rule, verified PT API fact and recipe (the short
        server instructions point here)."""
        return GUIDE

    @mcp.resource("pt://capabilities")
    async def resource_capabilities() -> str:
        """Server capabilities and version.

        DERIVED from the live tool registry: instead of keeping a hand-maintained list
        that can drift (it used to say nat="unsupported" while pt_apply_nat
        existed), it introspects the tools that are actually registered and reports
        the support of each feature based on whether its tool exists. If introspection
        fails for any reason, it falls back to the static CAPABILITIES values.
        """
        caps = dict(CAPABILITIES)
        try:
            tools = await mcp.list_tools()
            names = sorted(t.name for t in tools)
            caps["tools_count"] = len(names)
            caps["tools"] = names
            # Feature → support derived from whether the tool actually exists (no drift possible)
            caps["supported_live"] = {
                "nat": any(n.startswith("pt_apply_nat") for n in names),
                "acl": any(n.startswith("pt_apply_acl") for n in names),
                "modules": any("module" in n for n in names),
                "live_deploy": "pt_live_deploy" in names,
                "raw_js": "pt_send_raw" in names,
                # DHCP on a Server-PT (Services > DHCP); a router's goes through the CLI.
                "dhcp_server_pools": "pt_configure_dhcp_server" in names,
            }
        except Exception:
            pass
        return reply_json(caps)
