"""
PTBuilder script generator.

Converts a validated TopologyPlan into JavaScript compatible
with the PTBuilder extension for Packet Tracer.
"""

from __future__ import annotations
import json
from ...domain.models.plans import TopologyPlan
from ...shared.constants import (
    PT_DEVICE_TYPE,
    PT_DEVICE_TYPE_DEFAULT,
    PT_CONNECT_TYPE,
    PT_CONNECT_TYPE_DEFAULT,
)


def generate_ptbuilder_script(plan: TopologyPlan) -> str:
    """Generates a PTBuilder JS script from a validated plan.

    Uses lwAddDevice/lwAddLink (helpers injected as runtime patches) instead of the
    global addDevice/addLink — the helpers write to PT's Logical canvas, so devices
    and cables appear immediately without needing a save+reload.
    """
    lines: list[str] = []

    # json.dumps on every text field: a name with quotes or line breaks broke the
    # JS literal and the rest was executed as code in the Script Engine.
    # It is the same pattern that generate_executable_script() below already uses.
    for dev in plan.devices:
        device_type = PT_DEVICE_TYPE.get(dev.category, PT_DEVICE_TYPE_DEFAULT)
        lines.append(
            f'lwAddDevice({json.dumps(dev.name)}, {device_type}, '
            f'{json.dumps(dev.model)}, {int(dev.x)}, {int(dev.y)});'
        )

    # WiFi laptops: swap the ethernet NIC for a wireless one (→ Wireless0). It must go
    # before the links/config; auto-association to the AP is over RF (default SSID).
    for dev in plan.devices:
        if dev.category == "laptop" and getattr(dev, "wireless", False):
            lines.append(f'swapLaptopToWireless({json.dumps(dev.name)});')

    for mod in plan.modules:
        lines.append(
            f'addModule({json.dumps(mod.device)}, {json.dumps(mod.slot)}, '
            f'{json.dumps(mod.module)});'
        )

    for link in plan.links:
        connect_type = PT_CONNECT_TYPE.get(link.cable, PT_CONNECT_TYPE_DEFAULT)
        lines.append(
            f'lwAddLink({json.dumps(link.device_a)}, {json.dumps(link.port_a)}, '
            f'{json.dumps(link.device_b)}, {json.dumps(link.port_b)}, {connect_type});'
        )

    return "\n".join(lines)


def generate_executable_script(plan: TopologyPlan) -> str:
    """
    Generates a complete, executable JS script: devices, links,
    configureIosDevice() for routers/switches, and configurePcIp() for PCs.
    """
    from .cli_config_generator import generate_all_configs

    lines: list[str] = []
    lines.append(generate_ptbuilder_script(plan))

    configs = generate_all_configs(plan)
    for device_name, cli_block in configs.items():
        lines.append(f'configureIosDevice({json.dumps(device_name)}, {json.dumps(cli_block)});')

    from ...shared.utils import prefix_to_mask
    import ipaddress

    pcs = [d for d in plan.devices if d.category in ("pc", "server", "laptop")]

    # Fix F17: with DHCP active, the LAST wired host on a LAN sometimes does not
    # complete the DHCP DISCOVER. As a deterministic fallback we assign it a static IP
    # (the one the planner already calculated). We identify the last host per /24
    # subnet (the one with the highest IP).
    last_host_per_subnet: dict[str, str] = {}
    if plan.dhcp_pools:
        host_ip: dict[str, str] = {}
        by_subnet: dict[str, list[str]] = {}
        for pc in pcs:
            iface_ip = next(iter(pc.interfaces.values()), None) if pc.interfaces else None
            if not iface_ip:
                continue
            host_ip[pc.name] = iface_ip.split("/")[0]
            net = str(ipaddress.ip_interface(iface_ip).network)
            by_subnet.setdefault(net, []).append(pc.name)
        for net, names in by_subnet.items():
            names_sorted = sorted(
                names, key=lambda n: tuple(int(o) for o in host_ip[n].split("."))
            )
            last_host_per_subnet[net] = names_sorted[-1]

    last_names = set(last_host_per_subnet.values())

    for pc in pcs:
        if pc.interfaces:
            iface_ip = next(iter(pc.interfaces.values()), None)
            if iface_ip:
                ip, prefix = iface_ip.split("/")
                mask = prefix_to_mask(int(prefix))
                gw = pc.gateway or ""
                use_dhcp = bool(plan.dhcp_pools) and pc.name not in last_names
                if use_dhcp:
                    lines.append(f'configurePcIp({json.dumps(pc.name)}, true);')
                else:
                    lines.append(
                        f'configurePcIp({json.dumps(pc.name)}, false, '
                        f'{json.dumps(ip)}, {json.dumps(mask)}, {json.dumps(gw)});'
                    )
        elif getattr(pc, "wireless", False) and plan.dhcp_pools:
            # WiFi laptop with no wired link: gets DHCP via Wireless0 (associated with the AP).
            lines.append(f'configurePcIp({json.dumps(pc.name)}, true);')
        # IPv6 dual-stack: host via SLAAC (auto-config from the router's RA)
        if plan.dual_stack:
            lines.append(f'configurePcIpv6({json.dumps(pc.name)});')

    return "\n".join(lines)


def generate_full_script(plan: TopologyPlan) -> str:
    """
    Generates the complete script: PTBuilder + the CLI configuration block
    as comments (for visual reference).
    """
    from .cli_config_generator import generate_all_configs

    parts: list[str] = []
    parts.append(generate_ptbuilder_script(plan))

    configs = generate_all_configs(plan)
    if configs:
        parts.append("/* === CLI configuration per device ===")
        parts.append("Copy and paste into each device's CLI. */")
        for device_name, cli_block in configs.items():
            parts.append(f"/* --- {device_name} ---")
            for line in cli_block.splitlines():
                parts.append(line)
            parts.append("*/ ")

    return "\n".join(parts)
