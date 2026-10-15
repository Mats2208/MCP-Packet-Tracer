"""
MCP server for Packet Tracer.

Entry point: creates the server, registers tools/resources, and starts
on streamable-http (:39000) or stdio depending on the --stdio flag.
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

# Report OUR version in the handshake, not the SDK's.
#
# `create_initialization_options()` resolves the number with
# `self.version if self.version else pkg_version("mcp")`, and FastMCP does not expose
# `version` in its `__init__` nor a property for the lowlevel server, so the
# fallback always won: the server presented itself as "1.28.1" — the library —
# instead of "0.8.0". That number is the one Claude Desktop, Cursor and PacketSmith
# show in their server panel, and on top of that it changed on its own whenever the
# dependency was updated.
#
# `_mcp_server` is private and there is no public alternative; it is guarded so that
# a future SDK version that renames it degrades to the old behavior instead of
# breaking startup, which is the one thing that cannot be allowed here.
_lowlevel = getattr(mcp, "_mcp_server", None)
if _lowlevel is not None:
    _lowlevel.version = __version__

register_tools(mcp)
register_resources(mcp)
register_prompts(mcp)


def main():
    """Starts the MCP server.

    By default uses streamable-http on :39000.
    With --stdio uses the stdio transport (for debugging or legacy clients).
    """
    if "--stdio" in sys.argv:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
