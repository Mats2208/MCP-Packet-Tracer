"""
Plantillas de topología formales.

Cada plantilla define valores por defecto y restricciones
que el orquestador usa para generar planes.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from ...shared.enums import TopologyTemplate, RoutingProtocol


@dataclass(frozen=True)
class TemplateSpec:
    """Especificación de una plantilla de topología."""
    name: str
    key: TopologyTemplate
    description: str
    min_routers: int = 1
    max_routers: int = 20
    default_routers: int = 2
    default_pcs_per_lan: int = 3
    default_switches_per_router: int = 1
    requires_wan: bool = False
    default_routing: RoutingProtocol = RoutingProtocol.STATIC
    tags: tuple[str, ...] = ()


TEMPLATES: dict[TopologyTemplate, TemplateSpec] = {
    TopologyTemplate.SINGLE_LAN: TemplateSpec(
        name="Single LAN",
        key=TopologyTemplate.SINGLE_LAN,
        description="1 router + 1 switch + PCs. Red local simple.",
        min_routers=1, max_routers=1, default_routers=1,
        default_pcs_per_lan=5,
        tags=("básico", "lan", "principiante"),
    ),
    TopologyTemplate.MULTI_LAN: TemplateSpec(
        name="Multi LAN",
        key=TopologyTemplate.MULTI_LAN,
        description="N routers en cadena, cada uno con su LAN.",
        default_routers=2, default_pcs_per_lan=3,
        tags=("intermedio", "multi-lan", "routing"),
    ),
    TopologyTemplate.MULTI_LAN_WAN: TemplateSpec(
        name="Multi LAN + WAN",
        key=TopologyTemplate.MULTI_LAN_WAN,
        description="N routers con LANs + conexión WAN (Cloud).",
        default_routers=3, default_pcs_per_lan=3,
        requires_wan=True,
        tags=("intermedio", "wan", "cloud"),
    ),
    TopologyTemplate.STAR: TemplateSpec(
        name="Star (Hub & Spoke)",
        key=TopologyTemplate.STAR,
        description="1 router central conectado a N switches.",
        min_routers=1, max_routers=1, default_routers=1,
        default_switches_per_router=3, default_pcs_per_lan=4,
        tags=("básico", "star", "centralizado"),
    ),
    TopologyTemplate.HUB_SPOKE: TemplateSpec(
        name="Hub and Spoke",
        key=TopologyTemplate.HUB_SPOKE,
        description="1 router hub central + N routers spoke, cada uno con su LAN.",
        default_routers=4, default_pcs_per_lan=2,
        tags=("avanzado", "wan", "hub-spoke"),
    ),
    TopologyTemplate.BRANCH_OFFICE: TemplateSpec(
        name="Branch Office",
        key=TopologyTemplate.BRANCH_OFFICE,
        description="Oficina central + sucursales conectadas por WAN.",
        default_routers=3, default_pcs_per_lan=5,
        requires_wan=True,
        tags=("enterprise", "branch", "wan"),
    ),
    TopologyTemplate.THREE_ROUTER_TRIANGLE: TemplateSpec(
        name="Three Router Triangle",
        key=TopologyTemplate.THREE_ROUTER_TRIANGLE,
        description="3 routers en triángulo con redundancia.",
        min_routers=3, max_routers=3, default_routers=3,
        default_pcs_per_lan=3,
        default_routing=RoutingProtocol.OSPF,
        tags=("avanzado", "redundancia", "ospf"),
    ),
    TopologyTemplate.ROUTER_ON_A_STICK: TemplateSpec(
        name="Router on a Stick",
        key=TopologyTemplate.ROUTER_ON_A_STICK,
        description="1 router + 1 switch con inter-VLAN routing (futuro).",
        min_routers=1, max_routers=1, default_routers=1,
        default_switches_per_router=1, default_pcs_per_lan=6,
        tags=("avanzado", "vlan", "futuro"),
    ),
    TopologyTemplate.CUSTOM: TemplateSpec(
        name="Custom",
        key=TopologyTemplate.CUSTOM,
        description="Topología libre — todos los parámetros manuales.",
        tags=("libre", "custom"),
    ),
}


def get_template(key: TopologyTemplate) -> TemplateSpec:
    """Obtiene la spec de una plantilla."""
    return TEMPLATES[key]


def list_templates() -> list[TemplateSpec]:
    """Lista todas las plantillas con sus detalles."""
    return list(TEMPLATES.values())
