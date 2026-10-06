"""
Servidor MCP para Packet Tracer.

Punto de entrada: crea el servidor, registra tools/resources, y arranca
en streamable-http (:39000) o stdio según el flag --stdio.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from . import __version__
from .adapters.mcp.prompt_registry import register_prompts
from .adapters.mcp.resource_registry import register_resources
from .adapters.mcp.tool_registry import register_tools
from .settings import SERVER_NAME, SERVER_INSTRUCTIONS

TRANSPORT_PORT = 39000

mcp = FastMCP(
    SERVER_NAME,
    instructions=SERVER_INSTRUCTIONS,
    host="127.0.0.1",
    port=TRANSPORT_PORT,
    stateless_http=True,
)

# Decir NUESTRA version en el handshake, no la del SDK.
#
# `create_initialization_options()` resuelve el numero con
# `self.version if self.version else pkg_version("mcp")`, y FastMCP no expone
# `version` en su `__init__` ni una propiedad para el server lowlevel, asi que
# el fallback ganaba siempre: el servidor se presentaba como "1.28.1" —la
# libreria— en vez de "0.8.0". Ese numero es el que muestran Claude Desktop,
# Cursor y PacketSmith en su panel de servidores, y encima cambiaba solo al
# actualizar la dependencia.
#
# `_mcp_server` es privado y no hay alternativa publica; va con guardia para
# que una version futura del SDK que lo renombre degrade a lo de antes en vez
# de romper el arranque, que es lo unico que no se puede permitir aca.
_lowlevel = getattr(mcp, "_mcp_server", None)
if _lowlevel is not None:
    _lowlevel.version = __version__

register_tools(mcp)
register_resources(mcp)
register_prompts(mcp)


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
