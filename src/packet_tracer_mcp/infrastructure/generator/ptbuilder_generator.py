"""
Generador de scripts PTBuilder.

Convierte un TopologyPlan validado en JavaScript compatible
con la extensión PTBuilder de Packet Tracer.
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
    """Genera un script JS de PTBuilder a partir de un plan validado.

    Usa lwAddDevice/lwAddLink (helpers inyectados como runtime patches) en vez del
    addDevice/addLink global — los helpers escriben al canvas Logical de PT, así
    los devices y cables aparecen inmediatamente sin necesidad de save+reload.
    """
    lines: list[str] = []

    for dev in plan.devices:
        device_type = PT_DEVICE_TYPE.get(dev.category, PT_DEVICE_TYPE_DEFAULT)
        lines.append(
            f'lwAddDevice("{dev.name}", {device_type}, "{dev.model}", {dev.x}, {dev.y});'
        )

    # Laptops WiFi: cambiar el NIC ethernet por uno inalámbrico (→ Wireless0). Debe ir
    # antes de los links/config; la auto-asociación al AP es por RF (SSID default).
    for dev in plan.devices:
        if dev.category == "laptop" and getattr(dev, "wireless", False):
            lines.append(f'swapLaptopToWireless("{dev.name}");')

    for mod in plan.modules:
        lines.append(f'addModule("{mod.device}", "{mod.slot}", "{mod.module}");')

    for link in plan.links:
        connect_type = PT_CONNECT_TYPE.get(link.cable, PT_CONNECT_TYPE_DEFAULT)
        lines.append(
            f'lwAddLink("{link.device_a}", "{link.port_a}", '
            f'"{link.device_b}", "{link.port_b}", {connect_type});'
        )

    return "\n".join(lines)


def generate_executable_script(plan: TopologyPlan) -> str:
    """
    Genera script JS completo y ejecutable: dispositivos, enlaces,
    configureIosDevice() para routers/switches, y configurePcIp() para PCs.
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

    # Fix F17: con DHCP activo, el ÚLTIMO host cableado por LAN a veces no completa el
    # DHCP DISCOVER. Como fallback determinista le asignamos IP estática (la que el planner
    # ya calculó). Identificamos el último host por subred /24 (el de mayor IP).
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
            # Laptop WiFi sin link cableado: toma DHCP por Wireless0 (asociada al AP).
            lines.append(f'configurePcIp({json.dumps(pc.name)}, true);')
        # IPv6 dual-stack: host por SLAAC (auto-config desde el RA del router)
        if plan.dual_stack:
            lines.append(f'configurePcIpv6({json.dumps(pc.name)});')

    return "\n".join(lines)


def generate_full_script(plan: TopologyPlan) -> str:
    """
    Genera el script completo: PTBuilder + bloque de configuración CLI
    como comentarios (para referencia visual).
    """
    from .cli_config_generator import generate_all_configs

    parts: list[str] = []
    parts.append(generate_ptbuilder_script(plan))

    configs = generate_all_configs(plan)
    if configs:
        parts.append("/* === Configuraciones CLI por dispositivo ===")
        parts.append("Copiar y pegar en la CLI de cada dispositivo. */")
        for device_name, cli_block in configs.items():
            parts.append(f"/* --- {device_name} ---")
            for line in cli_block.splitlines():
                parts.append(line)
            parts.append("*/ ")

    return "\n".join(parts)
