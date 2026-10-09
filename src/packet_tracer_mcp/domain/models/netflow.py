"""NetFlow exporter on a PT device.

Unlike the rest of the advanced features, NetFlow is NOT applied via CLI:
PT's native API exposes `NFExporterManager.createNFExporter(name)` and the
exporter's setters, so it is configured as an object and can be read back to
verify it (`isFullyConfigured()`).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NetflowExporter(BaseModel):
    """A NetFlow exporter: where the device sends its flows."""

    device: str
    name: str
    destination_ip: str = ""
    udp_port: int = 2055
    version: int = 9
    source_port: str = ""            # source interface; empty = PT picks it
    monitors: list[str] = Field(default_factory=list)
