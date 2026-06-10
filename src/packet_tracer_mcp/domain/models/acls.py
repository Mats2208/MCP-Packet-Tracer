"""Modelos de ACL — reglas, planes y bindings.

Los ACL se aplican post-deploy a un router existente vía configureIosDevice
a través del bridge. No forman parte del TopologyPlan principal porque son
modificaciones discretas a una topología ya desplegada.
"""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


ACLAction = Literal["permit", "deny"]
ACLProtocol = Literal["ip", "icmp", "tcp", "udp", "esp", "ahp", "gre", "eigrp", "ospf"]
PortOp = Literal["eq", "neq", "lt", "gt", "range"]
Direction = Literal["in", "out"]
ACLType = Literal["standard", "extended"]


class ACLEntry(BaseModel):
    """Una regla individual de ACL.

    En IOS, las ACL standard solo filtran por source. Las extended
    permiten source + destination + protocolo + puertos + flags.
    """
    sequence: int | None = None  # Auto-asignado si es None (10, 20, 30, ...)
    action: ACLAction
    protocol: ACLProtocol = "ip"

    # Source (siempre requerido). Formatos:
    #   "any"
    #   "host 1.2.3.4"  (un host)
    #   "1.2.3.0 0.0.0.255"  (network + wildcard)
    source: str

    # Destination (solo extended). Mismos formatos que source.
    destination: str = ""

    # Source port (solo TCP/UDP). port_op y port van juntos.
    source_port_op: PortOp | None = None
    source_port: int | None = None
    source_port_end: int | None = None  # solo si port_op == "range"

    # Destination port (solo TCP/UDP).
    dest_port_op: PortOp | None = None
    dest_port: int | None = None
    dest_port_end: int | None = None

    # ICMP type (solo ICMP). Ej: "echo", "echo-reply", "host-unreachable".
    icmp_type: str = ""

    # TCP flags (solo TCP). Ej: ["established"], ["syn", "ack"].
    tcp_flags: list[str] = Field(default_factory=list)

    # Logging y comentarios opcionales.
    log: bool = False
    remark: str = ""


class ACLPlan(BaseModel):
    """Plan completo de una ACL para un router específico."""
    router: str  # nombre del dispositivo en PT
    name_or_number: str  # "101", "BLOCK_HTTP", etc.
    acl_type: ACLType
    entries: list[ACLEntry] = Field(default_factory=list)


class ACLBinding(BaseModel):
    """Aplicación de una ACL a una interfaz de router."""
    router: str
    interface: str  # ej: "GigabitEthernet0/0"
    acl_id: str  # debe coincidir con name_or_number de un ACLPlan
    direction: Direction
