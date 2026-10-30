"""
PTBuilder script generator.

Converts a validated TopologyPlan into JavaScript compatible
with the PTBuilder extension for Packet Tracer.
"""

from __future__ import annotations
import json
from ...domain.models.plans import TopologyPlan
from ...shared.constants import (
    pt_device_type,
    PT_CONNECT_TYPE,
    PT_CONNECT_TYPE_DEFAULT,
)


# ONE way to create devices and links, for the deploy script and the live tools
# alike: the extension's lwAddDevice (logical workspace, with a fallback to the
# global addDevice for models lw.addDevice silently refuses, e.g. Laptop-PT) and
# lwAddLink (numeric CONNECT_TYPE). core.js's addDevice/addLink stay in the
# extension for pt_send_raw users; nothing in Python calls them directly.

def add_device_call(name: str, model: str, category: str, x: int, y: int) -> str:
    """`lwAddDevice(...)` for one device, every text field through json.dumps."""
    return (
        f"lwAddDevice({json.dumps(name)}, {pt_device_type(model, category)}, "
        f"{json.dumps(model)}, {int(x)}, {int(y)})"
    )


def add_link_call(device_a: str, port_a: str, device_b: str, port_b: str, cable: str) -> str:
    """`lwAddLink(...)` for one link, with PT's numeric CONNECT_TYPE for `cable`."""
    connect_type = PT_CONNECT_TYPE.get(cable, PT_CONNECT_TYPE_DEFAULT)
    return (
        f"lwAddLink({json.dumps(device_a)}, {json.dumps(port_a)}, "
        f"{json.dumps(device_b)}, {json.dumps(port_b)}, {connect_type})"
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
        lines.append(add_device_call(dev.name, dev.model, dev.category, dev.x, dev.y) + ";")

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
        lines.append(add_link_call(link.device_a, link.port_a, link.device_b, link.port_b,
                                   link.cable) + ";")

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

    from .cli_config_generator import static_host_ips

    # Hosts keep the extension's configurePcIp here instead of host_js.host_ip_config_js
    # (what pt_host_ip_config sends), on purpose:
    # - host_ip_config_js runs dhcpRun at once. In the deploy the routers' DHCP pools
    #   are still being configured, so the request would fail; configurePcIp only sets
    #   the DHCP flag and the lease is renewed after the deploy (renew_dhcp_leases).
    # - it reports through reportResult, and the deploy lines are queued
    #   fire-and-forget: every host would push an unread result onto the bridge.
    # - it is ~3.5 KB per host against ~60 bytes, on a script that can hold
    #   a hundred hosts.
    pcs = [d for d in plan.devices if d.category in ("pc", "server", "laptop")]

    # Servers and the last host of each subnet keep their planned IP (Fix F17);
    # the router pools exclude those same addresses.
    static_names = set(static_host_ips(plan))
    pool_nets = [(ipaddress.ip_network(f"{p.network}/{p.mask}", strict=False), p) for p in plan.dhcp_pools]

    for pc in pcs:
        if pc.interfaces:
            iface_ip = next(iter(pc.interfaces.values()), None)
            if iface_ip:
                ip, prefix = iface_ip.split("/")
                mask = prefix_to_mask(int(prefix))
                gw = pc.gateway or ""
                pool = next((p for net, p in pool_nets if ipaddress.ip_address(ip) in net), None)
                # DHCP only where a pool serves the host's subnet; servers keep the
                # planned address (DNS/web need a fixed one).
                use_dhcp = pc.name not in static_names and pool is not None
                if use_dhcp:
                    lines.append(f'configurePcIp({json.dumps(pc.name)}, true);')
                else:
                    # A static host on a pool's subnet takes that pool's DNS, as a
                    # lease would have given it.
                    dns = f", {json.dumps(pool.dns)}" if pool is not None and pool.dns else ""
                    lines.append(
                        f'configurePcIp({json.dumps(pc.name)}, false, '
                        f'{json.dumps(ip)}, {json.dumps(mask)}, {json.dumps(gw)}{dns});'
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
