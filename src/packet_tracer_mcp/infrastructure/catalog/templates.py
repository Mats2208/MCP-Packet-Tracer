"""
Formal topology templates.

Each template defines default values and constraints
that the orchestrator uses to generate plans.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from ...shared.enums import TopologyTemplate, RoutingProtocol


@dataclass(frozen=True)
class TemplateSpec:
    """Specification of a topology template."""
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
        description="1 router + 1 switch + PCs. A simple local network.",
        min_routers=1, max_routers=1, default_routers=1,
        default_pcs_per_lan=5,
        tags=("basic", "lan", "beginner"),
    ),
    TopologyTemplate.MULTI_LAN: TemplateSpec(
        name="Multi LAN",
        key=TopologyTemplate.MULTI_LAN,
        description="N routers in a chain, each with its own LAN.",
        default_routers=2, default_pcs_per_lan=3,
        tags=("intermediate", "multi-lan", "routing"),
    ),
    TopologyTemplate.MULTI_LAN_WAN: TemplateSpec(
        name="Multi LAN + WAN",
        key=TopologyTemplate.MULTI_LAN_WAN,
        description="N routers with LANs + a WAN connection (Cloud).",
        default_routers=3, default_pcs_per_lan=3,
        requires_wan=True,
        tags=("intermediate", "wan", "cloud"),
    ),
    TopologyTemplate.STAR: TemplateSpec(
        name="Star (Hub & Spoke)",
        key=TopologyTemplate.STAR,
        description="1 central router connected to N switches.",
        min_routers=1, max_routers=1, default_routers=1,
        default_switches_per_router=3, default_pcs_per_lan=4,
        tags=("basic", "star", "centralized"),
    ),
    TopologyTemplate.HUB_SPOKE: TemplateSpec(
        name="Hub and Spoke",
        key=TopologyTemplate.HUB_SPOKE,
        description="1 central hub router + N spoke routers, each with its own LAN.",
        default_routers=4, default_pcs_per_lan=2,
        tags=("advanced", "wan", "hub-spoke"),
    ),
    TopologyTemplate.BRANCH_OFFICE: TemplateSpec(
        name="Branch Office",
        key=TopologyTemplate.BRANCH_OFFICE,
        description="Head office + branches connected over WAN.",
        default_routers=3, default_pcs_per_lan=5,
        requires_wan=True,
        tags=("enterprise", "branch", "wan"),
    ),
    TopologyTemplate.THREE_ROUTER_TRIANGLE: TemplateSpec(
        name="Three Router Triangle",
        key=TopologyTemplate.THREE_ROUTER_TRIANGLE,
        description="3 routers in a triangle with redundancy.",
        min_routers=3, max_routers=3, default_routers=3,
        default_pcs_per_lan=3,
        default_routing=RoutingProtocol.OSPF,
        tags=("advanced", "redundancy", "ospf"),
    ),
    TopologyTemplate.ROUTER_ON_A_STICK: TemplateSpec(
        name="Router on a Stick",
        key=TopologyTemplate.ROUTER_ON_A_STICK,
        description="1 router + 1 switch with inter-VLAN routing (.1q subinterfaces).",
        min_routers=1, max_routers=1, default_routers=1,
        default_switches_per_router=1, default_pcs_per_lan=6,
        tags=("advanced", "vlan", "inter-vlan"),
    ),
    TopologyTemplate.CUSTOM: TemplateSpec(
        name="Custom",
        key=TopologyTemplate.CUSTOM,
        description="Free-form topology — every parameter set by hand.",
        tags=("free-form", "custom"),
    ),
}


def get_template(key: TopologyTemplate) -> TemplateSpec:
    """Returns the spec of a template."""
    return TEMPLATES[key]


def list_templates() -> list[TemplateSpec]:
    """Lists all templates with their details."""
    return list(TEMPLATES.values())
