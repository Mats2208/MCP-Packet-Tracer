"""
Estimator: generates estimates of what is going to be created (dry-run).

Useful so the LLM or the user can see what is going to be done before
generating scripts.
"""

from __future__ import annotations
from ..models.requests import TopologyRequest
from ..models.plans import TopologyPlan


def estimate_from_request(request: TopologyRequest) -> dict:
    """Estimates resources without generating the full plan."""
    total_pcs = _normalized_total(request.pcs_per_lan, request.routers)
    total_laptops = _normalized_total(request.laptops_per_lan, request.routers)
    total_access_points = min(request.access_points, request.routers)

    total_switches = request.routers * request.switches_per_router
    total_devices = (
        request.routers
        + total_switches
        + total_pcs
        + total_laptops
        + total_access_points
        + request.servers
    )
    if request.has_wan:
        total_devices += 1

    # Estimated links
    router_links = max(0, request.routers - 1)  # chain
    router_switch_links = total_switches
    switch_pc_links = total_pcs
    switch_laptop_links = total_laptops
    switch_ap_links = total_access_points
    server_links = request.servers
    wan_link = 1 if request.has_wan else 0
    total_links = (
        router_links
        + router_switch_links
        + switch_pc_links
        + switch_laptop_links
        + switch_ap_links
        + server_links
        + wan_link
    )

    return {
        "devices": {
            "routers": request.routers,
            "switches": total_switches,
            "pcs": total_pcs,
            "laptops": total_laptops,
            "access_points": total_access_points,
            "servers": request.servers,
            "clouds": 1 if request.has_wan else 0,
            "total": total_devices,
        },
        "links": {
            "router_to_router": router_links,
            "router_to_switch": router_switch_links,
            "switch_to_pc": switch_pc_links,
            "switch_to_laptop": switch_laptop_links,
            "switch_to_access_point": switch_ap_links,
            "switch_to_server": server_links,
            "router_to_cloud": wan_link,
            "total": total_links,
        },
        "configs": {
            "routers_to_configure": request.routers,
            "dhcp_pools": request.routers if request.dhcp else 0,
            "static_routes": router_links * 2 if request.routing.value == "static" else 0,
            "ospf_configs": request.routers if request.routing.value == "ospf" else 0,
            "rip_configs": request.routers if request.routing.value == "rip" else 0,
            "eigrp_configs": request.routers if request.routing.value == "eigrp" else 0,
            "floating_routes": "yes (backup via alternate paths)" if request.floating_routes and request.routing.value == "static" else "no",
        },
        "subnets": {
            "lan_subnets": request.routers,
            "link_subnets": router_links + wan_link,
        },
        "complexity": _estimate_complexity(request),
    }


def estimate_from_plan(plan: TopologyPlan) -> dict:
    """Estimates resources from a plan that has already been generated."""
    return {
        "devices_to_create": len(plan.devices),
        "links_to_create": len(plan.links),
        "configs_to_generate": len([d for d in plan.devices if d.category in ("router", "switch")]),
        "dhcp_pools": len(plan.dhcp_pools),
        "static_routes": len(plan.static_routes),
        "ospf_configs": len(plan.ospf_configs),
        "rip_configs": len(plan.rip_configs),
        "eigrp_configs": len(plan.eigrp_configs),
        "validations": len(plan.validations),
        "has_errors": not plan.is_valid,
        "error_count": len(plan.errors),
    }


def _estimate_complexity(req: TopologyRequest) -> str:
    score = req.routers * 3 + (
        (sum(req.pcs_per_lan) if isinstance(req.pcs_per_lan, list) else req.pcs_per_lan * req.routers)
    ) + (
        (sum(req.laptops_per_lan) if isinstance(req.laptops_per_lan, list) else req.laptops_per_lan * req.routers)
    ) + req.servers * 2 + min(req.access_points, req.routers)
    if req.has_wan:
        score += 5
    if req.routing.value == "ospf":
        score += 10
    elif req.routing.value in ("eigrp", "rip"):
        score += 8
    if req.floating_routes:
        score += 5

    if score <= 10:
        return "simple"
    elif score <= 25:
        return "moderada"
    elif score <= 50:
        return "compleja"
    return "muy compleja"


def _normalized_total(values: int | list[int], routers: int) -> int:
    """Normalizes an int/list per router using the same convention as the orchestrator."""
    if isinstance(values, int):
        return values * routers

    normalized = list(values[:routers])
    while len(normalized) < routers:
        normalized.append(normalized[-1] if normalized else 0)
    return sum(normalized)
