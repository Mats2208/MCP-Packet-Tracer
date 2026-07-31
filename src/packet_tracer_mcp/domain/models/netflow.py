"""Exportador NetFlow sobre un dispositivo de PT.

A diferencia del resto de las features avanzadas, NetFlow NO se aplica por CLI:
la API nativa de PT expone `NFExporterManager.createNFExporter(name)` y los
setters del exportador, así que se configura por objeto y se puede releer para
verificar (`isFullyConfigured()`).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NetflowExporter(BaseModel):
    """Un exportador NetFlow: a dónde manda los flujos el dispositivo."""

    device: str
    name: str
    destination_ip: str = ""
    udp_port: int = 2055
    version: int = 9
    source_port: str = ""            # interfaz de origen; vacío = la elige PT
    monitors: list[str] = Field(default_factory=list)
