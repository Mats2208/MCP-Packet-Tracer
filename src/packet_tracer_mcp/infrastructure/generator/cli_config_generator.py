"""
IOS CLI configuration generator for Packet Tracer devices.

From the TopologyPlan, generates command blocks ready to
paste into the terminal of each router/switch.
"""

from __future__ import annotations
import ipaddress

from ...domain.models.plans import TopologyPlan, DevicePlan
from ...shared.utils import prefix_to_mask
from .vlan_cli_generator import (
    generate_switch_vlan_cli, generate_router_subinterface_cli, switch_supports_encap,
)

def generate_all_configs(plan: TopologyPlan) -> dict[str, str]:
    """
    Generates CLI configs for every device that needs one.
    Returns {device_name: cli_block}.
    """
    configs: dict[str, str] = {}

    for dev in plan.devices:
        if dev.category == "router":
            configs[dev.name] = _router_config(dev, plan)
        elif dev.category == "switch" and dev.interfaces and switch_supports_encap(dev.model):
            # A multilayer switch with addresses routes: routed ports, SVIs, OSPF, DHCP.
            configs[dev.name] = _router_config(dev, plan, l3_switch=True)
        elif dev.category == "switch":
            cfg = _switch_config(dev, plan)
            if cfg.strip():
                configs[dev.name] = cfg

    for name, extra in plan.extra_cli.items():
        if name in configs and extra:
            configs[name] = _with_extra_cli(configs[name], extra)

    return configs


def static_host_ips(plan: TopologyPlan) -> dict[str, str]:
    """
    {host_name: ip} of the wired hosts deployed with a fixed IP even when DHCP is on:
    every server, plus the last host of each subnet (Fix F17). The router pools must
    exclude these addresses, or a lease can duplicate them.
    """
    if not plan.dhcp_pools:
        return {}
    host_ip: dict[str, str] = {}
    by_subnet: dict[str, list[str]] = {}
    for dev in plan.devices:
        if dev.category not in ("pc", "server", "laptop") or not dev.interfaces:
            continue
        iface_ip = next(iter(dev.interfaces.values()), None)
        if not iface_ip:
            continue
        host_ip[dev.name] = iface_ip.split("/")[0]
        net = str(ipaddress.ip_interface(iface_ip).network)
        by_subnet.setdefault(net, []).append(dev.name)

    # Fix F17: with DHCP active, the LAST wired host on a LAN sometimes does not
    # complete the DHCP DISCOVER. As a deterministic fallback we assign it a static IP
    # (the one the planner already calculated): the host with the highest IP per subnet.
    static = {d.name for d in plan.devices if d.category == "server" and d.name in host_ip}
    for names in by_subnet.values():
        static.add(max(names, key=lambda n: ipaddress.ip_address(host_ip[n])))
    return {n: host_ip[n] for n in static}


def _with_extra_cli(cfg: str, extra: list[str]) -> str:
    """Insert the plan's extra lines before the trailing `end` / `write memory`."""
    lines = cfg.split("\n")
    tail = lines[-2:] if lines[-2:] == ["end", "write memory"] else []
    head = lines[: len(lines) - len(tail)]
    return "\n".join(head + list(extra) + tail)


def _ospf_point_to_point_interfaces(router: DevicePlan, plan: TopologyPlan) -> set[str]:
    """The router's /30 interfaces that an OSPF `network` statement covers.

    Set point-to-point at deploy time: as broadcast they sit in 2WAY/DROTHER waiting
    for a DR election, and changing the type afterwards makes PT flush the ASBR's
    default route (see interface_tuning_cli_generator).
    """
    nets = [
        ipaddress.ip_network(f"{n['network']}/{n['wildcard']}", strict=False)
        for o in plan.ospf_configs if o.router == router.name
        for n in o.networks
    ]
    out: set[str] = set()
    for iface, ip_cidr in router.interfaces.items():
        addr = ipaddress.ip_interface(ip_cidr)
        if addr.network.prefixlen == 30 and any(addr.ip in n for n in nets):
            out.add(iface)
    return out


