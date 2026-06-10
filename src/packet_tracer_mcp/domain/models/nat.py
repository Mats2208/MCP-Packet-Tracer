"""Modelos de NAT/PAT — configuración y modos.

El NAT se aplica post-deploy a un router existente vía configureIosDevice
a través del bridge, igual que las ACLs.

Tres modos:
  static  — 1:1 fijo. Cada IP privada se mapea a una IP pública permanente.
             Usar cuando: un servidor interno debe ser alcanzable desde internet
             siempre con la misma IP pública (ej: servidor web, FTP, correo).

  dynamic — Pool de IPs públicas asignadas bajo demanda. El router elige qué
             IP pública asignar a cada host interno según disponibilidad.
             Usar cuando: tienes más IPs públicas que el overload justifica pero
             menos que hosts internos; o cuando el tracking por IP pública importa.
             Raro en redes actuales.

  pat     — PAT (Port Address Translation) / NAT Overload. Muchos hosts internos
             comparten UNA sola IP pública usando números de puerto como
             diferenciador. Es lo que hacen casi todos los routers domésticos
             y empresariales cuando tienen una sola IP del ISP.
             Usar cuando: tienes 1 (o pocas) IPs públicas y N hosts internos.
             Sub-modos:
               use_interface_overload=True  → ip nat inside source list X interface <outside> overload
               use_interface_overload=False → ip nat inside source list X pool POOL overload
"""

from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field

NATMode = Literal["static", "dynamic", "pat"]


class NATStaticMapping(BaseModel):
    """Par inside-local ↔ inside-global para NAT estático."""
    inside_local: str   # IP privada, ej: "192.168.1.10"
    inside_global: str  # IP pública fija, ej: "200.1.1.5"


class NATPool(BaseModel):
    """Pool de IPs públicas para NAT dinámico o PAT con pool."""
    name: str = "NAT-POOL"
    start_ip: str         # primera IP del pool, ej: "200.1.1.1"
    end_ip: str           # última IP del pool,  ej: "200.1.1.10"
    netmask: str          # máscara de red, ej: "255.255.255.0"


class NATConfig(BaseModel):
    """Configuración completa de NAT/PAT para un router."""
    router: str
    mode: NATMode

    # Interfaz conectada a la red privada (LAN)
    inside_interface: str   # ej: "GigabitEthernet0/0"
    # Interfaz conectada a la red pública (WAN/Internet)
    outside_interface: str  # ej: "GigabitEthernet0/1"

    # --- Modo static ---
    # Lista de pares inside-local ↔ inside-global.
    # Requerido cuando mode="static".
    static_mappings: list[NATStaticMapping] = Field(default_factory=list)

    # --- Modos dynamic / pat ---
    # Número o nombre de ACL que identifica los hosts internos a traducir.
    acl_number: str = "1"
    # Redes internas en formato "network wildcard", ej: "192.168.1.0 0.0.0.255".
    # Se usan para generar el access-list inline. Si la ACL ya existe en PT
    # puedes dejar esta lista vacía y el generador omite el access-list.
    inside_networks: list[str] = Field(default_factory=list)

    # Pool de IPs públicas. Requerido para dynamic; opcional en pat cuando
    # use_interface_overload=True.
    pool: NATPool | None = None

    # Solo PAT: si True genera "ip nat inside source list X interface <outside> overload"
    # en lugar de usar un pool. Típico cuando el ISP asigna una única IP a la interfaz WAN.
    use_interface_overload: bool = False
