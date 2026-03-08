"""
Servidor MCP para Packet Tracer.

Punto de entrada: crea el servidor, registra tools/resources, y arranca
en streamable-http (:39000) o stdio según el flag --stdio.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from .adapters.mcp.resource_registry import register_resources
from .adapters.mcp.tool_registry import register_tools
from .settings import SERVER_NAME, SERVER_INSTRUCTIONS

TRANSPORT_PORT = 39000

mcp = FastMCP(
    SERVER_NAME,
    instructions=SERVER_INSTRUCTIONS,
    host="127.0.0.1",
    port=TRANSPORT_PORT,
)

register_tools(mcp)
register_resources(mcp)


def main():
    """Arranca el servidor MCP.

    Por defecto usa streamable-http en :39000.
    Con --stdio usa transporte stdio (para debug o clientes legacy).
    """
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