def _router_config(router: DevicePlan, plan: TopologyPlan, l3_switch: bool = False) -> str:
    """Generates the full config of a router (or of a layer-3 multilayer switch)."""
    lines: list[str] = []

    lines.append("enable")
    lines.append("configure terminal")
    lines.append(f"hostname {router.name}")
    lines.append("no ip domain-lookup")
    if router.interfaces_v6:
        lines.append("ipv6 unicast-routing")
    if l3_switch:
        lines.append("ip routing")
        # VLANs first: an SVI for a VLAN that does not exist stays down.
        access = [a for a in plan.access_ports if a.switch == router.name]
        trunks = [t for t in plan.trunks if t.switch == router.name]
        if access or trunks or plan.vlans:
            lines.extend(generate_switch_vlan_cli(plan.vlans, access, trunks, supports_encap=True))
    lines.append("")

    # --- Physical interfaces (the "Gig0/0.10" subinterfaces are emitted separately with
    #     their `encapsulation dot1Q`, so we skip them here) ---
    ospf_p2p = _ospf_point_to_point_interfaces(router, plan)
    for iface, ip_cidr in router.interfaces.items():
        if "." in iface:
            continue
        ip, prefix = ip_cidr.split("/")
        mask = prefix_to_mask(int(prefix))
        lines.append(f"interface {iface}")
        if l3_switch and not iface.lower().startswith("vlan"):
            lines.append(" no switchport")
        lines.append(f" ip address {ip} {mask}")
        if iface in ospf_p2p:
            lines.append(" ip ospf network point-to-point")
        # IPv6 on the same physical interface (dual-stack), if applicable
        v6 = router.interfaces_v6.get(iface)
        if v6:
            lines.append(f" ipv6 address {v6}")
            lines.append(" ipv6 enable")
        lines.append(" no shutdown")
        lines.append(" exit")
        lines.append("")

    # --- Subinterfaces .1q (inter-VLAN routing / router-on-a-stick) ---
    subifs = [s for s in plan.subinterfaces if s.router == router.name]
    if subifs:
        lines.extend(generate_router_subinterface_cli(subifs, router.interfaces_v6))
        lines.append("")

    # --- DHCP ---
    pools = [p for p in plan.dhcp_pools if p.router == router.name]
    static_ips = sorted(set(static_host_ips(plan).values()), key=ipaddress.ip_address)
    for pool in pools:
        lines.append(f"ip dhcp excluded-address {pool.excluded_start} {pool.excluded_end}")
        pool_net = ipaddress.ip_network(f"{pool.network}/{pool.mask}", strict=False)
        for ip in static_ips:
            if ipaddress.ip_address(ip) in pool_net:
                lines.append(f"ip dhcp excluded-address {ip}")
    for pool in pools:
        lines.append(f"ip dhcp pool {pool.pool_name}")
        lines.append(f" network {pool.network} {pool.mask}")
        lines.append(f" default-router {pool.gateway}")
        lines.append(f" dns-server {pool.dns}")
        lines.append(" exit")
        lines.append("")

    # --- Static routes ---
    static_routes = [r for r in plan.static_routes if r.router == router.name]
    for route in static_routes:
        line = f"ip route {route.destination} {route.mask} {route.next_hop}"
        if route.admin_distance != 1:
            line += f" {route.admin_distance}"
        lines.append(line)
    if static_routes:
        lines.append("")

    # --- OSPF ---
    ospf_cfgs = [o for o in plan.ospf_configs if o.router == router.name]
    for ospf in ospf_cfgs:
        lines.append(f"router ospf {ospf.process_id}")
        if ospf.router_id:
            lines.append(f" router-id {ospf.router_id}")
        for net in ospf.networks:
            lines.append(
                f" network {net['network']} {net['wildcard']} area {net['area']}"
            )
        lines.append(" exit")
        lines.append("")

    # --- RIP ---
    rip_cfgs = [r for r in plan.rip_configs if r.router == router.name]
    for rip in rip_cfgs:
        lines.append(f"router rip")
        lines.append(f" version {rip.version}")
        for net in rip.networks:
            lines.append(f" network {net}")
        if rip.no_auto_summary:
            lines.append(" no auto-summary")
        lines.append(" exit")
        lines.append("")

    # --- EIGRP ---
    eigrp_cfgs = [e for e in plan.eigrp_configs if e.router == router.name]
    for eigrp in eigrp_cfgs:
        lines.append(f"router eigrp {eigrp.as_number}")
        for net in eigrp.networks:
            lines.append(f" network {net['network']} {net['wildcard']}")
        if eigrp.no_auto_summary:
            lines.append(" no auto-summary")
        lines.append(" exit")
        lines.append("")

    lines.append("end")
    lines.append("write memory")

    return "\n".join(lines)


def _switch_config(switch: DevicePlan, plan: TopologyPlan) -> str:
    """Generates the config of a switch: hostname + (if any) VLANs/access/trunks."""
    lines: list[str] = []
    lines.append("enable")
    lines.append("configure terminal")
    lines.append(f"hostname {switch.name}")

    # VLAN / access / trunk for this switch
    access = [a for a in plan.access_ports if a.switch == switch.name]
    trunks = [t for t in plan.trunks if t.switch == switch.name]
    # The declared VLANs that have at least one port on this switch (or all of them,
    # if the plan defines them globally). We keep all of the plan's VLANs for simplicity.
    if access or trunks or plan.vlans:
        vlan_lines = generate_switch_vlan_cli(
            plan.vlans, access, trunks, supports_encap=switch_supports_encap(switch.model)
        )
        lines.extend(vlan_lines)

    lines.append("end")
    lines.append("write memory")
    return "\n".join(lines)


def generate_pc_config(device: DevicePlan, use_dhcp: bool | None = None) -> str:
    """Generates configuration instructions for a PC."""
    lines: list[str] = []
    lines.append(f"--- {device.name} ---")

    if device.interfaces:
        for iface, ip_cidr in device.interfaces.items():
            ip, prefix = ip_cidr.split("/")
            mask = prefix_to_mask(int(prefix))
            lines.append(f"IP Address: {ip}")
            lines.append(f"Subnet Mask: {mask}")
    if device.gateway:
        lines.append(f"Default Gateway: {device.gateway}")
    lines.append("DNS Server: 8.8.8.8")
    if use_dhcp:
        lines.append("Set to DHCP to get an IP automatically.")
    else:
        lines.append("Set a static IP with the values above.")
    return "\n".join(lines)
