"""Modelos de request — lo que entra del LLM."""

from __future__ import annotations
import ipaddress
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from ...shared.enums import RoutingProtocol, TopologyTemplate
from ...shared.constants import (
    DEFAULT_ROUTER, DEFAULT_SWITCH,
    DEFAULT_LAN_BASE, DEFAULT_LINK_BASE,
)

# De qué tamaño saca subredes el IPPlanner de cada pool. Una base con un prefijo
# más largo que esto no puede dar ni una sola subred.
_REQUIRED_PREFIX = {
    "base_network": 24,          # una /24 por LAN
    "inter_router_network": 30,  # una /30 por enlace router↔router
}


class TopologyRequest(BaseModel):
    """Petición de alto nivel — lo que el LLM genera a partir del usuario."""
    template: TopologyTemplate = TopologyTemplate.MULTI_LAN
    routers: int = Field(ge=1, le=20, default=2)
    switches_per_router: int = Field(ge=0, le=4, default=1)
    pcs_per_lan: list[int] | int = Field(default=3)
    laptops_per_lan: list[int] | int = Field(default=0)
    servers: int = Field(ge=0, le=10, default=0)
    access_points: int = Field(ge=0, le=20, default=0)
    has_wan: bool = False
    dhcp: bool = True
    routing: RoutingProtocol = RoutingProtocol.STATIC
    router_model: str = DEFAULT_ROUTER
    switch_model: str = DEFAULT_SWITCH
    base_network: str = DEFAULT_LAN_BASE
    inter_router_network: str = DEFAULT_LINK_BASE
    # Opciones avanzadas de routing
    floating_routes: bool = False          # Genera rutas estáticas de respaldo (AD=254)
    ospf_process_id: int = Field(ge=1, le=65535, default=1)
    eigrp_as: int = Field(ge=1, le=65535, default=100)
    # VLAN (router-on-a-stick): nº de VLANs a repartir entre los PCs (0 = default 2)
    vlans: int = Field(ge=0, le=64, default=0)
    # IPv6 dual-stack
    dual_stack: bool = False
    ipv6_base: str = "2001:db8::/32"
    # Laptops conectadas por WiFi (NIC inalámbrica + AP auto-asociado por LAN)
    wireless_laptops: bool = False

    @field_validator("base_network", "inter_router_network")
    @classmethod
    def _pool_can_yield_subnets(cls, v: str, info: ValidationInfo) -> str:
        """La base tiene que ser una red válida y dar al menos una subred.

        Sin esto el valor llegaba crudo hasta `IPPlanner`, donde una `/25` moría
        con `new prefix must be longer`, una `/24` con un `StopIteration` desnudo
        al pedir la segunda LAN, y un texto cualquiera con `AddressValueError`.
        Los tres salían como stacktrace en vez de algo que el LLM pudiera
        corregir.
        """
        needed = _REQUIRED_PREFIX[info.field_name]
        # strict=True igual que `IPPlanner`: si acá se aceptaran bits de host,
        # la validación pasaría y el planner reventaría después.
        try:
            net = ipaddress.IPv4Network(v)
        except ValueError as exc:
            raise ValueError(
                f"'{v}' no es una red IPv4 válida ({exc}). Ejemplo: 192.168.0.0/16"
            ) from None
        if net.prefixlen > needed:
            raise ValueError(
                f"'{v}' es una /{net.prefixlen} y de acá salen subredes /{needed}, "
                f"así que no entra ninguna. Usá /{needed} o más corto "
                f"(192.168.0.0/16 da 256 redes /24; 10.0.0.0/16 da 16384 /30)."
            )
        return v

    @field_validator("ipv6_base")
    @classmethod
    def _ipv6_pool_can_yield_subnets(cls, v: str) -> str:
        """Mismo trato para IPv6, de donde el planner saca /64."""
        try:
            net = ipaddress.IPv6Network(v)
        except ValueError as exc:
            raise ValueError(
                f"'{v}' no es una red IPv6 válida ({exc}). Ejemplo: 2001:db8::/32"
            ) from None
        if net.prefixlen > 64:
            raise ValueError(
                f"'{v}' es una /{net.prefixlen} y de acá salen subredes /64, "
                f"así que no entra ninguna. Usá /64 o más corto (ej. 2001:db8::/32)."
            )
        return v
