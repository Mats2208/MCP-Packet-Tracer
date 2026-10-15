"""
Explainer: generates human-readable explanations of the plan's decisions.

Useful for learning, and so the LLM can tell the user
why each decision was made.
"""

from __future__ import annotations
from ..models.plans import TopologyPlan


def explain_plan(plan: TopologyPlan) -> list[str]:
    """Generates a list of explanations for the plan's decisions."""
    explanations: list[str] = []

    # Devices
    routers = plan.devices_by_category("router")
    switches = plan.devices_by_category("switch")
    pcs = plan.devices_by_category("pc")
    servers = plan.devices_by_category("server")
    clouds = plan.devices_by_category("cloud")

    explanations.append(
        f"Topology with {len(routers)} router(s), {len(switches)} switch(es), "
        f"{len(pcs)} PC(s), {len(servers)} server(s)"
        + (f" and a WAN connection" if clouds else "") + "."
    )

    # Subnetting
    lan_subnets = set()
    link_subnets = set()
    for dev in routers:
        for iface, ip in dev.interfaces.items():
            prefix = ip.split("/")[1]
            if prefix == "24":
                lan_subnets.add(ip.rsplit(".", 1)[0] + ".0/24")
            elif prefix == "30":
                link_subnets.add(ip)

    if lan_subnets:
        explanations.append(
            f"{len(lan_subnets)} /24 subnets were assigned to LANs — "
            f"each LAN supports up to 254 hosts."
        )
    if link_subnets:
        explanations.append(
            f"Router-to-router links use /30 subnets (point to point) — "
            f"saving IP addresses with only 2 hosts per link."
        )

    # Cables
    cross_links = [l for l in plan.links if l.cable == "cross"]
    straight_links = [l for l in plan.links if l.cable == "straight"]
    if cross_links:
        explanations.append(
            f"{len(cross_links)} crossover cable(s) are used between devices "
            f"of the same type (router↔router, switch↔switch)."
        )
    if straight_links:
        explanations.append(
            f"{len(straight_links)} straight-through cable(s) are used between devices "
            f"of different types (router↔switch, switch↔PC)."
        )

    # DHCP
    if plan.dhcp_pools:
        explanations.append(
            f"{len(plan.dhcp_pools)} DHCP pool(s) were configured — "
            f"the PCs get their IP automatically."
        )
        for pool in plan.dhcp_pools:
            explanations.append(
                f"  Pool '{pool.pool_name}': red {pool.network}/{pool.mask}, "
                f"gateway {pool.gateway}"
            )

    # Routing
    if plan.static_routes:
        floating = [r for r in plan.static_routes if r.admin_distance != 1]
        primary = [r for r in plan.static_routes if r.admin_distance == 1]
        msg = f"{len(primary)} static route(s) were configured — each router knows how to reach the other routers' LANs."
        if floating:
            msg += f" Plus {len(floating)} floating backup route(s) with AD={floating[0].admin_distance}."
        explanations.append(msg)
    if plan.ospf_configs:
        explanations.append(
            f"OSPF (process {plan.ospf_configs[0].process_id}) was configured on {len(plan.ospf_configs)} router(s) — "
            f"routes are learned dynamically through LSAs."
        )
    if plan.rip_configs:
        explanations.append(
            f"RIP v{plan.rip_configs[0].version} was configured on {len(plan.rip_configs)} router(s) — "
            f"a distance-vector protocol, with no auto-summary enabled."
        )
    if plan.eigrp_configs:
        explanations.append(
            f"EIGRP (AS {plan.eigrp_configs[0].as_number}) was configured on {len(plan.eigrp_configs)} router(s) — "
            f"an advanced distance-vector protocol (Cisco) with fast convergence."
        )

    # Validation
    if plan.validations:
        explanations.append(
            f"Verificaciones sugeridas: {len(plan.validations)} "
            f"(ej: ping {plan.validations[0].from_device} → {plan.validations[0].to_target})"
        )

    return explanations
