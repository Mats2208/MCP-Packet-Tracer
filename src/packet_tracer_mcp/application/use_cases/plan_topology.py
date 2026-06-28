"""Use case: planificar topología."""

from __future__ import annotations
from ...domain.models.requests import TopologyRequest
from ...domain.models.plans import TopologyPlan
from ...domain.models.errors import ValidationResult
from ...domain.services.orchestrator import plan_from_request
from ...shared.enums import RoutingProtocol, TopologyTemplate
from ...shared.constants import DEFAULT_LAN_BASE, DEFAULT_LINK_BASE
from ..dto.requests import PlanTopologyDTO


def plan_topology(dto: PlanTopologyDTO) -> tuple[TopologyPlan, ValidationResult]:
    """Crea un plan desde un DTO."""
    kwargs: dict = dict(
        routers=dto.routers,
        pcs_per_lan=dto.pcs_per_lan,
        switches_per_router=dto.switches_per_router,
        servers=dto.servers,
        has_wan=dto.has_wan,
        dhcp=dto.dhcp,
        routing=RoutingProtocol(dto.routing),
        vlans=dto.vlans,
        dual_stack=dto.dual_stack,
        wireless_laptops=dto.wireless_laptops,
    )
    if dto.template:
        kwargs["template"] = TopologyTemplate(dto.template)
    if dto.ipv6_base:
        kwargs["ipv6_base"] = dto.ipv6_base
    if dto.router_model:
        kwargs["router_model"] = dto.router_model
    if dto.switch_model:
        kwargs["switch_model"] = dto.switch_model
    if dto.lan_base:
        kwargs["base_network"] = dto.lan_base
    if dto.link_base:
        kwargs["inter_router_network"] = dto.link_base

    request = TopologyRequest(**kwargs)
    return plan_from_request(request)
