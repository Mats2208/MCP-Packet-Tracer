"""Modelo de ajuste fino por interfaz (clock-rate, bandwidth, knobs OSPF/EIGRP)."""

from __future__ import annotations
from pydantic import BaseModel


class InterfaceTuning(BaseModel):
    """Parámetros de una interfaz concreta de un router. Todos opcionales."""
    router: str
    interface: str
    clock_rate: int | None = None        # solo en interfaces Serial (extremo DCE)
    bandwidth: int | None = None         # kbps
    ospf_cost: int | None = None
    ospf_priority: int | None = None
    ospf_hello_interval: int | None = None
    delay: int | None = None             # delay EIGRP (decenas de microsegundos)

    def is_serial(self) -> bool:
        return self.interface.lower().startswith("serial")
