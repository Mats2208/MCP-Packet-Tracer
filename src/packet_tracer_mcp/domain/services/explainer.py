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
        f"Topología con {len(routers)} router(s), {len(switches)} switch(es), "
        f"{len(pcs)} PC(s), {len(servers)} servidor(es)"
        + (f" y conexión WAN" if clouds else "") + "."
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
            f"Se asignaron {len(lan_subnets)} subredes /24 para LANs — "
            f"cada LAN soporta hasta 254 hosts."
        )
    if link_subnets:
        explanations.append(
            f"Los enlaces entre routers usan subredes /30 (punto a punto) — "
            f"ahorra direcciones IP usando solo 2 hosts por enlace."
        )

    # Cables
    cross_links = [l for l in plan.links if l.cable == "cross"]
    straight_links = [l for l in plan.links if l.cable == "straight"]
    if cross_links:
        explanations.append(
            f"Se usan {len(cross_links)} cable(s) cruzado(s) entre dispositivos "
            f"del mismo tipo (router↔router, switch↔switch)."
        )
    if straight_links:
        explanations.append(
            f"Se usan {len(straight_links)} cable(s) directos entre dispositivos "
            f"de diferente tipo (router↔switch, switch↔PC)."
        )

    # DHCP
    if plan.dhcp_pools:
        explanations.append(
            f"Se configuraron {len(plan.dhcp_pools)} pool(s) DHCP — "
            f"los PCs obtienen IP automáticamente."
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
        msg = f"Se configuraron {len(primary)} ruta(s) estática(s) — cada router sabe cómo alcanzar las LANs de los otros routers."
        if floating:
            msg += f" Además {len(floating)} ruta(s) flotante(s) de respaldo con AD={floating[0].admin_distance}."
        explanations.append(msg)
    if plan.ospf_configs:
        explanations.append(
            f"Se configuró OSPF (proceso {plan.ospf_configs[0].process_id}) en {len(plan.ospf_configs)} router(s) — "
            f"las rutas se aprenden dinámicamente mediante LSA."
        )
    if plan.rip_configs:
        explanations.append(
            f"Se configuró RIP v{plan.rip_configs[0].version} en {len(plan.rip_configs)} router(s) — "
            f"protocolo de vector de distancia, no auto-summary activado."
        )
    if plan.eigrp_configs:
        explanations.append(
            f"Se configuró EIGRP (AS {plan.eigrp_configs[0].as_number}) en {len(plan.eigrp_configs)} router(s) — "
            f"protocolo avanzado de vector de distancia (Cisco), convergencia rápida."
        )

    # Validation
    if plan.validations:
        explanations.append(
            f"Verificaciones sugeridas: {len(plan.validations)} "
            f"(ej: ping {plan.validations[0].from_device} → {plan.validations[0].to_target})"
        )

    return explanations
