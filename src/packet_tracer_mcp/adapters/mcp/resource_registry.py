"""
Registro de MCP Resources.

Define recursos estáticos que el LLM puede consultar.
"""

from __future__ import annotations
import json
from mcp.server.fastmcp import FastMCP

from ...infrastructure.catalog.devices import ALL_MODELS
from ...infrastructure.catalog.cables import CABLE_TYPES
from ...infrastructure.catalog.aliases import MODEL_ALIASES
from ...infrastructure.catalog.templates import list_templates
from ...shared.constants import CAPABILITIES


def register_resources(mcp: FastMCP) -> None:
    """Registra todos los resources en el servidor MCP."""

    @mcp.resource("pt://catalog/devices")
    def resource_device_catalog() -> str:
        """Catálogo completo de dispositivos disponibles en Packet Tracer."""
        catalog = {}
        for name, model in ALL_MODELS.items():
            catalog[name] = {
                "display_name": model.display_name,
                "category": model.category,
                "ports": [p.full_name for p in model.ports],
            }
        return json.dumps(catalog, indent=2, ensure_ascii=False)

    @mcp.resource("pt://catalog/cables")
    def resource_cable_catalog() -> str:
        """Tipos de cable disponibles en Packet Tracer."""
        return json.dumps(CABLE_TYPES, indent=2, ensure_ascii=False)

    @mcp.resource("pt://catalog/aliases")
    def resource_aliases() -> str:
        """Alias comunes para modelos de dispositivos."""
        return json.dumps(MODEL_ALIASES, indent=2, ensure_ascii=False)

    @mcp.resource("pt://catalog/templates")
    def resource_templates() -> str:
        """Plantillas de topología disponibles con descripción."""
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
        return json.dumps(data, indent=2, ensure_ascii=False)

    @mcp.resource("pt://capabilities")
    async def resource_capabilities() -> str:
        """Capacidades y versión del servidor MCP.

        Se DERIVA del registro de tools en vivo: en vez de mantener a mano una lista
        que puede desfasarse (antes decía nat="unsupported" mientras pt_apply_nat
        existía), introspecciona los tools realmente registrados y reporta el soporte
        de cada feature en función de si su tool existe. Si la introspección falla por
        cualquier razón, cae a los valores estáticos de CAPABILITIES.
        """
        caps = dict(CAPABILITIES)
        try:
            tools = await mcp.list_tools()
            names = sorted(t.name for t in tools)
            caps["tools_count"] = len(names)
            caps["tools"] = names
            # Feature → soporte derivado de la presencia real del tool (sin drift posible)
            caps["supported_live"] = {
                "nat": any(n.startswith("pt_apply_nat") for n in names),
                "acl": any(n.startswith("pt_apply_acl") for n in names),
                "modules": any("module" in n for n in names),
                "live_deploy": "pt_live_deploy" in names,
                "raw_js": "pt_send_raw" in names,
            }
        except Exception:
            pass
        return json.dumps(caps, indent=2, ensure_ascii=False)
