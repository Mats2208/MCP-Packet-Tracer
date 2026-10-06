"""Request models — what comes in from the LLM."""

from __future__ import annotations
import ipaddress
from pydantic import BaseModel, Field, ValidationInfo, field_validator

from ...shared.enums import RoutingProtocol, TopologyTemplate
from ...shared.constants import (
    DEFAULT_ROUTER, DEFAULT_SWITCH,
    DEFAULT_LAN_BASE, DEFAULT_LINK_BASE,
)

# The size of the subnets IPPlanner carves from each pool. A base with a prefix
# longer than this cannot yield even one subnet.
_REQUIRED_PREFIX = {
    "base_network": 24,          # one /24 per LAN
    "inter_router_network": 30,  # one /30 per router↔router link
}


class TopologyRequest(BaseModel):
    """High-level request — what the LLM generates from the user."""
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
    # Advanced routing options
    floating_routes: bool = False          # Generates backup static routes (AD=254)
    ospf_process_id: int = Field(ge=1, le=65535, default=1)
    eigrp_as: int = Field(ge=1, le=65535, default=100)
    # VLAN (router-on-a-stick): number of VLANs to spread across the PCs (0 = default 2)
    vlans: int = Field(ge=0, le=64, default=0)
    # IPv6 dual-stack
    dual_stack: bool = False
    ipv6_base: str = "2001:db8::/32"
    # Laptops connected over WiFi (wireless NIC + an AP auto-associated per LAN)
    wireless_laptops: bool = False

    @field_validator("base_network", "inter_router_network")
    @classmethod
    def _pool_can_yield_subnets(cls, v: str, info: ValidationInfo) -> str:
        """The base has to be a valid network and yield at least one subnet.

        Without this the value reached `IPPlanner` raw, where a `/25` died
        with `new prefix must be longer`, a `/24` with a bare `StopIteration`
        when asking for the second LAN, and any text with `AddressValueError`.
        All three came out as a stack trace instead of something the LLM could
        fix.
        """
        needed = _REQUIRED_PREFIX[info.field_name]
        # strict=True like `IPPlanner`: if host bits were accepted here,
        # validation would pass and the planner would blow up later.
        try:
            net = ipaddress.IPv4Network(v)
        except ValueError as exc:
            raise ValueError(
                f"'{v}' is not a valid IPv4 network ({exc}). Example: 192.168.0.0/16"
            ) from None
        if net.prefixlen > needed:
            raise ValueError(
                f"'{v}' is a /{net.prefixlen} and the subnets carved from it are /{needed}, "
                f"so not even one fits. Use /{needed} or shorter "
                f"(192.168.0.0/16 gives 256 /24 networks; 10.0.0.0/16 gives 16384 /30)."
            )
        return v

    @field_validator("ipv6_base")
    @classmethod
    def _ipv6_pool_can_yield_subnets(cls, v: str) -> str:
        """Same treatment for IPv6, from which the planner carves /64s."""
        try:
            net = ipaddress.IPv6Network(v)
        except ValueError as exc:
            raise ValueError(
                f"'{v}' is not a valid IPv6 network ({exc}). Example: 2001:db8::/32"
            ) from None
        if net.prefixlen > 64:
            raise ValueError(
                f"'{v}' is a /{net.prefixlen} and the subnets carved from it are /64, "
                f"so not even one fits. Use /64 or shorter (e.g. 2001:db8::/32)."
            )
        return v
