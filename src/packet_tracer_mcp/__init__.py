"""Packet Tracer MCP - MCP server for Cisco Packet Tracer."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _dist_version

# The version is declared ONCE, in `pyproject.toml`, and is read from the
# metadata of the installed package. Copying it here as a literal is the classic
# way for a release to ship announcing the previous number.
try:
    __version__ = _dist_version("packet-tracer-mcp")
except PackageNotFoundError:  # pragma: no cover - only when running without installing
    # Running from the source tree without `pip install -e .`. It is not an
    # error: the server starts anyway, it just cannot say which version it is.
    # An honest placeholder is better than a plausible lie.
    __version__ = "0.0.0+source"

__all__ = ["__version__"]
